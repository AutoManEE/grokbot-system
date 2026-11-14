"""#36 C8 (CTO ruling option b): gate ES-CSO escalation writers go through line_guard.guarded_write.
Temp GB_STATE only. Accident guard plus detection, not security."""
import importlib, json, os, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path))
    import gate
    importlib.reload(gate)
    return gate


def test_hold_escalation_is_on_its_line(g, tmp_path):
    import line_guard
    g.request("h1", "CFO", "money", "open", {"action": "purchase", "amount": 20, "category": "software"})
    rec = json.load(open(tmp_path / "queue" / "es_cso" / "h1.HOLD.json"))
    assert rec["line"] == "ES-CSO" and rec["from"] == "CSO" and rec["to"] == "Owner"
    assert line_guard.check_record(rec) == []


@pytest.mark.parametrize("role", ["CMO", "CBO"])
def test_cmo_cbo_have_no_outbound_line(g, tmp_path, role):
    """#36 C8 retest: CMO and CBO are off the outbound list until Owner adds a channel and a GT line."""
    import line_guard
    assert "outbound" not in g.LINES and line_guard.check_outbound(role)   # #37: gate asks line_guard, no own list
    with pytest.raises(SystemExit, match="no GATE line"):
        g.request(f"o-{role}", role, "outbound", "post on X")
    ok, why = g.require(f"o-{role}")
    assert not ok and not (tmp_path / "gates" / f"o-{role}.request.json").exists()


def test_off_line_escalation_refused_and_not_written(g, tmp_path, monkeypatch):
    import line_guard
    monkeypatch.setattr(line_guard, "load_lines", lambda brief=None: {})   # ES-CSO missing from the table
    with pytest.raises(line_guard.LineRefused):
        g.request("h2", "CFO", "money", "open", {"action": "purchase", "amount": 20, "category": "software"})
    assert not (tmp_path / "queue" / "es_cso" / "h2.HOLD.json").exists()
    ok, why = g.require("h2")
    assert not ok and "HOLD" in why                                             # verdict still blocks (fail closed)


# ---- #36 C8 retest (CSO p_c8.py): fail-open on detection

@pytest.mark.parametrize("v", ["HOLD", "VETO"])
def test_failed_escalation_writes_fallback_alarm(g, tmp_path, monkeypatch, v):
    import line_guard
    monkeypatch.setattr(line_guard, "load_lines", lambda brief=None: {})
    if v == "HOLD":
        d, kind = {"action": "purchase", "amount": 20, "category": "software"}, "money"
    else:
        d, kind = {"action": "withdraw", "amount": 10}, "money"
    with pytest.raises(line_guard.LineRefused):
        g.request(f"e{v}", "CFO", kind, "x", d)
    assert not g.require(f"e{v}")[0]
    fb = list((tmp_path / "alarms" / "fallback").glob(f"*e{v}.{v}.json"))
    assert len(fb) == 1
    rec = json.load(open(fb[0]))
    assert rec["fallback"] is True and rec["to"] == "Owner" and "LineRefused" in rec["error"]

