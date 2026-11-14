#!/usr/bin/env python3
"""route_chain.py (CAO, CSO re-screen 2 non-blocking item): hash chain for gb_state/flow/route_log.jsonl. Zero AI.
Every writer (flow.route_log, flow.log_model_call, relay._log) appends through append(): under flock it reads the
last record's hash and writes the new record with prev + hash (action_log canon/entry_hash). verify() walks the
file: lines written before the chain existed (no "hash" key) are allowed only as a leading legacy prefix; after the
first chained record every line must be chained and link to the one before. flow.takeover refuses on a broken chain
and counts an a_report record only if it is chained. Accident guard plus detection, not security: anyone who can
write the file can also recompute the chain.
Usage: route_chain.py verify [PATH]   (exit 0 PASS, 1 FAIL, 3 NO-LOG)"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import fcntl, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from action_log import canon, entry_hash, GENESIS


def _last_hash(f):
    f.seek(0, 2); end = f.tell(); pos, buf = end, b""
    while pos > 0:
        step = min(4096, pos); pos -= step; f.seek(pos); buf = f.read(step) + buf
        lines = [l for l in buf.split(b"\n") if l.strip()]
        if len(lines) >= 2 or pos == 0:
            break
    for l in reversed([l for l in buf.split(b"\n") if l.strip()]):
        try: e = json.loads(l)
        except ValueError: return None
        return e.get("hash") if isinstance(e, dict) and e.get("hash") else GENESIS
    return GENESIS


def append(path, rec):
    """Append rec with prev/hash under an exclusive flock. Returns the stored record."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "ab+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            prev = _last_hash(f)
            if prev is None: prev = GENESIS   # a malformed tail: the new record still links; verify() reports the break
            e = {k: v for k, v in rec.items() if k not in ("prev", "hash")}
            e["prev"] = prev; e["hash"] = entry_hash(prev, e)
            f.seek(0, 2); f.write((canon(e) + "\n").encode())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return e


def verify(path):
    """(ok, msg, chained_records)."""
    if not os.path.exists(path): return False, f"NO-LOG: {path} does not exist", 0
    prev, n, legacy, started = GENESIS, 0, 0, False
    for i, l in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        if not l.strip(): continue
        try: e = json.loads(l)
        except ValueError: return False, f"FAIL: malformed line {i}", n
        if not isinstance(e, dict): return False, f"FAIL: malformed line {i}", n
        if "hash" not in e:
            if started: return False, f"FAIL: unchained line {i} after the chain started", n
            legacy += 1; continue
        started = True
        if e.get("prev") != prev or e["hash"] != entry_hash(prev, {k: v for k, v in e.items() if k != "hash"}):
            return False, f"FAIL: chain broken at line {i}", n
        prev, n = e["hash"], n + 1
    return True, f"PASS: {n} chained records ({legacy} legacy lines before the chain)", n


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "verify":
        p = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.environ.get("GB_STATE", GB_ROOT + "/gb_state"), "flow", "route_log.jsonl")
        ok, msg, _ = verify(p); print(msg); sys.exit(0 if ok else (3 if msg.startswith("NO-LOG") else 1))
    print(__doc__); sys.exit(2)
