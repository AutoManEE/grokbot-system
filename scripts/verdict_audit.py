#!/usr/bin/env python3
"""C16 verdict_audit.py (CIO weekly, manual while routines are held; code by CAO). Zero AI.
Audits the CSO PASS/HOLD/VETO log in $GB_STATE/gates/*.verdict.json against the requests, plus the C3
detection alarms in $GB_STATE/queue/es_cso/. The CSO veto rule itself remains Owner's decision.
  V1 verdict is PASS, HOLD or VETO, and by CSO (or a Owner release carrying a quote_id and finance_guard PASS)
  V2 the request file exists and its hash matches request_hash (nothing changed after the verdict)
  V3 no CSO PASS on a kind=money request (money is released only by a Owner C9 override)
  V4 no self-approval: the CSO does not PASS a request filed by CSO
  V0 (#36 C16) a verdict with a missing, non-numeric or future ts is a problem, never a skip; backdating is
     judged against audit time (logs/verdict_audit_seen.jsonl), see audit(); first audit only: a verdict whose
     ts is older than the window but whose file changed inside it (backdated) is audited and flagged
  Owner release (#36 C16) is never trusted from verdict-file fields: it needs gates/<id>.release.json with
     allowed true, override_ok, finance_guard.allowed true, and a quote id VERIFIED by owner_override.quote_status
  H1 (C16) held_roles.json cosigns: a role cosigning its own hold, or a cosign by a role not allowed for that
     hold, is a problem
  Waivers (waivers.py): ids listed in alignment/waivers/*.json are always audited (never age out of the
     window). A problem moves to "waived" only when its file is ruled, the item
     names this gate id and check, and the verdict ts lies inside the item's bounds; otherwise it stays a problem.
Usage: verdict_audit.py [--days 7]   exit 0 PASS, 1 FAIL. Accident guard plus detection, not security.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, glob, hashlib, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
WAIVERS = os.environ.get("GB_WAIVERS", GB_ROOT + "/alignment/waivers")


def load_waivers(path=None):
    """{gate_id: waiver_text} for gate ids named in any waiver file (always audited)."""
    import waivers
    return waivers.listed_gate_ids(waivers.load(path or WAIVERS))


def owner_release_ok(st, gid, v, req):
    """True only when release.json backs the verdict; verdict-file release fields are ignored."""
    if v.get("by") != "Owner":
        return False
    rp = os.path.join(st, "gates", f"{gid}.release.json")
    try: rel = json.load(open(rp))
    except (OSError, ValueError): return False
    if not isinstance(rel, dict) or rel.get("allowed") is not True or not rel.get("override_ok"):
        return False
    if not (rel.get("finance_guard") or {}).get("allowed"):
        return False
    if rel.get("quote_id") != v.get("quote_id") or rel.get("request_hash") != _h(req):   # #36 C16 retest: required
        return False
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import owner_override
    try:
        import gate
        scopes = gate.release_scopes(gid, req)   # #36 C9: gate:<id> only (CSO ruling)
    except Exception:
        scopes = [f"gate:{gid}"]
    return owner_override.quote_status(str(rel.get("quote_id") or ""),
                                       owner_override.load_quotes(os.environ.get("GB_C9_QUOTES") or None),
                                       scope=scopes)[0] == "VERIFIED"


def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def audit_holds(path=None):
    """H1: cosigns that held_roles.cosign should have refused. Unreadable registry = problem (fail closed)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import alignment_check as ac
    from pathlib import Path
    data, err = ac.load_held_roles(Path(path) if path else None)
    if data is None:
        return [f"H1 held_roles: {err}"]
    roles = {h.get("id"): ac.norm_role(h.get("role")) for h in data.get("holds", []) if isinstance(h, dict)}
    known = ac.known_roles()
    out = [f"H1 {h.get('id')}: invalid or unknown hold role {h.get('role')!r} (is_held fails closed)"
           for h in data.get("holds", []) if isinstance(h, dict) and ac.valid_hold_role(h.get("role"), known) is None]
    for c in data.get("cosigns", []):
        if not isinstance(c, dict):
            continue
        hid, by = c.get("hold_id"), ac.norm_role(c.get("by"))
        role = roles.get(hid)
        if role is None:
            out.append(f"H1 {hid}: cosign by {by} names no hold"); continue
        if by == role:
            out.append(f"H1 {hid}: {by} cosigned its own hold")
        elif by not in ac.hold_clearers(role):
            out.append(f"H1 {hid}: {by} may not cosign a {role} hold"
                       + (f" (CIO only, Owner {ac.CSO_HOLD_QUOTE})" if role == "CSO" else ""))
    return out


def seen_path(st):
    return os.path.join(st, "logs", "verdict_audit_seen.jsonl")


def _load_seen(st):
    """({verdict file name: first record}, last run time) from the audit's own append-only seen ledger."""
    out, last = {}, None
    try:
        for line in open(seen_path(st)):
            try: r = json.loads(line)
            except ValueError: continue
            if isinstance(r, dict) and r.get("file"):
                out.setdefault(r["file"], r)
            if isinstance(r, dict) and isinstance(r.get("run"), (int, float)):
                last = r["run"] if last is None else max(last, r["run"])
    except OSError:
        pass
    return out, last


