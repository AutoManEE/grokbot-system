GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, copy; sys.path.insert(0, GB_ROOT + "/scripts"); import report_check as rc
G = dict(item="T1", bot="CFO", result="PASS", objective="o", options=[dict(name="a", deltas="+1", pros="p", cons="c")],
         decision="a", reasoning="r", confidence=80, conditions=[dict(name="x", required=True, result="PASS")])
def m(**kw): d = copy.deepcopy(G); d.update(kw); return d
GOOD = [G, m(result="FAIL", conditions=[dict(name="x", required=True, result="FAIL")]), m(confidence=0), m(confidence=100, result="PENDING"),
        m(conditions=[dict(name="x", required=False, result="FAIL"), dict(name="y", required=True, result="PASS")])]
BAD = [m(confidence=101), m(result="PASS", confidence=100, conditions=[dict(name="x", required=True, result="FAIL")]),
       m(conditions=[]), m(result="FAIL"), {k: v for k, v in G.items() if k != "decision"}]
def test_good(): assert all(rc.check(r) == [] for r in GOOD)
def test_bad(): assert all(rc.check(r) for r in BAD)

import copy, json, os
import report_check as rc2  # already imported as rc at top; keep using rc

PLANT = dict(item="planted74", bot="ATO", result="PASS", objective="total paid turns",
             options=[dict(name="a", deltas="d", pros="p", cons="c"), dict(name="b", deltas="d", pros="p", cons="c")],
             decision="74", reasoning="28 × 3 = 74", confidence=80,
             conditions=[dict(name="answer correct", required=True, result="PASS")])
PILOT_REASON = "4 runs/day × 7 days = 28 runs; 28 × 3 = 84"
PILOT = dict(item="pilot2b", bot="ATO", result="PASS", objective="total paid turns",
             options=[dict(name="a", deltas="d", pros="p", cons="c"), dict(name="b", deltas="d", pros="p", cons="c")],
             decision="84", reasoning=PILOT_REASON, confidence=90,
             conditions=[dict(name="answer correct", required=True, result="PASS")])
TASK_NUM = "A job uses 3 paid turns per run, 4 runs per day, for 7 days. What is the total paid turns? Give the number."

def test_arith_planted74_reasoning_fails():
    r = copy.deepcopy(PLANT)
    assert rc.check(r) == []
    assert r["result"] == "FAIL"
    d = {c["name"]: c for c in r["conditions"]}
    assert d["arith_recheck"]["required"] is True and d["arith_recheck"]["result"] == "FAIL"
    assert "28 × 3 = 74" in (d["arith_recheck"].get("note") or "")

def test_arith_pilot2b_text_passes_no_flag():
    r = copy.deepcopy(PILOT)
    assert rc.check(r, s_out="Answer: 84", task_text=TASK_NUM) == []
    assert r["result"] == "PASS"
    assert all(c["name"] != "arith_recheck" for c in r["conditions"])

def test_arith_runs_per_day_is_not_division():
    claims = rc.find_arith_claims("4 runs/day × 7 days = 28 runs; 28 × 3 = 84")
    assert [c["expr"] for c in claims] == ["4 × 7 = 28", "28 × 3 = 84"]
    assert not any(rc.arith_mismatch(c) for c in claims)

def test_decision_must_contain_s_out_number():
    r = copy.deepcopy(PILOT)
    r["decision"] = "Direct Multiplication"
    r["reasoning"] = "looks right"
    assert rc.check(r, s_out="3 × 4 × 7 = 84", task_text=TASK_NUM) == []
    assert r["result"] == "FAIL"
    d = {c["name"]: c for c in r["conditions"]}
    assert d["decision_contains_s_out_number"]["required"] is True and d["decision_contains_s_out_number"]["result"] == "FAIL"

def test_decision_with_s_out_number_passes():
    r = copy.deepcopy(PILOT)
    assert rc.check(r, s_out="84", task_text=TASK_NUM) == []
    assert r["result"] == "PASS"
    assert all(c["name"] != "decision_contains_s_out_number" for c in r["conditions"])

def test_number_rule_skips_non_numeric_task():
    r = copy.deepcopy(G)
    r["decision"] = "OK"
    assert rc.check(r, s_out="OK", task_text="Reply with the word OK.") == []
    assert r["result"] == "PASS"
    assert all(c["name"] != "decision_contains_s_out_number" for c in r["conditions"])

def test_scan_existing_dg_reports_no_false_flags():
    s = rc.scan_report_dir(GB_ROOT + "/gb_state/reports/dg")
    # counts are reported to the CAO; this test only requires no false flags
    assert s["false_flags"] == 0, s["false_flag_list"]
    # stash measured counts where the test runner prints them
    print("ARITH_SCAN", json.dumps(dict(scanned=s["scanned"], with_claims=s["with_claims"],
                                       false_flags=s["false_flags"], hits=len(s["hits"]),
                                       hit_list=s["hits"])))
