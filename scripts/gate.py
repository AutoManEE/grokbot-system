#!/usr/bin/env python3
"""gate.py (CSO-owned, code by CAO): GATE lines GT-CFO, GT-CAO, GT-COO. Zero AI.

Before money is spent or an irreversible step runs, the acting role files a request and waits; the CSO answers
PASS, HOLD or VETO; the acting script calls `require` and runs ONLY on a fresh PASS for that exact request.
  request --id ID --role CFO|CAO|COO --kind money|irreversible|outbound --summary TEXT [--details JSON]
  verdict --id ID --verdict PASS|HOLD|VETO --by CSO --reason TEXT [--ttl-min 60]
  release --id ID --override FILE   money HOLD only: Owner override + finance_guard PASS
  require --id ID        exit 0 only on PASS (money: plus a release.json with a VERIFIED quote); else exit 1
  status  --id ID
  access  --role R --account NAME --mode read|write   exit 0 only for an approved READ
Rules: money requests only from the CFO (GT-CFO). The finance bot is read-only by default: every spend request
(details.action in SPEND_ACTIONS) auto-HOLDs at request time and is filed to the Owner on ES-CSO. No gate
verdict from the CSO or anyone else can release a held money request. Release requires an Owner-signed override
carrying a verifiable, gate-scoped quote id AND finance_guard PASS against the Owner-approved limits; gate.py
records both, and each quote releases exactly one request (used-quote ledger with a high-water record).
Transfers, deposits, withdrawals, payments, any request with a destination, and any unknown action stay
auto-VETO. Outbound requires a channel and GT line the Owner has added (none -> automatic VETO). Only "CSO" may
set a CSO verdict; money PASS is only via release(), and require() re-checks release.json + a VERIFIED quote.
Missing, HOLD, VETO, expired or changed request = refuse (fail closed). HOLD/VETO also writes an ES-CSO item
(a file for the Owner; nothing is sent). State: $GB_STATE/gates, /queue/es_cso.
Accident guard plus detection, not security.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, hashlib, json, math, os, re, sys, time

STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
# Outbound has no list here: gate.request asks line_guard.check_outbound (GT-<role> STATED in the brief's line table).
LINES = {"money": {"CFO"}, "irreversible": {"CAO", "COO", "CFO"}}
OUTBOUND_CHANNELS = []  # the Owner adds channels explicitly (brief 3.4)
# Read-only account access the Owner approved. Viewing needs no GATE; any spend is a GT-CFO money request.
READ_ACCESS = {"finance_readonly": {"CFO"}}

TRANSFER_ACTIONS = {"transfer", "withdraw", "withdrawal", "deposit", "pay", "payment"}
SPEND_ACTIONS = {"spend", "purchase", "subscribe", "renew"}
# Roles with no money access at all, checked before anything else.
NO_MONEY_ROLES = {"CCO"}

def access(role, account, mode):
    """Return (allowed, reason). Only 'read' is ever granted here; writes/transfers always refused."""
    if mode != "read":
        return False, "only read access exists; spending is a GT-CFO request and transfers are auto-VETOed"
    if role in READ_ACCESS.get(account, set()):
        return True, f"{role} has read-only access to {account} (Owner-approved)"
    return False, f"{role} has no read access to {account}"


def _p(*a):
    d = os.path.join(STATE, *a[:-1]); os.makedirs(d, exist_ok=True); return os.path.join(d, a[-1])


def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else None


def _reject_constant(c):
    raise SystemExit(f"refused: non-finite number {c} in details")


def _num(x):
    """Finite real number only (#36 retest: +-inf and nan are never numbers here)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _d(req):
    return req.get("details") or {}


def is_transfer(req):
    """Transfers, deposits, withdrawals, payments, any destination, or any unknown action stay auto-VETO."""
    if req.get("kind") != "money":
        return False
    d = _d(req)
    act = str(d.get("action") or "").lower()
    return bool(d.get("destination")) or act in TRANSFER_ACTIONS or act not in SPEND_ACTIONS


def classify_money(req):
    return "transfer" if is_transfer(req) else "spend"


def auto_rules(req):
    """Return (verdict, reason) when a rule decides without the CSO, else None."""
    if req["kind"] == "outbound" and not OUTBOUND_CHANNELS:
        return "VETO", "no outbound channel added by the Owner"
    if req["kind"] == "money":
        if classify_money(req) == "transfer":
            return "VETO", "bots never move money in or out; only Owner-approved spend requests may be gated"
        return ("HOLD", "kind=money auto-HOLD: release needs an Owner override with a scoped quote id and "
                        "finance_guard PASS (accident guard plus detection, not security)")
    return None


def request(id_, role, kind, summary, details=None):
    if (role or "").upper() in NO_MONEY_ROLES and (kind == "money" or is_transfer(dict(kind=kind, details=details or {}))):
        raise SystemExit(f"REFUSED: {role} has no money or gate.py money access (kind '{kind}')")
    import alignment_check
    alignment_check.refuse_if_quarantined(role)
    if kind == "outbound":
        import line_guard
        try:
            errs = line_guard.check_outbound(role)
        except Exception as e:   # missing brief, malformed or duplicate table row: fail closed
            errs = [f"line table unreadable: {type(e).__name__}: {e}"]
        if errs:
            raise SystemExit(f"REFUSED: {role} has no GATE line for kind 'outbound' ({'; '.join(errs)})")
    elif role not in LINES.get(kind, set()):
        raise SystemExit(f"REFUSED: {role} has no GATE line for kind '{kind}'")
    req = dict(id=id_, role=role, kind=kind, summary=summary, details=details or {}, ts=time.time())
    path = _p("gates", f"{id_}.request.json")
    if os.path.exists(path):
        raise SystemExit("REFUSED: request id already used")
    json.dump(req, open(path, "w"), indent=1)
    auto = auto_rules(req)
    if auto:
        verdict(id_, auto[0], "CSO", "auto-rule: " + auto[1])
    return req


def verdict(id_, v, by, reason, ttl_min=60):
    if by != "CSO":
        raise SystemExit("REFUSED: only the CSO answers a GATE")
    if v not in ("PASS", "HOLD", "VETO"):
        raise SystemExit("REFUSED: verdict must be PASS, HOLD or VETO")
    req = _load(_p("gates", f"{id_}.request.json"))
    if req is None:
        raise SystemExit("REFUSED: no such request")
    old = _load(_p("gates", f"{id_}.verdict.json"))
    if old and old["verdict"] == "VETO":
        raise SystemExit("REFUSED: VETO is final; file a new request")
    if v == "PASS" and req.get("kind") == "money":
        raise SystemExit("REFUSED: a held money request cannot be released by a gate verdict; "
                         "release needs an Owner override with a scoped quote id and finance_guard PASS")
    out = dict(id=id_, verdict=v, by=by, reason=reason, ts=time.time(), expires=time.time() + ttl_min * 60, request_hash=_h(req))
    json.dump(out, open(_p("gates", f"{id_}.verdict.json"), "w"), indent=1)
    if v in ("HOLD", "VETO"):
        _escalate(_p("queue", "es_cso", f"{id_}.{v}.json"),
                  dict(line="ES-CSO", **{"from": "CSO"}, to="Owner", gate=id_, verdict=v, reason=reason, summary=req["summary"], ts=out["ts"]))
    return out


def _line_write(path, rec):
    """#36 C8 (CTO ruling option b): gate escalation files ride ES-CSO (CSO -> Owner), a one-way line that
    line_guard parses today, so they are written only through line_guard.guarded_write (refused = raises,
    fail closed; the HOLD/VETO verdict file is already written). Gate requests ride the two-way GT-* lines,
    which line_guard cannot parse yet; that parse is its own Open Item. Accident guard plus detection,
    not security."""
    import line_guard
    return line_guard.guarded_write(path, rec)


def fallback_dir():
    d = os.path.join(STATE, "alarms", "fallback"); os.makedirs(d, exist_ok=True); return d


def _escalate(path, rec):
    """#36 C8 retest: write an ES-CSO record through line_guard; if that write fails for ANY reason (missing
    brief, refused line, disk error), first drop a local fallback alarm in gb_state/alarms/fallback/ so Owner
    still gets something, then re-raise (fail loud). Returns the path written."""
    try:
        _line_write(path, rec)
        return path
    except BaseException as e:
        try:
            import line_guard   # #36 C8 spot-check 3: pid + uuid names, O_EXCL (no same-millisecond overwrite)
            line_guard.write_fallback(fallback_dir(), path, rec, e)
        except Exception:
            pass
        raise


def release_scopes(id_, req):
    """#36 C9: a money release quote must be scoped gate:<id> for THIS gate only.
    Ticket scopes, bare request-hash scopes and generic money:<kind>:<action> scopes never release anything.
    release.json must also carry this request's hash (checked in _money_release_ok), so one quote releases
    exactly one request."""
    return [f"gate:{id_}"]


def used_quotes_path():
    """#36 C9 retest-2: append-only used-quote ledger. It lives under logs/, not gates/, so deleting and refiling
    gate files under the same id does not free a quote."""
    return os.environ.get("GB_USED_QUOTES") or os.path.join(STATE, "logs", "used_quotes.jsonl")


def used_quotes_hwm_path():
    """#36 C9 spot-check 3: high-water record of the used-quote ledger, kept OUTSIDE it (audit/, not logs/)."""
    return os.environ.get("GB_USED_QUOTES_HWM") or os.path.join(STATE, "audit", "used_quotes.hwm.json")


def _ledger_lines(path):
    with open(path, "rb") as f:
        return f.read().split(b"\n")


def used_quotes_intact():
    """(ok, why). With a high-water record (lines N, sha256 of the first N lines), the ledger must exist, be a
    file, still start with those exact N lines, and every line must be a JSON object naming a quote_id.
    Missing, truncated, rewritten or garbage ledger -> False (every release refused, ES-CSO alarm). No record and
    no ledger = a fresh ledger (ok). A ledger without a record fails closed too."""
    path, hp = used_quotes_path(), used_quotes_hwm_path()
    hwm = None
    if os.path.lexists(hp):
        try:
            hwm = json.load(open(hp))
            assert isinstance(hwm, dict) and isinstance(hwm.get("lines"), int) and isinstance(hwm.get("sha256"), str)
        except Exception as e:
            return False, f"used-quote high-water record unreadable: {type(e).__name__}"
    if not os.path.lexists(path):
        if hwm is not None and hwm["lines"] != 0:
            return False, f"used-quote ledger missing (high-water {hwm['lines']} lines)"
        if hwm is None:
            return _rebuild_used_quotes(path)
        return True, "fresh ledger"
    if not os.path.isfile(path):
        return False, "used-quote ledger is not a regular file"
    try:
        raw = _ledger_lines(path)
    except OSError as e:
        return False, f"used-quote ledger unreadable: {type(e).__name__}"
    lines = raw[:-1] if raw and raw[-1] == b"" else raw
    for i, ln in enumerate(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            return False, f"used-quote ledger line {i + 1} is not JSON"
        if not (isinstance(r, dict) and r.get("quote_id")):
            return False, f"used-quote ledger line {i + 1} names no quote_id"
    if hwm is None:
        return (True, "fresh ledger") if not lines else (False, "used-quote ledger has lines but no high-water record")
    if len(lines) < hwm["lines"]:
        return False, f"used-quote ledger shrank: {len(lines)} lines < high-water {hwm['lines']}"
    if hashlib.sha256(b"\n".join(lines[:hwm["lines"]])).hexdigest() != hwm["sha256"]:
        return False, "used-quote ledger's first lines differ from the high-water record (rewritten)"
    return True, "intact"


def _rebuild_used_quotes(path):
    """#36 C9 spot-check 4: ledger AND high-water record both missing. Fresh only if no gates/*.release.json says
    allowed:true. Otherwise rebuild the used-quote set from those release files (quote_id, gate id, request hash),
    write the ledger and its record, and alarm ES-CSO; if any allowed release cannot be rebuilt -> refuse."""
    import glob as _g
    rels = []
    for f in sorted(_g.glob(_p("gates", "*.release.json"))):
        try:
            r = json.load(open(f))
        except Exception:
            r = None
        if isinstance(r, dict) and r.get("allowed") is False:
            continue
        if not (isinstance(r, dict) and r.get("allowed") is True and isinstance(r.get("quote_id"), str) and r["quote_id"]
                and isinstance(r.get("id"), str) and r.get("request_hash")):
            return False, f"used-quote ledger and record missing; {os.path.basename(f)} cannot be rebuilt, every release refused"
        rels.append(r)
    if not rels:
        return True, "fresh ledger"
    rels.sort(key=lambda r: (r.get("ts") or 0, r["id"]))
    seen, lines = set(), []
    for r in rels:
        if r["quote_id"] in seen:
            continue   # first allowed use wins; later ones were already refused at release time
        seen.add(r["quote_id"])
        lines.append(json.dumps(dict(quote_id=r["quote_id"], gate=r["id"], request_hash=r["request_hash"],
                                     ts=r.get("ts"), rebuilt=True)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    _write_hwm(path)
    _ledger_alarm(f"used-quote ledger and record were both missing; rebuilt {len(lines)} used quote(s) from release files")
    return True, f"rebuilt from {len(rels)} release file(s)"


def _write_hwm(path):
    lines = [l for l in _ledger_lines(path)]
    lines = lines[:-1] if lines and lines[-1] == b"" else lines
    hp = used_quotes_hwm_path(); os.makedirs(os.path.dirname(hp), exist_ok=True)
    tmp = hp + f".{os.getpid()}.tmp"
    json.dump(dict(lines=len(lines), sha256=hashlib.sha256(b"\n".join(lines)).hexdigest(), ledger=path, ts=time.time()), open(tmp, "w"))
    os.replace(tmp, hp)


def _ledger_alarm(why):
    """ES-CSO alarm (deduped per reason) through _escalate; never raises out of the release path."""
    key = hashlib.sha256(why.encode()).hexdigest()[:12]
    path = _p("queue", "es_cso", f"used-quotes-ledger.{key}.ALARM.json")
    if os.path.exists(path):
        return
    try:
        _escalate(path, dict(line="ES-CSO", **{"from": "CSO"}, to="Owner", kind="used-quote ledger broken",
                             verdict="ALARM", reason=why, ledger=used_quotes_path(), ts=time.time()))
    except BaseException:
        pass   # _escalate already dropped a fallback alarm


def quote_uses(qid):
    out = []
    try:
        for line in open(used_quotes_path()):
            try: r = json.loads(line)
            except ValueError: continue
            if isinstance(r, dict) and r.get("quote_id") == qid:
                out.append(r)
    except OSError:
        pass
    return out


def _use_quote(qid, id_, req_hash):
    """Append (quote, gate, request hash) under a lock; refuse if the quote was already used. Returns (ok, why)."""
    import fcntl
    path = used_quotes_path(); os.makedirs(os.path.dirname(path), exist_ok=True)
    ok, why = used_quotes_intact()
    if not ok:
        _ledger_alarm(why)
        return False, f"used-quote ledger not intact, every release refused: {why}"
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        ok, why = used_quotes_intact()
        if not ok:
            _ledger_alarm(why)
            return False, f"used-quote ledger not intact, every release refused: {why}"
        f.seek(0)
        for line in f:
            try: r = json.loads(line)
            except ValueError: continue
            if isinstance(r, dict) and r.get("quote_id") == qid:
                return False, f"quote {qid} already used for gate {r.get('gate')} (request {str(r.get('request_hash'))[:12]})"
        f.seek(0, 2)
        f.write(json.dumps(dict(quote_id=qid, gate=id_, request_hash=req_hash, ts=time.time())) + "\n")
        f.flush()
        _write_hwm(path)
    return True, "recorded"


def _money_release_ok(id_, req, ver):
    """#36 C3 fix 2: a money PASS counts only with gates/<id>.release.json (allowed true, same request,
    same quote id as the verdict) whose quote id is VERIFIED by owner_override.quote_status, the same
    check release() uses. Text in the verdict file never releases anything. Accident guard plus
    detection, not security (a file write can still forge release.json)."""
    rel = _load(_p("gates", f"{id_}.release.json"))
    if not isinstance(rel, dict) or rel.get("allowed") is not True or rel.get("id") != id_:
        return False, "money PASS without an allowed release.json (verdict-file text never releases)"
    if not rel.get("override_ok") or not (rel.get("finance_guard") or {}).get("allowed"):
        return False, "release.json lacks override_ok and finance_guard PASS"
    if not rel.get("request_hash"):
        return False, "release.json carries no request hash (one quote releases exactly one request)"
    if rel.get("request_hash") != _h(req):
        return False, "release.json does not match this request"
    if ver.get("quote_id") != rel.get("quote_id"):
        return False, "verdict quote id differs from release.json"
    import owner_override
    st, why = owner_override.quote_status(str(rel.get("quote_id") or ""),
                                          owner_override.load_quotes(os.environ.get("GB_C9_QUOTES") or None),
                                          scope=release_scopes(id_, req))
    if st != "VERIFIED":
        return False, f"release quote {st}: {why}"
    lok, lwhy = used_quotes_intact()
    if not lok:
        _ledger_alarm(lwhy)
        return False, f"used-quote ledger not intact: {lwhy}"
    uses = quote_uses(rel.get("quote_id"))
    if not uses or uses[0].get("gate") != id_ or uses[0].get("request_hash") != _h(req):
        return False, ("quote not in the used-quote ledger for this request (one quote releases exactly one request)"
                       if not uses else f"quote {rel.get('quote_id')} was first used by gate {uses[0].get('gate')} for another request")
    return True, "PASS (Owner release, verified quote)"


def require(id_):
    req, ver = _load(_p("gates", f"{id_}.request.json")), _load(_p("gates", f"{id_}.verdict.json"))
    if req is None: return False, "no request"
    if ver is None: return False, "no verdict yet (waiting on CSO)"
    if ver["verdict"] != "PASS": return False, f"verdict {ver['verdict']}: {ver['reason']}"
    if time.time() > ver["expires"]: return False, "PASS expired"
    if ver["request_hash"] != _h(req): return False, "request changed after verdict"
    if req.get("kind") == "money":
        return _money_release_ok(id_, req, ver)
    return True, "PASS"


def release(id_, override_path, quotes_path=None, settings=None, ledger_path=None, ttl_min=60):
    """Release a money HOLD only when an Owner override with a scoped quote id AND finance_guard PASS.
    An override never skips a guard check. Records both on the gate. Accident guard plus detection, not security."""
    req = _load(_p("gates", f"{id_}.request.json"))
    if req is None:
        raise SystemExit("REFUSED: no such request")
    if req.get("kind") != "money":
        raise SystemExit("REFUSED: release is only for kind=money HOLDs")
    ver = _load(_p("gates", f"{id_}.verdict.json"))
    if ver and ver.get("verdict") == "VETO":
        raise SystemExit("REFUSED: VETO is final; file a new request")
    if not ver or ver.get("verdict") != "HOLD":
        raise SystemExit("REFUSED: release applies only to a money HOLD")

    import owner_override
    ok, why, orec = owner_override.check_override(override_path, quotes_path=quotes_path, scope=release_scopes(id_, req))
    qid = (orec.get("quote_id") or orec.get("c9_quote_id") or orec.get("card_id") or orec.get("message_id")) if orec else None

    def _record(allowed, reason, guard=None):
        rec = dict(id=id_, ts=time.time(), allowed=allowed, reason=reason,
                   quote_id=qid, override_ok=ok, override_reason=why,
                   finance_guard=guard, request_hash=_h(req))
        json.dump(rec, open(_p("gates", f"{id_}.release.json"), "w"), indent=1)
        return rec

    if not ok:
        rec = _record(False, f"override failed: {why}")
        return False, rec["reason"], rec

    import finance_guard
    guard = finance_guard.check(_d(req), settings=settings, ledger_path=ledger_path)
    if not guard.get("allowed"):
        rec = _record(False, "finance_guard failed: " + ", ".join(guard.get("failed") or []), guard)
        return False, rec["reason"], rec

    used, uwhy = _use_quote(qid, id_, _h(req))   # one quote, one request, ever
    if not used:
        rec = _record(False, f"override refused: {uwhy}", guard)
        return False, rec["reason"], rec

    rec = _record(True, f"released: Owner override {qid} and finance_guard PASS", guard)
    out = dict(id=id_, verdict="PASS", by="Owner", reason=rec["reason"],
               ts=rec["ts"], expires=rec["ts"] + ttl_min * 60, request_hash=_h(req),
               release=True, quote_id=qid, finance_guard_allowed=True)
    json.dump(out, open(_p("gates", f"{id_}.verdict.json"), "w"), indent=1)
    finance_guard.record_spend(_d(req), id_, ledger_path=ledger_path)
    return True, rec["reason"], rec


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["request", "verdict", "require", "status", "access", "release"])
    p.add_argument("--id"); p.add_argument("--account"); p.add_argument("--mode"); p.add_argument("--role"); p.add_argument("--kind")
    p.add_argument("--summary", default=""); p.add_argument("--details", default="{}")
    p.add_argument("--verdict"); p.add_argument("--by"); p.add_argument("--reason", default=""); p.add_argument("--ttl-min", type=float, default=60); p.add_argument("--override")
    a = p.parse_args()
    if a.cmd != "access" and not a.id:
        p.error("--id is required")
    if a.cmd == "request":
        print(json.dumps(request(a.id, a.role, a.kind, a.summary, json.loads(a.details, parse_constant=_reject_constant))))
    elif a.cmd == "verdict":
        print(json.dumps(verdict(a.id, a.verdict, a.by, a.reason, a.ttl_min)))
    elif a.cmd == "access":
        ok, why = access(a.role, a.account, a.mode); print(why); sys.exit(0 if ok else 1)
    elif a.cmd == "require":
        ok, why = require(a.id); print(why); sys.exit(0 if ok else 1)
    elif a.cmd == "release":
        if not a.override:
            p.error("--override is required for release")
        ok, why, rec = release(a.id, a.override); print(json.dumps({"released": ok, "reason": why, "record": rec})); sys.exit(0 if ok else 1)
    else:
        print(json.dumps({"request": _load(_p("gates", f"{a.id}.request.json")), "verdict": _load(_p("gates", f"{a.id}.verdict.json"))}, indent=1))
