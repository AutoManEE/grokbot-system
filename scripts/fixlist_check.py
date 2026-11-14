#!/usr/bin/env python3
"""fixlist_check.py (P2-06, CAO): FIX and ADJUST lines. Zero AI.
  check -f FIX.json                  validate a fix list (fixed format, sender's own bots only)
  apply -f FIX.json --by CAO         CAO applies it to bots/<target>/ (prompt or model settings), bumps version,
                                     and logs the change in Section 5 format to the action log
Rules (CAO rejects anything else): fields from/target/issue/evidence/change/test all present; `from` is a C-role;
target is that C-role's own assistant (A..) or S-suite (S..); change.field in {prompt_append, prompt_replace
({old,new}, old exactly once), model, temperature, num_predict, create_helper}; create_helper makes a NEW helper dir only (never overwrites), with no tools and no
outside content, and the chain's A, S and reviewer models must all differ (gemma3:4b barred); tools and outside_content can never be changed; only the CAO applies (the COO never changes bots).
Template: <repo>/templates/fixlist_template.json
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, json, os, sys
sys.path.insert(0, GB_ROOT + "/scripts")
from croles import CROLES
BOTS = os.environ.get("GB_BOTS", GB_ROOT + "/bots")
LOG = os.environ.get("GB_ACTION_LOG", GB_ROOT + "/alignment/action_log.jsonl")
BARRED_MODELS = {"gemma3:4b"}  # barred for safety review
FIELDS = {"prompt_append", "prompt_replace", "model", "temperature", "num_predict", "create_helper"}
KEYS = ["from", "target", "issue", "evidence", "change", "test"]

import re as _re
COUNTERPART_S_RE = _re.compile(r"^S[A-Z]{2}-[A-Z]{3}$")
CSO_TEAM_IDS = frozenset({"ASO", "SSO", "CSO"})   # CSO standing rule: never a fix-list target, sender or subject


def own_targets(c):
    """A C-role's own helpers: its assistant A<XX>, its S-suite S<XX>, and (the Owner: 'each A
    also has an S per other agent out there') one per-counterpart S-suite S<XX>-<ROLE> for every OTHER known
    C-role <ROLE>. Nothing else."""
    if c == "CSO": return set()
    return {"A" + c[1:], "S" + c[1:]} | {f"S{c[1:]}-{r}" for r in CROLES if r != c and r not in CSO_TEAM_IDS}


def is_own_target(c, t):
    if not (isinstance(c, str) and isinstance(t, str)) or c not in CROLES:
        return False
    if any(tok in CSO_TEAM_IDS for tok in _re.split(r"[^A-Z]+", t.upper())) or c == "CSO":   # never ASO/SSO/S<XX>-CSO
        return False
    if t in ("A" + c[1:], "S" + c[1:]):
        return True
    if COUNTERPART_S_RE.fullmatch(t):   # S<XX>-<ROLE>: XX is the sender's, ROLE a known C-role other than the sender
        base, role = t.split("-")
        return base == "S" + c[1:] and role in CROLES and role != c and role not in CSO_TEAM_IDS and base not in CSO_TEAM_IDS
    return False


def base_id(t):
    """'SBO-CFO' -> 'SBO'; 'SBO' -> 'SBO'."""
    return str(t).split("-")[0]


def _strings(x):
    if isinstance(x, str): yield x
    elif isinstance(x, dict):
        for k, v in x.items(): yield str(k); yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x: yield from _strings(v)


def _text_names_cso_team(s):
    """True if s names ASO, SSO or CSO as a letter-run token (STO-CSO, aso), a dotted or spaced spelling
    (A.S.O, C S O, S.S.O), or a plural (CSOs, ASOs, SSOs). NFKC, case-insensitive; zero-width (Cf) dropped.
    CSO P1 re-screen condition (b)."""
    import unicodedata
    t = unicodedata.normalize("NFKC", s).upper()
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    if any(tok in CSO_TEAM_IDS or (tok.endswith("S") and tok[:-1] in CSO_TEAM_IDS)
           for tok in _re.split(r"[^A-Z]+", t) if tok):
        return True
    return any(_re.search(r"(?<![A-Z])%s([^A-Z0-9]{1,3})%s\1%s(?:\1?S)?\1?(?![A-Z])" % tuple(name), t) for name in CSO_TEAM_IDS)


def names_cso_team(fx):
    """CSO standing rule: no fix list that names CSO, SSO or ASO in any
    form: from, target, or any text in change (NFKC, case-insensitive; tokens, dotted/spaced names, plurals)."""
    return any(_text_names_cso_team(s) for s in _strings([fx.get("from"), fx.get("target"), fx.get("change")]))


def check(fx):
    errs = [f"missing {k}" for k in KEYS if k not in fx or fx[k] in ("", None)]
    if errs: return errs
    if names_cso_team(fx): errs.append("fix list names the CSO team (CSO/SSO/ASO): never applied")
    c, t = fx["from"], fx["target"]
    if c not in CROLES: errs.append("from is not a C-role")
    elif not is_own_target(c, t): errs.append(f"target {t} is not {c}'s own assistant, S-suite or per-counterpart S-suite (S{c[1:]}-<C-role>)")
    if c in CROLES:
        import line_guard   # C8: fix lists ride FX-<from> (from -> CAO) in the brief line table
        # Brief: "there is no FX-CAO"; CAO fixes AAO/SAO (and its SAO-<ROLE> S-suites) itself (AD-SAO / AD-AAO).
        if not (c == "CAO" and base_id(t) in ("AAO", "SAO")):
            errs.extend(line_guard.check_fixlist(fx))
    ch = fx["change"]
    if not isinstance(ch, dict) or set(ch) != {"field", "value"}: errs.append("change must be {field, value}")
    elif ch["field"] not in FIELDS: errs.append(f"field {ch['field']} not allowed (tools/outside_content are never changed)")
    elif ch["field"] == "temperature" and not (isinstance(ch["value"], (int, float)) and 0 <= ch["value"] <= 1): errs.append("temperature 0-1")
    elif ch["field"] == "num_predict" and not (isinstance(ch["value"], int) and 1 <= ch["value"] <= 4096): errs.append("num_predict 1-4096")
    elif ch["field"] in ("prompt_append", "model") and not (isinstance(ch["value"], str) and ch["value"].strip()): errs.append("value must be text")
    elif ch["field"] == "model" and ch["value"] in BARRED_MODELS: errs.append(f"model {ch['value']} is barred")
    elif ch["field"] == "create_helper": errs.extend(_check_create(fx))
    elif ch["field"] == "prompt_replace": errs.extend(_check_replace(fx))
    return errs

def _check_replace(fx):
    """prompt_replace: value {old, new}; old must appear exactly once in the target's prompt.md (a screened
    ADJUST of an earlier line, e.g. CTO's D2 STO ruling). Nothing else in the prompt changes."""
    v = fx["change"]["value"]
    if not (isinstance(v, dict) and set(v) == {"old", "new"} and all(isinstance(v[k], str) and v[k].strip() for k in v)):
        return ["prompt_replace value must be {old, new} (both text)"]
    try: cur = open(os.path.join(BOTS, fx["target"], "prompt.md")).read()
    except OSError: return [f"{fx['target']} has no prompt.md"]
    n = cur.count(v["old"])
    return [] if n == 1 else [f"prompt_replace old text found {n} times in {fx['target']}/prompt.md (must be exactly 1)"]

