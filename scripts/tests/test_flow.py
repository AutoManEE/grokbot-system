GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, time, importlib, pytest


@pytest.fixture
def cro_rehold(tmp_path, monkeypatch):
    """Live HOLD-CRO-1 was released. To keep testing the
    quarantine path, re-hold CRO in a tmp held_roles.json."""
    import held_roles as _hr
    import alignment_check as _ac
    p = tmp_path / "held_rehold.json"; p.write_text('{"holds": [], "cosigns": []}')
    _hr.hold("CRO", "test: CRO re-held to exercise quarantine", "CAO", p)
    monkeypatch.setattr(_ac, "HELD_ROLES", p); return p
sys.path.insert(0, GB_ROOT + "/scripts")
REP = "Objective: x\nOptions: a (Deltas 0, Pros p, Cons c)\nDecision: a\nReasoning: r\nConfidence: 90"
@pytest.fixture
def f(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import flow; importlib.reload(flow); return flow
def test_order_enforced(f):
    f.new("t", "COO", "count")
    with pytest.raises(f.Refused): f.a_report("t", REP)          # no S work yet
    with pytest.raises(f.Refused): f.c_review("t", "ACCEPT", "")  # no report yet
    f.s_run("t", "3 words")
    with pytest.raises(f.Refused): f.s_run("t", "again")          # waiting on review
    with pytest.raises(f.Refused): f.a_report("t", "not a report")
    with pytest.raises(f.Refused): f.close("t")
    time.sleep(0.01); f.a_report("t", REP); f.c_review("t", "ADJUST", "show the count")
    with pytest.raises(f.Refused): f.close("t")
    time.sleep(0.01); f.s_run("t", "count = 3"); time.sleep(0.01); f.a_report("t", REP); time.sleep(0.01)
    f.c_review("t", "ACCEPT", "ok"); f.close("t"); assert f.task("t")["state"] == "CLOSED"
def test_not_a_crole(f):
    with pytest.raises(f.Refused): f.new("x", "ASO", "t")
def test_cro_delegate_refused_before_ack(f, tmp_path, cro_rehold):
    """Negative test: Fort Knox quarantine blocks held CRO chain work."""
    p = tmp_path / "t.json"
    p.write_text('{"text": "negative onboarding test", "source": "internal"}')
    with pytest.raises(f.Refused) as ei:
        f.delegate("CRO", str(p), "CRO-ONBOARD-NEG")
    assert "quarantine" in str(ei.value)
    assert not (tmp_path / "flow" / "CRO-ONBOARD-NEG").exists()
