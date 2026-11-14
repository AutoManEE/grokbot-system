"""CSO go-live screen fixes C-F for relay.py: routes.json own-S only + malformed refuses (C), LIVE
marker content (D), audit high-water + O_EXCL handoffs (E), broadcast cap + origin C-role list (F).
tmp GB_STATE/GB_BOTS only; no models; LIVE is never created outside tmp."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, subprocess, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests"); sys.path.insert(0, GB_ROOT + "/bots")
from test_relay import r, box   # noqa: F401


def _routes(r, data):
    p = r.tmp / "s" / "relay" / "routes.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))


# ---- C: routes.json ----
@pytest.mark.parametrize("target", ["SSO", "OWNER", "Owner", "CSO", "ASO", "SFO-CSO", "SBO", "STO-CBO", "../SFO", "", None, 5, "SFOX"])
def test_route_target_must_be_own_s(r, target):
    _routes(r, {"routes": {"AFO|CBO": target}})
    r.send("ABO", "AFO", "Can you check the budget line format?", "CBO")
    with pytest.raises(r.Refused, match="routes.json"): r.route("AFO")
    assert not (r.tmp / "s" / "relay" / "AFO" / "dg").exists()


@pytest.mark.parametrize("target", ["SFO", "SFO-CBO", "sfo-cbo"])
def test_own_s_targets_allowed(r, target):
    _routes(r, {"routes": {"AFO|CBO": target}})
    r.send("ABO", "AFO", "Can you check the budget line format?", "CBO")
    assert r.route("AFO")["handoffs"][0]["s"] == target.upper()


@pytest.mark.parametrize("data", ["[1]", "{bad", '{"routes": [1]}', '"x"', '{"routes": {"bad key": "SFO"}}', "\x00{"])
def test_malformed_routes_refuses_not_crash(r, data):
    _routes(r, data)
    r.send("ABO", "AFO", "Can you check the budget line format?", "CBO")
    with pytest.raises(r.Refused, match="routes.json"): r.route("AFO")
    env = dict(os.environ, GB_STATE=str(r.tmp / "s"), GB_BOTS=str(r.tmp / "bots"))
    c = subprocess.run([sys.executable, GB_ROOT + "/scripts/relay.py", "route", "--a", "AFO"], env=env, capture_output=True, text=True)
    assert c.returncode == 2 and "Traceback" not in c.stderr and "REFUSED" in c.stderr


def test_default_s_from_settings_must_be_own(r):
    (r.tmp / "bots" / "AFO" / "settings.json").write_text(json.dumps(dict(id="AFO", kind="assistant", s_bots=["SSO"])))
    r.send("ABO", "AFO", "Can you check the budget line format?", "CBO")
    with pytest.raises(r.Refused, match="own S"): r.route("AFO")


# ---- D: LIVE marker ----
def _live(r): return r.tmp / "s" / "relay" / "LIVE"


@pytest.mark.parametrize("kind", ["symlink_file", "symlink_dangling", "dir", "empty", "text", "no_sha", "wrong_sha", "no_pass_ref", "list"])
def test_live_marker_must_be_valid(r, kind, tmp_path):
    L = _live(r); os.remove(L); good = json.loads(r.live_content("CSO PASS test-screen"))
    other = tmp_path / "other.json"; other.write_text(json.dumps(good))
    if kind == "symlink_file": os.symlink(other, L)                    # even a symlink to valid content
    elif kind == "symlink_dangling": os.symlink(tmp_path / "nowhere", L)
    elif kind == "dir": L.mkdir()
    elif kind == "empty": L.write_text("")
    elif kind == "text": L.write_text("CSO PASS")
    elif kind == "no_sha": L.write_text(json.dumps(dict(cso_pass_ref="CSO PASS x")))
    elif kind == "wrong_sha": L.write_text(json.dumps(dict(good, relay_sha256="0" * 64)))
    elif kind == "no_pass_ref": L.write_text(json.dumps(dict(good, cso_pass_ref="screen 22:45")))
    elif kind == "list": L.write_text("[1]")
    with pytest.raises(r.Refused, match="NOT live"): r.send("ABO", "AFO", "Please review the plan.", "t161u")
    with pytest.raises(r.Refused, match="NOT live"): r.route("AFO")
    assert not (r.tmp / "s" / "relay" / "audit.jsonl").exists() and not list((r.tmp / "s" / "relay").glob("A*"))


def test_relay_edit_after_screen_turns_it_off(r, monkeypatch, tmp_path):
    assert r.live_status() == (True, "")
    copy = tmp_path / "relay_copy.py"; copy.write_text(open(r.RELAY_PY).read() + "\n# edit\n")
    monkeypatch.setattr(r, "RELAY_PY", str(copy))
    ok, why = r.live_status(); assert not ok and "sha256" in why


def test_not_live_creates_no_relay_dir_or_audit(tmp_path, monkeypatch):
    import relay
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
    for call in (lambda: relay.send("ABO", "AFO", "Plan note.", "CBO"), lambda: relay.broadcast("ABO", "Plan note.", "CBO"), lambda: relay.route("AFO")):
        with pytest.raises(relay.Refused, match="NOT live"): call()
    assert not (tmp_path / "s").exists()


def test_live_template_prints_and_never_creates(tmp_path):
    env = dict(os.environ, GB_STATE=str(tmp_path / "s"))
    c = subprocess.run([sys.executable, GB_ROOT + "/scripts/relay.py", "live-template", "--cso-ref", "CSO PASS relay_golive"], env=env, capture_output=True, text=True)
    import relay
    assert c.returncode == 0 and json.loads(c.stdout)["relay_sha256"] == relay.file_sha256(relay.RELAY_PY)
    assert not (tmp_path / "s").exists()


# ---- E: audit high-water outside relay/, ES-CSO alarm, O_EXCL handoffs ----
def _audit(r): return r.tmp / "s" / "relay" / "audit.jsonl"
def _alarms(r): return sorted((r.tmp / "s" / "queue" / "es_cso").glob("relay-audit.*.ALARM.json"))


def test_hwm_kept_outside_relay_and_tracks_audit(r):
    r.broadcast("ABO", "Plan is ready for review.", "CBO")
    h = json.load(open(r.hwm_path()))
    assert "/relay/" not in r.hwm_path() and h["lines"] == len(_audit(r).read_text().splitlines()) == 5   # broadcast_sent + 4 writes (ASO included, the Owner)
    assert r.verify()[0] and _alarms(r) == []


@pytest.mark.parametrize("tamper", ["truncate_tail", "delete", "delete_both", "empty", "bad_hwm"])
def test_missing_or_shortened_audit_refuses_and_alarms(r, tamper):
    for i in range(2): r.send("ABO", "AFO", f"Plan note {i} for review.", "CBO")
    A = _audit(r); lines = A.read_text().splitlines()
    if tamper == "truncate_tail": A.write_text("\n".join(lines[:-1]) + "\n")       # a valid chain, just shorter
    elif tamper == "delete": A.unlink()
    elif tamper == "delete_both": A.unlink(); os.remove(r.hwm_path())               # messages remain in inboxes
    elif tamper == "empty": A.write_text("")
    elif tamper == "bad_hwm": open(r.hwm_path(), "w").write("{")
    before = len(list((r.tmp / "s" / "relay" / "AFO" / "inbox").glob("*.json")))
    with pytest.raises(r.Refused, match="not intact"): r.send("ABO", "AFO", "Another plan note.", "CBO")
    with pytest.raises(r.Refused, match="not intact"): r.route("AFO")
    assert len(list((r.tmp / "s" / "relay" / "AFO" / "inbox").glob("*.json"))) == before
    assert not r.verify()[0] and len(_alarms(r)) == 1
    a = json.load(open(_alarms(r)[0])); assert a["line"] == "ES-CSO" and a["verdict"] == "ALARM" and a["to"] == "Owner"
    assert not (A.exists() and len(A.read_text().splitlines()) > len(lines))          # nothing appended after tamper


def test_dg_written_o_excl_never_overwritten(r):
    out = r.send("ABO", "AFO", "Can you check the budget line format?", "CBO"); r.route("AFO")
    dg = r.tmp / "s" / "relay" / "AFO" / "dg" / f"{out['id']}.json"; orig = dg.read_text()
    os.remove(r.tmp / "s" / "relay" / "AFO" / "routed.jsonl")                       # forces a re-route
    res = r.route("AFO")
    assert res["handoffs"] == [] and res["skipped_existing"] == [out["id"]] and dg.read_text() == orig
    assert len(_alarms(r)) == 1 and "dg_exists_skipped" in _audit(r).read_text()
    src = open(r.RELAY_PY).read()
    assert "json.dump(dg, open(" not in src and "json.dump(dict(res, msg_id=mid, ts=now()), open(" not in src and src.count("_excl_json(") >= 3


# ---- F: broadcast cap per sender per hour; origin checked against the real C-role list ----
def test_broadcast_cap_per_sender_per_hour(r, monkeypatch):
    assert r.BROADCAST_CAP_PER_HOUR == 5
    for i in range(5): r.broadcast("ABO", f"Status line {i} for the batch review today.", "CBO")
    files = len(list((r.tmp / "s" / "relay").rglob("inbox/*.json")))
    with pytest.raises(r.Refused, match="broadcast cap"): r.broadcast("ABO", "Status line six for the review.", "CBO")
    assert len(list((r.tmp / "s" / "relay").rglob("inbox/*.json"))) == files       # nothing delivered
    r.broadcast("AFO", "Another sender has its own cap today.", "CFO")              # per sender
    for p in (r.tmp / "s" / "relay").rglob("inbox/*.json"): p.unlink()               # deleting inboxes does not reset it
    with pytest.raises(r.Refused, match="broadcast cap"): r.broadcast("ABO", "Status line seven for review.", "CBO")
    real = r.time.time
    monkeypatch.setattr(r.time, "time", lambda: real() + 3601)                       # an hour later: allowed again
    assert r.broadcasts_last_hour("ABO") == 0
    r.broadcast("ABO", "Status line eight for the review.", "CBO")


@pytest.mark.parametrize("origin", ["CZZ", "CQO", "CSX", "Owner", "", "t1u ", "t1u\nCBO", "cbo"])
def test_origin_must_be_real_c_role_or_owner_ref(r, origin):
    with pytest.raises(r.Refused, match="origin"): r.send("ABO", "AFO", "Please review the plan.", origin)


@pytest.mark.parametrize("origin", ["CBO", "CSO", "t159u"])
def test_real_origins_pass(r, origin):
    assert r.send("ABO", "AFO", "Please review the plan.", origin)["delivered"] == {"AFO": "inbox"}


# ---- Re-screen 2, first item: run-batch refuses entirely without a valid LIVE (no model call, nothing written) ----
@pytest.mark.parametrize("live", ["absent", "empty", "bad_json", "symlink"])
def test_run_batch_refuses_without_valid_live_even_with_dg_files(r, monkeypatch, live, tmp_path):
    import run
    calls = []
    monkeypatch.setattr(run, "run", lambda *a, **k: calls.append(a) or "x")
    monkeypatch.setattr(run, "complete", lambda *a, **k: calls.append(a) or "VERDICT: PASS")
    monkeypatch.setattr(run, "unload", lambda *a, **k: calls.append(a) or True)
    dg = r.tmp / "s" / "relay" / "AFO" / "dg"; dg.mkdir(parents=True)
    (dg / "f1.json").write_text(json.dumps(dict(line="DG", **{"from": "AFO"}, to="SFO", msg_id="f1", counterpart="ABO", text="Plan note.", origin="CBO")))
    L = r.tmp / "s" / "relay" / "LIVE"; os.remove(L)
    if live == "empty": L.write_text("")
    elif live == "bad_json": L.write_text("{")
    elif live == "symlink": other = tmp_path / "o.json"; other.write_text(r.live_content("CSO PASS x")); os.symlink(other, L)
    before = sorted(str(p) for p in (r.tmp / "s").rglob("*"))
    with pytest.raises(r.Refused, match="NOT live"): r.run_batch()
    env = dict(os.environ, GB_STATE=str(r.tmp / "s"), GB_BOTS=str(r.tmp / "bots"))
    c = subprocess.run([sys.executable, GB_ROOT + "/scripts/relay.py", "run-batch"], env=env, capture_output=True, text=True)
    assert c.returncode == 2 and "NOT live" in c.stderr
    assert calls == [] and sorted(str(p) for p in (r.tmp / "s").rglob("*")) == before


# ---- Re-screen 2 item 1: LIVE exactly two keys, exact hash, ref contains 'CSO PASS' ----
FINAL_REF = "CSO PASS gb_state/reports/cso/relay_golive_2034-01-12.md#rescreen-3"


@pytest.mark.parametrize("mut", ["extra_key", "upper", "pad", "lead_space", "ref_pass_only", "ref_lower", "rescreen2_no_pass", "ref_int", "sha_int"])
def test_live_exact_keys_and_hash(r, mut):
    good = json.loads(r.live_content(FINAL_REF)); j = dict(good)
    if mut == "extra_key": j["extra"] = "x"
    elif mut == "upper": j["relay_sha256"] = good["relay_sha256"].upper()
    elif mut == "pad": j["relay_sha256"] = good["relay_sha256"] + " "
    elif mut == "lead_space": j["relay_sha256"] = " " + good["relay_sha256"]
    elif mut == "ref_pass_only": j["cso_pass_ref"] = "PASS gb_state/reports/cso/x.md"
    elif mut == "ref_lower": j["cso_pass_ref"] = "cso pass gb_state/reports/cso/x.md"
    elif mut == "rescreen2_no_pass": j["cso_pass_ref"] = "gb_state/reports/cso/relay_golive_2034-01-12.md#rescreen-2"
    elif mut == "ref_int": j["cso_pass_ref"] = 5
    elif mut == "sha_int": j["relay_sha256"] = 5
    _live(r).write_text(json.dumps(j))
    assert not r.live_status()[0]
    with pytest.raises(r.Refused, match="NOT live"): r.send("ABO", "AFO", "Please review the plan.", "t161u")


def test_live_final_ref_accepted(r):
    _live(r).write_text(r.live_content(FINAL_REF))
    assert r.live_status() == (True, "") and set(json.loads(_live(r).read_text())) == {"cso_pass_ref", "relay_sha256"}
