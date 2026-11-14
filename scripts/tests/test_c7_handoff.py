"""C7: handoff_id required (flow task id), open_item_id optional (WARN), reject when a
report marks an Open Items row Done without open_item_id. flow stamps both ids."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import glob, importlib, json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
import report_check as rc
from test_delegate import GOOD, f, task   # noqa: F401
G = dict(item="T", bot="CTO", result="PASS", objective="o", options=[dict(name="a", deltas="d", pros="p", cons="c")],
         decision="a", reasoning="r", confidence=80, conditions=[dict(name="x", required=True, result="PASS")])


def test_ids(tmp_path):
    (tmp_path / "flow" / "t1").mkdir(parents=True); (tmp_path / "flow" / "t1" / "01_task.json").write_text("{}"); s = str(tmp_path)
    assert rc.check_handoff_ids({}, s)[0] and "handoff_id missing" in rc.check_handoff_ids({}, s)[0][0]
    assert "not a flow task id" in rc.check_handoff_ids({"handoff_id": "nope"}, s)[0][0]
    e, w = rc.check_handoff_ids({"handoff_id": "t1"}, s); assert not e and "open_item_id missing" in w[0]
    assert rc.check_handoff_ids({"handoff_id": "t1", "open_item_id": "31"}, s) == ([], [])
    e, _ = rc.check_handoff_ids({"handoff_id": "t1", "open_item_status": "Done"}, s); assert "Done" in e[0]
    assert rc.check_handoff_ids({"handoff_id": "t1", "open_item_id": "31", "open_item_status": "Done"}, s) == ([], [])


def test_rollup(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import cpo_rollup; importlib.reload(cpo_rollup)
    (tmp_path / "flow" / "t1").mkdir(parents=True); (tmp_path / "flow" / "t1" / "01_task.json").write_text("{}"); d = tmp_path / "reports/inbox/CTO"; d.mkdir(parents=True)
    (d / "ok.json").write_text(json.dumps({**G, "handoff_id": "t1"}))
    (d / "noh.json").write_text(json.dumps(G))
    (d / "done.json").write_text(json.dumps({**G, "handoff_id": "t1", "open_item_status": "Done"}))
    assert cpo_rollup.run() == {"forwarded": 1, "rejected": 2}
    roll = json.load(open(glob.glob(str(tmp_path / "reports/cio/rollup-*.json"))[0]))
    assert roll["warnings"][0]["file"] == "ok.json"
    assert os.path.exists(tmp_path / "reports/rejected/CTO/done.json.errors.json")


def test_flow_stamps_ids(f):
    r = f.delegate("CBO", task(f.tmp, text="Rank three tasks.", open_item_id=31), "h1")
    rep = json.load(open(r["inbox"])); assert rep["handoff_id"] == "h1" and rep["open_item_id"] == "31"
    assert rc.check_handoff_ids(rep, str(f.tmp)) == ([], [])


def test_handoff_id_traversal_and_missing_task_rejected(tmp_path):             # #36 C7, probe p_c7
    s = str(tmp_path); (tmp_path / "flow" / "empty").mkdir(parents=True)
    (tmp_path / "flow" / "ok").mkdir(); (tmp_path / "flow" / "ok" / "01_task.json").write_text("{}")
    (tmp_path / "x").mkdir(); (tmp_path / "x" / "01_task.json").write_text("{}")
    for bad in (".", "..", "../flow", "../x", "ok/..", ".hidden", "ok/../ok", "empty", "/etc"):
        e, _ = rc.check_handoff_ids({"handoff_id": bad, "open_item_id": "1"}, s)
        assert e and "not a flow task id" in e[0], bad
    assert rc.check_handoff_ids({"handoff_id": "ok", "open_item_id": "1"}, s) == ([], [])
