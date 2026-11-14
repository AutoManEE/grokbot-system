"""queue_read: an RQ-CFO item written on its brief line is listed PENDING for the CBO and closes only with the
Owner's answer. Synthetic item; tmp GB_STATE only."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, json, time, importlib
sys.path.insert(0, GB_ROOT + "/scripts")


def _alarm(state):
    import line_guard
    d = os.path.join(state, "queue", "rq"); os.makedirs(d, exist_ok=True)
    return line_guard.escalate_write(os.path.join(d, "CFO-limit-1.json"),
                                     dict(line="RQ-CFO", **{"from": "CFO"}, to="CBO", then="ES-CBO to Owner",
                                          kind="spend-limit alarm", text="Period spend limit reached. Spending paused.",
                                          ts=time.time()))


def test_alarm_to_cbo_and_close(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path))
    import line_guard, queue_read; importlib.reload(line_guard); importlib.reload(queue_read)
    _alarm(str(tmp_path))
    its = queue_read.items("rq"); assert len(its) == 1 and its[0]["line"] == "RQ-CFO" and its[0]["_status"] == "PENDING"
    dst = queue_read.close(its[0]["_file"], "Owner: pause confirmed")
    assert queue_read.items("rq") == [] and json.load(open(dst))["answer"]


def test_empty_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path))
    import queue_read; importlib.reload(queue_read)
    assert queue_read.items() == []
