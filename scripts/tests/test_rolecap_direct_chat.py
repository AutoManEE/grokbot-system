"""FI-rolecap-direct-chat: cso_screen.aso() no longer calls /api/chat itself; it goes
through bots/run.py complete(), so token_cap, the override allowlist and the cap_override route_log apply.
No network, no models."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import io, json, os, sys, inspect, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/bots")


def test_aso_source_has_no_direct_api_chat():
    import cso_screen
    src = inspect.getsource(cso_screen.aso)
    assert "/api/chat" not in src and "urlopen" not in src and "botrun.complete(" in src


def test_aso_goes_through_run_complete_with_cap_4(monkeypatch, tmp_path):
    import cso_screen, run
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
    seen = []
    def fake_complete(model, prompt, timeout=300, num_predict=None, temperature=0, keep_alive=None):
        seen.append((model, num_predict, prompt)); return "SAFE"
    monkeypatch.setattr(run, "complete", fake_complete)
    assert cso_screen.aso("hello", "phi4-mini", "http://127.0.0.1:9") == ("SAFE", "aso:SAFE")
    m, n, p = seen[0]
    assert m == "phi4-mini" and n == 4 and p.startswith(cso_screen.ASO_SYSTEM) and "<<<DATA\nhello\nDATA>>>" in p


def test_aso_real_clamp_logs_cap_override_and_cap(monkeypatch, tmp_path):
    """Through the real run.complete: the payload num_predict is min(reviewer 160, 4) = 4 and route_log gets a
    cap_override line from cso_screen:aso."""
    import cso_screen, run
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
    bodies = []
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout=None):
        u = req if isinstance(req, str) else req.full_url
        if u.endswith("/api/ps"): return R(json.dumps({"models": []}).encode())
        bodies.append(json.loads(req.data)); return R(json.dumps({"message": {"content": "SUSPECT"}, "eval_count": 1}).encode())
    monkeypatch.setattr(run.urllib.request, "urlopen", fake_urlopen)
    assert cso_screen.aso("hello", "phi4-mini")[0] == "SUSPECT"
    assert bodies and bodies[0]["options"]["num_predict"] == 4 and bodies[0]["keep_alive"] == 0
    p = tmp_path / "s" / "flow" / "route_log.jsonl"
    if p.exists():   # written once FI-pytest-log-guards is in (no pytest skip); before it the guard skips it
        recs = [json.loads(l) for l in open(p)]
        assert any(r["kind"] == "cap_override" and r["caller"] == "cso_screen:aso" and r["value"] == 4 for r in recs)


@pytest.mark.parametrize("ev", [4, 9])
def test_aso_cap_hit_blocks(monkeypatch, tmp_path, ev):
    import cso_screen, run
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(run.urllib.request, "urlopen",
                        lambda req, timeout=None: R(json.dumps({"models": []} if str(getattr(req, "full_url", req)).endswith("/api/ps")
                                                               else {"message": {"content": "SAFE"}, "eval_count": ev}).encode()))
    assert cso_screen.aso("hello", "phi4-mini") == ("BLOCK", "aso:cap_hit")
    recs = [json.loads(l) for l in open(tmp_path / "s" / "budget" / "cap_hits.jsonl")]
    assert recs[-1]["cap"] == 4 and recs[-1]["eval_count"] == ev
