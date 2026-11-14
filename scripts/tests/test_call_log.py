"""3c pilot: run._chat logs reload time (load_s) as its own field and any eviction, when GB_OLLAMA_CALL_LOG is set."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import io, json, sys
sys.path.insert(0, GB_ROOT + "/bots")
import run


class _R(io.BytesIO):
    pass


def test_call_log_fields(monkeypatch, tmp_path):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("GB_OLLAMA_CALL_LOG", str(log))
    monkeypatch.setenv("GB_STATE", str(tmp_path))
    monkeypatch.setenv("GB_OLLAMA_LOCK", str(tmp_path / "ollama.lock"))
    ps = iter([["qwen3:4b-instruct", "granite3.3:2b"], ["phi4-mini:latest", "qwen3:4b-instruct"]])
    monkeypatch.setattr(run, "_ps", lambda: next(ps))
    body = {"message": {"content": "84"}, "load_duration": 2_500_000_000, "total_duration": 9_000_000_000, "eval_duration": 6_000_000_000, "eval_count": 1}
    monkeypatch.setattr(run.urllib.request, "urlopen", lambda *a, **k: _R(json.dumps(body).encode()))
    assert run._chat("phi4-mini:latest", {"model": "phi4-mini:latest"}) == "84"
    rec = json.loads(log.read_text().splitlines()[0])
    assert rec["load_s"] == 2.5 and rec["total_s"] == 9.0 and rec["model"] == "phi4-mini:latest"
    assert rec["evicted"] == ["granite3.3:2b"]


def test_no_log_without_env(monkeypatch, tmp_path):
    """No call-log file without GB_OLLAMA_CALL_LOG (ps is read for route_log evictions, read-only)."""
    monkeypatch.delenv("GB_OLLAMA_CALL_LOG", raising=False)
    monkeypatch.setenv("GB_OLLAMA_LOCK", str(tmp_path / "ollama.lock"))
    monkeypatch.setattr(run, "_ps", lambda: [])
    monkeypatch.setattr(run, "ON_CALL", None)
    monkeypatch.setattr(run.urllib.request, "urlopen", lambda *a, **k: _R(b'{"message": {"content": "ok"}, "eval_count": 1}'))
    assert run._chat("m", {}) == "ok" and not list(tmp_path.glob("*.jsonl"))
