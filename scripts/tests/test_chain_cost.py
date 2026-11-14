"""CTO relay cost check, chain code: (1) keep_alive 0 on every chain call, (4) load_s,
compute_s, evictions and gates per call in route_log. Ollama HTTP is mocked; tmp GB_STATE/GB_BOTS only."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import importlib, io, json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/bots")


class Fake:
    """Mock Ollama: /api/ps returns `resident`; /api/chat records model + keep_alive and returns timing."""
    def __init__(self):
        self.calls, self.resident, self.timing, self.on_chat = [], [], {}, None

    def __call__(self, req, timeout=None):
        url = getattr(req, "full_url", str(req))
        if url.endswith("/api/ps"):
            return io.BytesIO(json.dumps({"models": [{"name": m} for m in self.resident]}).encode())
        body = json.loads(req.data)
        if not body["messages"]:                                  # run.unload: empty messages + keep_alive 0
            self.calls.append((body["model"], body["keep_alive"], "<unload>")); self.resident = []
            return io.BytesIO(json.dumps({"message": {"content": ""}, "done_reason": "unload"}).encode())
        if self.on_chat: self.on_chat(body)
        self.calls.append((body["model"], body["keep_alive"], body["messages"][-1]["content"][:40]))
        load, comp = self.timing.get(len(self.calls), (1.0, 5.0))
        self.resident = [] if body["keep_alive"] == 0 else [body["model"]]
        text = "VERDICT: PASS\nREASON: ok" if body["messages"][0]["role"] == "user" else "ok reply"
        return io.BytesIO(json.dumps({"message": {"content": text}, "load_duration": int(load * 1e9),
                                      "total_duration": int((load + comp) * 1e9), "eval_count": 10, "eval_duration": int(comp * 1e9)}).encode())


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s")); monkeypatch.setenv("GB_OLLAMA_LOCK", str(tmp_path / "ollama.lock"))
    monkeypatch.delenv("GB_OLLAMA_CALL_LOG", raising=False)
    import run, flow; importlib.reload(flow)
    fake = Fake(); monkeypatch.setattr(run.urllib.request, "urlopen", fake); monkeypatch.setattr(run, "_SEEN", set())
    return run, flow, fake, tmp_path


def calls_log(tmp):
    p = tmp / "s" / "flow" / "route_log.jsonl"
    return [json.loads(l) for l in open(p) if '"model_call"' in l] if p.exists() else []


def test_keep_alive_0_on_every_chain_call(env):
    run, flow, fake, tmp = env
    run.run("STO", "s work"); run.run("ATO", "a review"); run.complete("llama3.2:3b", "reviewer check")
    assert [ka for _, ka, _ in fake.calls] == [0, 0, 0] and run.KEEP_ALIVE == 0


def test_route_log_load_compute_evictions_and_gates(env):
    run, flow, fake, tmp = env
    fake.resident = ["granite3.3:2b"]                      # another model resident before call 1 -> evicted
    fake.timing = {1: (12.0, 20.0), 2: (31.0, 5.0), 3: (2.0, 70.0)}
    run.run("STO", "one"); run.run("STO", "two"); run.run("STO", "three")
    rec = calls_log(tmp)
    assert len(rec) == 3 and all({"load_s", "compute_s", "evictions", "gate_compute", "gate_load"} <= set(r) for r in rec)
    assert rec[0]["load_s"] == 12.0 and rec[0]["compute_s"] == 20.0 and rec[0]["evicted"] == ["granite3.3:2b"] and rec[0]["evictions"] == 1
    assert rec[0]["cold"] is True and rec[0]["gate_load"] == "EXEMPT (cold)" and rec[0]["gate_compute"] == "PASS"
    assert rec[1]["gate_load"] == "FAIL" and rec[1]["evictions"] == 0                 # warm load 31 s > 30 s
    assert rec[2]["gate_compute"] == "FAIL" and rec[2]["gate_load"] == "PASS"         # compute 70 s > 62.6 s
    assert rec[0]["bot"] == "STO" and rec[0]["keep_alive"] == 0


