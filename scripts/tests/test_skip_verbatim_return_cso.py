"""CSO r3: ruling (d) upper-case ANSWER: only, any
other answer-looking line in the note sends the reply to the model; returns survive a drain of the answered note.
Copy tree only; tmp GB_STATE/GB_BOTS, models mocked."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests")
from test_chain_cost import env, Fake  # noqa: F401
from test_relay_cost import relay_env  # noqa: F401
from test_relay import r, box  # noqa: F401
from test_skip_verbatim_return import _patch_models, _route_return, _audit, _dg, QUESTION


@pytest.mark.parametrize("note,reply", [
    ("RETURN\nQuestion: what is 2+3?\nAnswer: 5", "Answer: 5"),            # title case: model
    ("RETURN\nANSWER: 5\nAnswer: 6", "ANSWER: 5"),                          # second, title-case answer line
    ("RETURN\nANSWER: 5\nanswer: 6", "ANSWER: 5"),
    ("RETURN\nANSWER: 5\n  ANSWER: 6", "ANSWER: 5"),                        # indented second answer line
    ("RETURN\nANSWER: 5\nAnswer : 6", "ANSWER: 5"),
])
def test_non_exact_answer_shapes_go_to_model(relay_env, monkeypatch, note, reply):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, reply, calls)
    mid = _route_return(relay, text=note)
    out = relay.run_batch()
    assert not any(e["event"] == "SKIP_VERBATIM_RETURN" for e in _audit(tmp))
    assert out["calls"]["reviewer"] == 1 and len(calls) == 1 and out["results"][mid]["reviewer"] != "SKIP_VERBATIM_RETURN"


def test_upper_case_exact_still_skips(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    mid = _route_return(relay, text="RETURN\nQuestion: what is 2+3?\nANSWER: 5")
    out = relay.run_batch()
    assert out["results"][mid]["reviewer"] == "SKIP_VERBATIM_RETURN" and calls == []


def test_answer_lines_counts_every_case(r):
    assert len(r.answer_lines("ANSWER: 1\nAnswer: 2\nanswer: 3\n  ANSWER: 4\nNo answer here")) == 4
    assert r.ANSWER_LINE_RE.match("ANSWER: 5") and not r.ANSWER_LINE_RE.match("Answer: 5")


def test_return_still_allowed_after_the_answered_note_is_drained(r):
    if not hasattr(r, "drain"): pytest.skip("drain not in this tree")
    q = r.send("AFO", "ABO", QUESTION, "CBO")["id"]
    r.route("ABO")
    d = r.tmp / "s" / "relay" / "ABO" / "results"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{q}.json").write_text(json.dumps(dict(status="DONE", msg_id=q, verdict="PASS", **{"from": "ABO"})))
    r.audit("batch_result", msg_id=q, a="ABO", s="SBO", verdict="PASS")
    assert r.drain(a="ABO")["archived"] == 1
    mid = r.send("ABO", "AFO", "RETURN\nANSWER: 5", "CBO", reply_to=q, is_return=True)["id"]
    r.route("AFO")
    assert _dg(r.tmp, mid)["kind"] == "return"
