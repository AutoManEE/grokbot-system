"""C8 line_guard: line/from/to must match the brief line table; approvals by the line owner; null W7 limits FAIL."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
import line_guard as lg

L = lg.load_lines()


def test_table_parsed():
    assert L["W4"]["frm"] == "CFO" and L["W4"]["to"] == "CTO" and L["FX-CTO"]["to"] == "CAO"


def test_budget_from_cbo_on_w4_refused(tmp_path):
    with pytest.raises(lg.LineRefused, match="from 'CBO' is not CFO"):
        lg.guarded_write(str(tmp_path / "b.json"), {"line": "W4", "from": "CBO", "to": "CTO"})
    assert not (tmp_path / "b.json").exists()
    lg.guarded_write(str(tmp_path / "b.json"), {"line": "W4", "from": "CFO", "to": "CTO"}); assert (tmp_path / "b.json").exists()


def test_unknown_or_missing_line_refused():
    assert lg.check_record({"from": "CFO", "to": "CTO"}) == ["line missing"]
    assert "not in the brief" in lg.check_record({"line": "W99", "from": "CFO", "to": "CTO"})[0]


def test_approval_only_by_owner():
    assert lg.approval_ok({"line": "W4", "from": "CFO", "to": "CTO"})
    assert not lg.approval_ok({"line": "W4", "from": "CFO", "to": "CTO", "approved_by": "CBO"})


def test_fixlist_line():
    assert lg.check_fixlist({"from": "CTO"}) == []
    assert lg.check_fixlist({"from": "CAO"})   # no FX-CAO line


def _limits(st, task=5, w4=True, w4_from="CFO"):
    (st / "context").mkdir(parents=True, exist_ok=True); (st / "budget").mkdir(exist_ok=True)
    (st / "context" / "cto_cost_limits.json").write_text(json.dumps(dict(max_paid_turns_per_task=task, max_paid_turns_per_day_when_ollama_down=5)))
    if w4: (st / "budget" / "CTO_cost_limits_W4_CFO.json").write_text(json.dumps({"line": "W4", "from": w4_from, "to": "CTO"}))


def test_null_limits_fail_names_w4(tmp_path):
    _limits(tmp_path, task=None); r = lg.cost_limits(str(tmp_path)); assert r["result"] == "FAIL" and "missing W4 record" in r["note"]
    _limits(tmp_path); assert lg.cost_limits(str(tmp_path))["result"] == "PASS"
    _limits(tmp_path, w4_from="CBO"); assert lg.cost_limits(str(tmp_path))["result"] == "FAIL"


def test_cli_exit_2(tmp_path):
    import subprocess
    p = tmp_path / "r.json"; p.write_text(json.dumps({"line": "W4", "from": "CBO", "to": "CTO"}))
    assert subprocess.run([sys.executable, GB_ROOT + "/scripts/line_guard.py", "check", str(p)], capture_output=True).returncode == 2
