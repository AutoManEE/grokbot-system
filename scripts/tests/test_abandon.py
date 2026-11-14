"""Step 2: flow.py abandon (evidence-only tasks in any OPEN state; nothing deleted)."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import importlib, json, os, sys, time
import pytest
sys.path.insert(0, GB_ROOT + "/scripts")
REP = "Objective: x\nOptions: a (Deltas 0, Pros p, Cons c)\nDecision: a\nReasoning: r\nConfidence: 90"


@pytest.fixture
def f(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import flow; importlib.reload(flow); return flow


def test_abandon_fresh_task_keeps_files(f, tmp_path):
    f.new("t", "COO", "x")
    r = f.abandon("t", "test artifact", "CAO")
    assert r["prior_state"] == "OPEN" and f.task("t")["state"] == "ABANDONED"
    assert (tmp_path / "flow/t/06_abandon.json").exists() and (tmp_path / "flow/t/01_task.json").exists()
    assert len((tmp_path / "flow/abandon_log.jsonl").read_text().splitlines()) == 1


def test_abandon_after_adjust_and_awaiting_review(f):
    f.new("a", "COO", "x"); f.s_run("a", "out"); time.sleep(0.01); f.a_report("a", REP)
    f.abandon("a", "c-review refused, no current report", "COO")   # awaiting review
    f.new("b", "COO", "x"); f.s_run("b", "out"); time.sleep(0.01); f.a_report("b", REP); time.sleep(0.01)
    f.c_review("b", "ADJUST", "fix it")
    r = f.abandon("b", "close refused after ADJUST", "CAO")
    assert r["last_review"] == "ADJUST"


def test_abandoned_task_refuses_every_step(f):
    f.new("t", "COO", "x"); f.abandon("t", "why", "CAO")
    for call in (lambda: f.s_run("t", "o"), lambda: f.a_report("t", REP), lambda: f.c_review("t", "ACCEPT", ""),
                 lambda: f.close("t"), lambda: f.abandon("t", "again", "CAO")):
        with pytest.raises(f.Refused):
            call()


def test_abandon_rules(f):
    f.new("t", "COO", "x")
    with pytest.raises(f.Refused): f.abandon("t", "  ", "CAO")       # note required
    with pytest.raises(f.Refused): f.abandon("t", "why", "CFO")      # not owner / not CAO
    with pytest.raises(f.Refused): f.abandon("nope", "why", "CAO")   # no such task


def test_closed_task_cannot_be_abandoned(f):
    f.new("t", "COO", "x"); f.s_run("t", "o"); time.sleep(0.01); f.a_report("t", REP); time.sleep(0.01)
    f.c_review("t", "ACCEPT", "ok"); f.close("t")
    with pytest.raises(f.Refused): f.abandon("t", "why", "CAO")


def test_cao_can_abandon_a_held_roles_task(f, tmp_path, monkeypatch):
    """Case: the task's role is on hard hold; CAO cleanup still works."""
    os.makedirs(tmp_path / "flow/h/02_s_out"); os.makedirs(tmp_path / "flow/h/05_adjust")
    json.dump(dict(task="h", c_role="CRO", text="x", ts=0, state="OPEN"), open(tmp_path / "flow/h/01_task.json", "w"))
    assert f.abandon("h", "quarantine leak test artifact", "CAO")["c_role"] == "CRO"
    with pytest.raises(f.Refused): f.abandon("h", "x", "CRO")
