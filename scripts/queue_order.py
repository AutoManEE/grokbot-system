#!/usr/bin/env python3
"""queue_order.py (P2-03, CPO, code by CAO): next ready task per role, by the CBO's priority rank. Zero AI, read-only.
TASK lines only schedule: the CPO decides WHEN, never WHAT (brief 3.4).
Order: rows whose Status is active (Ready/Open/In progress/Waiting), every "Depends on" ID is Done/Closed, and
"Retry at" (CT, YYYY-MM-DD HH:MM) is empty or past. Sort: Priority rank (CBO) ascending (1 = first; blank = last),
then older Opened first, then lower ID. One next task per Owner.
Never ready: Status containing 'Waiting on Owner', 'needs Owner', 'held' (Item 30),
or 'Ready for Owner review' (TK-CAO-24: treat like Done/held for Owner).
Warns on stderr when Priority rank (CBO) is blank on an otherwise-active row.
Usage: queue_order.py --from-csv FILE [--now "YYYY-MM-DD HH:MM"] [--all]   (live Sheet read: export the tab to CSV
via a spreadsheet connector; scripts hold no spreadsheet credential)
"""
import argparse, csv, json, sys
from datetime import datetime
ACTIVE = ("ready", "open", "in progress", "waiting")
DONE = ("done", "closed")
HOLD = ("waiting on owner", "needs owner", "held", "ready for owner review")
RANK, DEPS, RETRY = "Priority rank (CBO)", "Depends on", "Retry at"

def _s(v):
    """Sheet/CSV cells may be missing or None (short rows, DictReader restval). Treat as empty text."""
    return "" if v is None else str(v)

def _held(status):
    st = _s(status).strip().lower()
    return any(h in st for h in HOLD)

def order(rows, now):
    by_id = {_s(r.get("ID")).strip(): r for r in rows if _s(r.get("ID")).strip()}
    ready = []
    for r in rows:
        status = _s(r.get("Status"))
        st = status.strip().lower()
        if _held(status):
            continue
        if not st.startswith(ACTIVE):
            continue
        deps = [d.strip() for d in _s(r.get(DEPS)).replace(";", ",").split(",") if d.strip()]
        if any(not _s(by_id.get(d, {}).get("Status")).strip().lower().startswith(DONE) for d in deps):
            continue
        ra = _s(r.get(RETRY)).strip()
        if ra and datetime.strptime(ra, "%Y-%m-%d %H:%M") > now:
            continue
        rk = _s(r.get(RANK)).strip()
        rid = _s(r.get("ID")).strip()
        if not rk:
            print(f"warning: blank Priority rank (CBO) on ID {rid}", file=sys.stderr)
        opened = _s(r.get("Opened")) or "9999"
        ready.append((int(rk) if rk.isdigit() else 10**9, opened, int(rid) if rid.isdigit() else 10**9, r))
    ready.sort(key=lambda x: x[:3])
    return [x[3] for x in ready]

def next_per_owner(rows, now):
    seen, out = set(), []
    for r in order(rows, now):
        owner = _s(r.get("Owner"))
        if owner not in seen:
            seen.add(owner); out.append(r)
    return out

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--from-csv", required=True); p.add_argument("--now"); p.add_argument("--all", action="store_true")
    a = p.parse_args()
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else datetime.now()
    rows = list(csv.DictReader(open(a.from_csv)))
    res = order(rows, now) if a.all else next_per_owner(rows, now)
    for r in res: print(json.dumps({k: r.get(k, "") for k in ("ID", "Owner", "Status", RANK, "Opened", "Item")}))
