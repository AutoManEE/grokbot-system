GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, json, os
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, GB_ROOT + "/scripts")
import cost_gate as c

CT = ZoneInfo("UTC")
# W7-shaped limits used in tests (points_per_job is a test fixture value, not a production hardcoded 5.3)
W7 = {"max_paid_turns_per_task": 5, "max_paid_turns_per_day_when_ollama_down": 5,
      "weekly_share_points": 10, "points_per_job": 4}

def test_default_null_waits():
    assert c.check(1, 0, json.load(open(c.DEFAULT)))[0] == "WAIT"

def test_limits_task_and_daily():
    # 3/0 RUN, 6/0 WAIT on task, 3/4 WAIT on daily (W7 caps: 5 per task, 5 per day)
    assert c.check(3, 0, W7, used_week=0) == ("RUN", "within CTO cost limits")
    v, why = c.check(6, 0, W7, used_week=0)
    assert v == "WAIT" and "task" in why
    v, why = c.check(3, 4, W7, used_week=0)
    assert v == "WAIT" and "day" in why

def test_weekly_under_at_over():
    # cap 10, job 4
    assert c.check(3, 0, W7, used_week=0)[0] == "RUN"          # 0+4 under
    assert c.check(3, 0, W7, used_week=6)[0] == "RUN"          # 6+4 = 10 at cap
    v, why = c.check(3, 0, W7, used_week=7)                    # 7+4 = 11 over
    assert v == "WAIT" and why == "weekly_share_points"

def test_points_source_missing_fails_closed():
    L = {"max_paid_turns_per_task": 5, "max_paid_turns_per_day_when_ollama_down": 5,
         "weekly_share_points": 10}
    v, why = c.check(3, 0, L, used_week=0)
    assert v == "WAIT" and why == "points source missing"

def test_week_spent_sums_only_pto_in_current_week(tmp_path):
    log = tmp_path / "log.jsonl"
    # a Monday and a Sunday in the same week, plus one outside
    week_start, _ = c.week_bounds(now=datetime(2034, 1, 9, 12, 0, tzinfo=CT))  # Mon
    # write minimal kind=PTO lines (week_spent only needs kind, ts, points)
    rows = [
        {"kind": "PTO", "ts": "2034-01-09 01:00:00 UTC", "points": 4},
        {"kind": "LOCAL_FAIL", "ts": "2034-01-09 02:00:00 UTC", "points": 4},  # not counted
        {"kind": "PTO", "ts": "2034-01-02 12:00:00 UTC", "points": 4},  # previous week
        {"kind": "PTO", "ts": "2034-01-11 18:00:00 UTC", "points": 4},  # same week (Wed)
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    spent, start, end = c.week_spent(str(log), lim=W7, now=datetime(2034, 1, 9, 21, 0, tzinfo=CT), default_points=4)
    assert spent == 8  # two in-week PTO entries
    assert start.weekday() == 0
