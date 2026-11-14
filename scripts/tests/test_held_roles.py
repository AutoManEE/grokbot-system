"""Step 0: hard hold (alignment/held_roles.json). Accident guard plus detection."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import importlib, json, os, subprocess, sys
from pathlib import Path
import pytest
sys.path.insert(0, GB_ROOT + "/scripts")
import alignment_check as ac
import held_roles as hr


@pytest.fixture
def held(tmp_path, monkeypatch):
    p = tmp_path / "held_roles.json"
    p.write_text(json.dumps({"holds": [], "cosigns": []}))
    monkeypatch.setattr(ac, "HELD_ROLES", p)
    return p


def test_existing_role_is_held_before_brief_check(held):
    hr.hold("CBO", "test hold", "CAO", held)
    q, why = ac.is_quarantined("CBO")
    assert q and "hard hold HOLD-CBO-1" in why


def test_cio_alone_does_not_clear(held):
    h = hr.hold("CBO", "t", "CAO", held)
    hr.cosign(h["id"], "CIO", "card x", held)
    assert ac.is_held("CBO")[0]
    assert "missing CSO" in ac.is_held("CBO")[1]


def test_cso_alone_does_not_clear(held):
    h = hr.hold("CBO", "t", "CAO", held)
    hr.cosign(h["id"], "CSO", "card y", held)
    assert ac.is_held("CBO")[0]
    assert "missing CIO" in ac.is_held("CBO")[1]


def test_both_cosigns_clear(held):
    h = hr.hold("CBO", "t", "CAO", held)
    hr.cosign(h["id"], "CIO", "card x", held)
    hr.cosign(h["id"], "CSO", "card y", held)
    assert not ac.is_held("CBO")[0]
    assert not ac.is_quarantined("CBO")[0]


def test_cosign_for_other_hold_does_not_clear_a_new_hold(held):
    h1 = hr.hold("CBO", "t", "CAO", held)
    hr.cosign(h1["id"], "CIO", "x", held); hr.cosign(h1["id"], "CSO", "y", held)
    hr.hold("CBO", "again", "CAO", held)
    assert ac.is_held("CBO")[0]


def test_only_cio_cso_cosign_and_only_cao_holds(held):
    h = hr.hold("CBO", "t", "CAO", held)
    with pytest.raises(SystemExit):
        hr.cosign(h["id"], "CBO", "x", held)
    with pytest.raises(SystemExit):
        hr.cosign(h["id"], "CIO", "  ", held)
    with pytest.raises(SystemExit):
        hr.hold("CBO", "t", "CSO", held)


def test_unreadable_fails_closed(held):
    held.write_text("{not json")
    assert ac.is_held("CBO")[0]


def test_gate_request_refuses_held_role(held, tmp_path, monkeypatch):
    hr.hold("CAO", "t", "CAO", held)
    monkeypatch.setenv("GB_STATE", str(tmp_path / "st"))
    import gate; importlib.reload(gate)
    with pytest.raises(SystemExit) as e:
        gate.request("h1", "CAO", "irreversible", "x")
    assert "hard hold" in str(e.value)


def test_cost_gate_waits_for_held_role(held):
    import cost_gate
    hr.hold("CAO", "t", "CAO", held)
    lim = {"max_paid_turns_per_task": 5, "max_paid_turns_per_day_when_ollama_down": 10}
    st, why = cost_gate.check(1, 0, lim, role="CAO")
    assert st == "WAIT" and "held role" in why


def test_flow_delegate_refuses_held_role(held, tmp_path, monkeypatch):
    hr.hold("CAO", "t", "CAO", held)
    monkeypatch.setenv("GB_STATE", str(tmp_path / "st"))
    import flow; importlib.reload(flow)
    task = tmp_path / "t.json"; task.write_text('{"text": "x", "source": "internal"}')
    with pytest.raises(flow.Refused) as e:
        flow.delegate("CAO", str(task), "HELD-TEST")
    assert "hard hold" in str(e.value)
    assert not (tmp_path / "st" / "flow" / "HELD-TEST").exists()


def test_missing_held_roles_file_fails_closed(tmp_path):                       # #36 quiz/held condition 3
    import alignment_check as ac
    data, err = ac.load_held_roles(tmp_path / "gone.json")
    assert data is None and "missing" in err
    held, why = ac.is_held("CRO", tmp_path / "gone.json")
    assert held and "fail" in why.lower()


# ---- the Owner: "CIO co-signs holds on the CSO" ----
def _reg(tmp_path):
    p = tmp_path / "held_roles.json"
    p.write_text(json.dumps(dict(holds=[dict(id="HOLD-CSO-1", role="CSO", reason="x", by="CAO"),
                                        dict(id="HOLD-CRO-1", role="CRO", reason="x", by="CAO")], cosigns=[])))
    return p


def test_cio_alone_clears_cso_hold(tmp_path):
    import held_roles as hr
    p = _reg(tmp_path)
    assert ac.is_held("CSO", p)[0]
    hr.cosign("HOLD-CSO-1", "CIO", "t-cio", path=p)
    assert not ac.is_held("CSO", p)[0]
    assert ac.hold_clearers("CSO") == ("CIO",) and ac.CSO_HOLD_QUOTE == "t2s1"


def test_cso_cosign_on_cso_hold_refused(tmp_path):
    import held_roles as hr
    p = _reg(tmp_path)
    with pytest.raises(SystemExit) as e:
        hr.cosign("HOLD-CSO-1", "CSO", "t-cso", path=p)
    assert "own hold" in str(e.value)
    ok, missing = ac.hold_cleared(dict(id="HOLD-CSO-1", role="CSO"), [dict(hold_id="HOLD-CSO-1", by="CSO", ref="r")])
    assert not ok and missing == ["CIO"]


@pytest.mark.parametrize("by", ["CBO", "CAO", "CTO", "Owner"])
def test_third_role_cannot_clear_cso_hold(tmp_path, by):
    import held_roles as hr
    p = _reg(tmp_path)
    with pytest.raises(SystemExit):
        hr.cosign("HOLD-CSO-1", by, "t-x", path=p)
    ok, missing = ac.hold_cleared(dict(id="HOLD-CSO-1", role="CSO"), [dict(hold_id="HOLD-CSO-1", by=by, ref="r")])
    assert not ok and missing == ["CIO"]


def test_other_holds_still_need_cio_and_cso(tmp_path):
    import held_roles as hr
    p = _reg(tmp_path)
    hr.cosign("HOLD-CRO-1", "CIO", "t-cio", path=p)
    held, why = ac.is_held("CRO", p)
    assert held and "CSO" in why
    hr.cosign("HOLD-CRO-1", "CSO", "t-cso", path=p)
    assert not ac.is_held("CRO", p)[0]


def test_verdict_audit_h1_flags_bad_hold_cosigns(tmp_path):
    import verdict_audit as va
    p = _reg(tmp_path)
    d = json.loads(p.read_text())
    d["cosigns"] = [dict(hold_id="HOLD-CSO-1", by="CSO", ref="r"), dict(hold_id="HOLD-CSO-1", by="CBO", ref="r"),
                    dict(hold_id="HOLD-CSO-1", by="CIO", ref="r"), dict(hold_id="HOLD-CRO-1", by="CSO", ref="r")]
    p.write_text(json.dumps(d))
    probs = va.audit_holds(str(p))
    assert len(probs) == 2 and any("CSO cosigned its own hold" in x for x in probs) and any("t2s1" in x for x in probs)
    assert va.audit_holds(str(tmp_path / "gone.json"))[0].startswith("H1 held_roles")


# ---- #36 retest-2 (CSO): hold roles must normalize to a known C-role; otherwise fail closed

@pytest.mark.parametrize("role", ["CSO\u200b", "C\u200bSO", "C S O", "CS0", "ZZZ", "", "\ufeffCSO", "CSOX"])
def test_invalid_hold_role_refused_and_fails_closed(tmp_path, role):
    import alignment_check as ac, held_roles as hr, verdict_audit as va
    assert ac.valid_hold_role(role) is None
    p = tmp_path / "h.json"; p.write_text(json.dumps(dict(holds=[], cosigns=[])))
    with pytest.raises(SystemExit, match="not a known C-role"):
        hr.hold(role, "test", "CAO", path=p)
    p.write_text(json.dumps(dict(holds=[dict(id="HOLD-X-1", role=role, reason="r")], cosigns=[])))
    for acro in ("CSO", "CFO", "CAO"):
        held, why = ac.is_held(acro, p)
        assert held and "failing closed" in why
    assert any("invalid or unknown hold role" in x for x in va.audit_holds(str(p)))


@pytest.mark.parametrize("role", ["CSO", "cso", " Cso ", "CXO"])
def test_valid_hold_roles_normalize(tmp_path, role):
    import alignment_check as ac, held_roles as hr
    assert ac.valid_hold_role(role) == role.strip().upper()
    p = tmp_path / "h.json"; p.write_text(json.dumps(dict(holds=[], cosigns=[])))
    rec = hr.hold(role, "test", "CAO", path=p)
    assert rec["role"] == role.strip().upper() and ac.is_held(role.strip().upper(), p)[0]
    assert not ac.is_held("CFO", p)[0]


def test_no_known_role_list_fails_closed():
    import alignment_check as ac
    assert ac.valid_hold_role("CSO", known=set()) is None
