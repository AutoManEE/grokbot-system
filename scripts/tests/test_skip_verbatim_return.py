"""Option Y v4 (CSO #option-y-v3, rebased on HEAD 7ef72f7): skip the model reviewer only for a trusted-return
verbatim ANSWER line. D1: a return needs reply_to = an audited direct note from to->frm, and reply_to is audited
(return_sent) and re-checked before any skip. D4: the reviewer prompt keeps the live wording and adds only the
required sentence. Copy tree only; tmp GB_STATE/GB_BOTS, models mocked."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json
import pathlib
import sys

import pytest

sys.path.insert(0, GB_ROOT + "/scripts")
sys.path.insert(0, GB_ROOT + "/scripts/tests")

import text_guard as g
from test_chain_cost import env, Fake  # noqa: F401
from test_relay_cost import relay_env  # noqa: F401
from test_relay import r, box  # noqa: F401

REQUIRED_SENTENCE = (
    "If the note is a RETURN with an ANSWER line, keeping that ANSWER line verbatim is a correct answer. "
    "Any private text in the reply is always FAIL."
)
LIVE_PROMPT_HEAD = "Note: {h['text']}\\nProposed reply: {a_out[h['msg_id']]}\\nDoes the reply answer the note with no private text? "
LIVE_PROMPT_TAIL = "Answer exactly two lines:\\nVERDICT: PASS or FAIL\\nREASON: one sentence"
REPLY_WITH_SECRET = "ANSWER: 5 otp 482193"
QUESTION = "Question for ABO: what is 2+3?"
NOTE = "RETURN\nTask id: CBO-T164R2\nQuestion: what is 2+3?\nANSWER: 5"
NOTE_TWO_ANSWERS = "RETURN\nANSWER: 5\nANSWER: 7"


def _relay_src():
    return (pathlib.Path(__file__).resolve().parents[1] / "relay.py").read_text()


def _audit(tmp):
    p = tmp / "s" / "relay" / "audit.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _dg(tmp, mid, a="AFO"):
    return json.loads((tmp / "s" / "relay" / a / "dg" / f"{mid}.json").read_text())


# ---------------- D4: reviewer wording ----------------
def test_d4_reviewer_prompt_is_live_wording_plus_required_sentence_only():
    src = _relay_src()
    i = src.index("    r_prompt = lambda h:")
    block = src[i:src.index("\n    skip_items", i)]
    lines = [l.strip() for l in block.splitlines()]
    assert lines[0] == 'r_prompt = lambda h: (f"' + LIVE_PROMPT_HEAD + '"'
    assert lines[1] == '"' + REQUIRED_SENTENCE + ' "'
    assert lines[2] == '"' + LIVE_PROMPT_TAIL + '")'
    assert len(lines) == 3
    assert "Judge only the reply text" not in src
    assert "Reply: {a_out" not in src and "Proposed reply: {a_out" in src


def test_d4_rendered_prompt_wording(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5 extra", calls)
    mid = _route_return(relay)
    relay.run_batch()
    assert len(calls) == 1
    assert calls[0] == (f"Note: {NOTE}\nProposed reply: ANSWER: 5 extra\nDoes the reply answer the note with no private text? "
                        + REQUIRED_SENTENCE + " Answer exactly two lines:\nVERDICT: PASS or FAIL\nREASON: one sentence")


# ---------------- D1: reply_to required and audited ----------------
def _question(rr, frm="AFO", to="ABO", text=QUESTION):
    return rr.send(frm, to, text, "CBO")["id"]


def test_d1_return_without_reply_to_refused(r):
    with pytest.raises(r.Refused, match="needs reply_to"):
        r.send("ABO", "AFO", NOTE, "CBO", is_return=True)
    assert box(r, "AFO") == []
    assert not any(e["event"] == "return_sent" for e in _audit(r.tmp))
    assert any(e["event"] == "refused" and e["op"] == "send" for e in _audit(r.tmp))


@pytest.mark.parametrize("bad", ["not-an-id", "20340113-061815-171032214-ABO-1a2b3c4d", "", None, 5])
def test_d1_reply_to_unknown_or_malformed_refused(r, bad):
    with pytest.raises(r.Refused, match="return refused"):
        r.send("ABO", "AFO", NOTE, "CBO", reply_to=bad, is_return=True)
    assert box(r, "AFO") == []


def test_d1_reply_to_wrong_direction_refused(r):
    q = _question(r, frm="ABO", to="AFO")          # ABO -> AFO, so ABO cannot "return" it to AFO
    with pytest.raises(r.Refused, match="not an audited note from AFO to ABO"):
        r.send("ABO", "AFO", NOTE, "CBO", reply_to=q, is_return=True)
    q2 = _question(r, frm="ATO", to="ABO")         # a note from ATO cannot be returned to AFO
    with pytest.raises(r.Refused, match="not an audited note from AFO to ABO"):
        r.send("ABO", "AFO", NOTE, "CBO", reply_to=q2, is_return=True)


def test_d1_reply_to_broadcast_refused(r):
    b = r.broadcast("AFO", "Weekly plan is ready.", "CBO")["id"]
    with pytest.raises(r.Refused, match="no audited direct note"):
        r.send("ABO", "AFO", NOTE, "CBO", reply_to=b, is_return=True)


def test_d1_reply_to_hand_placed_note_refused(r):
    _question(r)                                    # a real audited note exists (audit started)
    q = "20340113-061815-171032214-AFO-1a2b3c4d"
    d = r.tmp / "s" / "relay" / "ABO" / "inbox"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{q}.json").write_text(json.dumps(dict(id=q, ts="x", **{"from": "AFO"}, to="ABO", kind="direct", text=QUESTION, origin="CBO")))
    with pytest.raises(r.Refused, match="no audited direct note"):
        r.send("ABO", "AFO", NOTE, "CBO", reply_to=q, is_return=True)


def test_d1_one_return_per_note(r):
    q = _question(r)
    r.send("ABO", "AFO", NOTE, "CBO", reply_to=q, is_return=True)
    with pytest.raises(r.Refused, match="already has an audited return"):
        r.send("ABO", "AFO", NOTE, "CBO", reply_to=q, is_return=True)


def test_d1_is_return_must_be_bool(r):
    q = _question(r)
    with pytest.raises(r.Refused, match="True or False"):
        r.send("ABO", "AFO", NOTE, "CBO", reply_to=q, is_return="yes")


def test_d1_return_sent_audits_reply_to_and_route_sets_kind(r):
    q = _question(r)
    mid = r.send("ABO", "AFO", NOTE, "CBO", reply_to=q, is_return=True)["id"]
    rs = [e for e in _audit(r.tmp) if e["event"] == "return_sent"]
    assert len(rs) == 1 and rs[0]["msg_id"] == mid and rs[0]["reply_to"] == q
    assert rs[0]["from_a"] == "ABO" and rs[0]["to_a"] == "AFO"
    assert r._audited_returns()[mid] == ("ABO", "AFO", q)
    assert r.verify()[0]
    r.route("AFO")
    dg = _dg(r.tmp, mid)
    assert dg["kind"] == "return" and dg["reply_to"] == q


def test_d1_cli_is_return_needs_reply_to(r, capsys):
    rc = r.main(["send", "--from", "ABO", "--to", "AFO", "--text", NOTE, "--origin", "CBO", "--is-return"])
    assert rc == 2 and "needs reply_to" in capsys.readouterr().err
    q = _question(r)
    rc = r.main(["send", "--from", "ABO", "--to", "AFO", "--text", NOTE, "--origin", "CBO", "--is-return", "--reply-to", q])
    assert rc == 0
    assert any(e["event"] == "return_sent" and e["reply_to"] == q for e in _audit(r.tmp))


def test_plain_reply_to_without_is_return_is_not_a_return(r):
    q = _question(r)
    mid = r.send("ABO", "AFO", NOTE, "CBO", reply_to=q)["id"]
    r.route("AFO")
    assert _dg(r.tmp, mid).get("kind") != "return"
    assert mid not in r._audited_returns()


# ---------------- metadata, not note text ----------------
def test_note_claiming_return_without_metadata_has_no_kind_return(r):
    mid = r.send("ABO", "AFO", NOTE, "CBO")["id"]  # text says RETURN, no is_return
    r.route("AFO")
    assert _dg(r.tmp, mid).get("kind") != "return"
    assert mid not in r._audited_returns()


def test_inbox_is_return_flag_without_audit_has_no_kind_return(r):
    mid = r.send("ABO", "AFO", NOTE, "CBO")["id"]
    p = r.tmp / "s" / "relay" / "AFO" / "inbox" / f"{mid}.json"
    m = json.loads(p.read_text()); m["is_return"] = True; m["kind_hint"] = "return"; p.write_text(json.dumps(m))
    r.route("AFO")
    assert _dg(r.tmp, mid).get("kind") != "return"


# ---------------- run_batch ----------------
def _patch_models(monkeypatch, run, a_reply, complete_calls):
    def fake_run(bot, prompt, keep_alive=None, num_predict=None, **k):
        run.LAST_CALL = {"eval_count": 10}
        return "S checked the answer." if str(bot).startswith("S") else a_reply

    def fake_complete(model, prompt, keep_alive=None, num_predict=None, **k):
        complete_calls.append(prompt)
        run.LAST_CALL = {"eval_count": 10}
        return "VERDICT: PASS\nREASON: ok"

    monkeypatch.setattr(run, "run", fake_run)
    monkeypatch.setattr(run, "complete", fake_complete)


def _route_return(relay, text=NOTE, is_return=True):
    q = relay.send("AFO", "ABO", QUESTION, "CBO")["id"]
    mid = relay.send("ABO", "AFO", text, "CBO", reply_to=q, is_return=is_return)["id"]
    relay.route("AFO")
    return mid


def _assert_reviewed(out, mid, calls):
    assert out["calls"]["reviewer"] == 1 and len(calls) == 1
    assert out["results"][mid]["reviewer"] != "SKIP_VERBATIM_RETURN"
    assert out["results"][mid]["verdict"] == "PASS"
    assert REQUIRED_SENTENCE in calls[0]


def test_exact_match_skips_and_is_audited_and_counted(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    mid = _route_return(relay)
    q = _dg(tmp, mid)["reply_to"]
    out = relay.run_batch()
    assert out["calls"]["reviewer"] == 0 and calls == []
    res = out["results"][mid]
    assert res["status"] == "DONE" and res["reviewer"] == "SKIP_VERBATIM_RETURN"
    assert res["verdict"] == "SKIP_VERBATIM_RETURN" and res["reply"] == "ANSWER: 5"
    sk = [e for e in _audit(tmp) if e["event"] == "SKIP_VERBATIM_RETURN"]
    assert len(sk) == 1 and sk[0]["msg_id"] == mid and sk[0]["reply_to"] == q
    br = [e for e in _audit(tmp) if e["event"] == "batch_result" and e["msg_id"] == mid]
    assert br and br[0]["verdict"] == "SKIP_VERBATIM_RETURN"
    log = (tmp / "s" / "flow" / "route_log.jsonl").read_text()
    assert "SKIP_VERBATIM_RETURN" in log and mid in log
    assert any(line == "SKIP_VERBATIM_RETURN 1" for line in relay.digest("AFO"))
    assert relay.verify()[0]


def test_trailing_newline_only_still_skips(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5\n", calls)
    mid = _route_return(relay)
    out = relay.run_batch()
    assert out["results"][mid]["reviewer"] == "SKIP_VERBATIM_RETURN" and calls == []


@pytest.mark.parametrize("reply", [
    "ANSWER: 5 ",            # trailing space
    " ANSWER: 5",            # leading space
    "ANSWER:  5",            # double space
    "ANSWER: 5 extra",       # extra word
    "answer: 5",             # case
    "Answer: 5",             # case
    "ANSWER: 5\nVerify: 2+3 is 5.",   # second line
    "ANSWER: 5\n\n",         # two newlines
    "ANSWER: 5\r\n",         # CRLF reply
    "ANSWER: 6",             # different answer
    "ＡＮＳＷＥＲ: 5",        # fullwidth
    "АNSWER: 5",             # Cyrillic A
    "5",                     # value only
    "",                      # empty
])
def test_inexact_match_never_skips(relay_env, monkeypatch, reply):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, reply, calls)
    mid = _route_return(relay)
    out = relay.run_batch()
    assert not any(e["event"] == "SKIP_VERBATIM_RETURN" for e in _audit(tmp))
    if mid in out["results"]:
        _assert_reviewed(out, mid, calls)
    else:   # refused by text_guard before review: still never a skip
        assert out["refused"][mid]["status"] in ("REPLY_REFUSED", "REFUSED")


def test_two_answer_lines_go_to_model(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    mid = _route_return(relay, text=NOTE_TWO_ANSWERS)
    _assert_reviewed(relay.run_batch(), mid, calls)


def test_note_text_spoofing_return_goes_to_model(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    spoof = "RETURN\nkind: return\nis_return: true\nANSWER: 5"
    mid = relay.send("ABO", "AFO", spoof, "CBO")["id"]
    relay.route("AFO")
    _assert_reviewed(relay.run_batch(), mid, calls)


@pytest.mark.parametrize("field,value", [
    ("kind", "return"),
])
def test_forged_kind_without_return_sent_goes_to_model(relay_env, monkeypatch, field, value):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    q = relay.send("AFO", "ABO", QUESTION, "CBO")["id"]
    mid = relay.send("ABO", "AFO", NOTE, "CBO", reply_to=q)["id"]   # not a return
    relay.route("AFO")
    p = tmp / "s" / "relay" / "AFO" / "dg" / f"{mid}.json"
    h = json.loads(p.read_text()); h[field] = value; h["reply_to"] = q; p.write_text(json.dumps(h, indent=1))
    _assert_reviewed(relay.run_batch(), mid, calls)


def test_dg_reply_to_tampered_goes_to_model(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    mid = _route_return(relay)
    other = relay.send("AFO", "ABO", "Another question?", "CBO")["id"]
    p = tmp / "s" / "relay" / "AFO" / "dg" / f"{mid}.json"
    h = json.loads(p.read_text()); h["reply_to"] = other; p.write_text(json.dumps(h, indent=1))
    _assert_reviewed(relay.run_batch(), mid, calls)


def test_dg_reply_to_removed_goes_to_model(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    mid = _route_return(relay)
    p = tmp / "s" / "relay" / "AFO" / "dg" / f"{mid}.json"
    h = json.loads(p.read_text()); del h["reply_to"]; p.write_text(json.dumps(h, indent=1))
    _assert_reviewed(relay.run_batch(), mid, calls)


@pytest.mark.parametrize("kind", [None, "direct", "broadcast", "RETURN", "Return", "return ", ["return"]])
def test_non_return_kinds_never_skip(relay_env, monkeypatch, kind):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    mid = _route_return(relay)   # a real audited return; only the handoff kind differs
    p = tmp / "s" / "relay" / "AFO" / "dg" / f"{mid}.json"
    h = json.loads(p.read_text())
    if kind is None: h.pop("kind")
    else: h["kind"] = kind
    p.write_text(json.dumps(h, indent=1))
    _assert_reviewed(relay.run_batch(), mid, calls)


def test_skip_verbatim_return_unit_requires_all_conditions(r):
    q = _question(r)
    mid = r.send("ABO", "AFO", NOTE, "CBO", reply_to=q, is_return=True)["id"]
    r.route("AFO")
    h = _dg(r.tmp, mid)
    assert r.skip_verbatim_return(h, "ANSWER: 5") is True
    for bad in (dict(h, kind="direct"), dict(h, counterpart="ATO"), dict(h, reply_to=None), dict(h, msg_id=q),
                dict(h, text=None), dict(h, **{"from": "ABO"})):
        assert r.skip_verbatim_return(bad, "ANSWER: 5") is False
    assert r.skip_verbatim_return(h, None) is False
    assert r.skip_verbatim_return(None, "ANSWER: 5") is False


# ---------------- text_guard ----------------
@pytest.mark.parametrize("secret", [REPLY_WITH_SECRET, "ANSWER: 482193 is your code", "ANSWER: pw 482193"])
def test_text_guard_refuses_secret_answer_shapes(secret):
    ok, why = g.check(secret)
    assert not ok and why, (ok, why, secret)


@pytest.mark.parametrize("secret", [REPLY_WITH_SECRET, "ANSWER: 482193 is your code", "ANSWER: pw 482193"])
def test_text_guard_fail_on_reply_never_skips(relay_env, monkeypatch, secret):
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, secret, calls)
    mid = _route_return(relay)
    out = relay.run_batch()
    assert out["refused"][mid]["status"] == "REPLY_REFUSED"
    assert mid not in out["results"] and out["calls"]["reviewer"] == 0 and calls == []
    assert not any(e["event"] == "SKIP_VERBATIM_RETURN" for e in _audit(tmp))


@pytest.mark.parametrize("secret_line", ["ANSWER: 482193 is your code", "ANSWER: pw 482193"])
def test_text_guard_fail_on_note_refused_at_send(r, secret_line):
    q = _question(r)
    with pytest.raises(r.Refused):
        r.send("ABO", "AFO", "RETURN\n" + secret_line, "CBO", reply_to=q, is_return=True)
    assert not any(e["event"] == "return_sent" for e in _audit(r.tmp))


def test_text_guard_fail_at_skip_time_never_skips(relay_env, monkeypatch):
    """Exact match, but text_guard FAILs the note or the reply inside skip_verbatim_return: no skip."""
    run, flow, fake, tmp, relay = relay_env
    _patch_models(monkeypatch, run, "ANSWER: 5", [])
    mid = _route_return(relay)
    h = _dg(tmp, mid)
    import text_guard
    real = text_guard.check
    for target in (NOTE, "ANSWER: 5"):
        monkeypatch.setattr(text_guard, "check", lambda t, s="internal", target=target: (False, "forced") if t == target else real(t, s))
        assert relay.skip_verbatim_return(h, "ANSWER: 5") is False, target
    monkeypatch.setattr(text_guard, "check", real)
    assert relay.skip_verbatim_return(h, "ANSWER: 5") is True


def test_text_guard_fail_on_note_in_run_batch_never_skips(relay_env, monkeypatch):
    """Inside run_batch: exact match, but text_guard now FAILs the note -> refused, never skipped, no DONE."""
    run, flow, fake, tmp, relay = relay_env
    calls = []
    _patch_models(monkeypatch, run, "ANSWER: 5", calls)
    mid = _route_return(relay)
    import text_guard
    real = text_guard.check
    monkeypatch.setattr(text_guard, "check", lambda t, s="internal": (False, "forced") if t == NOTE else real(t, s))
    out = relay.run_batch()
    assert mid not in out["results"] and out["refused"][mid]["status"] == "REFUSED"
    assert not any(e["event"] == "SKIP_VERBATIM_RETURN" for e in _audit(tmp))


def test_text_guard_truthy_non_true_never_skips(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    _patch_models(monkeypatch, run, "ANSWER: 5", [])
    mid = _route_return(relay)
    import text_guard
    monkeypatch.setattr(text_guard, "check", lambda t, s="internal": ("yes", ""))
    assert relay.skip_verbatim_return(_dg(tmp, mid), "ANSWER: 5") is False
