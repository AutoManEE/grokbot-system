"""D2: decision_contains_s_out_number stays a hard (required) check; STO/ATO carry the number-only line."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys
sys.path.insert(0, GB_ROOT + "/scripts")
import report_check as rc

PILOT = "A job uses 3 paid turns per run and runs 4 times a day. How many paid turns does it use in one 7-day week? Give the number."


def _rep(dec):
    return dict(item="DG-ATO t", bot="ATO", result="PASS", objective="o", options=[dict(name="a", deltas="d", pros="p", cons="c")],
                decision=dec, reasoning="r", confidence=90, conditions=[dict(name="x", required=True, result="PASS")])


def test_yes_decision_is_required_fail():
    r = _rep("Yes"); rc.check(r, s_out="84", task_text=PILOT)
    c = [x for x in r["conditions"] if x["name"] == "decision_contains_s_out_number"]
    assert c and c[0]["required"] and c[0]["result"] == "FAIL" and r["result"] == "FAIL"


def test_number_decision_passes():
    r = _rep("84"); assert rc.check(r, s_out="84", task_text=PILOT) == [] and r["result"] == "PASS"


def test_prompts_carry_number_only_line():
    sto = open(GB_ROOT + "/bots/STO/prompt.md").read()
    assert "show your work" in sto.lower()                                   # CTO ADJUST: work stays visible
    assert "end with one final line: Answer: <number>." in sto
    assert "answer with the number only" not in sto                         # the D2 line was replaced, not stacked
    assert "Decision holds that number only" in open(GB_ROOT + "/bots/ATO/prompt.md").read()


def test_answer_line_s_out_feeds_decision_check():
    s = "3 * 4 * 7 = 12 * 7 = 84\nAnswer: 84"
    assert rc.extract_s_out_number(s) == "84"
    r = _rep("Yes"); rc.check(r, s_out=s, task_text=PILOT); assert r["result"] == "FAIL"
    r = _rep("84"); assert rc.check(r, s_out=s, task_text=PILOT) == [] and r["result"] == "PASS"


def test_fixlist_prompt_replace(tmp_path, monkeypatch):
    import json, fixlist_check as f
    d = tmp_path / "STO"; d.mkdir()
    (d / "settings.json").write_text(json.dumps({"id": "STO", "model": "m", "version": 1}))
    (d / "prompt.md").write_text("base.\nold line.\n")
    monkeypatch.setattr(f, "BOTS", str(tmp_path)); monkeypatch.setattr(f, "LOG", str(tmp_path / "log.jsonl"))
    fx = {"from": "CTO", "target": "STO", "issue": "i", "evidence": "e", "test": "t",
          "change": {"field": "prompt_replace", "value": {"old": "old line.", "new": "new line."}}}
    assert f.check(fx) == []
    f.apply(fx, "CAO")
    assert (d / "prompt.md").read_text() == "base.\nnew line.\n"
    assert f.check(fx) and "found 0 times" in f.check(fx)[0]       # old text gone: a second apply is refused
    fx["change"]["value"] = {"old": "x"}
    assert f.check(fx)
