#!/usr/bin/env python3
"""C8 line_guard.py (CTO): files that travel on a brief line carry line/from/to matching the line table in
alignment/brief.txt, or the write is refused (exit 2). An approval counts only when the approver is the line's
owner (the sender, e.g. W4 = CFO). Null W7 cost limits make alignment FAIL, naming the missing W4 record.

Accident guard plus detection, not security: from/to are fields on a file, not a verified caller identity.
  line_guard.py check FILE            exit 0 PASS, 2 refused
  line_guard.py cost-limits           exit 0 PASS, 1 FAIL
  line_guard.py outbound ROLE [FILE]  exit 0 PASS, 2 refused (Open Item #37: two-way GT lines)
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import json, os, re, sys

BRIEF = os.environ.get("GB_BRIEF", GB_ROOT + "/alignment/brief.txt")
STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
# Open Item #37 spot-check 3: one physical line per row ([ \t]*, never \s*), parsed ONLY inside the brief's line
# table (header 'ID | From → To | Type | Carries | Source' through the next blank line), so a row in a fenced
# example, a split row or a row elsewhere in the brief is never a line. A malformed or duplicate row inside the
# table raises LineTableError (loud, fail closed).
ROW_RE = re.compile(r"^([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)?)[ \t]*\|[ \t]*([^|\n]+?)[ \t]*(→|↔)[ \t]*([^|\n]+?)[ \t]*\|"
                    r"[ \t]*([^|\n]+?)[ \t]*\|([^\n]*)\|[ \t]*([^|\n]*?)[ \t]*$")
TABLE_HEADER_RE = re.compile(r"^ID[ \t]*\|[ \t]*From[ \t]*→[ \t]*To[ \t]*\|[ \t]*Type[ \t]*\|[ \t]*Carries[ \t]*\|[ \t]*Source[ \t]*$")
GATE_VERDICTS = ("PASS", "HOLD", "VETO")


class LineTableError(Exception):
    pass


class LineRefused(Exception):
    pass


def load_lines(brief=None):
    text = open(brief or BRIEF, encoding="utf-8").read().split("\n")
    heads = [k for k, l in enumerate(text) if TABLE_HEADER_RE.match(l)]
    if len(heads) != 1:
        raise LineTableError(f"brief line table header found {len(heads)} times (need exactly 1)")
    out = {}
    for k in range(heads[0] + 1, len(text)):
        l = text[k]
        if not l.strip():
            break
        m = ROW_RE.match(l)
        if not m:
            cols = l.split("|")
            if len(cols) > 1 and ("→" in cols[1] or "↔" in cols[1]):
                raise LineTableError(f"brief line {k + 1}: malformed line-table row: {l[:80]!r}")
            continue   # not a line (e.g. a ↛ denylist row): never parsed as one
        lid = m.group(1)
        if lid in out:
            raise LineTableError(f"brief line {k + 1}: duplicate line id {lid} (first at line {out[lid]['brief_line']})")
        src = m.group(7).strip()
        out[lid] = dict(id=lid, frm=m.group(2).strip(), to=m.group(4).strip(), type=m.group(5), two_way=m.group(3) == "↔",
                        source=src, stated=src.split(" ")[0] == "STATED", brief_line=k + 1)
    if not out:
        raise LineTableError("brief line table has no rows")
    return out


ANY_ROLE_NAMES = {"any bot", "any role", "any c-role"}


def _known_roles():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from croles import CROLES
    return set(CROLES)


def table_names(table):
    """#37 spot-check 4: a comma list in a From/To cell ('COO, CRO') is separate names, spaces and commas
    stripped; empty pieces dropped."""
    return [n.strip(" \t,") for n in str(table or "").split(",") if n.strip(" \t,")]


def _same(field, table):
    """'CIO' matches 'CIO' and 'CIO Intel tab'; 'COO' matches 'COO, CRO' (each comma-separated name on its own,
    never 'COO,'); 'any bot' matches any known C-role only; exact otherwise."""
    f = str(field or "").strip()
    if not f:
        return False
    for name in table_names(table):
        if name.lower() in ANY_ROLE_NAMES:
            if f in _known_roles():
                return True
            continue
        if f == name or name.split()[0] == f:
            return True
    return False


def check_record(rec, lines=None):
    lines = lines if lines is not None else load_lines()
    ln = str(rec.get("line") or "").strip()
    if not ln: return [f"line missing"]
    row = lines.get(ln)
    if not row: return [f"line {ln} is not in the brief line table"]
    if row.get("two_way"):
        return _check_two_way(rec, row)
    errs = []
    if not _same(rec.get("from"), row["frm"]): errs.append(f"{ln}: from {rec.get('from')!r} is not {row['frm']}")
    if not _same(rec.get("to"), row["to"]): errs.append(f"{ln}: to {rec.get('to')!r} is not {row['to']}")
    return errs


def _check_two_way(rec, row):
    """Open Item #37: two-way rows (A ↔ B). from/to must be A->B (out) or B->A (back). On a GATE line (GT-X:
    X ↔ CSO) the out leg is the request and must carry no verdict; the back leg is the CSO's answer and must
    carry verdict PASS, HOLD or VETO. Pure string check, no LLM."""
    ln, a, b = row["id"], row["frm"], row["to"]
    f, t = rec.get("from"), rec.get("to")
    if _same(f, a) and _same(t, b): leg = "out"
    elif _same(f, b) and _same(t, a): leg = "back"
    else: return [f"{ln}: from/to {f!r}->{t!r} is neither {a}->{b} nor {b}->{a}"]
    if row.get("type") == "GATE":
        v = rec.get("verdict")
        if leg == "out" and v not in (None, ""): return [f"{ln}: a request from {a} cannot carry verdict {v!r}"]
        if leg == "back" and v not in GATE_VERDICTS: return [f"{ln}: the {b} answer needs verdict PASS, HOLD or VETO, got {v!r}"]
    return []


def gate_line(role, lines=None):
    """Open Item #37: the GT line a role asks the CSO on, or None. CMO and CBO have none until Owner adds an
    outbound channel and its GATE line (brief 3.4), so their outbound path fails closed here."""
    lines = lines if lines is not None else load_lines()
    row = lines.get(f"GT-{role}")
    ok = (row and row.get("two_way") and row.get("type") == "GATE" and row.get("stated") is True
          and str(role) == row["frm"] and row["to"] == "CSO")   # spot-check 3: STATED, other party the CSO, exact role
    return row["id"] if ok else None


def check_outbound(role, rec=None, lines=None):
    """Open Item #37: an outbound step by ROLE needs its GT-<role> line in the brief; with REC, the record must
    ride that line (request out, or a CSO verdict back). Returns refusal reasons ([] = PASS)."""
    lines = lines if lines is not None else load_lines()
    ln = gate_line(role, lines)
    if not ln: return [f"{role}: no GT-{role} GATE line in the brief; outbound stays closed until Owner adds a channel and its GT line"]
    if rec is None: return []
    if str(rec.get("line") or "") != ln: return [f"{role}: outbound record rides {rec.get('line')!r}, not {ln}"]
    return check_record(rec, lines)


def guarded_write(path, rec, lines=None, exclusive=False):
    errs = check_record(rec, lines)
    if errs: raise LineRefused("; ".join(errs))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if exclusive:
        # CSO queue_read re-screen: O_CREAT|O_EXCL|O_NOFOLLOW so a planted symlink or existing file is never overwritten
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        # parent opened O_DIRECTORY|O_NOFOLLOW too: a dir swapped for a symlink after the caller's check is refused (ELOOP)
        dfd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            name = os.path.basename(path)
            fd = os.open(name, flags, 0o644, dir_fd=dfd)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(rec, f, indent=1)
                    f.flush(); os.fsync(f.fileno())
            except Exception:
                try: os.unlink(name, dir_fd=dfd)
                except OSError: pass
                raise
        finally:
            os.close(dfd)
        return path
    json.dump(rec, open(path, "w"), indent=1)
    return path


def fallback_dir(path=None):
    """gb_state/alarms/fallback/ of the state root the queue file lives in (<state>/queue/<sub>/<file>)."""
    ap = os.path.abspath(path) if path else ""
    parts = ap.split(os.sep)
    root = os.sep.join(parts[:-3]) if len(parts) > 3 and parts[-3] == "queue" else os.environ.get("GB_STATE", STATE)
    d = os.path.join(root, "alarms", "fallback"); os.makedirs(d, exist_ok=True); return d


def write_fallback(d, path, rec, err):
    """#36 C8 spot-check 3: one fallback alarm per failure, never overwritten: <ms>-<pid>-<uuid>-<basename>,
    opened with O_EXCL. Returns the file written."""
    import time as _t, uuid
    fb = os.path.join(d, f"{int(_t.time() * 1000)}-{os.getpid()}-{uuid.uuid4().hex}-{os.path.basename(path)}")
    fd = os.open(fb, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as f:
        json.dump(dict(rec, fallback=True, intended_path=path, error=f"{type(err).__name__}: {err}"), f, indent=1)
    return fb


def escalate_write(path, rec, lines=None, exclusive=False):
    """#36 C8 deferred queue writers (CPO batch): every rq/es_cso/owner/od queue file goes through guarded_write.
    If that write fails for ANY reason (missing brief, refused line, disk error), first drop a local fallback
    alarm in <state>/alarms/fallback/ naming the intended path and the error, then re-raise (fail loud).
    Same pattern as gate._escalate. Accident guard plus detection, not security.
    exclusive=True: open the dest with O_CREAT|O_EXCL|O_NOFOLLOW (CSO queue_read re-screen)."""
    try:
        return guarded_write(path, rec, lines, exclusive=exclusive)
    except BaseException as e:
        try:
            write_fallback(fallback_dir(path), path, rec, e)
        except Exception:
            pass
        raise


def approval_ok(rec, lines=None):
    """An approval counts only when approved_by (or from) is the line owner (the line's sender)."""
    lines = lines if lines is not None else load_lines()
    row = lines.get(str(rec.get("line") or ""))
    who = rec.get("approved_by") or rec.get("from")
    return bool(row) and _same(who, row["frm"]) and not check_record(rec, lines)


def check_fixlist(fx, lines=None):
    """Fix lists ride FX-<from> (from -> CAO)."""
    c = fx.get("from") or ""
    return check_record(dict(line=f"FX-{c}", **{"from": c, "to": "CAO"}), lines)


def cost_limits(state=None, lines=None):
    """W7 limits null -> FAIL naming the missing W4 record; a W4 record must be on W4 and approved by its owner."""
    st = state or STATE
    lp = os.path.join(st, "context", "cto_cost_limits.json")
    lim = json.load(open(lp)) if os.path.exists(lp) else {}
    keys = ("max_paid_turns_per_task", "max_paid_turns_per_day_when_ollama_down")
    w4p = lim.get("w4_record") or os.path.join(st, "budget", "CTO_cost_limits_W4_CFO.json")
    if any(lim.get(k) is None for k in keys):
        return dict(result="FAIL", note=f"CTO cost limits are null: missing W4 record (CFO -> CTO approved token budget) {w4p}")
    w4 = json.load(open(w4p)) if os.path.exists(w4p) else None
    if not w4: return dict(result="FAIL", note=f"W7 limits set but the W4 record {w4p} is missing")
    if not approval_ok(w4, lines): return dict(result="FAIL", note=f"W4 record {w4p} is not a W4 CFO -> CTO approval by CFO")
    return dict(result="PASS", note=f"W7 limits backed by W4 record {w4p}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        e = check_record(json.load(open(sys.argv[2]))); print(json.dumps(dict(ok=not e, errors=e))); sys.exit(2 if e else 0)
    if len(sys.argv) == 2 and sys.argv[1] == "cost-limits":
        r = cost_limits(); print(json.dumps(r)); sys.exit(0 if r["result"] == "PASS" else 1)
    if len(sys.argv) in (3, 4) and sys.argv[1] == "outbound":
        e = check_outbound(sys.argv[2], json.load(open(sys.argv[3])) if len(sys.argv) == 4 else None)
        print(json.dumps(dict(ok=not e, errors=e))); sys.exit(2 if e else 0)
    print(__doc__); sys.exit(2)
