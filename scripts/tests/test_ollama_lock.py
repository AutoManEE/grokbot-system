"""C12: in-process Ollama lock (bots/ollama_lock.py) in run.py and flow.py."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import importlib, json, os, subprocess, sys, time
import pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/bots")
import ollama_lock as ol

PY = sys.executable
HOLD_SNIPPET = ("import sys,time; sys.path.insert(0," + repr(GB_ROOT + '/bots') + "); import ollama_lock as ol\n"
                "with ol.hold(timeout=float(sys.argv[2])) as m:\n"
                "    t0=time.time(); time.sleep(float(sys.argv[1])); print(m, t0, time.time(), flush=True)\n")


@pytest.fixture
def lockfile(tmp_path, monkeypatch):
    p = tmp_path / "ollama.lock"
    monkeypatch.setenv("GB_OLLAMA_LOCK", str(p))
    return p


def test_env_never_moves_lock_outside_test_mode(monkeypatch, tmp_path):      # #36 C12, probe p_c12
    monkeypatch.setenv("GB_OLLAMA_LOCK", str(tmp_path / "x.lock")); monkeypatch.setenv("GB_STATE", str(tmp_path))
    monkeypatch.delenv("GB_OLLAMA_LOCK_TEST")
    assert ol.path() == GB_ROOT + "/gb_state/ollama.lock"
    monkeypatch.setenv("GB_OLLAMA_LOCK_TEST", "1"); monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert ol.path() == GB_ROOT + "/gb_state/ollama.lock"                      # flag alone, outside pytest


def test_cso_screen_aso_holds_lock(lockfile, monkeypatch):
    import cso_screen
    seen = {}
    class R:
        def read(self): return json.dumps({"message": {"content": "SAFE"}, "eval_count": 1}).encode()
    def fake(req, timeout=0):
        seen["held"] = ol.held(); return R()
    monkeypatch.setattr(cso_screen.urllib.request, "urlopen", fake)
    monkeypatch.setattr(cso_screen.json, "load", lambda r: json.loads(r.read()))
    assert cso_screen.aso("hi", "m", "http://x")[0] == "SAFE" and seen["held"] is True and not ol.held()


def test_default_path_follows_gb_state(monkeypatch, tmp_path):
    monkeypatch.delenv("GB_OLLAMA_LOCK", raising=False)
    monkeypatch.setenv("GB_STATE", str(tmp_path))
    assert ol.path() == str(tmp_path / "ollama.lock")
    monkeypatch.delenv("GB_STATE")
    assert ol.path() == GB_ROOT + "/gb_state/ollama.lock"


def test_run_chat_holds_lock(lockfile, monkeypatch):
    import run
    seen = {}
    class R:
        def read(self): return json.dumps({"message": {"content": "ok"}, "eval_count": 1}).encode()
    def fake_urlopen(req, timeout=0):
        seen["held"] = ol.held(); seen["holders"] = ol.holder_pids(str(lockfile))
        return R()
    monkeypatch.setattr(run.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(run.json, "load", lambda r: json.loads(r.read()))
    assert run._chat("m", {"model": "m"}) == "ok"
    assert seen["held"] is True and os.getpid() in seen["holders"]
    assert not ol.held()


def test_reentrant_no_deadlock(lockfile):
    with ol.hold(timeout=1) as m1:
        with ol.hold(timeout=1) as m2:
            assert m1 == "own" and m2 == "own" and ol.held()
    assert not ol.held()


def test_external_flock_parent_is_not_a_deadlock(lockfile):
    out = subprocess.run(["flock", str(lockfile), PY, "-c", HOLD_SNIPPET, "0", "3"],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[0] == "ancestor"


def test_other_holder_times_out(lockfile):
    holder = subprocess.Popen(["flock", str(lockfile), "sleep", "3"])
    try:
        time.sleep(0.4)
        with pytest.raises(ol.LockTimeout):
            with ol.hold(timeout=0.5):
                pass
    finally:
        holder.kill(); holder.wait()


def test_two_overlapping_callers_serialize(lockfile):
    a = subprocess.Popen([PY, "-c", HOLD_SNIPPET, "0.8", "10"], stdout=subprocess.PIPE, text=True)
    time.sleep(0.1)
    b = subprocess.Popen([PY, "-c", HOLD_SNIPPET, "0.8", "10"], stdout=subprocess.PIPE, text=True)
    ra = a.communicate(timeout=20)[0].split(); rb = b.communicate(timeout=20)[0].split()
    assert ra[0] == rb[0] == "own"
    (s1, e1), (s2, e2) = sorted([(float(ra[1]), float(ra[2])), (float(rb[1]), float(rb[2]))])
    assert s2 >= e1 - 0.01  # second run starts only after the first released


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); monkeypatch.delenv("GB_OLLAMA_LOCK", raising=False)
    import flow as f; importlib.reload(f); return f


def test_flow_delegate_holds_lock_for_whole_chain(flow, monkeypatch, tmp_path):
    seen = {}
    def fake(c, path, t, mode, from_src):
        seen["held"] = ol.held(); seen["path"] = ol.path(); return {"ok": True}
    monkeypatch.setattr(flow, "_delegate", fake)
    assert flow.delegate("CAO", "x.json", "t1") == {"ok": True}
    assert seen["held"] and seen["path"] == str(tmp_path / "ollama.lock")


def test_flow_lock_timeout_is_local_fail_not_pto(flow, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise ol.LockTimeout("busy")
    monkeypatch.setattr(flow, "_delegate", boom)
    rec = []
    monkeypatch.setattr(flow, "record_event", lambda *a: rec.append(a))
    with pytest.raises(flow.LocalFail) as e:
        flow.delegate("CAO", "x.json", "t2")
    assert "lock_timeout" in str(e.value) and rec[0][0] == "LOCAL_FAIL"


def test_held_role_refused_before_waiting_on_lock(flow, monkeypatch, tmp_path):
    import alignment_check as ac
    import held_roles as hr
    p = tmp_path / "held.json"; p.write_text('{"holds": [], "cosigns": []}')
    monkeypatch.setattr(ac, "HELD_ROLES", p); hr.hold("CAO", "t", "CAO", p)
    monkeypatch.setattr(ol, "hold", lambda *a, **k: (_ for _ in ()).throw(AssertionError("lock taken")))
    with pytest.raises(flow.Refused):
        flow.delegate("CAO", "x.json", "t3")
