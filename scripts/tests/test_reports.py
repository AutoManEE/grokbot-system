GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, json, glob, importlib, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
G = dict(item="T", bot="CFO", result="PASS", objective="o", options=[dict(name="a", deltas="d", pros="p", cons="c")],
         decision="a", reasoning="r", confidence=80, conditions=[dict(name="x", required=True, result="PASS")])
@pytest.fixture
def m(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import cpo_rollup, report_audit; importlib.reload(cpo_rollup); importlib.reload(report_audit)
    for i, role in enumerate(["CFO", "CTO", "COO"]):
        d = tmp_path / "reports" / "inbox" / role; d.mkdir(parents=True)
        (tmp_path / "flow" / f"T{i}").mkdir(parents=True)   # C7: handoff_id is a flow task id
        (tmp_path / "flow" / f"T{i}" / "01_task.json").write_text("{}")   # #36 C7: a task has 01_task.json
        (d / f"r{i}.json").write_text(json.dumps({**G, "bot": role, "item": f"T{i}", "handoff_id": f"T{i}"}, indent=2))
    (tmp_path / "reports" / "inbox" / "CFO" / "bad.json").write_text(json.dumps({**G, "confidence": 500}))
    return cpo_rollup, report_audit, tmp_path
def test_forward_unchanged_and_audit(m):
    cr, ra, tmp = m
    srcs = {json.loads(open(f).read())["item"]: open(f, "rb").read() for f in glob.glob(str(tmp / "reports/inbox/*/r*.json"))}
    assert cr.run() == {"forwarded": 3, "rejected": 1}
    fw = [p for p in glob.glob(str(tmp / "reports/cio/*.json")) if "rollup-" not in p]
    assert sorted(open(p, "rb").read() for p in fw) == sorted(srcs.values())   # byte-identical
    assert len(glob.glob(str(tmp / "reports/cio/rollup-*.json"))) == 1
    assert ra.audit()["result"] == "PASS"
def test_tamper_flagged(m):
    cr, ra, tmp = m; cr.run()
    p = [p for p in glob.glob(str(tmp / "reports/cio/*.json")) if "rollup-" not in p][0]
    open(p, "a").write(" ")
    r = ra.audit(); assert r["result"] == "FAIL" and "A2 changed" in r["problems"][0]
def test_unlogged_flagged(m):
    cr, ra, tmp = m; cr.run(); (tmp / "reports/cio/sneaky.json").write_text("{}")
    assert any(p.startswith("A3") for p in ra.audit()["problems"])
