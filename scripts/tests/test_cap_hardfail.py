"""CTO / CSO cap-hardfail re-screen: a reply that hits the token cap is a hard fail, not a retry.
Cap comes from the task, else the bot's settings.json (CBO/ABO 384, SBO 160, AAO 384). Enforced in flow
delegate and in all three relay run_batch phases (S, A, reviewer). Models mocked; tmp GB_STATE/GB_BOTS only.
The relay portion sits on top of the reworked P1 relay."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests")
from test_delegate import GOOD, f, task   # noqa: F401
from test_relay_cost import relay_env   # noqa: F401
from test_chain_cost import env, calls_log   # noqa: F401


def _bots(flow, monkeypatch, tmp_path, **caps):
    b = tmp_path / "capbots"
    for hid, cap in caps.items():
        (b / hid).mkdir(parents=True, exist_ok=True)
        (b / hid / "settings.json").write_text(json.dumps(dict(id=hid, num_predict=cap, model="x", kind="assistant" if hid.startswith("A") else "s-suite")))
    monkeypatch.setenv("GB_BOTS", str(b))
    monkeypatch.setattr(flow, "BOTS", str(b))
    return b


def _capped(monkeypatch, run, targets):
    """After each real/fake run() of bid, set LAST_CALL.eval_count to targets[bid]."""
    orig = run.run
    seen = []
    run.LAST_CALL = {}
    def wrapped(bid, text, **kw):
        seen.append(bid)
        out = orig(bid, text, **kw)
        if bid in targets:
            run.LAST_CALL = dict(eval_count=targets[bid], bot=bid)
        else:
            run.LAST_CALL = dict(bot=bid)   # this call did not hit a cap; do not leak a prior count
        return out
    monkeypatch.setattr(run, "run", wrapped)
    return seen


# ---- flow: settings fallback for CBO / ABO / SBO / AAO ----
def test_cbo_abo_settings_cap_384_is_hard_fail(f, monkeypatch, tmp_path):
    """CBO's assistant is ABO (num_predict 384). Task passes num_predict=None; cap comes from settings."""
    import run
    _bots(f, monkeypatch, tmp_path, ABO=384, SBO=160)
    seen = _capped(monkeypatch, run, {"ABO": 384})
    with pytest.raises(f.Refused, match=r"token cap \(384\)"):
        f.delegate("CBO", task(f.tmp, text="Rank three tasks."), "cb-cap")
    assert seen.count("ABO") == 1 and not list((f.tmp / "reports").rglob("cb-cap*"))


def test_cbo_abo_just_under_cap_is_not_refused(f, monkeypatch, tmp_path):
    import run
    _bots(f, monkeypatch, tmp_path, ABO=384, SBO=160)
    _capped(monkeypatch, run, {"ABO": 383})
    r = f.delegate("CBO", task(f.tmp, text="Rank three tasks."), "cb-under")
    assert r["result"] == "PASS"


def test_sbo_settings_cap_160_is_hard_fail_before_a(f, monkeypatch, tmp_path):
    """SBO cap 160 fires after S, before ABO is called. Task gives no num_predict."""
    import run
    _bots(f, monkeypatch, tmp_path, ABO=384, SBO=160)
    seen = _capped(monkeypatch, run, {"SBO": 160})
    with pytest.raises(f.Refused, match=r"token cap \(160\)"):
        f.delegate("CBO", task(f.tmp, text="Rank three tasks."), "sb-cap")
    assert seen == ["SBO"] and "ABO" not in seen


def test_aao_settings_cap_384_is_hard_fail(f, monkeypatch, tmp_path):
    """CAO's assistant is AAO (num_predict 384)."""
    import run
    _bots(f, monkeypatch, tmp_path, AAO=384, SAO=1024)
    seen = _capped(monkeypatch, run, {"AAO": 384})
    with pytest.raises(f.Refused, match=r"token cap \(384\)"):
        f.delegate("CAO", task(f.tmp, text="Rank three tasks."), "aa-cap")
    assert seen.count("AAO") == 1


def test_settings_cap_used_when_last_call_lacks_num_predict(f, monkeypatch, tmp_path):
    """The previous patch failed open: LAST_CALL has no num_predict and a_np is None. Settings must still fire."""
    import run
    _bots(f, monkeypatch, tmp_path, ABO=384, SBO=160)
    orig = run.run
    run.LAST_CALL = {}
    def wrapped(bid, text, **kw):
        out = orig(bid, text, **kw)
        if bid == "ABO":
            run.LAST_CALL = dict(eval_count=5000, bot="ABO")   # no num_predict key
        else:
            run.LAST_CALL = dict(bot=bid)
        return out
    monkeypatch.setattr(run, "run", wrapped)
    with pytest.raises(f.Refused, match=r"token cap \(384\)"):
        f.delegate("CBO", task(f.tmp, text="Rank three tasks."), "cb-nokey")


