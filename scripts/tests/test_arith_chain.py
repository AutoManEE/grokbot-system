"""D1: every '=' segment of a chain is checked against its neighbours."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import copy, json, sys
sys.path.insert(0, GB_ROOT + "/scripts")
import report_check as rc

TESTSET = GB_ROOT + "/gb_state/budget/reviewer_eval/testset.json"


def _flags(text):
    return [c["expr"] for c in rc.find_arith_claims(text) if rc.arith_mismatch(c)]


def test_chain_passes():
    assert _flags("3 * 4 * 7 = 12 * 7 = 84") == []
    assert len(rc.find_arith_claims("3 * 4 * 7 = 12 * 7 = 84")) == 2


def test_pilot_phi4_text_passes():
    s = ("Total paid turns = 3 * 4 * 7\nTotal paid turns = 12 * 7\nTotal paid turns = 84\n\nThe total paid turns are 84.")
    assert _flags(s) == []
    rep = dict(item="DG-ATO t", bot="ATO", result="PASS", objective="o", options=[dict(name="a", deltas="d", pros="p", cons="c")],
               decision="84", reasoning="3 * 4 * 7 = 12 * 7 = 84", confidence=90,
               conditions=[dict(name="x", required=True, result="PASS")])
    assert rc.check(rep, s_out=s, task_text="What is the total paid turns?") == [] and rep["result"] == "PASS"


def test_planted_wrong_middle_segment_flags():
    assert _flags("3 * 4 * 7 = 12 * 6") == ["3 × 4 × 7 = 12 × 6"]
    assert _flags("3 * 4 * 7 = 12 * 7 = 74") == ["12 × 7 = 74"]


def test_words_do_not_join_a_right_side():
    assert _flags("3 × 4 = 12 apples x 7") == []


def test_s_out_number_ignores_expression_rhs_and_takes_last_answer():
    assert rc.extract_s_out_number("3 * 4 * 7 = 12 * 7") is None
    assert rc.extract_s_out_number("3 * 4 * 7 = 12 * 7 = 84") == "84"
    assert rc.extract_s_out_number("Answer: 12 (draft)\n3 * 4 * 7 = 84\nAnswer: 84") == "84"
