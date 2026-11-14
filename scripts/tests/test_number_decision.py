"""E1/E2 (CTO D4 rulings): number-task decision normalization in code; ATO cap 256 and eval logging."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import io, json, sys
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/bots")
import flow, run

S84 = "3 * 4 * 7 = 12 * 7 = 84\n\nAnswer: 84"


def _rep(dec):
    return dict(decision=dec, result="PASS", conditions=[dict(name="x", required=True, result="PASS")])


def test_bare_number_passes():
    r = _rep("84"); assert flow.normalize_number_decision(r, S84) == "pass" and r["decision"] == "84" and "decision_normalized" not in r


def test_he_is_correct_fails():
    r = _rep("He is correct"); assert flow.normalize_number_decision(r, S84) == "fail"
    assert r["result"] == "FAIL" and any(c["name"] == "number_decision" and c["required"] for c in r["conditions"])


def test_sentence_with_single_matching_int_normalizes():
    r = _rep("He has 84 paid turns in a week")
    assert flow.normalize_number_decision(r, S84) == "normalized"
    assert r["decision"] == "84" and r["decision_normalized"] is True and r["decision_original"] == "He has 84 paid turns in a week"
    assert r["result"] == "PASS"


def test_two_ints_fail():
    r = _rep("84 or 85"); assert flow.normalize_number_decision(r, S84) == "fail" and r["result"] == "FAIL"


def test_int_not_matching_answer_or_no_answer_line_fails():
    assert flow.normalize_number_decision(_rep("It is 74"), S84) == "fail"
    assert flow.normalize_number_decision(_rep("It is 84"), "the total is 84") == "fail"   # no 'Answer: N' line
