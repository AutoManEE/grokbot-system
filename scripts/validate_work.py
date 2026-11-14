#!/usr/bin/env python3
"""validate_work.py (P2-08, CAO): W8 review gate. Only validated work reaches the CPO's queue. Zero AI.
Input: JSON list of candidate jobs {id, owner, origin (directive|coo_result|outside_finding|self_started),
  cao_validation: {by, result}, recheck_result (coo_result only), cio_decision (outside_finding only),
  owner_answer (self_started only)}.
A job is READY for the CPO only if ALL hold:
  V1 cao_validation.by == "CAO" and result == "PASS"
  V2 coo_result -> recheck_result == "PASS" (cao_recheck.py, R1)
  V3 outside_finding -> cio_decision == "apply now" (R2; outside content never becomes work otherwise)
  V4 self_started -> owner_answer == "yes" with a VERIFIED C9 owner_quote_id (section 6; C9: otherwise UNVERIFIED, PENDING)
Usage: validate_work.py -f jobs.json  -> {"ready": [...ids], "held": {id: [failed conditions]}}
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def gate(job):
    f = []
    v = job.get("cao_validation") or {}
    if v.get("by") != "CAO" or v.get("result") != "PASS": f.append("V1 CAO validation")
    o = job.get("origin")
    if o not in ("directive", "coo_result", "outside_finding", "self_started"): f.append("unknown origin")
    if o == "coo_result" and job.get("recheck_result") != "PASS": f.append("V2 CAO re-check of COO result")
    if o == "outside_finding" and job.get("cio_decision") != "apply now": f.append("V3 CIO apply-now decision")
    if o == "self_started":
        import owner_override
        if job.get("owner_answer") != "yes": f.append("V4 Owner's yes (PENDING)")
        elif owner_override.quote_status(job.get("owner_quote_id") or "",
                                         scope=[f"selfstart:{job.get('id')}", f"selfstart:SS-{job.get('id')}"])[0] != "VERIFIED":
            f.append("V4 Owner's yes is UNVERIFIED (no message or card id in alignment/c9_quotes.jsonl)")
    return f

def run(jobs):
    ready, held = [], {}
    for j in jobs:
        f = gate(j)
        (held.__setitem__(j["id"], f) if f else ready.append(j["id"]))
    return dict(ready=ready, held=held)

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("-f", "--file", required=True)
    print(json.dumps(run(json.load(open(p.parse_args().file)))))
