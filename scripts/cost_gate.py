#!/usr/bin/env python3
"""cost_gate.py (P4-04, CAO with CTO limits): W7 cost limits for work moved to a paid model (e.g. Ollama down). Zero AI.
  check --est-turns N [--used-today M] [--limits FILE]  -> RUN or WAIT (waits in the CPO queue)
Limits come from the CTO's W7 file (default <repo>/templates/context/cto_cost_limits.json; live copy at
$GB_STATE/context/cto_cost_limits.json if present). A null limit means not approved: WAIT (fail closed).

Weekly hard cap: weekly_share_points is enforced in UTC, Monday 00:00 through Sunday 23:59
(or a week definition from the limits / CFO record if one is present). Paid-fallback points are summed
from action-log entries with kind=PTO in the current week. The points-per-job value is read from the
limits file or the CFO record (never hardcoded). If that source is missing, the gate FAILs closed with
WAIT and reason 'points source missing'. If spent plus this job's points would exceed the cap, WAIT
with reason weekly_share_points.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
DEFAULT = GB_ROOT + "/templates/context/cto_cost_limits.json"
def action_log_path():
    return os.environ.get("GB_ACTION_LOG", GB_ROOT + "/alignment/action_log.jsonl")
CFO_RECORD_DEFAULT = os.path.join(STATE, "budget", "CTO_cost_limits_W4_CFO.json")
POINT_KEYS = ("points_per_job", "paid_fallback_points", "points_per_fallback_job", "paid_points_per_job")


def limits_path():
    live = os.path.join(STATE, "context", "cto_cost_limits.json"); return live if os.path.exists(live) else DEFAULT


def _cfo_record(lim=None):
    path = (lim or {}).get("w4_record") or CFO_RECORD_DEFAULT
    if path and os.path.exists(path):
        try:
            return json.load(open(path))
        except (ValueError, OSError):
            return {}
    return {}


def points_per_job(lim):
    """Numeric points-per-job from the limits file or the CFO W4 record. None if missing (do not invent)."""
    sources = [lim or {}, (lim or {}).get("approved") or {}]
    rec = _cfo_record(lim)
    if rec:
        sources.append(rec)
        if isinstance(rec.get("approved"), dict):
            sources.append(rec["approved"])
    for src in sources:
        if not isinstance(src, dict):
            continue
        for k in POINT_KEYS:
            v = src.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return None


def week_timezone(lim=None):
    rec = _cfo_record(lim)
    for src in (lim or {}, rec, rec.get("approved") or {}):
        if not isinstance(src, dict):
            continue
        tz = src.get("week_timezone") or src.get("timezone")
        if tz:
            return ZoneInfo(str(tz))
    return ZoneInfo(os.environ.get("GB_TZ", "UTC"))


def week_bounds(now=None, lim=None):
    """Current week: UTC Monday 00:00 inclusive to next Monday 00:00 exclusive,
    unless the limits/CFO record defines week_timezone or week_starts_on (0=Monday)."""
    tz = week_timezone(lim)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    start_on = 0  # Monday
    rec = _cfo_record(lim)
    for src in (lim or {}, rec, rec.get("approved") or {}):
        if isinstance(src, dict) and "week_starts_on" in src:
            try:
                start_on = int(src["week_starts_on"]) % 7
            except (TypeError, ValueError):
                pass
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (midnight.weekday() - start_on) % 7
    start = midnight - timedelta(days=delta)
    end = start + timedelta(days=7)
    return start, end


def _parse_ts(ts, tz):
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=tz)
    s = str(ts).strip()
    body, _, suffix = s.rpartition(" ")
    if suffix in ("CST", "CDT", "CT") or (suffix.isalpha() and body):
        try:
            return datetime.strptime(body, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
        except ValueError:
            continue
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    except ValueError:
        return None


def week_spent(log_path, lim=None, now=None, default_points=None):
    """Sum paid-fallback points from kind=PTO action-log entries in the current week."""
    start, end = week_bounds(now=now, lim=lim)
    tz = week_timezone(lim)
    pts = default_points if default_points is not None else points_per_job(lim)
    total = 0.0
    if not log_path or not os.path.exists(log_path):
        return total, start, end
    with open(log_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") != "PTO":
                continue
            ts = _parse_ts(e.get("ts"), tz)
            if ts is None or not (start <= ts < end):
                continue
            p = e.get("points", e.get("job_points", pts))
            if isinstance(p, (int, float)) and not isinstance(p, bool):
                total += float(p)
    return total, start, end


CCO_ROLE = "CCO"  # paid-model lane
CCO_QUOTE = ("I need to approve any uses here since goal is fully programmatic scripting and ollama use")


def check(est, used, lim, used_week=None, job_points=None, log=None, now=None, role=None, override=None):
    # New role starts at paid-fallback 0 until it has a W4/W7 allotment.
    # Accident guard plus detection, not a verified caller identity.
    if role:
        import alignment_check
        held, why = alignment_check.is_held(role)
        if held:
            return "WAIT", f"held role: {why}"
        if role.upper() == CCO_ROLE:
            # Every CCO paid action is HOLD until a Owner-signed override carries a C9 quote id
            # listed in alignment/c9_quotes.jsonl. Paid-fallback allotment stays 0 (no PTO lane).
            import owner_override
            ok, mwhy, _ = owner_override.check_override(override, scope=["cco-paid"]) if override is not None else (False, "no override", {})
            if not ok:
                return "HOLD", (f"CCO paid action needs a Owner override with a C9 quote id ({mwhy}); "
                                f"paid-fallback allotment 0; Owner quote: '{CCO_QUOTE}'")
        if alignment_check.is_new_role(role):
            return "WAIT", "new role paid-fallback 0 until W4/W7 allotment"
    a, b = lim.get("max_paid_turns_per_task"), lim.get("max_paid_turns_per_day_when_ollama_down")
    if not isinstance(a, int) or not isinstance(b, int):
        return "WAIT", "CTO cost limit not set"
    if est > a:
        return "WAIT", f"task {est} turns > per-task limit {a}"
    if used + est > b:
        return "WAIT", f"day total {used + est} > daily limit {b}"
    cap = lim.get("weekly_share_points")
    if cap is None and "weekly_share_points" not in lim:
        return "RUN", "within CTO cost limits"
    if not isinstance(cap, (int, float)) or isinstance(cap, bool):
        return "WAIT", "CTO cost limit not set"
    pts = job_points if job_points is not None else points_per_job(lim)
    if pts is None:
        return "WAIT", "points source missing"
    if used_week is None:
        used_week, _, _ = week_spent(log or action_log_path(), lim=lim, now=now, default_points=pts)
    if used_week + pts > cap:
        return "WAIT", "weekly_share_points"
    return "RUN", "within CTO cost limits"


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["check"]); p.add_argument("--est-turns", type=int, required=True)
    p.add_argument("--used-today", type=int, default=0); p.add_argument("--limits")
    p.add_argument("--role"); p.add_argument("--override", help="CCO: Owner-signed override JSON with a C9 quote id")
    a = p.parse_args(); v, why = check(a.est_turns, a.used_today, json.load(open(a.limits or limits_path())), role=a.role, override=a.override)
    print(json.dumps({"decision": v, "reason": why})); sys.exit(0 if v == "RUN" else 1)