def audit(state=None, days=7, now=None, waivers_path=None, held_roles_path=None, record=True):
    """#36 C16 retest: backdating is judged against AUDIT time, not file mtime. Every verdict an audit sees is
    appended to logs/verdict_audit_seen.jsonl (file, sha256, ts, seen_at). A verdict first seen now whose ts is
    older than the previous audit run is V0 (it should have been seen then; `touch -d` does not help); a seen
    verdict whose bytes changed is V0. Only the very first audit (no ledger yet) falls back to the mtime check."""
    st = state or STATE; now = now or time.time(); since = now - days * 86400
    seen, last_run = _load_seen(st)
    new_seen = []
    import waivers
    witems = waivers.load(waivers_path or WAIVERS)
    waived = waivers.listed_gate_ids(witems)
    waived_probs = []
    probs, counts = [], dict(PASS=0, HOLD=0, VETO=0, other=0)
    for vf in sorted(glob.glob(os.path.join(st, "gates", "*.verdict.json"))):
        gid = os.path.basename(vf).split(".")[0]
        try: v = json.load(open(vf))
        except ValueError: probs.append(f"{os.path.basename(vf)}: unreadable"); continue
        if not isinstance(v, dict): probs.append(f"{os.path.basename(vf)}: not an object"); continue
        gid = v.get("id") or gid
        ts = v.get("ts")
        fname = os.path.basename(vf)
        sha = hashlib.sha256(open(vf, "rb").read()).hexdigest()
        prev = seen.get(fname)
        if prev is None:
            new_seen.append(dict(file=fname, id=gid, sha256=sha, ts=ts if isinstance(ts, (int, float)) else None, seen_at=now))
        changed = prev is not None and prev.get("sha256") != sha
        if changed:
            probs.append(f"V0 {gid}: verdict file changed after the audit that first saw it")
        num_ts = isinstance(ts, (int, float)) and not isinstance(ts, bool)
        if not num_ts:
            probs.append(f"V0 {gid}: verdict ts missing or non-numeric ({ts!r})")
        elif ts > now + 300:
            probs.append(f"V0 {gid}: verdict ts is in the future")
        elif prev is None and last_run is not None and ts < last_run - 60:
            probs.append(f"V0 {gid}: verdict ts predates the previous audit run but the verdict was not seen then (backdated)")
        elif ts < since:
            if last_run is None and os.path.getmtime(vf) >= since:
                probs.append(f"V0 {gid}: verdict ts is older than the window but the file changed inside it (backdated)")
            elif not changed and gid not in waived:
                continue
        word = v.get("verdict"); counts[word if word in counts else "other"] += 1
        rp = os.path.join(st, "gates", f"{gid}.request.json")
        req = json.load(open(rp)) if os.path.exists(rp) else None
        owner_release = req is not None and owner_release_ok(st, gid, v, req)
        if word not in ("PASS", "HOLD", "VETO") or not (v.get("by") == "CSO" or owner_release):
            probs.append(f"V1 {gid}: verdict {word!r} by {v.get('by')!r}")
        if req is None: probs.append(f"V2 {gid}: request file missing"); continue
        if v.get("request_hash") != _h(req): probs.append(f"V2 {gid}: request changed after verdict")
        if word == "PASS" and req.get("kind") == "money" and not owner_release:
            wv = waivers.gate_waiver(gid, "C16 V3", ts, witems)
            if wv:
                waived_probs.append(f"V3 {gid}: CSO PASS on a money request [waived: {wv}]")
            else:
                probs.append(f"V3 {gid}: CSO PASS on a money request")
        if word == "PASS" and v.get("by") == "CSO" and str(req.get("role") or "").upper() == "CSO":
            probs.append(f"V4 {gid}: CSO passed its own request")
    probs = [p + (f" [waiver {waived[p.split()[1].rstrip(':')]}]" if len(p.split()) > 1 and p.split()[1].rstrip(":") in waived else "")
             for p in probs]
    if record and abs(now - time.time()) < 300:   # simulated-time audits never move the recorded run time
        os.makedirs(os.path.dirname(seen_path(st)), exist_ok=True)
        with open(seen_path(st), "a") as f:
            for r in new_seen:
                f.write(json.dumps(r) + "\n")
            f.write(json.dumps(dict(run=now)) + "\n")
    probs += audit_holds(held_roles_path)
    alarms = [p for p in glob.glob(os.path.join(st, "queue", "es_cso", "*.json")) if os.path.getmtime(p) >= since]
    return dict(result="FAIL" if probs else "PASS", window_days=days, verdicts=counts, es_cso_alarms=len(alarms),
                problems=probs, waived=waived_probs, waived_ids=sorted(waived))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=7)
    r = audit(days=ap.parse_args().days); print(json.dumps(r, indent=1)); sys.exit(1 if r["result"] == "FAIL" else 0)
