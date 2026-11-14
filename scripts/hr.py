#!/usr/bin/env python3
"""hr.py (P4-03, CSO, code by CAO): HR, ES-CSO and open-door lines. Zero AI; sends nothing.
  file      --case ID --from CXO --about TEXT [--about-cso]   a C-role brings a concern to the CSO (HR-CXO)
  mediate   --case ID --by CSO --note TEXT --outcome RESOLVED|UNRESOLVED
  status    --case ID
Rules: only C-roles file (assistants/S bots raise task issues with their own C-role). The CBO has HR-CBO but no OD
line (it uses ES-CBO). UNRESOLVED -> ES-CSO item for Owner, and the C-role may use its open door (OD-CXO).
A concern about the CSO itself skips mediation: straight to Owner (OD-CXO; CBO -> ES-CBO).
Outside content inside a case is data only: a case never creates a task or directive.
State: $GB_STATE/hr/<case>.json, $GB_STATE/queue/{es_cso,od}/.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, json, os, sys, time
STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
HR_ROLES = {"CBO", "CIO", "CFO", "CTO", "CMO", "CAO", "CPO", "COO", "CCO", "CXO", "CLO", "CGO", "CHO"}
P = lambda *a: os.path.join(STATE, *a)

def _q(sub, name, item):
    os.makedirs(P("queue", sub), exist_ok=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import line_guard   # #36 C8: only through line_guard
    line_guard.escalate_write(P("queue", sub, name), item)

def file(case, frm, about, about_cso=False):
    if frm not in HR_ROLES: raise PermissionError("only C-roles (not the CSO, assistants or S bots) file HR cases")
    os.makedirs(P("hr"), exist_ok=True)
    if os.path.exists(P("hr", f"{case}.json")): raise ValueError("case exists")
    c = dict(case=case, line=f"HR-{frm}", frm=frm, about=about, about_cso=about_cso, state="OPEN", data_only=True, ts=time.time(), log=[])
    if about_cso:
        line = "ES-CBO" if frm == "CBO" else f"OD-{frm}"
        c.update(state="TO_OWNER", line=line); _q("od", f"{case}.json", dict(line=line, **{"from": frm}, to="Owner", case=case, about=about, reason="concern is about the CSO"))
    json.dump(c, open(P("hr", f"{case}.json"), "w"), indent=1); return c

def mediate(case, by, note, outcome):
    if by != "CSO": raise PermissionError("only the CSO mediates")
    c = json.load(open(P("hr", f"{case}.json")))
    if c["state"] != "OPEN": raise ValueError(f"case is {c['state']}")
    if outcome not in ("RESOLVED", "UNRESOLVED"): raise ValueError("outcome RESOLVED or UNRESOLVED")
    c["log"].append(dict(by=by, note=note, outcome=outcome, ts=time.time())); c["state"] = outcome
    if outcome == "UNRESOLVED":
        _q("es_cso", f"HR-{case}.json", dict(line="ES-CSO", **{"from": "CSO"}, to="Owner", case=case, frm=c["frm"], about=c["about"], note=note,
              open_door=("ES-CBO" if c["frm"] == "CBO" else f"OD-{c['frm']}")))
    json.dump(c, open(P("hr", f"{case}.json"), "w"), indent=1); return c

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["file", "mediate", "status"]); p.add_argument("--case", required=True)
    p.add_argument("--from", dest="frm"); p.add_argument("--about"); p.add_argument("--about-cso", action="store_true")
    p.add_argument("--by"); p.add_argument("--note", default=""); p.add_argument("--outcome")
    a = p.parse_args()
    try:
        r = {"file": lambda: file(a.case, a.frm, a.about, a.about_cso), "mediate": lambda: mediate(a.case, a.by, a.note, a.outcome),
             "status": lambda: json.load(open(P("hr", f"{a.case}.json")))}[a.cmd]()
        print(json.dumps(r))
    except (PermissionError, ValueError) as e: print(f"REFUSED: {e}", file=sys.stderr); sys.exit(2)