def _model_of(hid):
    try: return json.load(open(os.path.join(BOTS, hid, "settings.json"))).get("model")
    except (OSError, ValueError): return None

def _check_create(fx, pending=None):
    """create_helper: new helper dir only (never overwrites); no tools, no outside content;
    A, S and reviewer models all different; gemma3:4b barred."""
    v, t = fx["change"]["value"], fx["target"]
    if not isinstance(v, dict): return ["create_helper value must be an object"]
    errs = []
    if os.path.exists(os.path.join(BOTS, t)): errs.append(f"{t} already exists; create_helper never overwrites")
    kind = "assistant" if t[0] == "A" else "s-suite"
    if v.get("kind", kind) != kind: errs.append(f"kind must be {kind} for {t}")
    for k in ("model", "reviewer_model", "prompt"):
        if not (isinstance(v.get(k), str) and v[k].strip()): errs.append(f"create_helper needs {k}")
    if v.get("tools") not in (None, []) or v.get("outside_content") not in (None, False):
        errs.append("tools and outside_content can never be set")
    m, rv = v.get("model"), v.get("reviewer_model")
    if m in BARRED_MODELS or rv in BARRED_MODELS: errs.append("gemma3:4b is barred")
    other = ("S" if t[0] == "A" else "A") + base_id(t)[1:]   # SBO-CFO pairs with ABO
    om = (pending or {}).get(other) or _model_of(other)
    trio = [x for x in (m, om, rv) if x]
    if m and rv and m == rv: errs.append("helper model equals the reviewer model")
    if om and m == om: errs.append(f"{t} and {other} would share model {m}")
    if om and rv and om == rv: errs.append(f"{other} model equals the reviewer model")
    return errs

