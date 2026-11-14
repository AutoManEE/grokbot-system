#!/usr/bin/env python3
"""bots/run.py (P2-01, hosted by COO, settings owned by CAO): run one assistant or S bot on local Ollama, no tools.
  run.py BOT_ID -f INPUT [--outside]     prints the model's text answer
--outside (raw outside content) is allowed only for the CSO team (ASO, SSO); any other bot is refused.
The prompt and model come only from bots/<ID>/prompt.md + settings.json (changed only by the CAO via AD).
If Ollama is down, exits 3 and the bot is on PTO (its C-role finishes the work on the paid model).
C12: every model call holds flock on <repo>/gb_state/ollama.lock (bots/ollama_lock.py); no external flock needed.
A lock timeout exits 4 (LOCAL_FAIL), never PTO.
CTO relay cost check: every chain call sends keep_alive 0 so only one model is resident at a
time (it unloads only this call's own model after the call; nothing ever stops or unloads another model). A caller
that batches several calls to one model may pass a short keep_alive for all but the phase's last call (relay.py).
Every call records load_s, compute_s (total minus load), evicted models and the CTO gates (compute <= 62.6 s,
warm load <= 30 s; a model's first load in this process is cold and exempt) in LAST_CALL and hands them to ON_CALL
(flow.py logs them to route_log).
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import time, argparse, json, os, re, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ollama_lock
HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
LOAD_FAIL_RE = re.compile(
    r"not found|no such model|out of memor|system memory|cannot load|"
    r"failed to load|error loading|requires more.*memory|unable to load|load error",
    re.I,
)
FALLBACK_REVIEW_MODEL = "qwen3:4b-instruct"

class ModelLoadError(Exception):
    """The requested model could not be loaded (not found, out of RAM, or Ollama load error)."""
    def __init__(self, model, detail):
        self.model = model
        self.detail = detail
        super().__init__(f"model {model} failed to load: {detail}")


def load(bid):
    d = os.path.join(HERE, bid)
    if not os.path.isdir(d): raise SystemExit(f"REFUSED: unknown bot {bid}")
    return open(os.path.join(d, "prompt.md")).read().strip(), json.load(open(os.path.join(d, "settings.json")))


_FLOW_PY = GB_ROOT + "/scripts/flow.py"
_SCRIPTS_DIR = GB_ROOT + "/scripts"

# CTO: only these hard-coded call sites may pass an
# explicit num_predict override into run.run / run.complete. Never task text or a CLI flag.
# Allowlisted call sites: flow.py, relay.py and cso_screen.py.
OVERRIDE_ALLOWED = frozenset({"flow", "relay", "cso_screen"})
# CTO condition (1): an allowlisted caller file must also be git-tracked in OVERRIDE_GIT and unmodified
# vs HEAD at call time. No test-mode bypass: tests monkeypatch _tracked_and_clean or point OVERRIDE_GIT at a
# tmp repo (never at <repo>). Adding or removing a module needs a CTO cosign through fixlist_check.
OVERRIDE_GIT = GB_ROOT
_OVERRIDE_GIT_CACHE = {}   # (realpath, dev, ino, mtime_ns, ctime_ns, size, HEAD) -> (ok, why)


def _screened_script(n):
    """realpath of <repo>/scripts/<n>.py. A symlinked file (or one that resolves outside scripts/)
    is not the screened file: PermissionError, no model call."""
    p = os.path.join(_SCRIPTS_DIR, n + ".py")
    if os.path.islink(p):
        raise PermissionError(f"{p} is a symlink; not the screened file, no model call")
    rp = os.path.realpath(p)
    if os.path.dirname(rp) != os.path.realpath(_SCRIPTS_DIR):
        raise PermissionError(f"{p} resolves outside scripts/ ({rp}); no model call")
    return rp


def _flow():
    """scripts/flow.py (token_cap / raise_if_cap_hit), and only that file: a flow module from anywhere else
    (a bots/flow.py shadow, a copy elsewhere, or a symlinked scripts/flow.py) is refused before any model call."""
    m = sys.modules.get("flow")
    want = _screened_script("flow")
    if m is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("flow", _FLOW_PY)
        m = importlib.util.module_from_spec(spec); sys.modules["flow"] = m
        try: spec.loader.exec_module(m)
        except BaseException: sys.modules.pop("flow", None); raise
    if os.path.realpath(getattr(m, "__file__", None) or "") != want:
        raise PermissionError(f"flow loaded from {getattr(m, '__file__', None)!r}, not {_FLOW_PY}; no model call")
    return m


_TESTS_DIR = GB_ROOT + "/scripts/tests"


def _test_wrapper_frame(fr):
    """A frame from a genuine scripts/tests module (its own module globals, code from that module's file) is a
    transparent test wrapper (e.g. a monkeypatched run.run that forwards to the real one). A fake co_filename
    with foreign globals is not. Never true in production (no test module is imported)."""
    g = fr.f_globals
    name = g.get("__name__")
    mod = sys.modules.get(name) if isinstance(name, str) else None
    if mod is None or g is not vars(mod):
        return False
    mf = os.path.realpath(getattr(mod, "__file__", None) or "")
    return (os.path.dirname(mf) == os.path.realpath(_TESTS_DIR) and os.path.basename(mf).startswith("test_")
            and os.path.realpath(fr.f_code.co_filename or "") == mf)


GIT_BIN = "/usr/bin/git"   # absolute: a caller's PATH can never substitute a fake git
# Minimal fixed env: no inherited GIT_*, HOME, XDG or PATH; no system/global config. Only read-only plumbing
# (rev-parse) is run, never diff/status/ls-files, so no index refresh: no fsmonitor, filters, textconv,
# external diff, assume-unchanged or skip-worktree can run code or hide an edit.
_GIT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "XDG_CONFIG_HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _git(*args):
    """Read-only git plumbing on OVERRIDE_GIT/.git (explicit --git-dir: no discovery of a parent .git; cwd /),
    absolute GIT_BIN, fixed minimal env, core.fsmonitor/hooksPath forced off, 5 s timeout."""
    import subprocess
    gd = os.path.join(os.path.realpath(OVERRIDE_GIT), ".git")
    return subprocess.run([GIT_BIN, "--git-dir=" + gd, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                           *args], capture_output=True, text=True, timeout=5, env=dict(_GIT_ENV), cwd="/")


def _clear_override_git_cache():
    """Tests: drop the per-process cache after pointing OVERRIDE_GIT at a tmp repo."""
    _OVERRIDE_GIT_CACHE.clear()


def _blob_id(data, fmt):
    import hashlib
    h = hashlib.sha256() if fmt == "sha256" else hashlib.sha1()
    h.update(b"blob %d\0" % len(data)); h.update(data)
    return h.hexdigest()


def _tracked_and_clean(path):
    """(ok, why): path is tracked in OVERRIDE_GIT's HEAD and its bytes equal the HEAD blob. Fail-closed on any
    error. No test-mode deferral of any kind. HEAD is read on every call; the result is cached per process by
    (realpath, dev, ino, mtime_ns, ctime_ns, size, HEAD). The file bytes are hashed here (no worktree/index
    git command), so git config, attributes and index flags cannot run code or hide an edit."""
    try:
        repo = os.path.realpath(OVERRIDE_GIT)
        rp = os.path.realpath(path)
        st = os.stat(rp)
        rel = os.path.relpath(rp, repo)
    except (OSError, ValueError):
        return False, "git check failed"
    if rel == os.curdir or rel.startswith(os.pardir + os.sep) or rel == os.pardir or os.path.isabs(rel):
        return False, "caller is outside the repo"
    try:
        h = _git("rev-parse", "--verify", "-q", "HEAD^{commit}")
        head = h.stdout.strip() if h.returncode == 0 else ""
    except Exception:
        head = ""
    if not head:
        return False, "git HEAD unreadable"
    key = (rp, st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size, head)
    hit = _OVERRIDE_GIT_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        fmt = _git("rev-parse", "--show-object-format")
        fmt = fmt.stdout.strip() if fmt.returncode == 0 else ""
        b = _git("rev-parse", "--verify", "-q", f"{head}:{rel}")
        if fmt not in ("sha1", "sha256"):
            out = (False, "git check failed")
        elif b.returncode != 0 or not b.stdout.strip():
            out = (False, "caller is not git-tracked")
        else:
            with open(rp, "rb") as fh:
                data = fh.read()
            st2 = os.stat(rp)
            if (st2.st_ino, st2.st_mtime_ns, st2.st_ctime_ns, st2.st_size) != (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size):
                out = (False, "git check failed")   # changed while checking: do not cache a stale answer
                return out
            out = ((True, "tracked and clean") if _blob_id(data, fmt) == b.stdout.strip()
                   else (False, "caller is modified vs HEAD"))
    except Exception:
        out = (False, "git check failed")
    _OVERRIDE_GIT_CACHE[key] = out
    return out


def _log_override_refused(caller, value, bot, reason):
    """route_log kind=cap_override_refused for a git-check refusal (value already validated). No test-mode check."""
    fl = _flow()
    want = _screened_script("route_chain")
    rc = sys.modules.get("route_chain")
    if rc is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("route_chain", want)
        rc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)
    if os.path.realpath(getattr(rc, "__file__", None) or "") != want:
        raise PermissionError(f"route_chain loaded from {getattr(rc, '__file__', None)!r}, not {want}")
    st = os.environ.get("GB_STATE") or getattr(fl, "STATE", GB_ROOT + "/gb_state")
    rec = dict(kind="cap_override_refused", ts=time.time(), at=time.strftime("%Y-%m-%d %H:%M:%S %Z"),
               caller=caller, value=value, bot=bot, reason=reason)
    return rc.append(os.path.join(st, "flow", "route_log.jsonl"), rec)


def _require_override(hid, value):
    """An override must be a positive int (bool excluded) and come from the IMMEDIATE caller of run.run /
    run.complete, whose frame globals are the module object of an allowlisted scripts/<name>.py (also when that
    script runs as __main__), with code from that same file. A fake co_filename (exec/compile), a callback
    handed to an allowlisted module, python -c, the CLI or any other script is refused before any model call.
    Validated values only are logged to route_log (kind cap_override) with the caller and value.
    Residual (in-process): code that execs inside an allowlisted module's own globals can still pass; such
    code can also call run._chat directly, so the role cap (min(role, override)) is the real ceiling."""
    Refused = _flow().Refused
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Refused(f"num_predict override {value!r} is not a positive integer; refused, no model call")
    here = os.path.realpath(__file__)
    fr = sys._getframe(1)
    while fr is not None and (os.path.realpath(fr.f_code.co_filename or "") == here or _test_wrapper_frame(fr)):
        fr = fr.f_back
    label, ok = "unknown", False
    if fr is not None:
        fn = fr.f_code.co_filename or ""
        label = f"{os.path.splitext(os.path.basename(fn))[0] or '?'}:{fr.f_code.co_name}"
        g = fr.f_globals
        modname = g.get("__name__")
        mod = sys.modules.get(modname) if isinstance(modname, str) else None
        mfile = getattr(mod, "__file__", None) or ""
        n = os.path.splitext(os.path.basename(mfile))[0] if modname == "__main__" else modname
        if n in OVERRIDE_ALLOWED and mod is not None and g is vars(mod):
            try:
                want = _screened_script(n)
            except PermissionError:
                want = None
            ok = bool(want) and os.path.realpath(fn) == want and os.path.realpath(mfile) == want
    if not ok:
        raise Refused(f"num_predict override {value!r} from {label} is not a hard-coded allowlisted call site; refused")
    good, why = _tracked_and_clean(want)
    if not good:
        try:
            _log_override_refused(label, value, hid, why)
        except Exception:
            pass   # the call is refused either way
        raise Refused(f"num_predict override {value!r} from {label} is refused: {why}; no model call")
    _flow().log_cap_override(label, value, bot=hid)


def _clamp(hid, num_predict):
    """Central clamp for every run.run / run.complete: role_cap, then min with settings (or the
    explicit override if given). Invalid ids and missing caps are Refused before any model call."""
    return _flow().token_cap(hid, num_predict)


def _after_clamp(hid, cap):
    """Hard fail on a cap hit: log cap_hits.jsonl, then Refused; never a retry."""
    _flow().raise_if_cap_hit(hid, cap)


def run(bid, text, outside=False, timeout=300, review_of=None, fmt=None, model=None, num_predict=None, keep_alive=None):
    """review_of: (CSO chain) the S bot's one-word verdict, shown to the assistant for review.
    fmt: JSON schema for Ollama structured output (assistant Section 5 reports).
    model: optional override used only for a documented reviewer fallback; default is settings.json.
    keep_alive: default KEEP_ALIVE (0, CTO cost check); never stops or unloads another model."""
    prompt, s = load(bid)
    if outside and not s["outside_content"]:
        raise PermissionError(f"{bid} may not read outside content (only ASO/SSO)")
    if s["tools"]: raise PermissionError("bots run with no tools")
    if review_of is not None and not outside: raise PermissionError("review_of is only for the CSO screening chain")
    use_model = model or s["model"]
    force = os.environ.get("GB_FORCE_MODEL_LOAD_FAILURE", "").strip()
    if force and use_model == force:
        raise ModelLoadError(use_model, f"forced load failure (GB_FORCE_MODEL_LOAD_FAILURE={force})")
    if num_predict is not None:
        _require_override(bid, num_predict)
    cap = _clamp(bid, s["num_predict"] if num_predict is None else num_predict)  # role_cap, then min with this call's settings or override
    ask = "One word: SAFE, SUSPECT, or BLOCK?"
    if review_of is not None:
        ask = (f"Your S-suite worker classified this data as: {review_of}. Review that verdict against the data yourself. "
               "Reply with your final verdict, one word: SAFE, SUSPECT, or BLOCK?")
    user = ("<<<DATA\n" + text[:12000] + "\nDATA>>>\n" + ask) if outside else text
    payload = {"model": use_model, "stream": False, "options": {"temperature": s["temperature"], "num_predict": cap},
               "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": user}]}
    if fmt is not None: payload["format"] = fmt
    out = _chat(use_model, payload, timeout, keep_alive=keep_alive, bot=bid)
    _after_clamp(bid, cap)
    return out


KEEP_ALIVE = 0            # CTO relay cost check: one model resident at a time
GATE_COMPUTE_S = 62.6     # CTO gates per call
GATE_WARM_LOAD_S = 30.0
ON_CALL = None            # flow.py sets this to log each call to route_log
_SEEN = set()             # models loaded at least once in this process (first load = cold, exempt from the load gate)
LAST_CALL = {}   # E2: eval_count / eval tokens per s / load_s of the most recent model call (in-process)


def _ps():
    """Loaded models (Ollama /api/ps); None if unreadable. Read-only."""
    try:
        return sorted(m.get("name") or m.get("model") for m in
                      json.load(urllib.request.urlopen(URL + "/api/ps", timeout=5)).get("models") or [])
    except Exception:
        return None


def _log_call(path, model, data, before, after, wall_s):
    """3c pilot: one JSONL line per model call. load_s (reload time) is its own field; evicted = models
    loaded before the call that are gone after it, other than this call's model."""
    ns = lambda k: round((data.get(k) or 0) / 1e9, 3)
    evicted = sorted(set(before or []) - set(after or []) - {model}) if before is not None and after is not None else None
    rec = dict(ts=time.strftime("%Y-%m-%d %H:%M:%S %Z"), model=model, load_s=ns("load_duration"),
               total_s=ns("total_duration"), eval_s=ns("eval_duration"), eval_count=data.get("eval_count"),
               eval_tps=round(data["eval_count"] / ns("eval_duration"), 2) if data.get("eval_count") and ns("eval_duration") else None, wall_s=wall_s,
               loaded_before=before, loaded_after=after, evicted=evicted)
    try:
        with open(path, "a") as fh: fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def call_metrics(model, data, before, after, cold):
    """load_s, compute_s (total minus load), evicted (loaded before, gone after, not this model) and the CTO gates."""
    ns = lambda k: (data.get(k) or 0) / 1e9
    load_s = round(ns("load_duration"), 3)
    compute_s = round(max(ns("total_duration") - ns("load_duration"), 0.0), 3)
    evicted = sorted(set(before) - set(after) - {model}) if before is not None and after is not None else None
    return dict(load_s=load_s, compute_s=compute_s, evicted=evicted, evictions=len(evicted) if evicted is not None else None,
                cold=cold, gate_compute="PASS" if compute_s <= GATE_COMPUTE_S else "FAIL",
                gate_load="EXEMPT (cold)" if cold else ("PASS" if load_s <= GATE_WARM_LOAD_S else "FAIL"))


def _chat(model, payload, timeout=300, keep_alive=None, bot=None):
    """POST /api/chat with keep_alive (default 0). Never stops or unloads another model."""
    payload = dict(payload, keep_alive=KEEP_ALIVE if keep_alive is None else keep_alive)
    body = json.dumps(payload).encode()
    try:
        with ollama_lock.hold():  # C12: every model call holds <repo>/gb_state/ollama.lock
            log = os.environ.get("GB_OLLAMA_CALL_LOG")
            before = _ps()
            t0 = time.time()
            r = urllib.request.urlopen(urllib.request.Request(URL + "/api/chat", body, {"Content-Type": "application/json"}), timeout=timeout)
            data = json.load(r)
            after = _ps()
            cold = model not in _SEEN; _SEEN.add(model)
            ev_raw, ed = data.get("eval_count"), (data.get("eval_duration") or 0) / 1e9
            if isinstance(ev_raw, bool) or not isinstance(ev_raw, int) or ev_raw < 0:  # missing/odd/negative eval_count: unknown, fail closed
                raise _flow().Refused("eval_count missing, negative or not an int; no cap check possible, call refused")
            ev = ev_raw
            LAST_CALL.clear(); LAST_CALL.update(model=model, bot=bot, eval_count=ev, eval_tps=round(ev / ed, 2) if ed else None,
                                                keep_alive=payload["keep_alive"], **call_metrics(model, data, before, after, cold))
            if log:
                _log_call(log, model, data, before, after, round(time.time() - t0, 3))
            if ON_CALL:
                try: ON_CALL(dict(LAST_CALL))
                except Exception: pass   # logging never breaks a model call
            return data["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = str(e)
        if e.code == 404 or LOAD_FAIL_RE.search(detail or ""):
            raise ModelLoadError(model, f"HTTP {e.code}: {detail}") from e
        raise


def unload(model, timeout=30):
    """CTO: unload THIS model only (POST /api/chat, empty messages, keep_alive 0) under ollama.lock.
    Used by relay run-batch's finally block when a model group fails or exits early. Never touches another model.
    Returns True if Ollama accepted it."""
    body = json.dumps(dict(model=model, messages=[], keep_alive=0)).encode()
    try:
        with ollama_lock.hold():
            urllib.request.urlopen(urllib.request.Request(URL + "/api/chat", body, {"Content-Type": "application/json"}), timeout=timeout).read()
        return True
    except Exception:
        return False


def complete(model, prompt, timeout=300, num_predict=None, temperature=0, keep_alive=None):
    """Single-prompt call with an explicit model (no bot prompt). Used for the independent
    reviewer check. keep_alive default 0; never stops or unloads another model.
    num_predict default (None) is the reviewer role cap 160; an explicit value is an override
    and is allowed only from OVERRIDE_ALLOWED call sites, then clamped to min(role, override)."""
    force = os.environ.get("GB_FORCE_MODEL_LOAD_FAILURE", "").strip()
    if force and model == force:
        raise ModelLoadError(model, f"forced load failure (GB_FORCE_MODEL_LOAD_FAILURE={force})")
    if num_predict is not None:
        _require_override("reviewer", num_predict)
    cap = _clamp("reviewer", 160 if num_predict is None else num_predict)  # reviewer role cap 160; tighter overrides stay lower
    payload = {"model": model, "stream": False,
               "options": {"temperature": temperature, "num_predict": cap},
               "messages": [{"role": "user", "content": prompt}]}
    out = _chat(model, payload, timeout, keep_alive=keep_alive, bot="reviewer")
    _after_clamp("reviewer", cap)
    return out

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("bot"); p.add_argument("-f", "--file", default="-"); p.add_argument("--outside", action="store_true")
    a = p.parse_args()
    text = sys.stdin.read() if a.file == "-" else open(a.file).read()
    try: print(run(a.bot, text, a.outside))
    except PermissionError as e: print(f"REFUSED: {e}", file=sys.stderr); sys.exit(2)
    except ollama_lock.LockTimeout as e: print(f"LOCAL_FAIL: {e}", file=sys.stderr); sys.exit(4)
    except OSError as e: print(f"PTO: Ollama unavailable ({type(e).__name__})", file=sys.stderr); sys.exit(3)
