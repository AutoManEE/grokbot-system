#!/usr/bin/env python3
"""flow.py (P2-02, CAO): DELEGATE lines as task folders, with the brief's order enforced. Zero AI itself.
Every task: C-role -> assistant -> S-suite -> assistant review/report -> C-role review (brief 3.4).
Folder $GB_STATE/flow/<task>/:
  01_task.json     new      --task T --c-role CXO --text "..."     (C-role hands the task out, DG-CXO)
  02_s_out/N.txt   s-run    --task T [--exec | --from FILE]         (S-suite does the work, DG-AXO)
  03_a_report.md   a-report --task T [--exec | --from FILE]         (assistant reviews S work -> Section 5 report)
  04_c_review.json c-review --task T --decision ACCEPT|ADJUST --note "..."   (C-role reviews; final say)
  05_adjust/N.txt  (written by c-review ADJUST; next s-run must address it)
  close    --task T   only after the latest c-review is ACCEPT
  abandon  --task T --note "reason" --by CXO|CAO   mark an OPEN task ABANDONED (evidence kept, never deleted)
  delegate --role CXO --task FILE [--id ID]   ONE command for the whole DG chain: S-suite work -> assistant
                   review as a Section 5 JSON report (Ollama structured output, report_check.py, 1 retry) ->
                   $GB_STATE/reports/dg/<CXO>/<id>.json. Then the C-role runs c-review (ACCEPT/ADJUST) and close.
                   Task file: {"text": "...", "source": "internal"|"cso_screened", "money_inputs": {...} (CFO)}.
                   A task file with "kind": "code" is refused (exit 2) before any model call (CTO 2A, Option D).
                   Non-CSO roles refuse raw outside text (only ASO/SSO read it). CFO money math is computed by
                   scripts/finance_facts.py calc(); AFO/SFO only quote those numbers (checked in code).
                   CSO: screening is the default (SSO screens, ASO reviews the verdict; only the verdict leaves the team).
                   CSO --mode work: internal non-private text goes to ASO/SSO as a normal work task; refuses outside text and money math (exit 2) instead of silently screening.
Out-of-order steps are refused (exit 2). --exec calls bots/run.py (local Ollama, no tools).
Exit codes: 0 ok; 2 REFUSED; 3 PTO (Ollama is down per ho_check AND cost_gate says RUN — paid
fallback; an action-log entry with kind=PTO is appended); 4 LOCAL_FAIL (Ollama is up: retry
local; or Ollama is down but cost_gate says WAIT). LOCAL_FAIL is also logged (kind=LOCAL_FAIL)
with the same fields as PTO. The paid-model fallback is never used as a speed-up when Ollama is up.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, glob, hashlib, json, os, re, sys, time
from types import MappingProxyType
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
sys.path.insert(0, GB_ROOT + "/bots")
from croles import CROLES as _CROLES_SRC
# Literal kept for alignment_check.load_croles (regex on this file). Must equal croles.CROLES.
CROLES = {"CBO", "CIO", "CFO", "CTO", "CMO", "CSO", "CAO", "CPO", "COO", "CRO", "CCO", "CXO", "CLO", "CGO", "CHO"}
if CROLES != set(_CROLES_SRC):
    raise RuntimeError("flow.CROLES drifted from croles.CROLES")
S5 = ["Objective", "Options", "Decision", "Reasoning", "Confidence"]

class Refused(Exception): pass

# FI-s-cap-handoff (CTO  2A.4): run as a script, this module is __main__; bots/run.py imports "flow" for
# token_cap / raise_if_cap_hit. Register this module under that name too, so run.py raises THIS Refused and the
# CLI exits 2 (REFUSED) instead of a second module's Refused escaping as a traceback (exit 1). run.py's realpath
# guard still applies: __file__ is this scripts/flow.py.
if __name__ == "__main__":
    sys.modules.setdefault("flow", sys.modules[__name__])

def _quarantine(c):
    """Refuse a new role whose onboarding is not PASS (accident guard, not identity)."""
    import alignment_check
    alignment_check.refuse_if_quarantined(c, raise_cls=Refused)

def tdir(t): return os.path.join(STATE, "flow", t)
def task(t):
    p = os.path.join(tdir(t), "01_task.json")
    if not os.path.exists(p): raise Refused("no such task")
    return json.load(open(p))
def n_files(t, sub): return sorted(glob.glob(os.path.join(tdir(t), sub, "*.txt")))
def review(t):
    p = os.path.join(tdir(t), "04_c_review.json"); return json.load(open(p)) if os.path.exists(p) else None

def new(t, c, text):
    _quarantine(c)
    if c not in CROLES: raise Refused("not a C-role")
    if os.path.exists(tdir(t)): raise Refused("task exists")
    os.makedirs(os.path.join(tdir(t), "02_s_out")); os.makedirs(os.path.join(tdir(t), "05_adjust"))
    json.dump(dict(task=t, c_role=c, assistant="A" + c[1:], s_suite="S" + c[1:], text=text, ts=time.time(), state="OPEN"),
              open(os.path.join(tdir(t), "01_task.json"), "w"), indent=1)

FORMAT_RE = re.compile(r"\b(options?|confidence|fields?|schema|json|report|section 5|deltas|pros|cons|objective|reasoning)\b", re.I)
MONEY_COND_RE = re.compile(r"\b(money|usd|net|gross|fees?|facts|pnl|p&l|notional|price|risk|reward)\b", re.I)

ORIG_TASK_RE = re.compile(
    r"\boriginal\b.*\b(tasks?|instructions?|requests?|prompts?)\b|"
    r"\b(tasks?|instructions?|requests?|prompts?)\b.*\boriginal\b", re.I)

def demote_original_task_conditions(rep, has_adjust):
    """When an ADJUST note exists (05_adjust files), a condition that checks compliance with the
    pre-ADJUST original task cannot be required: the ADJUST note wins. Money conditions are never
    demoted. With no ADJUST note, these conditions keep full force. Returns the demoted names."""
    if not has_adjust:
        return []
    demoted, req_fail_before = [], False
    for x in rep.get("conditions") or []:
        if x.get("required") and x.get("result") == "FAIL":
            req_fail_before = True
        name = re.sub(r"[_\-]+", " ", str(x.get("name", "")))
        if MONEY_COND_RE.search(name) or not ORIG_TASK_RE.search(name):
            continue
        if x.get("required"):
            x["required"] = False
            x["note"] = "ADJUST note overrides the original task: this condition is not required"
            demoted.append(x.get("name"))
    real_req_fail = any(x.get("required") and x.get("result") == "FAIL" for x in (rep.get("conditions") or []))
    if rep.get("result") == "FAIL" and req_fail_before and demoted and not real_req_fail:
        rep["result"] = "PASS"
    return demoted

def demote_format_conditions(rep, task_text):
    """A condition the assistant made up about its OWN report format (options count, fields, schema...), not drawn
    from the task text, can't be required and can't turn the report into FAIL; report_check checks format in code.
    Task, rule and money conditions keep full force. Returns the demoted names."""
    demoted, req_fail_before = [], False
    for x in rep.get("conditions") or []:
        name = re.sub(r"[_\-]+", " ", str(x.get("name", "")))
        if x.get("required") and x.get("result") == "FAIL": req_fail_before = True
        kw = [m.lower() for m in FORMAT_RE.findall(name)]
        if not kw or MONEY_COND_RE.search(name) or any(re.search(rf"\b{re.escape(k)}", task_text, re.I) for k in kw): continue
        if x.get("required"):
            x["required"] = False; x["note"] = "made-up format condition, not from task: not required (code checks format)"; demoted.append(x.get("name"))
    real_req_fail = any(x.get("required") and x.get("result") == "FAIL" for x in rep.get("conditions") or [])
    if rep.get("result") == "FAIL" and req_fail_before and demoted and not real_req_fail: rep["result"] = "PASS"
    return demoted

def adjust_first(c, text, note):
    """Newest ADJUST note goes FIRST (small models weight the start); original text follows as context."""
    if not note: return text
    return (f"ADJUST note from {c} (newest; it overrides the original task wherever they conflict; follow it exactly):\n{note}"
            f"\n\nOriginal task (context only):\n{text}")

FALLBACK_REVIEW_MODEL = "qwen3:4b-instruct"

def _log_load_failure(t, model, error, fallback):
    """Write the exact reviewer load-failure error into the task's flow log. Never unloads a model."""
    rec = dict(event="load_failure", task=t, model=model, error=str(error), fallback=fallback,
               ts=time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    os.makedirs(tdir(t), exist_ok=True)
    open(os.path.join(tdir(t), "03_a_load_failure.json"), "w").write(json.dumps(rec, indent=1) + "\n")
    return rec

def _run_assistant_review(a_id, prompt, t, fmt=None, num_predict=None):
    """Run the assistant reviewer. If its configured model fails to load (OOM, not found, load error),
    log the exact error and fall back to qwen3:4b-instruct. Never unloads or stops another model.
    Returns (raw_text, meta) where meta has reviewer_model, same_model_review, load_error."""
    import run
    configured = run.load(a_id)[1]["model"]
    try:
        raw = run.run(a_id, prompt, fmt=fmt, num_predict=num_predict)
        return raw, dict(reviewer_model=configured, same_model_review=False, load_error=None)
    except run.ModelLoadError as e:
        _log_load_failure(t, e.model, e.detail, FALLBACK_REVIEW_MODEL)
        try:
            raw = run.run(a_id, prompt, fmt=fmt, model=FALLBACK_REVIEW_MODEL, num_predict=num_predict)
        except run.ModelLoadError as e2:
            raise OSError(f"PTO: reviewer fallback {FALLBACK_REVIEW_MODEL} also failed to load: {e2}") from e2
        return raw, dict(reviewer_model=FALLBACK_REVIEW_MODEL, same_model_review=True, load_error=str(e))

INDEPENDENT_MODEL = "phi4-mini"   # default reviewer; per chain see chain_reviewer()
ALT_REVIEWER_MODEL = "llama3.2:3b"   # CSO 3c ruling: reviewer for chains whose A or S model is phi4-mini
BOTS = os.environ.get("GB_BOTS", GB_ROOT + "/bots")
BARRED_REVIEWERS = {"gemma3:4b"}     # barred for safety review

def _settings(hid):
    root = os.environ.get("GB_BOTS") or BOTS
    try: return json.load(open(os.path.join(root, hid, "settings.json")))
    except (OSError, ValueError): return {}

ROLE_ID_RE = re.compile(r"^(S[A-Z]{2}(-[A-Z]{3})?|A[A-Z]{2}|reviewer)$")  # ASCII only; always fullmatch


def role_cap(hid):
    """CTO ruling: every S = 160,
    every reviewer = 160, every A = 384. Role from bot id: S[A-Z]{2} (incl. S<XX>-<C>) = 160,
    A[A-Z]{2} = 384, reviewer = 160. The id must fullmatch ROLE_ID_RE (ASCII); anything else
    (lowercase, Reviewer, C-roles, '', None, path-like, non-ASCII) is Refused, with no model call.
    Per-bot settings may only go lower (effective cap = min(settings, role cap))."""
    if not isinstance(hid, str) or not ROLE_ID_RE.fullmatch(hid):
        raise Refused(f"{hid!r} is not a valid bot id for a role cap; no cap, no call")
    if hid == "reviewer" or hid.startswith("S"):
        return 160
    return 384

# Read-only derived view of the role rule for the three ids the CSO round-3 screen pinned.
# Values are computed from role_cap(); token_cap still uses role_cap for every bot.
CTO_CAPS = MappingProxyType({hid: role_cap(hid) for hid in ("SBO", "ABO", "AAO")})


def token_cap(hid, override=None):
    """Resolved num_predict. CTO option (b): when an override is passed, the cap is
    min(role_cap, override). With no override, settings can only lower the cap (min(role_cap, settings)).
    An override can never go higher than the role cap (S 160, A 384, reviewer 160).
    Fails closed: not a positive int (missing, null, string, bool, NaN, <= 0) is Refused, never 'no cap'."""
    rc = role_cap(hid)  # fail closed on an invalid id; never skip the role cap
    v = override if override is not None else _settings(hid).get("num_predict")
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise Refused(f"{hid}: num_predict {v!r} is not a positive integer; no cap, no call")
    return min(v, rc)

def cap_hits_path(state=None):
    return os.path.join(state or os.environ.get("GB_STATE", GB_ROOT + "/gb_state"), "budget", "cap_hits.jsonl")

def log_cap_hit(bot, phase, eval_count, cap, state=None):
    """Append-only cap-hit record (ts, bot, phase, eval_count, cap). Opens cap_hits.jsonl with
    O_APPEND|O_CREAT|O_NOFOLLOW via dir_fd on gb_state/budget. Refuses if budget/ is a symlink.
    A failed log write raises Refused for this call only (never FileExistsError)."""
    import stat
    from datetime import datetime
    from zoneinfo import ZoneInfo
    rec = dict(ts=datetime.now(ZoneInfo(os.environ.get("GB_TZ", "UTC"))).strftime("%Y-%m-%d %H:%M:%S %Z"),
               bot=bot, phase=phase, eval_count=eval_count, cap=cap)
    path = cap_hits_path(state)
    budget = os.path.dirname(path)
    name = os.path.basename(path)
    try:
        if os.path.lexists(budget):
            st = os.lstat(budget)
            if stat.S_ISLNK(st.st_mode):
                raise Refused("budget/ is a symlink; cap hit not logged, call refused")
            if not stat.S_ISDIR(st.st_mode):
                raise Refused("budget/ is not a directory; cap hit not logged, call refused")
        else:
            os.makedirs(budget, exist_ok=True)
            st = os.lstat(budget)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise Refused("budget/ is a symlink; cap hit not logged, call refused")
        flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        flags_file = os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW
        dfd = os.open(budget, flags_dir)
        try:
            fd = os.open(name, flags_file, 0o644, dir_fd=dfd)
            try:
                os.write(fd, (json.dumps(rec, sort_keys=True) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            os.close(dfd)
    except Refused:
        raise
    except OSError as e:
        raise Refused(f"cap_hits.jsonl write failed ({type(e).__name__}); call refused") from e
    return rec

def raise_if_cap_hit(hid, cap=None, eval_count=None, phase=None):
    """CTO: a reply that hits the token cap is a hard fail, not a retry.
    When eval_count is not passed, use run.LAST_CALL only if it belongs to this bot (or has no bot key).
    Every cap hit is appended to gb_state/budget/cap_hits.jsonl."""
    import run
    cap = token_cap(hid, cap)
    if eval_count is None:
        lc = run.LAST_CALL or {}
        if lc.get("bot") not in (None, hid):
            return
        ev = lc.get("eval_count")
    else:
        ev = eval_count
    if cap and ev is not None and ev >= cap:
        log_cap_hit(hid, phase or hid, ev, cap)
        raise Refused(f"reply hit the token cap ({cap}); hard fail, not a retry")

def model_family(model):
    m = (model or "").lower().split(":")[0]
    return re.sub(r"[\d.]+$", "", m).rstrip("-_") or (model or "")

def chain_reviewer(a_id, s_id=None):
    """Per-chain INDEPENDENT_MODEL (CSO 3c): S settings reviewer_model, else A's; else the default
    phi4-mini unless the chain's A or S already runs phi4-mini, then llama3.2:3b. A, S and the reviewer
    must all differ; alignment_check enforces that. Returns (reviewer, same_model: bool)."""
    s_id = s_id or ("S" + a_id[1:])
    a, s = _settings(a_id), _settings(s_id)
    rv = s.get("reviewer_model") or a.get("reviewer_model")
    if not rv:
        rv = ALT_REVIEWER_MODEL if INDEPENDENT_MODEL in (a.get("model"), s.get("model")) else INDEPENDENT_MODEL
    fams = {model_family(m) for m in (a.get("model"), s.get("model")) if m}
    return rv, model_family(rv) in fams
INDEPENDENT_PROMPT = """You are an independent checker reviewing an assistant's Section 5 report of S-suite work.

Task:
{task}

S-suite output:
{s_out}

Assistant Section 5 report (JSON):
{report}

Decide whether the work and the report should PASS or FAIL.
Rules:
- Arithmetic must be correct (for example 3x4x7 is 84, not 74).
- Required conditions named in the task must be present and scored correctly.
- If any required condition is FAIL, the report result must be FAIL.
- The decision and S-suite output must follow the latest task instruction (an ADJUST note overrides the original).
- When a FACTS block is given, money figures must quote those FACTS (net P&L is net_usd, not gross_usd).
- Do not fail a substantively correct OK/number/FACTS report only for extra formatting commentary.

Reply with exactly two lines and no other text:
VERDICT: PASS
REASON: <one short sentence>
or
VERDICT: FAIL
REASON: <one short sentence>
"""
VERDICT_RE = re.compile(r"\bVERDICT\s*[:\-]\s*(PASS|FAIL)\b", re.I)

def parse_independent_verdict(text):
    """Parse VERDICT: PASS/FAIL from the independent checker. Returns (PASS|FAIL|None, reason)."""
    if not text or not str(text).strip():
        return None, "empty"
    m = VERDICT_RE.search(text)
    if not m:
        return None, (str(text).strip()[:240] or "unparsed")
    reason = ""
    r = re.search(r"\bREASON\s*[:\-]\s*(.+)", text, re.I)
    if r:
        reason = r.group(1).strip()
    return m.group(1).upper(), reason

def apply_independent_review(rep, t, task_text, s_out, a_id=None):
    """After the a-report: run phi4-mini as an independent checker. Adds required condition
    independent_review (FAIL if verdict is FAIL or cannot be parsed). On load failure: condition
    is N/A and not required, report is labeled same-model review, exact error is logged, and no
    other model is unloaded. Returns the condition dict."""
    import run
    a_id = a_id or str(rep.get("bot") or "")
    reviewer, same = chain_reviewer(a_id) if a_id[:1] == "A" else (INDEPENDENT_MODEL, False)
    if same:  # reviewer re-checks its own model family: label it, never hide it
        rep["same_model_review"] = True
    code_fail = rep.get("result") == "FAIL"  # code checks already ran; the reviewer is a backstop only
    prompt = INDEPENDENT_PROMPT.format(task=task_text or "", s_out=s_out or "",
                                       report=json.dumps({k: rep.get(k) for k in
                                           ("item", "bot", "result", "objective", "options",
                                            "decision", "reasoning", "confidence", "conditions")}, indent=1))
    conds = [c for c in (rep.get("conditions") or []) if c.get("name") != "independent_review"]
    try:
        if reviewer in BARRED_REVIEWERS:
            raise run.ModelLoadError(reviewer, "barred for safety review")
        raw = run.complete(reviewer, prompt)
        open(os.path.join(tdir(t), "03_independent_raw.txt"), "w").write(raw or "")
        verdict, reason = parse_independent_verdict(raw)
        if verdict == "PASS":
            cond = dict(name="independent_review", required=True, result="PASS",
                        note=reason or "VERDICT: PASS")
        else:
            # FAIL or unparsed
            why = reason if verdict == "FAIL" else f"unparsed independent verdict: {(raw or '')[:200]}"
            cond = dict(name="independent_review", required=True, result="FAIL", note=why)
            rep["result"] = "FAIL"
        rep["independent_reviewer_model"] = reviewer
        rep["independent_review_verdict"] = verdict or "unparsed"
    except run.ModelLoadError as e:
        _log_load_failure(t, e.model, e.detail, "none (independent review skipped)")
        cond = dict(name="independent_review", required=False, result="N/A",
                    note=f"load failure: {e.detail}")
        rep["same_model_review"] = True
        rep["independent_reviewer_model"] = reviewer
        rep["independent_review_verdict"] = "N/A"
    conds.append(cond)
    rep["conditions"] = conds
    if code_fail:  # a reviewer PASS can never lift a code FAIL (report_check, content_recheck, arith, money)
        rep["result"] = "FAIL"
    return cond


LOCAL_FAIL_EXIT = 4  # local failure, retry local (Ollama is up, or cost_gate WAIT). Not a PTO.

class LocalFail(Exception):
    """Ollama is up (retry this locally) or cost_gate said WAIT. CLI exits 4."""

class PtoNeeded(OSError):
    """Ollama is down and cost_gate said RUN. CLI exits 3."""

PTO_ERROR_MAX = 500

def classify_pto_reason(exc):
    """Map an exception to reason in {timeout, model_down, oom, other}."""
    chunks, cur = [], exc
    for _ in range(5):
        if cur is None:
            break
        chunks.append(f"{type(cur).__name__}: {cur}")
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    text = " ".join(chunks).lower()
    if any(s in text for s in ("out of memor", "system memory", "requires more", " oom", "oom:", "oom,")):
        return "oom"
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    if any(s in text for s in ("unavailable", "connection refused", "errno 111", "pto:", "ollama is down", "ollama down")):
        return "model_down"
    return "other"

def record_event(kind, role, task, assistant, reason, exc, elapsed_s):
    """Append a Section 5 action-log entry with the exact fields local_share.py reads:
    kind, role, task, assistant, reason, error, elapsed_s, loadavg."""
    import action_log as al
    err = str(exc)
    if len(err) > PTO_ERROR_MAX:
        err = err[:PTO_ERROR_MAX]
    loadavg = list(os.getloadavg())
    entry = dict(
        item=f"{kind} {role} {task}", bot=role or "CAO", result="INFO",
        objective=f"Record a {kind} exit for {role} task {task}",
        options=[dict(name="append action-log entry", deltas=str(kind),
                      pros="machine-readable for local_share", cons="none"),
                 dict(name="stderr only", deltas="no log",
                      pros="old behavior", cons="paid-fallback counts stay unknown")],
        decision=f"log kind={kind} reason={reason}",
        reasoning=f"{reason}: {err}", confidence=90,
        conditions=[dict(name="event logged", required=True, result="PASS")],
        kind=kind, role=role, task="" if task is None else str(task),
        assistant=assistant or "", reason=reason, error=err,
        elapsed_s=elapsed_s, loadavg=loadavg,
    )
    log = os.environ.get("GB_ACTION_LOG", GB_ROOT + "/alignment/action_log.jsonl")
    return al.append(log, entry)

def handle_ollama_error(exc, role, task, assistant, t0):
    """On timeout or error: ask ho_check. If Ollama is up, log LOCAL_FAIL and raise LocalFail (exit 4).
    If Ollama is down and cost_gate says RUN, log kind PTO reason model_down and raise PtoNeeded (exit 3).
    If cost_gate says WAIT, log LOCAL_FAIL with the gate reason and raise LocalFail (exit 4)."""
    import ho_check, cost_gate
    elapsed = round(time.time() - t0, 3)
    st = ho_check.check(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    if st.get("ollama") == "UP":
        reason = classify_pto_reason(exc)
        if reason == "model_down":
            reason = "other"
        record_event("LOCAL_FAIL", role, task, assistant, reason, exc, elapsed)
        raise LocalFail(f"LOCAL_FAIL: {reason}: {exc}") from exc
    lim = json.load(open(cost_gate.limits_path()))
    est = lim.get("max_paid_turns_per_task")
    if not isinstance(est, int):
        est = 0
    decision, why = cost_gate.check(est, 0, lim)
    if decision == "RUN":
        record_event("PTO", role, task, assistant, "model_down", exc, elapsed)
        raise PtoNeeded(f"PTO: Ollama down; {exc}") from exc
    record_event("LOCAL_FAIL", role, task, assistant, why, exc, elapsed)
    raise LocalFail(f"LOCAL_FAIL: cost_gate WAIT ({why}): {exc}") from exc

def _open_only(tk):
    if tk.get("state") != "OPEN": raise Refused(f"task {str(tk.get('state')).lower()}")

# FI-s-cap-handoff (CTO  2A.2, Option A): bounded output contract on every S model call. The S cap stays 160
# tokens; the contract keeps answers well under it (about 80 words / 100 tokens).
S_OUTPUT_CONTRACT = ("Output contract: answer in at most 80 words (about 100 tokens). No preamble, do not restate "
                     "or echo the task, no headings. Stop right after the answer.")

def s_prompt(c, text, note=None):
    """The S-suite prompt: ADJUST note first (adjust_first), then the bounded output contract."""
    return adjust_first(c, text, note) + "\n\n" + S_OUTPUT_CONTRACT

def s_run(t, out_text=None, exe=False, num_predict=None):
    tk = task(t)
    _open_only(tk)
    outs, adj, rv = n_files(t, "02_s_out"), n_files(t, "05_adjust"), review(t)
    if outs and not (rv and rv["decision"] == "ADJUST" and len(adj) == len(outs)):
        raise Refused("S work already submitted; waiting for assistant report / C-role review")
    if exe:
        import run
        prompt = s_prompt(tk["c_role"], tk["text"], open(adj[-1]).read() if adj else None)
        out_text = run.run(tk["s_suite"], prompt, num_predict=num_predict)
    open(os.path.join(tdir(t), "02_s_out", f"{len(outs) + 1:02d}.txt"), "w").write(out_text)

def a_report(t, rep=None, exe=False):
    tk = task(t); _open_only(tk); outs = n_files(t, "02_s_out")
    if not outs: raise Refused("no S-suite output to review")
    rp = os.path.join(tdir(t), "03_a_report.md")
    if os.path.exists(rp) and os.path.getmtime(rp) >= os.path.getmtime(outs[-1]): raise Refused("report already covers the latest S work")
    if exe:
        prompt = f"Task from {tk['c_role']}:\n{tk['text']}\n\nS-suite output to review:\n{open(outs[-1]).read()}"
        rep, meta = _run_assistant_review(tk["assistant"], prompt, t)
        if meta["same_model_review"] and "same-model review" not in (rep or "").lower():
            rep = (rep or "") + "\nSame-model review: yes"
    missing = [k for k in S5 if not re.search(rf"\b{k}\b", rep or "", re.I)]
    if missing: raise Refused(f"report not in Section 5 format; missing {missing}")
    open(rp, "w").write(rep)

MAX_BATCH_ADJUST = 3   # FX-CTO: 3 review rounds per batch
LESSONS_CAP = 2000     # chars of the latest lessons put in the A prompt
BOTS = os.environ.get("GB_BOTS", GB_ROOT + "/bots")

def _write_a_report_json(t, rep):
    """CSO go-live screen (G): every 03_a_report.json is written here and its sha256 logged to route_log (kind a_report),
    so takeover can tell a report the flow wrote from a hand-written or edited one."""
    p = os.path.join(tdir(t), "03_a_report.json")
    json.dump(rep, open(p, "w"), indent=1)
    route_log("a_report", task=t, sha256=_file_sha(p), result=rep.get("result"), bot=rep.get("bot"))

def _file_sha(p):
    with open(p, "rb") as fh: return hashlib.sha256(fh.read()).hexdigest()

def logged_report_sha(t):
    """Latest logged 03_a_report.json sha256 for task t (chained route_log records of kind a_report only), or None."""
    p = os.path.join(STATE, "flow", "route_log.jsonl"); last = None
    if not os.path.exists(p): return None
    for l in open(p):
        try: r = json.loads(l)
        except ValueError: continue
        if isinstance(r, dict) and r.get("kind") == "a_report" and r.get("task") == t and r.get("hash"): last = r.get("sha256")
    return last

def logged_adjust_rounds(b):
    """Distinct ADJUST rounds for batch b from CHAINED route_log records only: 'review' records with decision ADJUST,
    plus rounds 1..adjust_rounds of a chained 'review_backfill' (a logged ruling). The batch file is not trusted."""
    p = os.path.join(STATE, "flow", "route_log.jsonl"); rounds = set()
    if not b or not os.path.exists(p): return 0
    for l in open(p):
        try: r = json.loads(l)
        except ValueError: continue
        if not (isinstance(r, dict) and r.get("hash") and r.get("batch") == b): continue
        if r.get("kind") == "review" and r.get("decision") == "ADJUST" and isinstance(r.get("round"), int): rounds.add(r["round"])
        elif r.get("kind") == "review_backfill" and isinstance(r.get("adjust_rounds"), int): rounds |= set(range(1, r["adjust_rounds"] + 1))
    return len(rounds)

def route_log(kind, **kw):
    """(d) jsonl CTO can score: a2a from/to, review rounds per batch, takeovers."""
    import route_chain   # CSO re-screen 2: route_log is hash-chained (prev/hash per record)
    rec = dict(kind=kind, ts=time.time(), at=time.strftime("%Y-%m-%d %H:%M:%S %Z"), **kw)
    return route_chain.append(os.path.join(STATE, "flow", "route_log.jsonl"), rec)

CALL_LOG_KEYS = ("bot", "model", "keep_alive", "load_s", "compute_s", "evictions", "evicted", "cold", "gate_compute", "gate_load",
                 "eval_count", "eval_tps")

def log_model_call(meta, **ctx):
    """CTO relay cost check: every chain call logs load_s, compute_s and evictions to route_log,
    with the gates (compute <= 62.6 s, warm load <= 30 s; cold first load exempt)."""
    st = os.environ.get("GB_STATE")
    rec = dict(kind="model_call", ts=time.time(), at=time.strftime("%Y-%m-%d %H:%M:%S %Z"), **ctx,
               **{k: meta.get(k) for k in CALL_LOG_KEYS})
    import route_chain
    return route_chain.append(os.path.join(st or STATE, "flow", "route_log.jsonl"), rec)

def log_cap_override(caller, value, bot=None):
    """CTO C2: every explicit num_predict override is logged to route_log with caller and value."""
    st = os.environ.get("GB_STATE")
    rec = dict(kind="cap_override", ts=time.time(), at=time.strftime("%Y-%m-%d %H:%M:%S %Z"),
               caller=caller, value=value, bot=bot)
    import route_chain
    return route_chain.append(os.path.join(st or STATE, "flow", "route_log.jsonl"), rec)

try:   # run.py hands every model call's metrics to flow (bots/ is on sys.path above)
    import run as _run_mod
    _run_mod.ON_CALL = log_model_call
except Exception:
    pass

def _batch_path(b): return os.path.join(STATE, "flow", "batches", f"{b}.json")
def batch(b):
    p = _batch_path(b)
    return json.load(open(p)) if os.path.exists(p) else dict(batch=b, tasks=[], rounds=0, adjusts=0, reviews=[])

def _save_batch(bs):
    os.makedirs(os.path.dirname(_batch_path(bs["batch"])), exist_ok=True); json.dump(bs, open(_batch_path(bs["batch"]), "w"), indent=1)

def register_batch_task(t, b):
    """CTO ruling A: a round is one completed review pass over the batch, counted by batch id whether the
    task ids are new or repeated. A retest carries the batch id (delegate --batch): while the batch has tasks waiting
    for review in an open pass, a new task joins that pass; once every task in the pass is reviewed, the next
    delegated task opens the next round. The same task id re-delegated after its review always goes to a later round."""
    if not re.fullmatch(r"[\w.-]+", b or ""): raise Refused("bad --batch id")
    tk = task(t)
    if tk.get("batch") and tk["batch"] != b: raise Refused(f"task belongs to batch {tk['batch']}")
    bs = batch(b); pend = bs.setdefault("pending", {}); pend.pop(t, None)
    own = [x["round"] for x in bs["reviews"] if x["task"] == t]
    rnd = max(pend.values()) if pend else bs["rounds"] + 1
    if own: rnd = max(rnd, max(own) + 1)
    pend[t] = rnd
    if t not in bs["tasks"]: bs["tasks"].append(t)
    tk["batch"] = b; json.dump(tk, open(os.path.join(tdir(t), "01_task.json"), "w"), indent=1)
    _save_batch(bs)
    route_log("batch_register", batch=b, task=t, c_role=tk.get("c_role"), round=rnd)
    return rnd

def backfill_rounds(b, tasks_in_order, ref):
    """CTO ruling A one-time backfill: set an existing batch's review rounds to 1..N, one per task in order (each
    was a completed review pass under a new task id). Refused if already backfilled, if a task has no review in
    the batch, or without a ruling reference. Written to route_log with backfill=true."""
    bs = batch(b)
    if bs.get("backfill"): raise Refused(f"batch {b} was already backfilled")
    if not (ref or "").strip(): raise Refused("backfill needs --note citing the rulings")
    if len(set(tasks_in_order)) != len(tasks_in_order): raise Refused("each task once")
    for t in tasks_in_order:
        if not any(x["task"] == t for x in bs["reviews"]): raise Refused(f"task {t} has no review in batch {b}")
    before = dict(rounds=bs["rounds"], adjusts=bs["adjusts"], review_rounds=[x["round"] for x in bs["reviews"]])
    order = {t: i for i, t in enumerate(tasks_in_order, 1)}
    for x in bs["reviews"]:
        if x["task"] in order: x["round"] = order[x["task"]]
    bs["rounds"] = max(x["round"] for x in bs["reviews"])
    bs["adjusts"] = len({x["round"] for x in bs["reviews"] if x["decision"] == "ADJUST"})
    bs["backfill"] = dict(ref=ref.strip(), before=before, ts=time.time())
    _save_batch(bs)
    route_log("review_backfill", backfill=True, batch=b, tasks=tasks_in_order, rounds=bs["rounds"], adjust_rounds=bs["adjusts"],
              before=before, ref=ref.strip())
    return bs

def c_review(t, decision, note, batch_id=None):
    tk = task(t); _open_only(tk); rp = os.path.join(tdir(t), "03_a_report.md"); outs = n_files(t, "02_s_out")
    if not os.path.exists(rp) or os.path.getmtime(rp) < os.path.getmtime(outs[-1]): raise Refused("no current assistant report to review")
    rv = review(t)
    if rv and rv["ts"] >= os.path.getmtime(rp): raise Refused("already reviewed this report")
    if decision not in ("ACCEPT", "ADJUST"): raise Refused("decision must be ACCEPT or ADJUST")
    b = batch_id or tk.get("batch") or f"BATCH-{t}"   # CTO ruling A: a task with no batch id starts a new batch
    if b:
        if not re.fullmatch(r"[\w.-]+", b): raise Refused("bad --batch id")
        if tk.get("batch") and tk["batch"] != b: raise Refused(f"task belongs to batch {tk['batch']}")
        bs = batch(b)
        # A batch round is one review pass over the batch's tasks: a task's Nth review in the batch is round N.
        own = sum(1 for x in bs["reviews"] if x["task"] == t)
        pend = bs.setdefault("pending", {})
        if t in pend: rnd = pend.pop(t)   # CTO ruling A: delegated with the batch id -> the pass it was registered in
        else: rnd = 1 + own if own else max(1, bs["rounds"])   # legacy (no delegate --batch): a new task joins the current round
        adj_rounds = sorted({x["round"] for x in bs["reviews"] if x["decision"] == "ADJUST"})
        if decision == "ADJUST" and rnd not in adj_rounds and len(adj_rounds) >= MAX_BATCH_ADJUST:
            raise Refused(f"batch {b} already used {MAX_BATCH_ADJUST} ADJUST rounds; use close --takeover")
        if not tk.get("batch"):
            tk["batch"] = b; json.dump(tk, open(os.path.join(tdir(t), "01_task.json"), "w"), indent=1)
        if t not in bs["tasks"]: bs["tasks"].append(t)
        bs["reviews"].append(dict(task=t, decision=decision, round=rnd, ts=time.time()))
        bs["rounds"] = max(x["round"] for x in bs["reviews"])
        bs["adjusts"] = len({x["round"] for x in bs["reviews"] if x["decision"] == "ADJUST"})   # ADJUST rounds
        os.makedirs(os.path.dirname(_batch_path(b)), exist_ok=True); json.dump(bs, open(_batch_path(b), "w"), indent=1)
        route_log("review", batch=b, task=t, c_role=tk["c_role"], assistant=tk.get("assistant"), decision=decision,
                  round=rnd, batch_rounds=bs["rounds"], adjust_rounds=bs["adjusts"])
    json.dump(dict(decision=decision, note=note, by=tk["c_role"], ts=time.time(), batch=b), open(os.path.join(tdir(t), "04_c_review.json"), "w"))
    if decision == "ADJUST":
        open(os.path.join(tdir(t), "05_adjust", f"{len(n_files(t, '05_adjust')) + 1:02d}.txt"), "w").write(note)

# ---------------- delegate: one command per C-role (DG lines end to end) ----------------
S5_SCHEMA = {"type": "object", "required": ["objective", "options", "decision", "reasoning", "confidence", "conditions", "result"],
  "properties": {"objective": {"type": "string"},
    "options": {"type": "array", "minItems": 2, "items": {"type": "object", "required": ["name", "deltas", "pros", "cons"],
                "properties": {k: {"type": "string"} for k in ("name", "deltas", "pros", "cons")}}},
    "decision": {"type": "string"}, "reasoning": {"type": "string"}, "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
    "conditions": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["name", "required", "result"],
                   "properties": {"name": {"type": "string"}, "required": {"type": "boolean"}, "result": {"type": "string", "enum": ["PASS", "FAIL", "N/A"]}}}},
    "result": {"type": "string", "enum": ["PASS", "FAIL"]},
    "note": {"type": "string"}}}   # optional plain-string note for the C-role (CSO round-3 ruling), e.g. a rule difference
MONEY_RE = re.compile(r"(\$\s?\d|\bp&l\b|\bpnl\b|\bfees?\b|\bprofit\b|\bnotional\b|\bmargin\b|\bnet\b[^.]*\d|\busd\b|\bprice\b[^.]*\d)", re.I)
NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*\.\d+|\$\s?-?\d[\d,]*(?:\.\d+)?")

def _nums(text):
    out = []
    for m in NUM_RE.findall(text or ""):
        try: out.append(float(m.replace("$", "").replace(",", "").strip()))
        except ValueError: pass
    return out

def _money_nums(text):
    """Money-looking numbers: '$'-prefixed, followed by USD, or with 2+ decimals (cents/prices). Scores like 100.0 are not money."""
    out = []
    for m in NUM_RE.finditer(text or ""):
        tok = m.group(0); tail = (text[m.end():m.end() + 5] or "").upper()
        dec = tok.split(".")[1] if "." in tok else ""
        if "$" in tok or tail.strip().startswith("USD") or len(dec) >= 2:
            try: out.append(float(tok.replace("$", "").replace(",", "").strip()))
            except ValueError: pass
    return out

def money_numbers_ok(report_text, allowed):
    """Every money number in the report must be a task input or a finance_facts.py fact (±0.005), sign-insensitive."""
    bad = [n for n in _money_nums(report_text) if not any(abs(abs(n) - abs(a)) <= 0.005 for a in allowed)]
    return not bad, bad

def _load_task(path):
    raw = open(path).read()
    try: tk = json.loads(raw)
    except ValueError: tk = {"text": raw}
    if not isinstance(tk, dict) or not str(tk.get("text", "")).strip(): raise Refused("task file needs non-empty text")
    if str(tk.get("kind") or "").strip().lower() == "code":   # FI-s-cap-handoff (CTO  2A.3, Option D)
        raise Refused('task kind "code": multi-line code builds do not go through the 160-token S chain; '
                      "the C-role writes them directly (CTO 2A, Option D); no model call")
    return tk

def _pto(a_id, s_id):
    import ho_check
    st = ho_check.check(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    return [b for b in (a_id, s_id) if st["bots"].get(b) != "READY"]

def _to_inbox(c, t, rep):
    d = os.path.join(STATE, "reports", "dg", c); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{t}.json"); json.dump(rep, open(p, "w"), indent=1); return p

def _md(rep):
    o = [f"Objective: {rep['objective']}", "Options:"] + [f"- {x['name']} (Deltas {x['deltas']}; Pros {x['pros']}; Cons {x['cons']})" for x in rep["options"]]
    o += [f"Decision: {rep['decision']}", f"Reasoning: {rep['reasoning']}", f"Confidence: {rep['confidence']}", "Conditions:"]
    o += [f"- {c['name']}: {c['result']}{' (required)' if c['required'] else ''}" for c in rep["conditions"]] + [f"Result: {rep['result']}"]
    if rep.get("same_model_review"):
        o.append("Same-model review: yes")
    if rep.get("reviewer_model"):
        o.append(f"Reviewer model: {rep['reviewer_model']}")
    return "\n".join(o)

def _cso_is_outside(tk, text, from_src=None):
    """Outside/raw content: --from, a non-internal source, a URL, fetched text, or rules not SAFE."""
    import cso_screen
    if from_src:
        return True
    src = tk.get("source", "internal")
    if src not in ("internal", "cso_screened"):
        return True
    if tk.get("fetched") or tk.get("fetched_text"):
        return True
    if re.search(r"https?://", text or "", re.I):
        return True
    return cso_screen.rules(text)[0] != "SAFE"

def _cso_is_money_math(tk, text):
    """Refuse work-mode money *math* (compute/calculate), not mere policy mentions of $ or profit."""
    if tk.get("money_inputs"):
        return True
    if not MONEY_RE.search(text or ""):
        return False
    return bool(re.search(
        r"\b(compute|calculate|how much|what is the (net|p&l|pnl|profit|fee)|report (the )?(net|p&l|pnl))\b",
        text or "", re.I))

def delegate(c, path, t=None, mode=None, from_src=None, batch_id=None):
    """C12: refuse quarantined roles first, then hold the Ollama lock for the whole chain so
    overlapping delegate calls serialize (re-entrant; an ancestor's flock counts as held).
    batch_id (CTO ruling A): a retest carries its batch id; the task is registered in the batch's open pass."""
    _quarantine(c)
    if batch_id is not None and not re.fullmatch(r"[\w.-]+", batch_id): raise Refused("bad --batch id")
    import ollama_lock
    t0 = time.time()
    try:
        with ollama_lock.hold():
            r = _delegate(c, path, t, mode, from_src)
            if batch_id and isinstance(r, dict) and r.get("task"): r["batch"], r["batch_round"] = batch_id, register_batch_task(r["task"], batch_id)
            return r
    except ollama_lock.LockTimeout as e:
        tid = t or f"{c}-lock"
        record_event("LOCAL_FAIL", c, tid, "A" + c[1:], "lock_timeout", e, round(time.time() - t0, 3))
        raise LocalFail(f"LOCAL_FAIL: lock_timeout: {e}") from e


A2A_GUARD = "CAO guard, pending Owner"   # the A-to-A money refusal is CAO's, not Owner's rule (still pending)
# the Owner: the CSO-team A-to-A refusal is removed; ASO is a normal A (its S is SSO).

def a_ids():
    return sorted(d for d in os.listdir(BOTS) if re.fullmatch(r"A[A-Z]{2}", d) and os.path.isdir(os.path.join(BOTS, d)) and ("C" + d[1:]) in CROLES)

def delegate_a2a(from_a, to_a, path, t=None):
    """(c) A-to-A: the task goes to the receiving A's S chain and its review; the report serves the asking A
    (reports/dg/<from_a>/). The task's C-role is the asking A's chief; C wakes only at c-review rounds."""
    from_a, to_a = (from_a or "").upper(), (to_a or "").upper()
    known = a_ids()
    for x in (from_a, to_a):
        if x not in known: raise Refused(f"unknown assistant {x or '(empty)'}")
    if from_a == to_a: raise Refused("A-to-A needs two different assistants")
    import alignment_check
    for x in (from_a, to_a):
        held, why = alignment_check.is_held(x)
        if held: raise Refused(f"held role: {why}")
    for x in ("C" + from_a[1:], "C" + to_a[1:]):
        _quarantine(x)
    import ollama_lock
    t = t or f"A2A-{from_a}-{to_a}-{time.strftime('%Y%m%d-%H%M%S')}"
    with ollama_lock.hold():
        r = _delegate("C" + from_a[1:], path, t, route=(from_a, to_a))
    route_log("a2a", task=t, from_a=from_a, to_a=to_a, c_role="C" + from_a[1:], result=r.get("result"),
              guards=f"no money tasks: {A2A_GUARD}")
    return r

def _delegate(c, path, t=None, mode=None, from_src=None, route=None):
    """C-role -> assistant -> S-suite -> assistant review -> Section 5 report (report_check) -> C-role DG inbox."""
    import report_check
    _quarantine(c)
    if c not in CROLES: raise Refused("not a C-role")
    tk = _load_task(path); text = tk["text"]
    t = t or f"{c}-{time.strftime('%Y%m%d-%H%M%S')}"
    a_id, s_id = "A" + c[1:], "S" + c[1:]
    if route:
        a_id, s_id = route[1], "S" + route[1][1:]
        # the Owner: A-to-A to or from ASO runs like any other A. A routed task always runs on the receiving
        # A's chain, so ASO as the SENDER never triggers the CSO screening branch below; the standard path still
        # refuses raw outside text (cso_screen rules) and the money guard below still applies.
    cso_mode = (mode or "screen").lower()
    if c == "CSO" and not route and cso_mode not in ("screen", "work"):
        raise Refused("CSO --mode must be screen or work")
    if c == "CSO" and not route and cso_mode != "work":
        return _delegate_cso(t, text, tk)
    if c == "CSO" and not route and cso_mode == "work":
        if _cso_is_outside(tk, text, from_src=from_src):
            raise Refused("CSO work mode refuses raw outside text; screening is the default (omit --mode or use --mode screen)")
        if _cso_is_money_math(tk, text):
            raise Refused("CSO work mode refuses money math")
        # fall through to the standard ASO/SSO work path
    import cso_screen
    if tk.get("source", "internal") not in ("internal", "cso_screened") or cso_screen.rules(text)[0] != "SAFE":
        raise Refused("task text is raw/unscreened outside content: only the CSO team (ASO/SSO) reads it; route it through cso_screen first")
    t0 = time.time()
    pto = _pto(a_id, s_id)
    if pto:
        handle_ollama_error(OSError(f"PTO: {pto} unavailable; {c} finishes this task on the paid model"), c, t, a_id, t0)
    if c == "CTO" and not route and is_number_ato(c, text):   # CTO round-2 ruling: code-built Decision, ATO reviews
        return delegate_number_batch(c, [dict(t=t, path=path)])[0]
    facts, allowed = None, []
    if route and (tk.get("money_inputs") or (MONEY_RE.search(text) and "CFO" in (c, "C" + route[1][1:]))):
        # CAO guard, pending Owner
        route_log("a2a_refused", task=t, from_a=route[0], to_a=route[1], reason="money task", guard=A2A_GUARD)
        raise Refused(f"A-to-A refuses money tasks; CFO money math runs only on the CFO chain ({A2A_GUARD})")
    if route and a_id == "ASO":   # CSO r4 fix (cso_relay_join #relay-join F2): A-to-A into ASO keeps CSO work-mode refusals
        why = ("raw outside text" if _cso_is_outside(tk, text, from_src=from_src) else
               "money math" if _cso_is_money_math(tk, text) else "")
        if why:
            route_log("a2a_refused", task=t, from_a=route[0], to_a=route[1], reason=f"ASO work mode: {why}", guard=A2A_GUARD)
            raise Refused(f"A-to-A to ASO refuses {why} (CSO work-mode rule); outside text goes through the CSO screen")
    if c == "CFO" and (tk.get("money_inputs") or MONEY_RE.search(text)):
        mi = tk.get("money_inputs")
        if not mi: raise Refused("CFO task has money math: give money_inputs; finance_facts.py computes, AFO/SFO never do money math")
        import finance_facts
        facts = finance_facts.calc(**mi); allowed = _nums(text) + [float(v) for v in mi.values() if isinstance(v, (int, float))] + list(facts.values())
        text = (text + "\n\nFACTS computed in code by scripts/finance_facts.py (exact; do NOT recompute or change any number; "
                "quote them as given): " + json.dumps(facts))
    if os.path.exists(tdir(t)):  # ADJUST round: same task id, C-role's adjust note goes to the S-suite
        rv = review(t)
        _open_only(task(t))
        if task(t)["c_role"] != c or not rv or rv["decision"] != "ADJUST": raise Refused("task exists and is not waiting on an ADJUST round")
        text = task(t)["text"]
    else:
        new(t, c, text)
        if route or tk.get("open_item_id") is not None:
            tkj = task(t)
            if route: tkj.update(assistant=a_id, s_suite=s_id, from_a=route[0], to_a=route[1])
            if tk.get("open_item_id") is not None: tkj["open_item_id"] = str(tk["open_item_id"])
            json.dump(tkj, open(os.path.join(tdir(t), "01_task.json"), "w"), indent=1)
    try:
        return _delegate_run(c, t, text, a_id, s_id, facts, allowed, num_predict=(1024 if (c == "CSO" and not route) or a_id == "ASO" else None),
                             inbox_role=route[0] if route else None)
    except (Refused, LocalFail, PtoNeeded):
        raise
    except OSError as e:
        handle_ollama_error(e, c, t, a_id, t0)


def _norm_answer(s):
    s = re.sub(r"\s+", " ", (s or "").strip().lower())
    return s.rstrip(" .;:")

def s_suite_answer(s_out):
    """Best compact reading of the S-suite's stated answer: a short first line unless it looks
    like a preamble; otherwise an Answer:/Final answer: line or the last short line."""
    lines = [ln.strip() for ln in (s_out or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    first = lines[0]
    if (len(first.split()) <= 12 and not first.endswith("?")
            and not re.match(r"^(we are given|step\b|note:|task:|original task)", first, re.I)):
        return first
    for ln in reversed(lines):
        m = re.search(r"(?:final\s+)?answer\s*:\s*(.+)$", ln, re.I)
        if m:
            return m.group(1).strip()
    if len(lines[-1].split()) <= 12:
        return lines[-1]
    return (s_out or "").strip()

def apply_s_suite_output_correct(rep, s_out):
    """If the assistant's decision differs from the S-suite's stated answer, require
    s_suite_output_correct=FAIL and force result FAIL. When they match, leave the
    condition absent or PASS. Returns True if they differ."""
    if not isinstance(rep, dict):
        return False
    dec = _norm_answer(str(rep.get("decision") or ""))
    ans = _norm_answer(s_suite_answer(s_out))
    differ = bool(dec and ans and dec != ans)
    conds = [c for c in (rep.get("conditions") or []) if c.get("name") != "s_suite_output_correct"]
    if differ:
        conds.append(dict(name="s_suite_output_correct", required=True, result="FAIL",
                          note=f"decision {rep.get('decision')!r} differs from S-suite answer {s_suite_answer(s_out)!r}"))
        rep["conditions"] = conds
        rep["result"] = "FAIL"
        return True
    # match: keep a PASS only if the model already filed the condition
    if any(c.get("name") == "s_suite_output_correct" for c in (rep.get("conditions") or [])):
        conds.append(dict(name="s_suite_output_correct", required=True, result="PASS"))
        rep["conditions"] = conds
    return False

ATO_NUMBER_NUM_PREDICT = 384   # CTO: no exceptions to the role rule (every A = 384). The 768 special case is withdrawn.
ANSWER_LINE_RE = re.compile(r"^\s*answer\s*:\s*(-?\d+)\s*\.?\s*$", re.I | re.M)

def normalize_number_decision(rep, s_out):
    r"""E1 (CTO D4 ruling): number task. Decision ^\d+$ passes as is. Otherwise take the integers in the
    decision in code: exactly one, equal to the S-suite's final 'Answer: N' line, normalizes the decision
    to N (decision_normalized=true, decision_original kept). Anything else is a required FAIL
    (number_decision); no paid fallback. Returns 'pass' | 'normalized' | 'fail'."""
    dec = str(rep.get("decision") or "").strip()
    if re.fullmatch(r"\d+", dec):
        return "pass"
    ints = re.findall(r"(?<![\d.])-?\d+(?![\d.])", dec)
    ans = ANSWER_LINE_RE.findall(s_out or "")
    if len(ints) == 1 and ans and ints[0] == ans[-1]:
        rep["decision_original"] = dec; rep["decision"] = ints[0]; rep["decision_normalized"] = True
        return "normalized"
    conds = [x for x in rep.get("conditions") or [] if x.get("name") != "number_decision"]
    conds.append(dict(name="number_decision", required=True, result="FAIL",
                      note=f"decision {dec[:80]!r} is not a number; integers {ints[:3]}, S Answer {ans[-1] if ans else None}"))
    rep["conditions"] = conds; rep["result"] = "FAIL"
    return "fail"

CHECK_ITEM_RE = re.compile(r"\((\d{1,2})\)\s*(.+?)(?=,?\s*(?:and\s+)?\(\d{1,2}\)|\.\s|\.$|$)", re.S)

def checklist_items(text):
    """Checklist items named in the task: '(1) ... (2) ...' inline, or numbered lines '1. ...' / '1) ...'.
    Needs at least 2 items in order 1..N; otherwise [] (no skeleton; the report_check backstop still runs)."""
    found = [(int(n), " ".join(s.split()).strip(" ,;")) for n, s in CHECK_ITEM_RE.findall(text or "")]
    if len(found) < 2:
        found = [(int(n), s.strip()) for n, s in re.findall(r"(?m)^\s*(\d{1,2})[.)]\s+(.+)$", text or "")]
    if len(found) < 2 or [n for n, _ in found] != list(range(1, len(found) + 1)):
        return []
    return [s for _, s in found]

WRITE_TASK_RE = re.compile(r"^\W*(?:please\s+)?(?:write|draft|list)\b", re.I)
CHECK_ASK_RE = re.compile(r"\b(?:check|checks|verify|verifies|pass|passes|meet|meets)\b", re.I)
CHECK_FACT_RE = re.compile(r"\bfacts?\b\s*:|[$]\s?\d|\d\s?%", re.I)

def is_check_task(text):
    """CSO root cause (FX-CSO-ASO-workmode-v7): a PASS/FAIL skeleton only makes sense when the task gives
    facts and asks to check, verify or pass them. A task that starts with write, draft or list asks for text, so it
    gets no skeleton (the report_check work-mode backstop still runs)."""
    t = text or ""
    if WRITE_TASK_RE.match(t):
        return False
    return bool(CHECK_ASK_RE.search(t) and CHECK_FACT_RE.search(t))

def checklist_schema(n):
    s = json.loads(json.dumps(S5_SCHEMA))
    s["required"] = s["required"] + ["item_results"]
    s["properties"]["item_results"] = {"type": "array", "minItems": n, "maxItems": n, "items": {
        "type": "object", "required": ["result", "reason"],
        "properties": {"result": {"type": "string", "enum": ["PASS", "FAIL"]}, "reason": {"type": "string"}}}}
    return s

def apply_checklist_decision(rep, items, item_results):
    """CSO ruling: code builds the decision skeleton from the task's checklist; ASO fills only each
    item's PASS or FAIL. ASO's own decision text is kept as decision_model and never used. Returns an error or None."""
    if not isinstance(item_results, list) or len(item_results) != len(items):
        return f"item_results must have exactly {len(items)} entries, one per checklist item in order"
    lines = []
    for i, (label, r) in enumerate(zip(items, item_results), 1):
        res = str((r or {}).get("result") or "").strip().upper()
        if res not in ("PASS", "FAIL"):
            return f"item_results[{i}] result must be PASS or FAIL"
        why = " ".join(str((r or {}).get("reason") or "").split())[:80]
        short = label if len(label) <= 60 else label[:57].rstrip() + "..."
        lines.append(f"{i}. {short}: {res}" + (f" - {why}" if res == "FAIL" and why else ""))
    rep["decision_model"] = rep.get("decision")
    rep["decision"] = "; ".join(lines)
    rep["decision_from_code"] = True
    return None

WRITE_NUM_PREDICT = 200   # CSO round-3 ruling (A4): a WRITE reply is a few plain lines; the cap stops a repeat loop early
NUM_TOKEN_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:%|x\b)", re.I)   # $N, N%, Nx
MARK_RE = re.compile(r"(?:[:\-\u2013]\s*|^)(PASS|FAIL)\b")

def parse_write_lines(raw):
    """Plain WRITE reply -> (lines, note). Numbering/bullets stripped, blank and fence lines skipped, exact repeats
    dropped; a line starting 'Note:' becomes the report's note."""
    lines, note = [], None
    for ln in (raw or "").splitlines():
        x = ln.strip()
        if not x or x.startswith("```"): continue
        if re.match(r"(?i)note\s*:", x):
            note = x.split(":", 1)[1].strip() or None; continue
        x = re.sub(r"^(?:\d{1,2}[.)]|[-*\u2022])\s*", "", x).strip()
        if x and x not in lines: lines.append(x)
    return lines, note

def write_lines_attempt(c, t, a_id, text, s_text, errs, attempt, lessons=""):
    """WRITE task: ASO returns plain lines (no JSON); code checks them and wraps them in the Section 5 report.
    Returns (rep or None, errs, review_meta, a_eval). None means retry; two Nones end in Refused (exit 2, no report)."""
    import run
    prompt = (f"Task from {c}:\n{text}\n\nS-suite output:\n{s_text}\n\nWRITE task: reply with ONLY the requested lines as plain "
              "text, one per line, numbered 1., 2., ... No JSON, no PASS or FAIL marks, no other words. Keep the task's wording "
              "and numbers. If a rule in the task differs from the rules you were given, add one last line starting 'Note:' "
              "that names the difference for the CSO.")
    if lessons: prompt += f"\n\nLessons from your C-role's takeovers (latest, follow them):\n{lessons}\n"
    if errs: prompt += f"\nYour last reply was rejected: {errs}. Fix it."
    raw, meta = _run_assistant_review(a_id, prompt, t, fmt=None, num_predict=WRITE_NUM_PREDICT)
    a_eval = dict(run.LAST_CALL or {}, num_predict=WRITE_NUM_PREDICT)
    open(os.path.join(tdir(t), f"03_a_raw_{attempt + 1}.txt"), "w").write(raw)
    if (a_eval.get("eval_count") or 0) >= WRITE_NUM_PREDICT:
        # CTO: a reply that hits the cap is a hard fail, not a retry
        raise Refused(f"reply hit the token cap ({WRITE_NUM_PREDICT}); hard fail, not a retry")
    lines, note = parse_write_lines(raw)
    if not lines:
        return None, ["no lines written"], meta, a_eval
    items = checklist_items(text)
    tok = lambda x: {re.sub(r"[\s,]", "", m).lower() for m in NUM_TOKEN_RE.findall(x)}
    new_nums = sorted(tok(" ".join(lines)) - tok(text))
    conds = [dict(name="lines written", required=True, result="PASS")]
    if items:
        conds.append(dict(name=f"one line per checklist item ({len(items)})", required=True, result="PASS" if len(lines) == len(items) else "FAIL"))
    conds.append(dict(name="no PASS/FAIL marks on lines", required=True, result="FAIL" if any(MARK_RE.search(l) for l in lines) else "PASS"))
    conds.append(dict(name="numbers kept from the task", required=True, result="FAIL" if new_nums else "PASS"))
    if new_nums: conds[-1]["note"] = f"numbers not in the task: {new_nums[:5]}"
    rep = dict(item=f"DG-{a_id} {t}", bot=a_id, result="FAIL" if any(x["result"] == "FAIL" for x in conds) else "PASS",
               objective="Write the requested lines (WRITE task); code wraps them in this report.",
               options=[dict(name="ASO lines as written", deltas=f"{len(lines)} lines", pros="task wording kept", cons="model-written text"),
                        dict(name="S-suite draft", deltas="not used as the decision", pros="first draft", cons="not reviewed")],
               decision=" | ".join(lines),
               reasoning="Code wrapped ASO's plain lines into this report (CSO round-3 ruling); every condition is checked in code.",
               confidence=80, conditions=conds, wrapped_by_code=True)
    if note: rep["note"] = note
    return rep, [], meta, a_eval

def apply_code_items(rep, items, item_results, code_items):
    """CSO round-3 ruling: code's PASS/FAIL wins for every item code could decide; ASO keeps only the reason.
    A difference is flagged in code_overrides. Items code could not decide (result None) stay ASO's and are flagged."""
    merged, overrides, unchecked = [], [], []
    for i, (a, k) in enumerate(zip(item_results, code_items), 1):
        a = dict(a or {}); aso = str(a.get("result") or "").strip().upper()
        if k["result"] is None:
            unchecked.append(dict(item=i, why=k["why"]))
        elif aso != k["result"]:
            overrides.append(dict(item=i, aso=aso or None, code=k["result"], code_why=k["why"], aso_reason=a.get("reason")))
            a["result"], a["reason"] = k["result"], k["why"]
        elif k["result"] == "FAIL" and not str(a.get("reason") or "").strip():
            a["reason"] = k["why"]
        merged.append(a)
    rep["facts_from_code"] = dict(items=[dict(n=i, item=k["item"], result=k["result"], numbers=k["numbers"], why=k["why"])
                                         for i, k in enumerate(code_items, 1)])
    rep["code_overrides"] = overrides
    if unchecked: rep["code_unchecked"] = unchecked
    return merged

def set_code_item_conditions(rep, items, item_results):
    """Per-item conditions come from the merged (code-wins) results; ASO's own conditions that restate an item are
    dropped so a wrong ASO verdict cannot fail the report. A non-required condition flags any ASO/code mismatch."""
    labels = [set(re.findall(r"[a-z]+", x.lower())) for x in items]
    def restates(name):
        w = set(re.findall(r"[a-z]+", (name or "").lower()))
        return bool(w) and any(len(w & l) >= max(2, int(0.6 * len(w))) for l in labels)
    keep = [x for x in rep["conditions"] if not restates(x.get("name"))]
    item_conds = [dict(name=f"item {i}: {x[:60]}", required=True, result=str((r or {}).get("result")).upper())
                  for i, (x, r) in enumerate(zip(items, item_results), 1)]
    flag = dict(name="ASO item verdicts match code", required=False, result="FAIL" if rep.get("code_overrides") else "PASS")
    if rep.get("code_overrides"): flag["note"] = f"code won on items {[o['item'] for o in rep['code_overrides']]}"
    rep["conditions"] = item_conds + keep + [flag]
    rep["result"] = "FAIL" if any(x.get("required") and x.get("result") == "FAIL" for x in rep["conditions"]) else "PASS"

# ---- CTO round-2 ruling: number tasks on the CTO chain, code-built Decision ----
ATO_VERDICT_RE = re.compile(r"^\s*(AGREE|DISAGREE)\b[\s.:-]*(.*)$", re.I)
ATO_REVIEW_NUM_PREDICT = 64
ATO_REVIEW_PROMPT = ("Task:\n{task}\n\nS-suite output:\n{s_out}\n\nCode took the Decision from the S-suite's final 'Answer:' line: {dec}.\n"
                     "Check the work and that number against the task. Reply with exactly two lines:\n"
                     "AGREE or DISAGREE\nReason: one short sentence.")

def code_number_decision(s_out):
    """Decision from S's 'Answer: N' line. Missing, or more than one Answer: line (CTO: strictly), is a hard FAIL.
    Returns (decision_or_None, error_or_None)."""
    ans = ANSWER_LINE_RE.findall(s_out or "")
    if not ans: return None, "no 'Answer: N' line in the S-suite output"
    if len(ans) > 1: return None, f"more than one 'Answer:' line ({len(ans)}): ambiguous"
    return ans[0], None

def parse_ato_review(raw):
    lines = [l for l in (raw or "").strip().splitlines() if l.strip()]
    m = ATO_VERDICT_RE.match(lines[0]) if lines else None
    if not m: return None, f"unparsed ATO review: {(raw or '')[:120]!r}"
    reason = (m.group(2) or "").strip() or (lines[1] if len(lines) > 1 else "")
    return m.group(1).upper(), re.sub(r"^\s*reason\s*:\s*", "", reason, flags=re.I)[:200]

def is_number_ato(c, text):
    import report_check
    return c == "CTO" and report_check.task_asks_for_number(text)

def number_phase_s(c, t, path, s_text=None):
    """Phase S: open (or ADJUST-resume) the task and run the S-suite once."""
    _quarantine(c)
    if c not in CROLES: raise Refused("not a C-role")
    tk = _load_task(path); text = tk["text"]
    import cso_screen
    if tk.get("source", "internal") not in ("internal", "cso_screened") or cso_screen.rules(text)[0] != "SAFE":
        raise Refused("task text is raw/unscreened outside content")
    if not is_number_ato(c, text): raise Refused("not a CTO number task")
    if os.path.exists(tdir(t)):
        rv = review(t); _open_only(task(t))
        if task(t)["c_role"] != c or not rv or rv["decision"] != "ADJUST": raise Refused("task exists and is not waiting on an ADJUST round")
    else:
        new(t, c, text)
    if s_text is not None: s_run(t, s_text)
    else: s_run(t, exe=True)

def number_phase_a(t):
    """Phase A: code writes the Decision; ATO only reviews (AGREE / DISAGREE + one reason line)."""
    import report_check
    tk = task(t); text = tk["text"]; s_out = open(n_files(t, "02_s_out")[-1]).read()
    dec, err = code_number_decision(s_out)
    raw, verdict, why = None, None, None
    for attempt in range(2):
        raw, _meta = _run_assistant_review("ATO", ATO_REVIEW_PROMPT.format(task=text, s_out=s_out, dec=dec if dec else "none (hard FAIL)"),
                                           t, num_predict=ATO_REVIEW_NUM_PREDICT)
        open(os.path.join(tdir(t), f"03_a_raw_{attempt + 1}.txt"), "w").write(raw or "")
        verdict, why = parse_ato_review(raw)
        if verdict: break
    conds = [dict(name="s_answer_line_unique", required=True, result="FAIL" if err else "PASS", note=err or f"Answer: {dec}"),
             dict(name="ato_agree", required=True, result="PASS" if verdict == "AGREE" else "FAIL", note=f"{verdict or 'unparsed'}: {why}")]
    rep = dict(item=f"DG-ATO {t}", bot="ATO", objective="Review the S-suite's number for the task; code writes the Decision.",
               options=[dict(name="accept", deltas=f"Decision {dec}", pros="code took it from the Answer line", cons="only as good as S"),
                        dict(name="adjust", deltas="send back to S", pros="catches a wrong number", cons="one more round")],
               decision=dec or "none", reasoning=f"ATO {verdict or 'unparsed'}: {why}", confidence=80 if verdict == "AGREE" else 40,
               conditions=conds, result="PASS" if (not err and verdict == "AGREE") else "FAIL",
               decision_from_code=True, ato_verdict=verdict or "unparsed", ato_reason=why,
               c_review_hint="ADJUST" if verdict != "AGREE" or err else "ACCEPT", handoff_id=t)
    errs = report_check.check(rep, s_out=s_out, task_text=text)
    if errs:
        rep["conditions"].append(dict(name="report_check", required=True, result="FAIL", note="; ".join(map(str, errs))[:200])); rep["result"] = "FAIL"
    rep["a_eval"] = dict(__import__("run").LAST_CALL, num_predict=ATO_REVIEW_NUM_PREDICT)
    _write_a_report_json(t, rep)
    open(os.path.join(tdir(t), "03_a_report.md"), "w").write(_md(rep))
    return rep

def number_phase_r(t):
    """Phase R: the chain reviewer (llama for phi4 chains) runs independently; then the report goes to the inbox."""
    tk = task(t); rep = json.load(open(os.path.join(tdir(t), "03_a_report.json")))
    apply_independent_review(rep, t, tk["text"], open(n_files(t, "02_s_out")[-1]).read(), "ATO")
    if rep["result"] == "FAIL": rep["c_review_hint"] = "ADJUST"
    _write_a_report_json(t, rep)
    open(os.path.join(tdir(t), "03_a_report.md"), "w").write(_md(rep))
    return dict(task=t, c_role="CTO", assistant="ATO", s_suite="STO", result=rep["result"], decision=rep["decision"],
                ato_verdict=rep["ato_verdict"], c_review_hint=rep["c_review_hint"], inbox=_to_inbox("CTO", t, rep))

def delegate_number_batch(c, items):
    """CTO round-2 ruling: run a number batch BY MODEL so each model loads once: all S calls, then all ATO
    reviews, then all reviewer calls. items: [dict(t=..., path=..., s_text=None)]. Holds the Ollama lock."""
    import ollama_lock
    with ollama_lock.hold():
        for it in items: number_phase_s(c, it["t"], it["path"], it.get("s_text"))
        for it in items: number_phase_a(it["t"])
        return [number_phase_r(it["t"]) for it in items]

def _delegate_run(c, t, text, a_id, s_id, facts, allowed, num_predict=None, inbox_role=None):
    import report_check, run
    s_cap = token_cap(s_id, num_predict)
    s_run(t, exe=True, num_predict=s_cap)
    raise_if_cap_hit(s_id, s_cap)
    outs = n_files(t, "02_s_out")
    adj = n_files(t, "05_adjust")
    base = (f"Task from {c}:\n{adjust_first(c, text, open(adj[-1]).read() if adj else None)}\n\nS-suite output to review:\n{open(outs[-1]).read()}\n\nWrite your review as ONE Section 5 report in JSON. "
            "At least two options. Conditions are measurable checks with PASS/FAIL; any required FAIL means result FAIL. "
            "Be brief: every text field under 40 words, at most 3 options and 4 conditions; do not copy code or long text into the report. "
            + ("Do not compute money: only quote the FACTS numbers. " if facts else ""))
    items = checklist_items(text) if a_id == "ASO" and is_check_task(text) else []   # CSO work mode, CHECK tasks only (v7); keyed on the chain's A (the Owner: A-to-A to or from ASO)
    schema = checklist_schema(len(items)) if items else S5_SCHEMA
    code_items = None
    if items:
        import check_facts   # CSO round-3 ruling: code does every numeric comparison; ASO writes only the reasons
        code_items = check_facts.check(text, items)
        base += ("\n\nChecklist (code writes decision from it): fill item_results with exactly one entry per item, in order, "
                 "each result PASS or FAIL with a short reason. Do not put a verdict in decision. Code already compared the "
                 "numbers; your result must match code's, and your reason explains it.\n"
                 + "\n".join(f"{i}. {x}" + (f"  [code: {r['result']} - {r['why']}]" if r["result"] else "")
                             for i, (x, r) in enumerate(zip(items, code_items), 1)) + "\n")
    les = latest_lessons(a_id)
    if les: base += f"\n\nLessons from your C-role's takeovers (latest, follow them):\n{les}\n"
    errs, rep, review_meta, fallback = [], None, None, False
    number_task = c != "CFO" and report_check.task_asks_for_number(text)
    a_np = token_cap(a_id, num_predict)   # CTO: no ATO bypass; every A uses the role cap (384)
    write_task = a_id == "ASO" and bool(WRITE_TASK_RE.match(text or ""))   # CSO round-3 ruling (A4): plain lines, code wraps; keyed on the chain's A
    for attempt in range(2):
        if write_task:
            s_text = open(outs[-1]).read()
            rep, errs, review_meta, a_eval = write_lines_attempt(c, t, a_id, text, s_text, errs, attempt, les)
            if rep is None: continue   # no lines: retry once, then Refused (exit 2, no report). Cap hits already raised.
            rep["a_eval"] = a_eval
            errs = report_check.check(rep, s_out=s_text, task_text=text)
            if not errs: break
            continue
        prompt = base + (f"\nYour last report was rejected: {errs}. Fix it." if errs else "")
        if fallback:
            import run
            raw = run.run(a_id, prompt, fmt=schema, model=FALLBACK_REVIEW_MODEL, num_predict=a_np)
            meta = dict(reviewer_model=FALLBACK_REVIEW_MODEL, same_model_review=True,
                        load_error=(review_meta or {}).get("load_error"))
        else:
            raw, meta = _run_assistant_review(a_id, prompt, t, fmt=schema, num_predict=a_np)
            if meta["same_model_review"]:
                fallback = True
        review_meta = meta
        a_eval = dict(run.LAST_CALL or {}, num_predict=a_np)   # E2: resolved cap (settings.json when the task gave none)
        open(os.path.join(tdir(t), f"03_a_raw_{attempt + 1}.txt"), "w").write(raw)
        raise_if_cap_hit(a_id, a_np, a_eval.get("eval_count"))
        try: rep = json.loads(raw)
        except ValueError: errs = ["not valid JSON (probably cut off: too long; keep every field under 40 words)"]; continue
        item_results = rep.get("item_results") if isinstance(rep, dict) else None
        note = rep.get("note") if isinstance(rep, dict) else None
        rep = dict(item=f"DG-{a_id} {t}", bot=a_id, **{k: rep.get(k) for k in ("result", "objective", "options", "decision", "reasoning", "confidence", "conditions")})
        if note is not None: rep["note"] = note   # optional; report_check refuses a non-string note
        if items:
            if code_items and isinstance(item_results, list) and len(item_results) == len(items):
                item_results = apply_code_items(rep, items, item_results, code_items)
            e = apply_checklist_decision(rep, items, item_results)
            if e: errs = [e]; continue
        if isinstance(rep.get("conditions"), list):
            demote_format_conditions(rep, text + "\n" + (open(adj[-1]).read() if adj else ""))
            demote_original_task_conditions(rep, bool(adj))
        if items and code_items and isinstance(rep.get("conditions"), list):
            set_code_item_conditions(rep, items, item_results)
        if any(x.get("required") and x.get("result") == "FAIL" for x in rep["conditions"] or []): rep["result"] = "FAIL"  # rule: required FAIL -> FAIL
        if facts:
            ok, bad = money_numbers_ok(json.dumps(rep), allowed)
            rep["conditions"].append(dict(name="money numbers only from finance_facts.py facts", required=True, result="PASS" if ok else "FAIL"))
            if not ok: rep["result"] = "FAIL"; rep["reasoning"] += f" [code check: numbers not from facts: {bad[:5]}]"
        s_text = open(outs[-1]).read()
        rep["a_eval"] = a_eval
        if number_task:
            normalize_number_decision(rep, s_text)
        if a_id == "APO":
            apply_s_suite_output_correct(rep, s_text)
        errs = report_check.check(rep, s_out=s_text, task_text=text)
        if not errs: break
    if errs: raise Refused(f"assistant report failed report_check twice: {errs}")
    if facts is not None or "facts_from_code" not in rep: rep["facts_from_code"] = facts   # CSO CHECK: set by apply_code_items
    rep["handoff_id"] = t   # C7: the flow task id is the CPO hand-off id
    if task(t).get("open_item_id"): rep["open_item_id"] = task(t)["open_item_id"]
    if review_meta:
        rep["reviewer_model"] = review_meta["reviewer_model"]
        if review_meta["same_model_review"]:
            rep["same_model_review"] = True
    apply_independent_review(rep, t, text, open(outs[-1]).read())
    open(os.path.join(tdir(t), "03_a_report.md"), "w").write(_md(rep))
    _write_a_report_json(t, rep)
    return dict(task=t, c_role=c, assistant=a_id, s_suite=s_id, result=rep["result"], inbox=_to_inbox(inbox_role or c, t, rep), attempts=attempt + 1)

def _delegate_cso(t, text, tk):
    import cso_screen, report_check
    r = cso_screen.chain(text)
    if r.get("pto"):
        handle_ollama_error(OSError("PTO: ASO/SSO unavailable; CSO treats the content as SUSPECT"),
                            "CSO", t, "ASO", time.time())
    new(t, "CSO", f"screen outside content sha256={hashlib.sha256(text.encode()).hexdigest()} len={len(text)} source={tk.get('source_name', 'unknown')}")
    s_run(t, f"SSO verdict: {r['sso']}")  # only the verdict word leaves the CSO team, never the raw text
    rep = dict(item=f"DG-ASO {t}", bot="ASO", result="PASS" if r["verdict"] == "SAFE" else "FAIL",
        objective="Screen one piece of outside content for prompt injection before any bot uses it (I1/I2).",
        options=[dict(name=v, deltas=f"verdict {v}", pros=p, cons=q) for v, p, q in (("SAFE", "content usable as data", "risk if wrong"),
                 ("SUSPECT", "HOLD, then Owner (no CSO closer-look)", "delay"), ("BLOCK", "dropped", "may lose a real finding"))],
        handoff_id=t, decision=r["verdict"], reasoning=f"rules + SSO work + ASO review: {r['reasons']}", confidence=90 if r["sso"] == r["aso"] else 60,
        conditions=[dict(name="rules layer SAFE", required=True, result="PASS" if not [x for x in r["reasons"] if not x.startswith(("sso:", "aso_review:"))] else "FAIL"),
                    dict(name="SSO verdict SAFE", required=False, result="PASS" if r["sso"] == "SAFE" else "FAIL"),
                    dict(name="ASO review SAFE", required=True, result="PASS" if r["aso"] == "SAFE" else "FAIL")])
    if any(x["required"] and x["result"] == "FAIL" for x in rep["conditions"]): rep["result"] = "FAIL"
    e = report_check.check(rep)
    if e: raise Refused(f"CSO report invalid: {e}")
    open(os.path.join(tdir(t), "03_a_report.md"), "w").write(_md(rep))
    _write_a_report_json(t, rep)
    return dict(task=t, c_role="CSO", assistant="ASO", s_suite="SSO", result=rep["result"], verdict=r["verdict"], inbox=_to_inbox("CSO", t, rep), attempts=1)

def close(t):
    tk = task(t); rv = review(t)
    _open_only(tk)
    _quarantine(tk.get("c_role") or "")
    if not rv or rv["decision"] != "ACCEPT": raise Refused("C-role has not accepted the latest report")
    tk["state"] = "CLOSED"; tk["closed_ts"] = time.time(); json.dump(tk, open(os.path.join(tdir(t), "01_task.json"), "w"), indent=1)

def note_text_ok(text):
    """Same non-private text checks as a task: non-empty, cso_screen rules SAFE, no URL, no secret pattern."""
    import cso_screen, repo_guard
    if not (text or "").strip(): return False, "empty note"
    if re.search(r"https?://", text, re.I): return False, "note has a URL (outside content)"
    if cso_screen.rules(text)[0] != "SAFE": return False, "note fails cso_screen rules"
    hits = repo_guard.scan("lessons.md", text)
    if hits: return False, f"note looks private: {hits[0].split(':')[0]}"
    return True, ""

def lessons_path(a_id): return os.path.join(BOTS, a_id, "lessons.md")

def latest_lessons(a_id, cap=LESSONS_CAP):
    p = lessons_path(a_id)
    if not os.path.exists(p): return ""
    txt = open(p).read().strip()
    return txt if len(txt) <= cap else "..." + txt[-cap:]

def takeover(t, note_text):
    """(b) After round 3 fails: C closes the task itself and its 'how I did it' note goes to the A's lessons."""
    tk = task(t); _open_only(tk); rv = review(t)
    _quarantine(tk.get("c_role") or "")
    b = tk.get("batch")
    # CSO re-screen 2: the batch file's 'adjusts' can be hand-edited; count only ADJUST rounds logged in the chained route_log
    rounds = logged_adjust_rounds(b) if b else 0
    exhausted = bool(rv and rv["decision"] == "ADJUST" and rounds >= MAX_BATCH_ADJUST)
    rpj = os.path.join(tdir(t), "03_a_report.json")
    import route_chain   # CSO re-screen 2: takeover trusts route_log only if its hash chain is intact
    rl_ok, rl_msg, _ = route_chain.verify(os.path.join(STATE, "flow", "route_log.jsonl"))
    if not rl_ok and not rl_msg.startswith("NO-LOG"):
        raise Refused(f"takeover refused: route_log chain not intact ({rl_msg}); CAO checks it")
    try: last_fail = json.load(open(rpj)).get("result") == "FAIL"
    except (OSError, ValueError): last_fail = False
    # CSO go-live screen (G): a FAIL report counts only if it is the report the flow wrote (sha256 logged in route_log)
    if last_fail and (logged_report_sha(t) is None or logged_report_sha(t) != _file_sha(rpj)):
        last_fail = False
    # RELAY-SPEC-1 fix: after ADJUST then a FAIL report, c-review refuses 'already reviewed', so the C must be
    # able to take over on the FAIL itself, not only after round-3 exhaustion.
    if not (exhausted or last_fail):
        raise Refused(f"takeover only after a FAIL report or review round {MAX_BATCH_ADJUST} fails (ADJUST rounds {rounds})")
    ok, why = note_text_ok(note_text)
    if ok:   # CSO go-live screen (A): lessons go into a git-tracked file fed to the A's prompt -> the hardened relay guard too
        import text_guard
        ok, why = text_guard.check(note_text, "internal")
    if not ok: raise Refused(f"takeover note refused: {why}")
    a_id = tk["assistant"]; p = lessons_path(a_id); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as fh:
        fh.write(f"\n## {time.strftime('%Y-%m-%d %H:%M %Z')} takeover {t} by {tk['c_role']}\n{note_text.strip()}\n")
    json.dump(dict(by=tk["c_role"], note=note_text.strip(), batch=b, rounds=rounds, ts=time.time()),
              open(os.path.join(tdir(t), "07_takeover.json"), "w"), indent=1)
    tk["state"] = "CLOSED"; tk["closed_ts"] = time.time(); tk["closed_by_takeover"] = True
    json.dump(tk, open(os.path.join(tdir(t), "01_task.json"), "w"), indent=1)
    why = "round-3 exhaustion" if exhausted else "FAIL report"
    route_log("takeover", task=t, batch=b, c_role=tk["c_role"], assistant=a_id, rounds=rounds, note_chars=len(note_text.strip()), allowed_by=why)
    import action_log
    action_log.append(os.environ.get("GB_ACTION_LOG", action_log.DEFAULT_LOG), dict(
        item=f"takeover {t}", bot=tk["c_role"], result="PASS",
        objective=f"C takes over {t} ({why}) and closes it; lessons go to {a_id}.",
        options=[dict(name="close --takeover", deltas=f"lessons appended to bots/{a_id}/lessons.md", pros="clean close after a C takeover", cons="A did not finish")],
        decision=f"closed by takeover ({why}, ADJUST rounds {rounds})", reasoning=note_text.strip()[:600], confidence=90,
        conditions=[dict(name="takeover allowed (FAIL report or round-3 exhaustion)", required=True, result="PASS"),
                    dict(name="lessons note passes non-private text checks", required=True, result="PASS")]))

def abandon(t, note, by):
    """Mark an OPEN task ABANDONED (evidence-only; any step: no S work, awaiting report or review,
    after ADJUST, close refused). Nothing is deleted. Writes 06_abandon.json, sets state in
    01_task.json (as close does), and appends a line to $GB_STATE/flow/abandon_log.jsonl.
    --by must be the task's C-role or CAO (flow owner); it is a claim, not identity
    (accident guard plus detection). A held/quarantined task role does not block cleanup."""
    tk = task(t)
    if not (note or "").strip(): raise Refused("abandon needs --note with the reason")
    by = (by or "").strip().upper()
    if by not in (tk.get("c_role"), "CAO"): raise Refused("only the task's C-role or CAO may abandon it")
    if by != "CAO": _quarantine(by)
    if tk.get("state") != "OPEN": raise Refused(f"task {str(tk.get('state')).lower()}; only an OPEN task can be abandoned")
    rv = review(t)
    rec = dict(task=t, c_role=tk.get("c_role"), by=by, note=note.strip(), ts=time.time(),
               at=time.strftime("%Y-%m-%d %H:%M:%S %Z"), prior_state=tk.get("state"),
               s_outputs=len(n_files(t, "02_s_out")), has_report=os.path.exists(os.path.join(tdir(t), "03_a_report.md")),
               last_review=(rv or {}).get("decision"))
    json.dump(rec, open(os.path.join(tdir(t), "06_abandon.json"), "w"), indent=1)
    tk["state"] = "ABANDONED"; tk["abandoned_ts"] = rec["ts"]; tk["abandon_note"] = rec["note"]; tk["abandoned_by"] = by
    json.dump(tk, open(os.path.join(tdir(t), "01_task.json"), "w"), indent=1)
    with open(os.path.join(STATE, "flow", "abandon_log.jsonl"), "a") as fh: fh.write(json.dumps(rec) + "\n")
    return rec

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", choices=["delegate", "new", "s-run", "a-report", "c-review", "close", "status", "abandon", "backfill-rounds"])
    p.add_argument("--batch", help="delegate/c-review/backfill-rounds: batch id; a round is one review pass over the batch (max 3 ADJUST rounds)")
    p.add_argument("--tasks", help="backfill-rounds: comma-separated task ids, one per round, in order")
    p.add_argument("--takeover", action="store_true", help="close: C takes over after round 3 fails (needs --note-file)")
    p.add_argument("--note-file", help="close --takeover: C's 'how I did it' note, appended to bots/<A>/lessons.md")
    p.add_argument("--lessons", help="close --takeover: the lessons note as text (instead of --note-file)")
    p.add_argument("--from-a"); p.add_argument("--to-a")
    p.add_argument("--task", help="(required except backfill-rounds) task id; for delegate: path to the task file (JSON {text, ...} or plain text)")
    p.add_argument("--role", help="delegate: the C-role handing out the task"); p.add_argument("--id", help="delegate: optional task id"); p.add_argument("--c-role"); p.add_argument("--text"); p.add_argument("--from", dest="src")
    p.add_argument("--exec", action="store_true"); p.add_argument("--decision"); p.add_argument("--note", default=""); p.add_argument("--by", help="abandon: the task's C-role or CAO")
    p.add_argument("--mode", default="screen", choices=["screen", "work"], help="CSO only: screen (default) or work (internal text to ASO/SSO)")
    a = p.parse_args(); src = open(a.src).read() if a.src else None
    if a.cmd != "backfill-rounds" and not a.task: p.error("--task is required")
    t0 = time.time()
    try:
        {"delegate": lambda: print(json.dumps(delegate_a2a(a.from_a, a.to_a, a.task, a.id) if (a.from_a or a.to_a) else delegate(a.role, a.task, a.id, mode=a.mode, from_src=a.src, batch_id=a.batch))), "new": lambda: new(a.task, a.c_role, a.text), "s-run": lambda: s_run(a.task, src, a.exec),
         "a-report": lambda: a_report(a.task, src, a.exec), "c-review": lambda: c_review(a.task, a.decision, a.note, batch_id=a.batch),
         "close": lambda: takeover(a.task, a.lessons if a.lessons is not None else (open(a.note_file).read() if a.note_file else "")) if a.takeover else close(a.task), "abandon": lambda: print(json.dumps(abandon(a.task, a.note, a.by))), "status": lambda: print(json.dumps(task(a.task))),
         "backfill-rounds": lambda: print(json.dumps({k: v for k, v in backfill_rounds(a.batch, [x for x in (a.tasks or "").split(",") if x], a.note).items() if k != "reviews"}))}[a.cmd]()
    except Refused as e: print(f"REFUSED: {e}", file=sys.stderr); sys.exit(2)
    except LocalFail as e:
        print(f"LOCAL_FAIL: {e}; retry locally (exit {LOCAL_FAIL_EXIT})", file=sys.stderr); sys.exit(LOCAL_FAIL_EXIT)
    except PtoNeeded as e:
        print(f"PTO: Ollama down ({e}); C-role finishes with --from", file=sys.stderr); sys.exit(3)
    except OSError as e:
        role = a.role or a.c_role or ""
        tid = a.id if a.cmd == "delegate" else a.task
        assistant = ("A" + role[1:]) if role.startswith("C") and len(role) >= 2 else ""
        try:
            handle_ollama_error(e, role, tid, assistant, t0)
        except LocalFail as e2:
            print(f"LOCAL_FAIL: {e2}; retry locally (exit {LOCAL_FAIL_EXIT})", file=sys.stderr); sys.exit(LOCAL_FAIL_EXIT)
        except PtoNeeded as e2:
            print(f"PTO: Ollama down ({e2}); C-role finishes with --from", file=sys.stderr); sys.exit(3)
        print(f"PTO: Ollama unavailable ({type(e).__name__}); C-role finishes with --from", file=sys.stderr); sys.exit(3)
