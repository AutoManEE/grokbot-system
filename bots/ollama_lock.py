"""ollama_lock.py (C12, CAO): every local model call holds flock on the Ollama lock.

Lock file <repo>/gb_state/ollama.lock (COO pattern). #36 C12: GB_OLLAMA_LOCK / GB_STATE move it only
in an explicit pytest test mode (GB_OLLAMA_LOCK_TEST=1 under pytest); cso_screen --aso
also hold it. bots/run.py takes it around every /api/chat call and flow.py takes it
around a whole delegate chain, so overlapping chains serialize. No external `flock`
wrapper is needed.

No deadlock when the caller already holds it:
  * re-entrant inside one process (a depth counter; flow.py holds it, run.py nests);
  * if an ANCESTOR process already holds the lock (for example the old pattern
    `flock <repo>/gb_state/ollama.lock python3 flow.py ...`), we detect that via
    /proc/locks and run under the caller's lock instead of waiting on ourselves.
Waiting is bounded (GB_OLLAMA_LOCK_TIMEOUT seconds, default 900); a timeout raises
LockTimeout, which flow.py records as LOCAL_FAIL (exit 4), never PTO.
Never evicts or unloads another model; this only serializes our own calls.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import contextlib, fcntl, os, threading, time

DEFAULT = GB_ROOT + "/gb_state/ollama.lock"
_state = threading.local()
_lock = threading.RLock()
_depth = 0
_fd = None
_mode = None  # "own" or "ancestor"


class LockTimeout(RuntimeError):
    pass


def test_mode():
    """#36 C12: only an explicit test flag inside a pytest run may move the lock (GB_OLLAMA_LOCK_TEST=1 AND
    PYTEST_CURRENT_TEST set). Model runs never follow GB_STATE or GB_OLLAMA_LOCK, so an env var cannot skip it."""
    return os.environ.get("GB_OLLAMA_LOCK_TEST") == "1" and bool(os.environ.get("PYTEST_CURRENT_TEST"))


def path():
    """<repo>/gb_state/ollama.lock. In test mode only: GB_OLLAMA_LOCK, else $GB_STATE/ollama.lock."""
    if not test_mode():
        return DEFAULT
    if os.environ.get("GB_OLLAMA_LOCK"):
        return os.environ["GB_OLLAMA_LOCK"]
    st = os.environ.get("GB_STATE")
    return os.path.join(st, "ollama.lock") if st else DEFAULT


def _ancestors():
    pids, pid = set(), os.getpid()
    for _ in range(64):
        try:
            with open(f"/proc/{pid}/stat") as f:
                data = f.read()
            ppid = int(data.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if ppid <= 1:
            break
        pids.add(ppid); pid = ppid
    return pids


def holder_pids(p=None):
    """PIDs listed in /proc/locks as holding a FLOCK on the lock file's inode."""
    p = p or path()
    try:
        st = os.stat(p)
        lines = open("/proc/locks").read().splitlines()
    except OSError:
        return set()
    out = set()
    for ln in lines:
        parts = ln.split()
        if "->" in parts:  # waiters
            continue
        try:
            i = parts.index("FLOCK")
            pid = int(parts[i + 3]); dev_ino = parts[i + 4]
        except (ValueError, IndexError):
            continue
        if int(dev_ino.rsplit(":", 1)[1]) == st.st_ino:
            out.add(pid)
    return out


def ancestor_holds(p=None):
    return bool(holder_pids(p) & _ancestors())


def held():
    return _depth > 0


@contextlib.contextmanager
def hold(timeout=None, poll=0.2):
    global _depth, _fd, _mode
    with _lock:
        if _depth > 0:
            _depth += 1
            try:
                yield _mode
            finally:
                _depth -= 1
            return
        p = path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
        if timeout is None:
            timeout = float(os.environ.get("GB_OLLAMA_LOCK_TIMEOUT", "900"))
        deadline = time.time() + timeout
        mode = None
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                mode = "own"; break
            except BlockingIOError:
                if ancestor_holds(p):
                    mode = "ancestor"; break
                if time.time() >= deadline:
                    os.close(fd)
                    raise LockTimeout(f"ollama lock {p} busy for {timeout:.0f}s (another model run holds it)")
                time.sleep(poll)
        _fd, _mode, _depth = fd, mode, 1
        try:
            yield mode
        finally:
            _depth = 0
            if mode == "own":
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd); _fd = None; _mode = None
