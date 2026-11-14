"""FI-pytest-log-guards: flow.log_model_call, flow.log_cap_override and
relay._log always write their route_log record; PYTEST_CURRENT_TEST can no longer suppress the audit trail.
tmp GB_STATE only; no models."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import importlib, inspect, json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/bots")


def _log(st):
    p = st / "flow" / "route_log.jsonl"
    return [json.loads(l) for l in open(p)] if p.exists() else []


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path / "s"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "spoofed by a local process")
    return tmp_path / "s"


def test_log_model_call_writes_with_pytest_env_set(st):
    import flow
    flow.log_model_call(dict(bot="SAO", model="phi4-mini", eval_count=3), task="t1")
    recs = _log(st); assert [r["kind"] for r in recs] == ["model_call"] and recs[0]["bot"] == "SAO" and recs[0]["hash"]


def test_log_cap_override_writes_with_pytest_env_set(st):
    import flow
    flow.log_cap_override("flow:s_run", 160, bot="SAO")
    recs = _log(st); assert recs[-1]["kind"] == "cap_override" and recs[-1]["value"] == 160 and recs[-1]["caller"] == "flow:s_run"


def test_relay_log_writes_with_pytest_env_set(st):
    import relay
    relay._log("resident_check", phase="S", next_model="phi4-mini", resident=[], max_resident=1, result="PASS", why=None)
    recs = _log(st); assert recs[-1]["kind"] == "resident_check" and recs[-1]["source"] == "relay"


def test_route_log_chain_still_verifies(st):
    import flow, relay, route_chain
    flow.log_model_call(dict(bot="SAO"), task="t"); flow.log_cap_override("flow:s_run", 160, bot="SAO")
    relay._log("group_unload", model="phi4-mini")
    ok, msg, _ = route_chain.verify(str(st / "flow" / "route_log.jsonl")); assert ok, msg


def test_conftest_points_gb_state_at_tmp_when_unset(tmp_path):
    assert os.environ.get("GB_STATE") and os.environ["GB_STATE"] != GB_ROOT + "/gb_state"
    assert os.path.realpath(os.environ["GB_STATE"]).startswith(os.path.realpath(str(tmp_path)))


def test_no_pytest_env_check_left_in_the_three_writers():
    import flow, relay
    for fn in (flow.log_model_call, flow.log_cap_override, relay._log):
        assert "PYTEST_CURRENT_TEST" not in inspect.getsource(fn), fn.__name__
    for path in (GB_ROOT + "/scripts/flow.py", GB_ROOT + "/scripts/relay.py"):
        assert "PYTEST_CURRENT_TEST" not in open(path).read(), path
