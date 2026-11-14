#!/usr/bin/env python3
"""report_audit.py (P3-03, CIO, code by CAO): check the CPO forwarded every report unchanged. Zero AI.
Compares every report in $GB_STATE/reports/cio/ (except rollups) with the hash-chained report log:
  A1 report log chain intact | A2 every logged report exists in cio/ with the same sha256 |
  A3 every report in cio/ is in the log (nothing slipped in unlogged)
Exit 0 PASS, 1 FAIL (problems listed).
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import glob, hashlib, json, os, sys
sys.path.insert(0, GB_ROOT + "/scripts"); import action_log as al
STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
R = lambda *a: os.path.join(STATE, "reports", *a)

def audit():
    probs = []; log = R("report_log.jsonl")
    ok, msg = al.verify(log)
    if not ok: probs.append(f"A1 {msg}")
    logged = {e["forwarded_as"]: e["sha256"] for e in al.read(log)}
    for name, h in logged.items():
        p = R("cio", name)
        if not os.path.exists(p): probs.append(f"A2 missing in cio/: {name}")
        elif hashlib.sha256(open(p, "rb").read()).hexdigest() != h: probs.append(f"A2 changed after forwarding: {name}")
    for p in glob.glob(R("cio", "*.json")):
        n = os.path.basename(p)
        if not n.startswith("rollup-") and n not in logged: probs.append(f"A3 not in report log: {n}")
    return dict(result="FAIL" if probs else "PASS", problems=probs, reports=len(logged))

if __name__ == "__main__":
    r = audit(); print(json.dumps(r)); sys.exit(0 if r["result"] == "PASS" else 1)
