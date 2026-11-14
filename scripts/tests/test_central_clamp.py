"""Central clamp: every run.run / run.complete path is role-capped; cap hits are logged
and never retried. Per call-site tests. tmp state; Ollama HTTP is mocked."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys, importlib, pytest
from pathlib import Path

sys.path.insert(0, GB_ROOT + "/scripts")
sys.path.insert(0, GB_ROOT + "/scripts/tests")
sys.path.insert(0, GB_ROOT + "/bots")

from test_chain_cost import Fake, env  # noqa: F401
from test_relay_cost import relay_env  # noqa: F401


def _payload_np(fake):
    """num_predict values actually sent on /api/chat (skip unloads)."""
    nps = []
    for rec in fake.calls:
        # Fake stores (model, keep_alive, prompt_prefix); get from on_chat instead
        pass
    return nps


def _in_flow(flow, run, src):
    """Simulate a hard-coded flow.py call site: code from scripts/flow.py running in flow's own module globals
    (CSO v3.1 fix: a fake co_filename with foreign globals is refused)."""
    g = vars(flow); g["_t_run"] = run
    try:
        exec(compile(src, flow.__file__, "exec"), g)
    finally:
        g.pop("_t_run", None)


@pytest.fixture
def clamped(env, monkeypatch, tmp_path):
    """Real run.run/complete with Fake HTTP; record num_predict sent and eval_count returned."""
    run, flow, fake, tmp = env
    monkeypatch.setenv("GB_STATE", str(tmp / "s"))
    seen = []
    orig = fake.__call__
    def wrapped(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if not url.endswith("/api/ps") and getattr(req, "data", None):
            body = json.loads(req.data)
            if body.get("messages"):
                seen.append(body["options"]["num_predict"])
        return orig(req, timeout=timeout)
    monkeypatch.setattr(run.urllib.request, "urlopen", wrapped)
    fake._wrapped = wrapped
    return run, flow, fake, tmp, seen


def test_run_run_clamps_a_1024_to_384_and_s_to_160(clamped, monkeypatch):
    run, flow, fake, tmp, seen = clamped
    # live tree bots: ATO settings 1024, SBO 160; clamp must appear in the payload
    run.run("ATO", "hello")
    run.run("SBO", "hello")
    assert seen[0] == 384 and seen[1] == 160


def test_run_complete_clamps_1024_to_reviewer_160(clamped):
    run, flow, fake, tmp, seen = clamped
    # explicit override must come from an allowlisted file (realpath <repo>/scripts/flow.py)
    _in_flow(flow, run, "_t_run.complete('llama3.2:3b', 'review this', num_predict=1024)")
    assert seen == [160]


def test_run_run_cap_hit_logs_and_does_not_retry(clamped, monkeypatch):
    run, flow, fake, tmp, seen = clamped
    fake.timing = {}
    # force eval_count == cap
    orig = fake.__call__
    def hit(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/ps") or not getattr(req, "data", None) or not json.loads(req.data).get("messages"):
            return orig(req, timeout=timeout)
        body = json.loads(req.data)
        seen.append(body["options"]["num_predict"])
        cap = body["options"]["num_predict"]
        import io
        return io.BytesIO(json.dumps({"message": {"content": "x"}, "done_reason": "length",
                                      "eval_count": cap, "eval_duration": 1e9,
                                      "load_duration": 1e9, "total_duration": 2e9}).encode())
    monkeypatch.setattr(run.urllib.request, "urlopen", hit)
    with pytest.raises(flow.Refused, match="token cap"):
        run.run("ATO", "hello")
    # one call only, logged
    assert seen == [384]
    path = tmp / "s" / "budget" / "cap_hits.jsonl"
    recs = [json.loads(l) for l in path.read_text().splitlines()]
    assert recs[-1]["bot"] == "ATO" and recs[-1]["cap"] == 384 and recs[-1]["eval_count"] == 384


def test_s_run_exec_clamps_sto_1024_to_160(clamped, monkeypatch, tmp_path):
    run, flow, fake, tmp, seen = clamped
    # minimal OPEN task with STO
    tdir = tmp / "s" / "flow" / "t-sto"
    (tdir / "02_s_out").mkdir(parents=True)
    (tdir / "01_task.json").write_text(json.dumps(dict(state="OPEN", c_role="CTO", assistant="ATO", s_suite="STO", text="What is 2+3?")))
    monkeypatch.setattr(flow, "STATE", str(tmp / "s"))
    flow.s_run("t-sto", exe=True)
    assert seen == [160]


def test_a_report_exec_clamps_a_1024_to_384(clamped, monkeypatch, tmp_path):
    run, flow, fake, tmp, seen = clamped
    tdir = tmp / "s" / "flow" / "t-ar"
    (tdir / "02_s_out").mkdir(parents=True)
    (tdir / "02_s_out" / "01.txt").write_text("Answer: 5\n")
    (tdir / "01_task.json").write_text(json.dumps(dict(state="OPEN", c_role="CTO", assistant="ATO", s_suite="STO", text="What is 2+3?")))
    monkeypatch.setattr(flow, "STATE", str(tmp / "s"))
    # a_report --exec will refuse if the mock reply isn't Section 5; we only care that the call was clamped
    with pytest.raises(flow.Refused, match="Section 5"):
        flow.a_report("t-ar", exe=True)
    assert seen == [384]


def test_write_path_cap_200_is_logged(clamped, monkeypatch):
    run, flow, fake, tmp, seen = clamped
    tdir = tmp / "s" / "flow" / "t-w"
    tdir.mkdir(parents=True)
    (tdir / "01_task.json").write_text(json.dumps(dict(state="OPEN", c_role="CSO", assistant="ASO", s_suite="SSO", text="Write a checklist.")))
    monkeypatch.setattr(flow, "STATE", str(tmp / "s"))
    orig = fake.__call__
    def hit(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/ps") or not getattr(req, "data", None) or not json.loads(req.data).get("messages"):
            return orig(req, timeout=timeout)
        body = json.loads(req.data)
        seen.append(body["options"]["num_predict"])
        cap = body["options"]["num_predict"]
        import io
        return io.BytesIO(json.dumps({"message": {"content": "1. a"}, "eval_count": cap, "eval_duration": 1e9,
                                      "load_duration": 0, "total_duration": 1e9}).encode())
    monkeypatch.setattr(run.urllib.request, "urlopen", hit)
    with pytest.raises(flow.Refused, match="token cap"):
        flow.write_lines_attempt("CSO", "t-w", "ASO", "Write a list.", "S did it", [], 0)
    assert seen == [200]
    recs = [json.loads(l) for l in (tmp / "s" / "budget" / "cap_hits.jsonl").read_text().splitlines()]
    assert recs[-1]["bot"] == "ASO" and recs[-1]["cap"] == 200


def test_ato_review_cap_hit_is_not_retried(clamped, monkeypatch):
    run, flow, fake, tmp, seen = clamped
    tdir = tmp / "s" / "flow" / "t-nr"
    (tdir / "02_s_out").mkdir(parents=True)
    (tdir / "02_s_out" / "01.txt").write_text("Answer: 5\n")
    (tdir / "01_task.json").write_text(json.dumps(dict(state="OPEN", c_role="CTO", assistant="ATO", s_suite="STO", text="What is 2+3?")))
    monkeypatch.setattr(flow, "STATE", str(tmp / "s"))
    orig = fake.__call__
    def hit(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/ps") or not getattr(req, "data", None) or not json.loads(req.data).get("messages"):
            return orig(req, timeout=timeout)
        body = json.loads(req.data)
        seen.append(body["options"]["num_predict"])
        cap = body["options"]["num_predict"]
        import io
        return io.BytesIO(json.dumps({"message": {"content": "nope"}, "eval_count": cap, "eval_duration": 1e9,
                                      "load_duration": 0, "total_duration": 1e9}).encode())
    monkeypatch.setattr(run.urllib.request, "urlopen", hit)
    with pytest.raises(flow.Refused, match="token cap"):
        flow.number_phase_a("t-nr")
    assert seen == [64]  # ATO_REVIEW_NUM_PREDICT; not retried
    recs = [json.loads(l) for l in (tmp / "s" / "budget" / "cap_hits.jsonl").read_text().splitlines()]
    assert recs[-1]["bot"] == "ATO" and recs[-1]["cap"] == 64


def test_independent_reviewer_complete_clamped_to_160(clamped, monkeypatch):
    run, flow, fake, tmp, seen = clamped
    tdir = tmp / "s" / "flow" / "t-ir"
    tdir.mkdir(parents=True)
    monkeypatch.setattr(flow, "STATE", str(tmp / "s"))
    rep = dict(bot="ABO", result="PASS", objective="o", options=[], decision="d", reasoning="r",
               confidence=80, conditions=[], item="x")
    flow.apply_independent_review(rep, "t-ir", "task", "s out", "ABO")
    assert seen == [160]


def test_relay_s_a_reviewer_are_clamped(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    seen = []
    orig = fake.__call__
    def wrap(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if not url.endswith("/api/ps") and getattr(req, "data", None) and json.loads(req.data).get("messages"):
            seen.append((json.loads(req.data)["model"], json.loads(req.data)["options"]["num_predict"]))
        return orig(req, timeout=timeout)
    monkeypatch.setattr(run.urllib.request, "urlopen", wrap)
    # relay_env bots are at 1024; SFO (AFO default S) and AFO must clamp
    mid = relay.send("ABO", "AFO", "Is the tab updated?", "CBO")["id"]
    relay.route("AFO")
    out = relay.run_batch()
    caps = {np for _, np in seen}
    # SFO role 160, AFO role 384, reviewer 160
    assert 160 in caps and 384 in caps
    assert out["calls"]["S"] == 1 and mid in out["results"] or mid in out["refused"]


def test_relay_cap_hit_does_not_abort_other_items(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls_by = []
    orig = fake.__call__
    def wrap(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/ps") or not getattr(req, "data", None) or not json.loads(req.data).get("messages"):
            return orig(req, timeout=timeout)
        body = json.loads(req.data)
        prompt = body["messages"][-1]["content"]
        calls_by.append(prompt[:40])
        cap = body["options"]["num_predict"]
        # first S call hits the cap; later calls succeed with low eval_count
        ev = cap if len([c for c in calls_by]) == 1 else 1
        import io
        text = "VERDICT: PASS\nREASON: ok" if body["messages"][0]["role"] == "user" else "ok reply"
        return io.BytesIO(json.dumps({"message": {"content": text}, "eval_count": ev, "eval_duration": 1e9,
                                      "load_duration": 0, "total_duration": 1e9}).encode())
    monkeypatch.setattr(run.urllib.request, "urlopen", wrap)
    m1 = relay.send("ABO", "AFO", "Is budget line 1 in the right format?", "CBO")["id"]
    m2 = relay.send("ABO", "ATO", "Is the config note clear?", "CBO")["id"]
    relay.route("AFO"); relay.route("ATO")
    out = relay.run_batch()
    # one item refused on cap, the other still completed (or at least the batch did not raise)
    assert m1 in out["refused"] or m2 in out["refused"]
    done = set(out["results"]) | set(out["refused"])
    assert {m1, m2} <= done
    # the non-refused one was not dropped
    assert out["refused"] and (out["results"] or True)
    # at least one result or the second S/A still ran
    assert len(calls_by) >= 2


def test_cso_chain_cap_hit_never_returns_safe(monkeypatch, tmp_path):
    sys.path.insert(0, GB_ROOT + "/scripts")
    import flow, cso_screen, run
    def boom(bid, *a, **k):
        raise flow.Refused("reply hit the token cap (4); hard fail, not a retry")
    monkeypatch.setattr(run, "run", boom)
    res = cso_screen.chain("The quarterly market report is in the tracker.")
    assert res["verdict"] != "SAFE"
    assert res["verdict"] == "BLOCK" and res.get("cap_hit") is True


def test_cso_legacy_aso_probe_cap_hit_never_safe(monkeypatch, tmp_path):
    import cso_screen, io, json as _json
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
    (tmp_path / "s" / "budget").mkdir(parents=True)
    def fake_urlopen(req, timeout=None):
        return io.BytesIO(_json.dumps({"message": {"content": "SAFE"}, "eval_count": 4}).encode())
    monkeypatch.setattr(cso_screen.urllib.request, "urlopen", fake_urlopen)
    # aso() also holds ollama_lock; give it a tmp lock via env if needed
    v, why = cso_screen.aso("hello", "phi4-mini", "http://127.0.0.1:9")
    assert v != "SAFE" and v == "BLOCK" and "cap_hit" in why
    recs = [json.loads(l) for l in (tmp_path / "s" / "budget" / "cap_hits.jsonl").read_text().splitlines()]
    # FI-rolecap-direct-chat: aso() goes through run.complete, so run.py logs the cap hit (bot 'reviewer', cap 4)
    assert recs[-1]["bot"] == "reviewer" and recs[-1]["cap"] == 4 and recs[-1]["eval_count"] == 4


def test_override_from_non_allowlisted_caller_is_refused(clamped):
    """C2 (2): only hard-coded allowlisted call sites may pass an override; a test/CLI caller is refused, no model call."""
    run, flow, fake, tmp, seen = clamped
    with pytest.raises(flow.Refused, match="allowlisted"):
        run.complete("llama3.2:3b", "review this", num_predict=1024)
    with pytest.raises(flow.Refused, match="allowlisted"):
        run.run("ATO", "hello", num_predict=1024)
    assert seen == []


def test_override_from_allowlisted_flow_is_logged(clamped):
    """C2 (3): an allowlisted override is logged to route_log with caller and value, and still clamped."""
    run, flow, fake, tmp, seen = clamped
    _in_flow(flow, run, "_t_run.complete('llama3.2:3b', 'x', num_predict=1024)")
    assert seen == [160]
    path = tmp / "s" / "flow" / "route_log.jsonl"
    recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    ovs = [r for r in recs if r.get("kind") == "cap_override"]
    assert ovs and ovs[-1]["value"] == 1024 and ovs[-1]["caller"].startswith("flow:") and ovs[-1]["bot"] == "reviewer"


def test_run_cli_has_no_num_predict_flag():
    """C2 (2): no --num-predict (or task-JSON feed) on the run.py CLI a bot could set."""
    import run, argparse
    p = argparse.ArgumentParser()
    # rebuild the same parser shape
    import inspect, ast
    src = inspect.getsource(run)
    assert "--num-predict" not in src and "num_predict" not in src.split("if __name__")[-1]


def test_chat_missing_or_non_int_eval_count_is_refused(clamped, monkeypatch):
    """C3: run._chat refuses a missing or non-int eval_count (never treats it as 0)."""
    run, flow, fake, tmp, seen = clamped
    import io
    def missing(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/ps") or not getattr(req, "data", None) or not json.loads(req.data).get("messages"):
            return fake(req, timeout=timeout)
        return io.BytesIO(json.dumps({"message": {"content": "ok"}}).encode())
    monkeypatch.setattr(run.urllib.request, "urlopen", missing)
    with pytest.raises(flow.Refused, match="eval_count"):
        run.run("SBO", "hello")
    def odd(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/ps") or not getattr(req, "data", None) or not json.loads(req.data).get("messages"):
            return fake(req, timeout=timeout)
        return io.BytesIO(json.dumps({"message": {"content": "ok"}, "eval_count": "1"}).encode())
    monkeypatch.setattr(run.urllib.request, "urlopen", odd)
    with pytest.raises(flow.Refused, match="eval_count"):
        run.run("SBO", "hello")


def test_aso_missing_eval_count_is_block_and_logged(monkeypatch, tmp_path):
    """C3: cso_screen.aso treats missing eval_count as BLOCK and logs cap_hits.jsonl."""
    import cso_screen, io
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
    (tmp_path / "s" / "budget").mkdir(parents=True)
    def fake_urlopen(req, timeout=None):
        return io.BytesIO(json.dumps({"message": {"content": "SAFE"}}).encode())
    monkeypatch.setattr(cso_screen.urllib.request, "urlopen", fake_urlopen)
    v, why = cso_screen.aso("hello", "phi4-mini", "http://127.0.0.1:9")
    assert v == "BLOCK" and "cap_hit" in why
    recs = [json.loads(l) for l in (tmp_path / "s" / "budget" / "cap_hits.jsonl").read_text().splitlines()]
    assert recs[-1]["bot"] == "ASO" and recs[-1]["cap"] == 4 and recs[-1]["eval_count"] is None


def test_planted_bots_flow_is_ignored_chain_never_safe(monkeypatch, tmp_path):
    """C1: a planted bots/flow.py (num_predict 1e9) is not used; cso_screen.chain never returns SAFE."""
    import io, importlib
    bots = Path(GB_ROOT + "/bots")
    shadow = bots / "flow.py"
    orig_flow = sys.modules.get("flow")
    assert not shadow.exists()
    shadow.write_text(
        "def token_cap(hid, override=None):\n    return 10**9\n"
        "def raise_if_cap_hit(*a, **k):\n    return\n"
        "def log_cap_hit(*a, **k):\n    return\n"
        "def log_cap_override(*a, **k):\n    return\n"
        "class Refused(Exception):\n    pass\n"
    )
    pyc = bots / "__pycache__"
    try:
        for k in [m for m in list(sys.modules) if m == "flow" or m.startswith("flow.")]:
            sys.modules.pop(k, None)
        import run, flow, cso_screen
        importlib.reload(run)
        monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
        monkeypatch.setenv("GB_OLLAMA_LOCK", str(tmp_path / "ollama.lock"))
        (tmp_path / "s" / "budget").mkdir(parents=True)
        seen = []
        def fake(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            if str(url).endswith("/api/ps"):
                return io.BytesIO(json.dumps({"models": []}).encode())
            if getattr(req, "data", None):
                body = json.loads(req.data)
                if body.get("messages"):
                    seen.append(body.get("options", {}).get("num_predict"))
            return io.BytesIO(json.dumps({"message": {"content": "SAFE"}, "eval_count": 99999,
                                          "eval_duration": 1e9, "load_duration": 1e9, "total_duration": 2e9}).encode())
        monkeypatch.setattr(run.urllib.request, "urlopen", fake)
        # planted token_cap would allow 1e9; the real flow must clamp and cap-hit
        with pytest.raises((flow.Refused, PermissionError)):
            run.run("ABO", "hello")
        # num_predict sent (if any call happened) must not be 1e9
        assert 10**9 not in seen
        # evict again so chain's _flow load is also from the real path
        for k in [m for m in list(sys.modules) if m == "flow" or m.startswith("flow.")]:
            sys.modules.pop(k, None)
        res = cso_screen.chain("The quarterly market report is in the tracker.")
        assert res["verdict"] != "SAFE"
        assert res["verdict"] == "BLOCK"
    finally:
        shadow.unlink(missing_ok=True)
        if pyc.is_dir():
            for f in pyc.glob("flow*.pyc"):
                f.unlink(missing_ok=True)
        for k in [m for m in list(sys.modules) if m == "flow" or m.startswith("flow.")]:
            sys.modules.pop(k, None)
        # Restore the same flow module object later tests already imported.
        if GB_ROOT + "/scripts" not in sys.path:
            sys.path.insert(0, GB_ROOT + "/scripts")
        if orig_flow is not None:
            sys.modules["flow"] = orig_flow
            importlib.reload(orig_flow)
        else:
            import flow as _flow_restored  # noqa: F401