RULINGS = os.environ.get("GB_RULINGS", GB_ROOT + "/alignment/rulings")
COSIGN_TARGETS = ("AAO", "SAO")   # CIO: CAO's own helpers change only with a CTO cosign


def cto_ref_ok(ref):
    """(ok, why). A CTO cosign ref must be traceable: a ruling file under alignment/rulings/ that names CTO, or
    an action_log entry that names CTO and quotes the ref verbatim. A made-up id finds nothing and is refused."""
    ref = str(ref or "").strip()
    if len(ref) < 3:
        return False, "cosign ref missing"
    cand = ref if os.path.isabs(ref) else os.path.join(RULINGS, os.path.basename(ref))
    if os.path.realpath(cand).startswith(os.path.realpath(RULINGS) + os.sep) and os.path.isfile(cand):
        if "CTO" in open(cand, encoding="utf-8", errors="replace").read():
            return True, f"ruling file {os.path.basename(cand)}"
    try:
        for line in open(LOG, encoding="utf-8"):
            if ref in line and "CTO" in line:
                return True, "action_log entry naming CTO quotes the ref"
    except OSError:
        pass
    return False, f"cosign ref {ref!r} is not a CTO ruling file or a CTO message/ruling in the action log"


def _cosign_guard(fx, cosign, cosign_ref):
    if base_id(fx.get("target") or "") not in COSIGN_TARGETS:   # SAO-<ROLE> needs the cosign too
        return None
    if str(cosign or "").strip().upper() != "CTO":
        raise PermissionError(f"{fx.get('target')} fix lists need --cosign CTO --cosign-ref <CTO message or ruling id>")
    ok, why = cto_ref_ok(cosign_ref)
    if not ok:
        raise PermissionError(why)
    return dict(by="CTO", ref=str(cosign_ref).strip(), verified=why)


