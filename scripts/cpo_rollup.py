#!/usr/bin/env python3
"""cpo_rollup.py (P3-02, CPO, code by CAO): RP lines in, UP-CIO out. Zero AI.
Each C-role drops Section 5 reports (JSON) in $GB_STATE/reports/inbox/<ROLE>/ (RP-<ROLE>). This script:
  1. validates each with report_check.py and C7 ids: handoff_id (flow task id) required, open_item_id WARN when missing,
     reject when missing on a report that marks an Open Items row Done (invalid -> reports/rejected/<ROLE>/ + .errors.json, not forwarded)
  2. forwards the file UNCHANGED (byte copy) to reports/cio/ (UP-CIO) and records its sha256
  3. appends every forwarded report to the report action log $GB_STATE/reports/report_log.jsonl (hash-chained,
     same format as action_log.py; kept local because reports may hold private data, so it is never committed)
  4. writes the CPO roll-up reports/cio/rollup-<ts>.json (counts by role and result + the hashes)
Only the CPO sends reports to the CIO; RP roles: CBO, CIO, CFO, CTO, CMO, CSO, CAO, COO.
Usage: cpo_rollup.py
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import glob, hashlib, json, os, shutil, sys, time
sys.path.insert(0, GB_ROOT + "/scripts")
import action_log as al, report_check as rc
STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
RP = ["CBO", "CIO", "CFO", "CTO", "CMO", "CSO", "CAO", "COO", "CRO"]
R = lambda *a: os.path.join(STATE, "reports", *a)

def run():
    os.makedirs(R("cio"), exist_ok=True); fwd, rej, warn = [], [], []
    for role in RP:
        for f in sorted(glob.glob(R("inbox", role, "*.json"))):
            raw = open(f, "rb").read()
            try:
                rep = json.loads(raw); errs = rc.check(rep)
                if not errs:   # C7 (CPO ruling): handoff_id required; open_item_id WARN unless the report marks a row Done
                    e7, w7 = rc.check_handoff_ids(rep, state=STATE); errs = e7
                    if w7 and not e7: warn.append(dict(role=role, file=os.path.basename(f), warnings=w7))
            except ValueError: rep, errs = None, ["not JSON"]
            if not errs and rep.get("bot") != role: errs = [f"report bot {rep.get('bot')} != inbox role {role}"]
            if errs:
                d = R("rejected", role); os.makedirs(d, exist_ok=True); shutil.move(f, os.path.join(d, os.path.basename(f)))
                json.dump(errs, open(os.path.join(d, os.path.basename(f) + ".errors.json"), "w")); rej.append(f); continue
            h = hashlib.sha256(raw).hexdigest(); name = f"{role}-{int(time.time()*1000)}-{os.path.basename(f)}"
            open(R("cio", name), "wb").write(raw); os.remove(f)
            al.append(R("report_log.jsonl"), {**rep, "item": f"REPORT {role} {rep['item']}", "forwarded_as": name, "sha256": h})
            fwd.append(dict(role=role, file=name, sha256=h, result=rep["result"], handoff_id=rep.get("handoff_id"), open_item_id=rep.get("open_item_id")))
    if fwd:
        roll = dict(by="CPO", line="UP-CIO", ts=time.time(), count=len(fwd), by_role={r: sum(x["role"] == r for x in fwd) for r in RP},
                    by_result={k: sum(x["result"] == k for x in fwd) for k in ("PASS", "FAIL", "PENDING")}, reports=fwd, warnings=warn)
        json.dump(roll, open(R("cio", f"rollup-{int(time.time()*1000)}.json"), "w"), indent=1)
    return dict(forwarded=len(fwd), rejected=len(rej))   # C7 warnings are in the rollup file

if __name__ == "__main__":
    print(json.dumps(run()))
