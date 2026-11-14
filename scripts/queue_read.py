#!/usr/bin/env python3
"""queue_read.py (P1-06, CBO/CSO read side, code by CAO): list items waiting for the CBO or Owner. Zero AI, sends nothing.
  list [--line rq|es_cso|all]          RQ-* items for the CBO (then ES-CBO to Owner), ES-CSO items for Owner
  close --file PATH --answer TEXT      moves an item to done/ with Owner's answer (unanswered items stay PENDING)
  legacy-fill --file PATH              option (b): fill from/to on a narrowly scoped alignment-TEAM item written before the writer fix (LEGACY_CUTOFF)
                                       (stays in es_cso; closing still needs Owner's answer). Also --legacy-fill flag.
State: $GB_STATE/queue/{rq,es_cso,done}. The CBO reads this at each wake and tells Owner in chat.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, glob, json, os, re, shutil, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
TZ = ZoneInfo(os.environ.get("GB_TZ", "UTC"))
LEGACY_CUTOFF = datetime(2034, 1, 13, 0, 0, tzinfo=TZ)
LEGACY_FLOOR = datetime(2034, 1, 12, 0, 0, tzinfo=TZ)   # the old writer ran; nothing older is a legacy item
LEGACY_NAME_RE = re.compile(r"^alignment-TEAM-.+\.json$")

def items(line="all"):
    dirs = {"rq": ["rq"], "es_cso": ["es_cso"], "all": ["rq", "es_cso"]}[line]
    out = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(STATE, "queue", d, "*.json"))):
            it = json.load(open(f)); it["_file"] = f; it["_status"] = "PENDING"; out.append(it)
    return sorted(out, key=lambda x: x.get("ts", 0))

def _qroot():
    return os.path.realpath(os.path.join(STATE, "queue"))

def _require_real_done(qroot):
    """Refuse if queue/done is a symlink or realpath(done) is not qroot/done (CSO queue_read re-screen)."""
    expected = os.path.join(qroot, "done")
    lexical = os.path.join(STATE, "queue", "done")
    if os.path.lexists(lexical) and (os.path.islink(lexical) or os.path.realpath(lexical) != expected):
        raise ValueError("REFUSED: queue/done is a symlink or resolves outside qroot/done")
    os.makedirs(expected, exist_ok=True)
    if os.path.islink(expected) or os.path.realpath(expected) != expected:
        raise ValueError("REFUSED: queue/done is a symlink or resolves outside qroot/done")
    return expected

def close(path, answer):
    """Move one rq/es_cso item to done/ with Owner's answer. #36 C8: the done
    record goes through line_guard.escalate_write (guarded_write on the item's own brief line, fallback alarm on any
    failure), never a raw json.dump. Refuses: no answer, a path outside queue/rq or queue/es_cso, a symlink or non-file,
    queue/done a symlink or outside qroot/done, and an existing done/ file (never overwritten). The done file is opened
    with O_CREAT|O_EXCL|O_NOFOLLOW. The original is removed only after the guarded write succeeded."""
    if not (isinstance(answer, str) and answer.strip()):
        raise ValueError("REFUSED: an answer from Owner is required to close an item")
    qroot = _qroot()
    rp = os.path.realpath(path)
    if os.path.islink(path) or not os.path.isfile(rp) or not rp.endswith(".json") or \
            os.path.dirname(rp) not in (os.path.join(qroot, "rq"), os.path.join(qroot, "es_cso")):
        raise ValueError("REFUSED: close takes a regular .json item file in queue/rq or queue/es_cso")
    it = json.load(open(rp))
    if not isinstance(it, dict): raise ValueError("REFUSED: queue item is not a JSON object")
    it.update(answer=answer, closed_ts=time.time())
    d = _require_real_done(qroot)
    dst = os.path.join(d, os.path.basename(rp))
    if os.path.lexists(dst): raise ValueError(f"REFUSED: {os.path.basename(dst)} already in done/ (never overwritten)")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import line_guard
    line_guard.escalate_write(dst, it, exclusive=True)
    os.remove(rp); return dst

def _parse_ts(ts):
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        try: return datetime.fromtimestamp(float(ts), TZ)
        except (ValueError, OverflowError, OSError): return None
    if not isinstance(ts, str):
        return None
    s = ts.strip()
    for suf in (" UTC", " CST", " CDT", " CT"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(s, fmt).replace(tzinfo=TZ)
                except ValueError:
                    continue
            return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)

def legacy_fill_problem(path, it=None):
    """'' if this file may be legacy-filled, else the reason. Every gate is required (CSO option (b))."""
    qroot = _qroot()
    if os.path.islink(path):
        return "path is a symlink"
    rp = os.path.realpath(path)
    if not os.path.isfile(rp) or not rp.endswith(".json") or os.path.dirname(rp) != os.path.join(qroot, "es_cso"):
        return "file is not a regular .json in queue/es_cso"
    if not LEGACY_NAME_RE.fullmatch(os.path.basename(rp)):
        return "name is not alignment-TEAM-*.json"
    if it is None:
        try: it = json.load(open(rp))
        except (OSError, ValueError, UnicodeDecodeError):
            return "item is not readable JSON"
    if not isinstance(it, dict):
        return "item is not a JSON object"
    if it.get("line") != "ES-CSO":
        return "line is not ES-CSO"
    if "from" in it and it.get("from") is not None:
        return "from is present and not None"
    if it.get("to") != ["CSO", "Owner"]:
        return "to is not ['CSO', 'Owner']"
    if it.get("raised_by") != "CAO":
        return "raised_by is not CAO"
    if it.get("kind") != "alignment team FAIL":
        return "kind is not alignment team FAIL"
    ts = _parse_ts(it.get("ts"))
    if ts is None or not (LEGACY_FLOOR <= ts < LEGACY_CUTOFF):
        return "ts is missing or outside the legacy window, before the writer fix"
    return ""

def legacy_fill(path):
    """Option (b), --legacy-fill: when every gate holds, set from=CSO and to=Owner, keep the original under
    legacy_original with legacy_filled ['from','to'], write through escalate_write (exclusive), and delete the
    original only after the write succeeds. The item stays in es_cso; closing still needs Owner's answer.
    Do not run this on the live pre-Nov-13 items until Owner answers."""
    qroot = _qroot()
    rp = os.path.realpath(path)
    it = json.load(open(rp)) if os.path.isfile(rp) else None
    why = legacy_fill_problem(path, it)
    if why:
        raise ValueError(f"REFUSED: legacy-fill {why}")
    original = json.loads(json.dumps(it))   # deep copy of the unfilled record
    filled = dict(it)
    filled["from"] = "CSO"
    filled["to"] = "Owner"
    filled["legacy_original"] = original
    filled["legacy_filled"] = ["from", "to"]
    staging = rp + ".legacy-fill"
    if os.path.lexists(staging):
        raise ValueError("REFUSED: a legacy-fill staging file already exists")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import line_guard
    line_guard.escalate_write(staging, filled, exclusive=True)
    os.replace(staging, rp)
    return rp

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["list", "close", "legacy-fill"])
    p.add_argument("--line", default="all"); p.add_argument("--file"); p.add_argument("--answer")
    p.add_argument("--legacy-fill", action="store_true", help="alias for the legacy-fill command; also allowed with close to fill then close")
    a = p.parse_args()
    if a.cmd == "list":
        for it in items(a.line): print(json.dumps({k: v for k, v in it.items()}))
    elif a.cmd == "legacy-fill" or (a.cmd == "close" and a.legacy_fill and not a.answer):
        if not a.file: sys.exit("REFUSED: --file is required")
        try: print(legacy_fill(a.file))
        except ValueError as e: sys.exit(str(e))
    else:
        if not a.answer: sys.exit("REFUSED: an answer from Owner is required to close an item")
        try:
            path = a.file
            if a.legacy_fill:
                path = legacy_fill(path)
            print(close(path, a.answer))
        except ValueError as e:
            sys.exit(str(e))
