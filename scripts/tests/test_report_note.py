"""CSO round-3 ruling: optional plain-string `note` on Section 5 reports (schema + report_check). Mocks only."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, sys
sys.path.insert(0, GB_ROOT + "/scripts")
import flow, report_check
from test_delegate import GOOD, f, task   # noqa: F401

BASE = dict(item="x", bot="ASO", result="PASS", objective="o", decision="d", reasoning="r", confidence=80,
            options=[dict(name="a", deltas="d", pros="p", cons="c"), dict(name="b", deltas="d", pros="p", cons="c")],
            conditions=[dict(name="c1", required=True, result="PASS")])


def test_schema_note_optional_string():
    assert flow.S5_SCHEMA["properties"]["note"] == {"type": "string"} and "note" not in flow.S5_SCHEMA["required"]
    assert "note" in flow.checklist_schema(2)["properties"]


def test_report_check_note():
    assert report_check.check(dict(BASE)) == []
    assert report_check.check(dict(BASE, note="Line 3 follows the task text; CSO should confirm.")) == []
    for bad in (None, 3, ["a"], {"a": 1}):
        assert "note must be a plain string" in report_check.check(dict(BASE, note=bad))


def test_note_kept_in_flow_report(f):
    f.reply = {**GOOD, "note": "rule differs from task"}
    r = f.delegate("CTO", task(f.tmp, text="Review the config change."), "n1")
    assert json.load(open(r["inbox"]))["note"] == "rule differs from task"


def test_non_string_note_refused_twice(f):
    f.reply = {**GOOD, "note": 5}
    import pytest
    with pytest.raises(f.Refused, match="note must be a plain string"):
        f.delegate("CTO", task(f.tmp, text="Review the config change."), "n2")