# ---- relay run_batch phases (on reworked P1 relay) ----
def _put_settings(tmp, hid, cap, kind, model):
    d = tmp / "bots" / hid
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps(dict(id=hid, kind=kind, model=model, num_predict=cap, s_bots=None)))


def _bump(run, monkeypatch, targets):
    """targets: bid or 'reviewer' -> eval_count to write into LAST_CALL after the call."""
    orig_run, orig_c = run.run, run.complete
    def wrap_run(bid, *a, **k):
        out = orig_run(bid, *a, **k)
        if bid in targets:
            run.LAST_CALL = dict(run.LAST_CALL or {}, eval_count=targets[bid])
        return out
    def wrap_c(*a, **k):
        out = orig_c(*a, **k)
        if "reviewer" in targets:
            run.LAST_CALL = dict(run.LAST_CALL or {}, eval_count=targets["reviewer"])
        return out
    monkeypatch.setattr(run, "run", wrap_run)
    monkeypatch.setattr(run, "complete", wrap_c)


def test_relay_sbo_cap_160_hard_fail_s_phase(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    _put_settings(tmp, "ABO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "AFO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "SBO", 160, "s-suite", "phi4-mini")
    mid = relay.send("AFO", "ABO", "Is budget line 1 in the right format?", "CBO")["id"]
    relay.route("ABO")
    _bump(run, monkeypatch, {"SBO": 160})
    n = len(fake.calls)
    out = relay.run_batch()
    assert mid in out["refused"] and "token cap (160)" in out["refused"][mid]["why"]
    assert mid not in out["results"]
    # S ran once; A and reviewer did not run for this handoff
    models = [m for m, _, _ in fake.calls[n:]]
    assert models.count("phi4-mini") == 1 and "qwen3:4b-instruct" not in models and "llama3.2:3b" not in models


def test_relay_abo_cap_384_hard_fail_a_phase(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    _put_settings(tmp, "ABO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "AFO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "SBO", 160, "s-suite", "phi4-mini")
    mid = relay.send("AFO", "ABO", "Is budget line 1 in the right format?", "CBO")["id"]
    relay.route("ABO")
    _bump(run, monkeypatch, {"ABO": 384})
    out = relay.run_batch()
    assert mid in out["refused"] and "token cap (384)" in out["refused"][mid]["why"] and "A reply" in out["refused"][mid]["why"]
    assert mid not in out["results"]


def test_relay_aao_cap_384_hard_fail_a_phase(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    _put_settings(tmp, "AAO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "ABO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "SAO", 1024, "s-suite", "phi4-mini")
    mid = relay.send("ABO", "AAO", "Is the config note clear?", "CBO")["id"]
    relay.route("AAO")
    _bump(run, monkeypatch, {"AAO": 384})
    out = relay.run_batch()
    assert mid in out["refused"] and "token cap (384)" in out["refused"][mid]["why"]
    assert mid not in out["results"]


def test_relay_reviewer_cap_160_hard_fail(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    _put_settings(tmp, "ABO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "AFO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "SBO", 160, "s-suite", "phi4-mini")
    mid = relay.send("AFO", "ABO", "Is budget line 1 in the right format?", "CBO")["id"]
    relay.route("ABO")
    _bump(run, monkeypatch, {"reviewer": 160})
    out = relay.run_batch()
    assert mid in out["refused"] and "token cap (160)" in out["refused"][mid]["why"] and "reviewer" in out["refused"][mid]["why"]
    assert mid not in out["results"]


def test_relay_under_cap_still_completes(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    _put_settings(tmp, "ABO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "AFO", 384, "assistant", "qwen3:4b-instruct")
    _put_settings(tmp, "SBO", 160, "s-suite", "phi4-mini")
    mid = relay.send("AFO", "ABO", "Is budget line 1 in the right format?", "CBO")["id"]
    relay.route("ABO")
    _bump(run, monkeypatch, {"SBO": 159, "ABO": 383, "reviewer": 159})
    out = relay.run_batch()
    assert mid in out["results"] and out["results"][mid]["status"] == "DONE" and mid not in out["refused"]
