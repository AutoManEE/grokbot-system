#!/usr/bin/env python3
"""action_log.py (A0, CAO-owned): append-only, hash-chained action log in Section 5 format. Zero AI.

Every entry is one JSON line in the log (default <repo>/alignment/action_log.jsonl):
  ts, item, bot, result (PASS/FAIL/PENDING/NEEDS-CBO/SKIPPED/INFO), objective, options[{name,deltas,pros,cons}],
  decision, reasoning, confidence (0-100), conditions[{name,required,result}], defaults_chosen[], prev, hash
hash = sha256(prev + canonical JSON of the entry without 'hash'). Editing or deleting any earlier line breaks
the chain, and `verify` FAILs naming the first bad line. Corrections are new entries (append-only).

Usage:
  action_log.py append --json @entry.json [--log PATH]      (prints the new hash)
  action_log.py verify [--log PATH]                          (exit 0 PASS, 1 FAIL)
  action_log.py render [--log PATH] [--out PATH.md]          (human-readable Markdown)
Rules enforced on append: all Section 5 fields present; confidence is an int 0-100; if any required condition
is FAIL, result must be FAIL (confidence never overrides a failed required condition).
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, hashlib, json, os, sys, fcntl
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_LOG = GB_ROOT + "/alignment/action_log.jsonl"
REQUIRED = ["item", "bot", "result", "objective", "options", "decision", "reasoning", "confidence", "conditions"]
RESULTS = {"PASS", "FAIL", "PENDING", "NEEDS-CBO", "SKIPPED", "INFO"}
GENESIS = "0" * 64


def canon(d):
    return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def entry_hash(prev, body):
    return hashlib.sha256((prev + canon(body)).encode()).hexdigest()


def validate(e):
    errs = [f"missing field: {k}" for k in REQUIRED if k not in e]
    if errs:
        return errs
    if e["result"] not in RESULTS:
        errs.append(f"result must be one of {sorted(RESULTS)}")
    c = e["confidence"]
    if not isinstance(c, int) or isinstance(c, bool) or not 0 <= c <= 100:
        errs.append("confidence must be an integer 0-100")
    if not isinstance(e["options"], list) or not e["options"]:
        errs.append("options must be a non-empty list")
    else:
        for o in e["options"]:
            for k in ("name", "deltas", "pros", "cons"):
                if k not in o:
                    errs.append(f"option missing {k}")
    if not isinstance(e["conditions"], list):
        errs.append("conditions must be a list")
    else:
        for cd in e["conditions"]:
            if not {"name", "required", "result"} <= set(cd):
                errs.append("condition needs name, required, result")
            elif cd["result"] not in ("PASS", "FAIL", "N/A"):
                errs.append(f"condition result must be PASS/FAIL/N/A: {cd['name']}")
        if e["result"] == "PASS" and any(cd.get("required") and cd.get("result") == "FAIL" for cd in e["conditions"]):
            errs.append("a required condition FAILed, so result cannot be PASS")
    return errs


def read(log):
    if not os.path.exists(log):
        return []
    with open(log) as f:
        return [json.loads(l) for l in f if l.strip()]


def verify(log):
    prev = GENESIS
    entries = read(log)
    for n, e in enumerate(entries, 1):
        body = {k: v for k, v in e.items() if k != "hash"}
        if e.get("prev") != prev or e.get("hash") != entry_hash(prev, body):
            return False, f"FAIL: chain broken at line {n} (item {e.get('item')})"
        prev = e["hash"]
    return True, f"PASS: {len(entries)} entries, chain intact"


def append(log, e):
    errs = validate(e)
    if errs:
        raise ValueError("; ".join(errs))
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        lines = [json.loads(l) for l in f if l.strip()]
        prev = lines[-1]["hash"] if lines else GENESIS
        e = dict(e)
        e.setdefault("ts", datetime.now(ZoneInfo(os.environ.get("GB_TZ", "UTC"))).strftime("%Y-%m-%d %H:%M:%S %Z"))
        e.pop("hash", None)
        e["prev"] = prev
        e["hash"] = entry_hash(prev, e)
        f.seek(0, 2)
        f.write(canon(e) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    return e["hash"]


def render(log):
    out = ["# Action log (Section 5 format) — generated from action_log.jsonl; do not edit", ""]
    for e in read(log):
        out.append(f"## {e['ts']} · {e['item']} · {e['bot']} · **{e['result']}**")
        out.append(f"- **Objective:** {e['objective']}")
        out.append("- **Options:**")
        for o in e["options"]:
            out.append(f"  - {o['name']} — Deltas: {o['deltas']}; Pros: {o['pros']}; Cons: {o['cons']}")
        out.append(f"- **Decision:** {e['decision']}")
        out.append(f"- **Reasoning:** {e['reasoning']}")
        if e["conditions"]:
            out.append("- **Conditions:** " + "; ".join(
                f"{c['name']}{' (required)' if c['required'] else ''} = {c['result']}" for c in e["conditions"]))
        if e.get("defaults_chosen"):
            out.append("- **Defaults chosen:** " + "; ".join(e["defaults_chosen"]))
        if e.get("commit"):
            out.append(f"- **Commit:** {e['commit']}")
        out.append(f"- **Confidence:** {e['confidence']}")
        out.append(f"- hash `{e['hash'][:12]}` prev `{e['prev'][:12]}`")
        out.append("")
    return "\n".join(out)


def load_json_arg(s):
    if s.startswith("@"):
        return json.load(open(s[1:]))
    if s == "-":
        return json.load(sys.stdin)
    return json.loads(s)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["append", "verify", "render"])
    p.add_argument("--json")
    p.add_argument("--log", default=DEFAULT_LOG)
    p.add_argument("--out")
    a = p.parse_args()
    if a.cmd == "append":
        try:
            print(append(a.log, load_json_arg(a.json)))
        except ValueError as ex:
            print(f"REFUSED: {ex}", file=sys.stderr); sys.exit(2)
    elif a.cmd == "verify":
        ok, msg = verify(a.log); print(msg); sys.exit(0 if ok else 1)
    else:
        md = render(a.log)
        if a.out:
            open(a.out, "w").write(md + "\n")
        else:
            print(md)
