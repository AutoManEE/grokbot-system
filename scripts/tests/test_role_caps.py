"""Role-cap patch tests: ASCII role_cap fail-closed, derived CTO_CAPS, clamp,
cap_hits.jsonl dir_fd/O_NOFOLLOW, invalid still refused. tmp GB_STATE/GB_BOTS only."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, stat, sys, pytest
from pathlib import Path


def _scripts_dir():
    p = Path(__file__).resolve()
    if (p.parents[1] / "flow.py").exists():
        return p.parents[1]
    cand = p.parents[1] / "b" / "scripts"
    if (cand / "flow.py").exists():
        return cand
    return Path(GB_ROOT + "/scripts")


def _load(monkeypatch, tmp_path):
    scripts = _scripts_dir()
    sys.path.insert(0, GB_ROOT + "/scripts")
    sys.path.insert(0, str(scripts))
    monkeypatch.setenv("GB_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("GB_BOTS", str(tmp_path / "bots"))
    import importlib
    import flow, relay
    importlib.reload(flow)
    monkeypatch.setattr(flow, "BOTS", str(tmp_path / "bots"))
    monkeypatch.setattr(relay, "bots", lambda: str(tmp_path / "bots"))
    return flow, relay


def _put(tmp_path, hid, cap, kind=None):
    d = tmp_path / "bots" / hid
    d.mkdir(parents=True, exist_ok=True)
    if kind is None:
        kind = "s-suite" if hid.startswith("S") else "assistant"
    (d / "settings.json").write_text(json.dumps(dict(id=hid, kind=kind, model="x", num_predict=cap)))


def test_sbo_cfo_1024_clamps_to_160(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    _put(tmp_path, "SBO-CFO", 1024)
    assert flow.role_cap("SBO-CFO") == 160
    assert flow.token_cap("SBO-CFO") == 160
    assert relay._bot_cap("SBO-CFO") == 160


def test_sao_cbo_1024_clamps_to_160(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    _put(tmp_path, "SAO-CBO", 1024)
    assert flow.role_cap("SAO-CBO") == 160
    assert flow.token_cap("SAO-CBO") == 160
    assert relay._bot_cap("SAO-CBO") == 160


def test_a_at_384_stays_384_and_over_clamps(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    _put(tmp_path, "ABO", 384)
    assert flow.role_cap("ABO") == 384
    assert flow.token_cap("ABO") == 384
    assert relay._bot_cap("ABO") == 384
    _put(tmp_path, "ACO", 1024)
    assert flow.token_cap("ACO") == 384
    assert relay._bot_cap("ACO") == 384


def test_reviewer_role_cap_160(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    assert flow.role_cap("reviewer") == 160
    assert relay.role_cap("reviewer") == 160
    with pytest.raises(flow.Refused, match="not a positive integer"):
        flow.token_cap("reviewer")
    assert flow.token_cap("reviewer", 1024) == 160
    assert flow.token_cap("reviewer", 80) == 80


def test_cto_caps_is_read_only_derived_view(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    assert dict(flow.CTO_CAPS) == {"SBO": 160, "ABO": 384, "AAO": 384}
    assert flow.CTO_CAPS["SBO"] == flow.role_cap("SBO")
    assert flow.CTO_CAPS["ABO"] == flow.role_cap("ABO")
    assert flow.CTO_CAPS["AAO"] == flow.role_cap("AAO")
    with pytest.raises(TypeError):
        flow.CTO_CAPS["SBO"] = 1


def test_invalid_still_refused(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    for hid, val in (("SBO-CFO", 0), ("SAO-CBO", -1), ("ABO", True), ("AAO", "abc"), ("SFO-CBO", None)):
        if val is None:
            d = tmp_path / "bots" / hid
            d.mkdir(parents=True, exist_ok=True)
            (d / "settings.json").write_text(json.dumps(dict(id=hid, num_predict=None)))
        else:
            _put(tmp_path, hid, val)
        with pytest.raises(flow.Refused, match="not a positive integer"):
            flow.token_cap(hid)
        assert relay._bot_cap(hid) is None


# Cyrillic A in "SAO" (S + CYRILLIC A + O)
_CYRILLIC_SAO = "S" + "\u0410" + "O"
_BAD_IDS = [None, "", "S", "A", "Reviewer", "abo", "ABO\n", "XYZ", "CBO", "SXX-../../x", _CYRILLIC_SAO, "ABO "]


def test_role_cap_refuses_invalid_ids_no_model_call(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    import run
    seen = []
    monkeypatch.setattr(run, "run", lambda *a, **k: seen.append(("run", a, k)) or "nope")
    monkeypatch.setattr(run, "complete", lambda *a, **k: seen.append(("complete", a, k)) or "nope")
    for hid in _BAD_IDS:
        with pytest.raises(flow.Refused, match="not a valid bot id"):
            flow.role_cap(hid)
        with pytest.raises(flow.Refused, match="not a valid bot id"):
            flow.token_cap(hid, 1024)
        assert relay._bot_cap(hid) is None
    assert seen == []


def test_cap_hit_logged_append_only(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    import run
    _put(tmp_path, "SBO-CFO", 1024)
    run.LAST_CALL = dict(bot="SBO-CFO", eval_count=160)
    with pytest.raises(flow.Refused, match=r"token cap \(160\)"):
        flow.raise_if_cap_hit("SBO-CFO", phase="S")
    path = tmp_path / "state" / "budget" / "cap_hits.jsonl"
    assert path.is_file()
    recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(recs) == 1
    r = recs[0]
    assert r["bot"] == "SBO-CFO" and r["phase"] == "S" and r["eval_count"] == 160 and r["cap"] == 160
    assert "ts" in r
    run.LAST_CALL = dict(bot="SBO-CFO", eval_count=200)
    with pytest.raises(flow.Refused, match=r"token cap \(160\)"):
        flow.raise_if_cap_hit("SBO-CFO", phase="S")
    recs2 = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(recs2) == 2 and recs2[0] == r


def test_budget_symlink_refused(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", state / "budget")
    assert stat.S_ISLNK(os.lstat(state / "budget").st_mode)
    with pytest.raises(flow.Refused, match="symlink"):
        flow.log_cap_hit("ABO", "A", 384, 384, state=str(state))
    assert not list((tmp_path / "elsewhere").glob("*"))


def test_cap_hits_symlink_refused_nofollow(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    state = tmp_path / "state"
    budget = state / "budget"
    budget.mkdir(parents=True)
    target = tmp_path / "planted.jsonl"
    target.write_text("nope\n")
    os.symlink(target, budget / "cap_hits.jsonl")
    with pytest.raises(flow.Refused, match="cap_hits.jsonl write failed"):
        flow.log_cap_hit("ABO", "A", 384, 384, state=str(state))
    assert target.read_text() == "nope\n"


def test_log_cap_hit_existing_file_not_fileexistserror(tmp_path, monkeypatch):
    flow, relay = _load(monkeypatch, tmp_path)
    state = tmp_path / "state"
    budget = state / "budget"
    budget.mkdir(parents=True)
    (budget / "cap_hits.jsonl").write_text("")
    rec = flow.log_cap_hit("ABO", "A", 10, 384, state=str(state))
    assert rec["bot"] == "ABO"
    lines = (budget / "cap_hits.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["bot"] == "ABO"


def test_override_above_role_cap_never_exceeds(tmp_path, monkeypatch):
    """C2 option (b): an override above the role cap (S 160, A 384, reviewer 160) is clamped; it can never go higher."""
    flow, relay = _load(monkeypatch, tmp_path)
    _put(tmp_path, "SBO", 80)   # settings lower; override must still not exceed role
    assert flow.token_cap("SBO", 1024) == 160
    assert flow.token_cap("SBO", 160) == 160
    assert flow.token_cap("SBO", 80) == 80
    _put(tmp_path, "ABO", 4)
    assert flow.token_cap("ABO", 9999) == 384
    assert flow.token_cap("ABO", 384) == 384
    assert flow.token_cap("ABO", 4) == 4
    # reviewer has no settings file; override 1024 clamps to 160, 40 stays 40
    assert flow.token_cap("reviewer", 1024) == 160
    assert flow.token_cap("reviewer", 160) == 160
    assert flow.token_cap("reviewer", 40) == 40
    # option (b): when an override is passed, settings are ignored (SSO/ASO-style settings 4 + override 1024 -> role cap)
    _put(tmp_path, "SSO", 4)
    assert flow.token_cap("SSO", 1024) == 160
    _put(tmp_path, "ASO", 4)
    assert flow.token_cap("ASO", 1024) == 384
