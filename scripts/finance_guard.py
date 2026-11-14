#!/usr/bin/env python3
"""finance_guard.py (guardrail code owned by the CAO, used by gate.py for the CFO): check a spend request against
the Owner-approved limits. Zero AI. It never moves money; it only says whether a held request may be released.

The finance bot is read-only by default. Limits live in alignment/finance_settings.json, which only the Owner edits:
  read_only           true = every spend is refused (the default)
  per_request_limit   max amount for one request (null = not approved -> refuse)
  period_limit        max total spend in the rolling window (null = not approved -> refuse)
  period_days         length of the rolling window
  allowed_categories  categories the Owner approved (an unlisted category is refused)
Checks (all required; any FAIL = refuse, named):
  F1 read_only_off  F2 amount_valid  F3 per_request  F4 period_total  F5 category  F6 no_destination
Spends released through gate.py are appended to the spend ledger ($GB_STATE/finance/spend_ledger.jsonl), which F4 sums.
Missing or unreadable settings = refuse (fail closed). Accident guard plus detection, not security.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, json, math, os, sys, time

SETTINGS = os.environ.get("GB_FINANCE_SETTINGS") or os.path.join(GB_ROOT, "alignment", "finance_settings.json")
STATE = os.environ.get("GB_STATE", os.path.join(GB_ROOT, "gb_state"))


def ledger_default():
    return os.path.join(os.environ.get("GB_STATE", STATE), "finance", "spend_ledger.jsonl")


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_settings(path=None):
    try:
        s = json.load(open(path or SETTINGS))
        return s if isinstance(s, dict) else None
    except Exception:
        return None


def spent_in_window(ledger_path, days, now=None):
    now = now or time.time()
    total = 0.0
    try:
        with open(ledger_path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and _num(r.get("amount")) and now - float(r.get("ts", 0)) <= days * 86400:
                    total += r["amount"]
    except FileNotFoundError:
        pass
    return total


def check(details, settings=None, ledger_path=None, now=None):
    s = settings if settings is not None else load_settings()
    res = {}
    if not isinstance(s, dict):
        return dict(allowed=False, failed=["settings"], checks={"settings": "FAIL: missing or unreadable"})
    d = details if isinstance(details, dict) else {}
    amt = d.get("amount")
    res["F1 read_only_off"] = "PASS" if s.get("read_only") is False else "FAIL: finance bot is read-only"
    res["F2 amount_valid"] = "PASS" if _num(amt) and amt > 0 else "FAIL: amount must be a positive number"
    lim = s.get("per_request_limit")
    res["F3 per_request"] = ("PASS" if _num(lim) and _num(amt) and amt <= lim
                             else "FAIL: per-request limit not approved" if not _num(lim) else "FAIL: over the per-request limit")
    plim, days = s.get("period_limit"), s.get("period_days") or 30
    if not _num(plim):
        res["F4 period_total"] = "FAIL: period limit not approved"
    else:
        spent = spent_in_window(ledger_path or ledger_default(), days, now)
        res["F4 period_total"] = "PASS" if _num(amt) and spent + amt <= plim else f"FAIL: period total would exceed the limit"
    cats = s.get("allowed_categories") or []
    res["F5 category"] = "PASS" if d.get("category") in cats else "FAIL: category not on the Owner-approved list"
    res["F6 no_destination"] = "PASS" if not d.get("destination") else "FAIL: requests with a destination are transfers"
    failed = [k for k, v in res.items() if v != "PASS"]
    return dict(allowed=not failed, failed=failed, checks=res)


def record_spend(details, gate_id, ledger_path=None):
    p = ledger_path or ledger_default()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(dict(gate=gate_id, amount=(details or {}).get("amount"),
                                category=(details or {}).get("category"), ts=time.time())) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("details", help="JSON details of the spend request")
    a = ap.parse_args()
    r = check(json.loads(a.details)); print(json.dumps(r, indent=1)); sys.exit(0 if r["allowed"] else 1)
