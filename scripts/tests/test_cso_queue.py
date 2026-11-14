GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, json, glob, importlib
from datetime import datetime
sys.path.insert(0, GB_ROOT + "/scripts")
import queue_order as q
G = dict(item="29", bot="CSO", result="PENDING", objective="onboard", options=[dict(name="a", deltas="d", pros="p", cons="c")],
         decision="wait", reasoning="team paused", confidence=90, conditions=[dict(name="unpaused", required=True, result="N/A")])
def test_cso_task_in_queue():
    row = {"ID": "29", "Item": "Onboard CSO", "Owner": "CSO", "Status": "Waiting (team paused; Owner unpauses)", "Opened": "2034-01-09",
           "Priority rank (CBO)": "", "Depends on": "", "Retry at": ""}
    assert [r["Owner"] for r in q.next_per_owner([row], datetime(2034, 1, 9, 12))] == ["CSO"]
def test_cso_report_reaches_cio(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import cpo_rollup; importlib.reload(cpo_rollup)
    d = tmp_path / "reports/inbox/CSO"; d.mkdir(parents=True); (tmp_path / "flow" / "t29").mkdir(parents=True); (tmp_path / "flow" / "t29" / "01_task.json").write_text("{}"); (d / "r.json").write_text(json.dumps({**G, "handoff_id": "t29"}))
    assert cpo_rollup.run()["forwarded"] == 1 and glob.glob(str(tmp_path / "reports/cio/CSO-*"))
