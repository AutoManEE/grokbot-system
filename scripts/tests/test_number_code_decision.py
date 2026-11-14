"""CTO round-2 ruling: number tasks on the CTO chain. Code writes the Decision from S's
'Answer: N' line; ATO only reviews (AGREE / DISAGREE + reason); the reviewer runs independently; a batch runs
by model (all S, then all ATO, then all reviewer calls). All model calls mocked."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
from test_delegate import GOOD, f, task   # noqa: F401

Q = "A job uses 3 paid turns per run and runs 4 times a day. How many paid turns does it use in one 7-day week? Give the number."


def _run(f, s, ato, t="x1"):
    f.s_reply = s; f.reply = ato
    r = f.delegate("CTO", task(f.tmp, text=Q), t)
    return r, json.load(open(r["inbox"]))


def test_missing_answer_line_hard_fail(f):
    r, rep = _run(f, "3 * 4 * 7 = 84", "AGREE\nReason: right")
    assert rep["result"] == "FAIL" and rep["decision"] == "none"
    assert any(c["name"] == "s_answer_line_unique" and c["result"] == "FAIL" and "no 'Answer" in c["note"] for c in rep["conditions"])


def test_more_than_one_answer_line_hard_fail(f):
    r, rep = _run(f, "Answer: 84\nwait\nAnswer: 84", "AGREE\nReason: right")
    assert rep["result"] == "FAIL" and any("more than one" in c["note"] for c in rep["conditions"] if c["name"] == "s_answer_line_unique")


def test_agree_bare_number_decision(f):
    r, rep = _run(f, "3 * 4 * 7 = 12 * 7 = 84\n\nAnswer: 84", "AGREE\nReason: 3 x 4 x 7 is 84.")
    assert rep["decision"] == "84" and rep["decision_from_code"] and rep["ato_verdict"] == "AGREE"
    assert rep["result"] == "PASS" and rep["c_review_hint"] == "ACCEPT" and rep["independent_review_verdict"] == "PASS"
    a = [c for c in f.calls if c[0] == "ATO"][0]; assert "Answer:' line: 84" in a[1] and not a[4]   # review prompt, no schema


def test_disagree_marks_adjust(f):
    r, rep = _run(f, "Answer: 74", "DISAGREE\nReason: 3 x 4 x 7 is 84, not 74.")
    assert rep["result"] == "FAIL" and rep["c_review_hint"] == "ADJUST" and rep["decision"] == "74"
    assert r["c_review_hint"] == "ADJUST"


def test_reviewer_fail_also_rejects(f):
    f.independent_text = "VERDICT: FAIL\nREASON: 74 is wrong"
    r, rep = _run(f, "Answer: 74", "AGREE\nReason: ok")
    assert rep["result"] == "FAIL" and rep["c_review_hint"] == "ADJUST"


def test_batch_runs_by_model(f):
    f.s_reply = "Answer: 84"; f.reply = "AGREE\nReason: ok"
    items = [dict(t=f"b{i}", path=task(f.tmp, text=Q)) for i in range(3)]
    for i, it in enumerate(items): (f.tmp / f"t{i}.json").write_text(json.dumps(dict(text=Q))); it["path"] = str(f.tmp / f"t{i}.json")
    out = f.delegate_number_batch("CTO", items)
    order = [c[0] if c[0] != "complete" else "REV" for c in f.calls]
    assert order == ["STO"] * 3 + ["ATO"] * 3 + ["REV"] * 3
    assert [o["decision"] for o in out] == ["84"] * 3


def test_new_task_joins_current_batch_round(f):
    f.s_reply = "Answer: 84"; f.reply = "DISAGREE\nReason: x"
    for t in ("r1", "r2"): f.delegate("CTO", task(f.tmp, text=Q), t)
    import os, time
    for t in ("r1", "r2"):
        rp = f.tmp / "flow" / t / "03_a_report.md"; m = os.path.getmtime(rp) + 5; os.utime(rp, (m, m))
    f.c_review("r1", "ADJUST", "n", batch_id="BB"); f.c_review("r2", "ACCEPT", "n", batch_id="BB"); f.close("r2")
    f.delegate("CTO", task(f.tmp, text=Q), "r1"); f.delegate("CTO", task(f.tmp, text=Q), "r3")
    for t in ("r1", "r3"):
        rp = f.tmp / "flow" / t / "03_a_report.md"; m = os.path.getmtime(rp) + 10; os.utime(rp, (m, m))
    f.c_review("r1", "ADJUST", "n", batch_id="BB"); f.c_review("r3", "ADJUST", "n", batch_id="BB")
    assert [x["round"] for x in f.batch("BB")["reviews"]] == [1, 1, 2, 2]
