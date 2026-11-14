#!/usr/bin/env python3
"""held_roles.py: hard holds on roles (alignment/held_roles.json, CAO-owned).

alignment_check.is_quarantined checks this FIRST, so flow.py, gate.py request and
cost_gate refuse a held role even if its onboarding check would PASS. A hold
clears only when both a CIO cosign and a CSO cosign naming the hold id are
recorded. This is an accident guard plus detection, not security: --by is not a
verified caller identity on the shared box.

  held_roles.py show
  held_roles.py hold --role CRO --reason TEXT --by CAO
  held_roles.py cosign --hold-id HOLD-CRO-1 --by CIO --ref "message or card id"
"""
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alignment_check as ac


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")


def _load(path):
    data, err = ac.load_held_roles(path)
    if data is None:
        raise SystemExit(f"REFUSED: {err}")
    data.setdefault("holds", []); data.setdefault("cosigns", [])
    return data


def _save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def hold(role, reason, by, path=None):
    path = path or ac.HELD_ROLES
    if by != "CAO":
        raise SystemExit("REFUSED: only CAO records a hold (held_roles.json is CAO-owned)")
    if not reason.strip():
        raise SystemExit("REFUSED: reason required")
    r = ac.valid_hold_role(role)
    if r is None:
        raise SystemExit(f"REFUSED: hold role {role!r} is not a known C-role (three letters, no hidden characters)")
    role = r
    data = _load(path)
    n = 1 + sum(1 for h in data["holds"] if h.get("role") == role)
    rec = dict(id=f"HOLD-{role}-{n}", role=role, reason=reason, by=by, ts=_now())
    data["holds"].append(rec); _save(path, data)
    return rec


def cosign(hold_id, by, ref, path=None):
    path = path or ac.HELD_ROLES
    by = ac.norm_role(by)
    if by not in ac.HOLD_CLEARERS:
        raise SystemExit("REFUSED: only CIO or CSO may cosign a hold release")
    if not str(ref or "").strip():
        raise SystemExit("REFUSED: ref (message or card id) required")
    data = _load(path)
    hh = [h for h in data["holds"] if h.get("id") == hold_id]
    if not hh:
        raise SystemExit(f"REFUSED: no hold {hold_id}")
    if any(ac.norm_role(h.get("role")) == by for h in hh):
        raise SystemExit(f"REFUSED: {by} cannot cosign its own hold (#36 C16)")
    for h in hh:   # the Owner: CIO alone cosigns holds on the CSO
        allowed = ac.hold_clearers(h.get("role"))
        if by not in allowed:
            raise SystemExit(f"REFUSED: only {' and '.join(allowed)} may cosign {hold_id}"
                             + (f" (Owner {ac.CSO_HOLD_QUOTE})" if str(h.get("role")).upper() == "CSO" else ""))
    rec = dict(hold_id=hold_id, by=by, ref=ref, ts=_now())
    data["cosigns"].append(rec); _save(path, data)
    return rec


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    h = sub.add_parser("hold"); h.add_argument("--role", required=True); h.add_argument("--reason", required=True); h.add_argument("--by", required=True)
    c = sub.add_parser("cosign"); c.add_argument("--hold-id", required=True); c.add_argument("--by", required=True); c.add_argument("--ref", required=True)
    a = p.parse_args(argv)
    if a.cmd == "show":
        data = _load(ac.HELD_ROLES)
        for hh in data["holds"]:
            ok, missing = ac.hold_cleared(hh, data["cosigns"])
            print(f"{hh['id']} {hh['role']}: {'CLEARED' if ok else 'HELD (missing ' + ', '.join(missing) + ')'} - {hh['reason']}")
        return 0
    rec = hold(a.role.upper(), a.reason, a.by) if a.cmd == "hold" else cosign(a.hold_id, a.by, a.ref)
    print(json.dumps(rec, ensure_ascii=False)); return 0


if __name__ == "__main__":
    sys.exit(main())