def apply(fx, by, cosign=None, cosign_ref=None):
    if by != "CAO": raise PermissionError("only the CAO changes a bot's settings (AD lines)")
    errs = check(fx)
    if errs: raise ValueError("; ".join(errs))
    co = _cosign_guard(fx, cosign, cosign_ref)
    f, v = fx["change"]["field"], fx["change"]["value"]
    if f == "create_helper":
        return _create(fx, v, co)
    d = os.path.join(BOTS, fx["target"]); sp = os.path.join(d, "settings.json"); s = json.load(open(sp))
    if f == "prompt_append":
        with open(os.path.join(d, "prompt.md"), "a") as fh: fh.write("\n" + v.strip() + "\n")
    elif f == "prompt_replace":
        pp = os.path.join(d, "prompt.md"); cur = open(pp).read()
        open(pp, "w").write(cur.replace(v["old"], v["new"], 1))
    else:
        s[f] = v
    s["version"] = s.get("version", 1) + 1; json.dump(s, open(sp, "w"), indent=1)
    import action_log as al
    return al.append(LOG, dict(item=f"AD-{fx['target']} v{s['version']}", bot="CAO", result="PASS",
        objective=f"Meet {fx['from']}'s fix list: {fx['issue']}", options=[dict(name=f"{f} change", deltas=str(v)[:200], pros="targets the miss", cons="needs re-test")],
        decision=f"applied {f} to {fx['target']}", reasoning=f"evidence: {fx['evidence']}; test: {fx['test']}"
        + (f"; CTO cosign ref {co['ref']}" if co else ""), confidence=70,
        conditions=[dict(name="fix list format + own-bot check", required=True, result="PASS")]
        + ([dict(name=f"CTO cosign {co['ref']}", required=True, result="PASS")] if co else []),
        **({"cosign": co} if co else {})))

def _create(fx, v, co=None):
    t, c = fx["target"], fx["from"]
    d = os.path.join(BOTS, t)
    os.makedirs(d)  # fails if it exists (never overwrites)
    kind = "assistant" if t[0] == "A" else "s-suite"
    s = dict(id=t, kind=kind, c_role=c, parent=(c if kind == "assistant" else "A" + c[1:]),
             model=v["model"], temperature=v.get("temperature", 0), num_predict=v.get("num_predict", 1024),
             tools=[], outside_content=False, version=1, s_bots=([f"{t}-1"] if kind == "s-suite" else None),
             reviewer_model=v["reviewer_model"])
    json.dump(s, open(os.path.join(d, "settings.json"), "w"), indent=1)
    open(os.path.join(d, "prompt.md"), "w").write(v["prompt"].strip() + "\n")
    if v.get("readme"): open(os.path.join(d, "README.md"), "w").write(v["readme"].strip() + "\n")
    import action_log as al
    return al.append(LOG, dict(item=f"AD-{t} v1 (create)", bot="CAO", result="PASS",
        objective=f"Create {t} for {c}'s fix list: {fx['issue']}",
        options=[dict(name="create_helper", deltas=f"model {v['model']}, reviewer {v['reviewer_model']}", pros="new helper via fix list, not hand-edited", cons="needs re-test")],
        decision=f"created {t}", reasoning=f"evidence: {fx['evidence']}; test: {fx['test']}", confidence=70,
        conditions=[dict(name="fix list format + own-bot check + three distinct models", required=True, result="PASS")],
        **({"cosign": co} if co else {})))

def check_all(items):
    """Validate a list; create_helper items see earlier items' models (A then S)."""
    pending, out = {}, []
    for fx in items:
        e = check(fx)
        if not e and fx.get("change", {}).get("field") == "create_helper":
            e = _check_create(fx, pending)
            pending[fx["target"]] = fx["change"]["value"].get("model")
        out.append(e)
    return out

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["check", "apply"]); p.add_argument("-f", "--file", required=True); p.add_argument("--by")
    p.add_argument("--cosign"); p.add_argument("--cosign-ref")
    a = p.parse_args(); raw = json.load(open(a.file))
    items = raw if isinstance(raw, list) else [raw]
    if a.cmd == "check":
        all_err = []
        for i, (fx, e) in enumerate(zip(items, check_all(items))):
            all_err.extend([f"item {i}: {x}" for x in e] if len(items) > 1 else e)
        print(json.dumps({"ok": not all_err, "errors": all_err, "items": len(items)}))
        sys.exit(0 if not all_err else 1)
    try:
        pre = [x for es in check_all(items) for x in es]
        if pre: raise ValueError("; ".join(pre))
        hashes = [apply(fx, a.by, a.cosign, a.cosign_ref) for fx in items]
        print(json.dumps(hashes if len(hashes) > 1 else hashes[0]))
    except (PermissionError, ValueError) as e:
        print(f"REJECTED: {e}", file=sys.stderr); sys.exit(2)
