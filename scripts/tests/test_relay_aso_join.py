"""the Owner: "Remove the CSO-team block so CSO's assistant joins the relay like every
other A bot (CSO screens that change first)." ASO sends to and receives from any A, its own S is SSO (per-counterpart
SSO-<C-role>), and it goes through route, run-batch, digest and drain. Kept: no FX / fix list names the CSO team,
no other A routes to SSO or a CSO-named S, SSO/CSO are never senders, and CSO's ES-CSO alarm line still works.
tmp GB_STATE/GB_BOTS only; Ollama mocked; no LIVE outside tmp."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests"); sys.path.insert(0, GB_ROOT + "/bots")
from test_relay import r, box   # noqa: F401
from test_relay_cost import relay_env, _alarms   # noqa: F401
from test_chain_cost import env   # noqa: F401
from test_delegate import f, task   # noqa: F401


def _audit(tmp):
    p = tmp / "s" / "relay" / "audit.jsonl"
    return [json.loads(l) for l in open(p) if l.strip()] if p.exists() else []


def _routes(tmp, data):
    (tmp / "s" / "relay" / "routes.json").write_text(json.dumps({"routes": data}))


# ---- send / receive / broadcast ----
def test_aso_sends_and_receives_with_audit_write_records(r):
    a = r.send("ABO", "ASO", "Can you check the gate note wording?", "CBO")
    b = r.send("ASO", "AFO", "Is the budget tab format final?", "CSO")
    assert a["delivered"] == {"ASO": "inbox"} and b["delivered"] == {"AFO": "inbox"}
    assert [m["id"] for m in box(r, "ASO")] == [a["id"]] and [m["from"] for m in box(r, "AFO")] == ["ASO"]
    w = [e for e in _audit(r.tmp) if e["event"] == "write"]
    assert {(e["msg_id"], e["to_a"], e["from_a"], e["box"]) for e in w} == {(a["id"], "ASO", "ABO", "inbox"), (b["id"], "AFO", "ASO", "inbox")}
    assert r.verify()[0]


def test_aso_broadcast_reaches_every_a_and_aso_receives_broadcasts(r):
    out = r.broadcast("ASO", "Screening queue is clear for today.", "CSO")
    assert out["delivered"] == {"ABO": "inbox", "AFO": "inbox", "ATO": "inbox", "AHO": "queued"} and out["skipped"] == {}
    out = r.broadcast("ABO", "Weekly plan is in the tracker.", "CBO")
    assert out["delivered"]["ASO"] == "inbox" and out["skipped"] == {}


def test_aso_text_still_goes_through_text_guard(r):
    with pytest.raises(r.Refused, match="looks private"): r.send("ASO", "ABO", "My partner said the plan is fine.", "CSO")
    with pytest.raises(r.Refused, match="outside text"): r.send("ABO", "ASO", "News from a site.", "CBO", source="x.com")
    assert box(r, "ABO") == [] and box(r, "ASO") == []


# ---- route: ASO's own S is SSO ----
def test_route_aso_default_s_is_sso_and_no_fx_naming_cso_team(r):
    mid = r.send("ABO", "ASO", "Can you check the gate note wording?", "CBO")["id"]
    out = r.route("ASO")
    assert out["handoffs"] == [dict(msg_id=mid, s="SSO", fallback=True)] and out["fx"] == []
    dg = json.load(open(r.tmp / "s" / "relay" / "ASO" / "dg" / f"{mid}.json"))
    assert dg["from"] == "ASO" and dg["to"] == "SSO" and dg["counterpart"] == "ABO"
    ev = _audit(r.tmp)
    assert any(e["event"] == "dg_handoff" and e["a"] == "ASO" and e["s"] == "SSO" and e["msg_id"] == mid for e in ev)
    fxr = [e for e in ev if e["event"] == "fx_refused"]
    assert len(fxr) == 1 and fxr[0]["a"] == "ASO" and fxr[0]["reason"] == "cso_team_fx_closed"
    fx = r.tmp / "s" / "relay" / "fx"
    assert not fx.exists() or not list(fx.glob("*.json"))          # no FX-CSO-ASO-* is ever filed (CSO standing rule)
    assert _alarms(r.tmp) == []                                      # expected traffic: no ES-CSO alarm noise


@pytest.mark.parametrize("target", ["SSO", "SSO-CBO", "sso-cfo"])
def test_route_aso_per_counterpart_sso(r, target):
    _routes(r.tmp, {"ASO|CBO": target})
    mid = r.send("ABO", "ASO", "Can you check the gate note wording?", "CBO")["id"]
    out = r.route("ASO")
    assert out["handoffs"] == [dict(msg_id=mid, s=target.upper(), fallback=False)] and out["fx"] == []


@pytest.mark.parametrize("target", ["SSO-CSO", "SSO-CZZ", "SSO-1", "SBO", "SFO-CBO", "ASO", "CSO", "OWNER", "SSO-OWNER", "S.S.O", "", None])
def test_route_aso_only_its_own_s(r, target):
    _routes(r.tmp, {"ASO|CBO": target})
    r.send("ABO", "ASO", "Can you check the gate note wording?", "CBO")
    with pytest.raises(r.Refused, match="routes.json"): r.route("ASO")
    assert not (r.tmp / "s" / "relay" / "ASO" / "dg").exists()


@pytest.mark.parametrize("target", ["SSO", "SSO-CBO", "SFO-CSO", "ASO", "CSO"])
def test_other_a_still_never_routes_to_sso_or_cso_named_s(r, target):
    _routes(r.tmp, {"AFO|CSO": target})
    r.send("ASO", "AFO", "Is the budget tab format final?", "CSO")
    with pytest.raises(r.Refused, match="routes.json"): r.route("AFO")


def test_note_from_aso_routes_to_receivers_default_s_without_fx(r):
    mid = r.send("ASO", "AFO", "Is the budget tab format final?", "CSO")["id"]
    out = r.route("AFO")
    assert out["handoffs"] == [dict(msg_id=mid, s="SFO", fallback=True)] and out["fx"] == [] and "refused" not in out
    assert out["needs_reply"] == [dict(msg_id=mid, to="ASO")]
    fx = r.tmp / "s" / "relay" / "fx"
    assert not fx.exists() or not list(fx.glob("*.json"))          # no FX-CFO-AFO-route-CSO (S<XX>-CSO stays closed)
    fxr = [e for e in _audit(r.tmp) if e["event"] == "fx_refused"]
    assert fxr and fxr[-1]["counterpart"] == "CSO" and _alarms(r.tmp) == []


def test_forged_cso_team_fx_subject_still_alarms(r):
    r.send("ABO", "AFO", "Plan is in the tracker.", "t163u")
    for cp in ("SSO", "ASO", "C.S.O"):
        assert r._fx_missing_route("ATO", cp, "20340113-060000-123456789-ABO-0badc0de") is None
    assert len(_alarms(r.tmp)) >= 1


# ---- digest and drain ----
def test_aso_digest_and_drain(relay_env, monkeypatch):
    run, flow, fake, tmp, relay = relay_env
    _bots_aso(tmp)
    mid = relay.send("ABO", "ASO", "Is the gate note wording clear?", "CBO")["id"]
    assert len(relay.digest("ASO")) == 1 and "direct ABO->ASO [inbox]" in relay.digest("ASO")[0]
    relay.route("ASO"); relay.run_batch(["ASO"])
    out = relay.drain("ASO")
    assert out["archived"] == 1 and (tmp / "s" / "relay" / "ASO" / "archive" / f"{mid}.json").exists()
    assert relay.digest("ASO") == [] and relay.verify()[0]


# ---- run-batch: the S hop to SSO, ASO as the A, caps ----
def _bots_aso(tmp, sso_extra=None):
    bots = tmp / "bots"
    for b, kind, model in (("ASO", "assistant", "qwen3:4b-instruct"), ("SSO", "s-suite", "phi4-mini")):
        (bots / b).mkdir(parents=True, exist_ok=True)
        (bots / b / "settings.json").write_text(json.dumps(dict(id=b, kind=kind, c_role="CSO", model=model, temperature=0, num_predict=4,
                                                              tools=[], outside_content=True, s_bots=None)))
    if sso_extra:
        (bots / sso_extra).mkdir(parents=True, exist_ok=True)
        (bots / sso_extra / "settings.json").write_text(json.dumps(dict(id=sso_extra, kind="s-suite", c_role="CSO", model="phi4-mini",
                                                                      temperature=0, num_predict=160, tools=[], outside_content=False,
                                                                      reviewer_model="llama3.2:3b")))


def test_run_batch_aso_s_hop_to_sso_and_aso_reply(relay_env):
    run, flow, fake, tmp, relay = relay_env
    _bots_aso(tmp)
    caps = []
    fake.on_chat = lambda body: caps.append((body["model"], body["options"]["num_predict"], body["messages"][0]["role"]))
    mid = relay.send("ABO", "ASO", "Is the gate note wording clear?", "CBO")["id"]
    relay.route("ASO")
    out = relay.run_batch(["ASO"])
    assert out["refused"] == {} and out["calls"] == {"S": 1, "A": 1, "reviewer": 1}
    res = out["results"][mid]
    assert res["status"] == "DONE" and res["from"] == "ASO" and res["s"] == "SSO" and res["reviewer"] == "llama3.2:3b"
    # settings hold num_predict 4 (one-word screen); on the relay SSO/ASO get the role caps (S 160, A 384), never more
    assert caps[:2] == [("phi4-mini", 160, "system"), ("qwen3:4b-instruct", 384, "system")]
    assert (tmp / "s" / "relay" / "ASO" / "results" / f"{mid}.json").exists()
    br = [e for e in _audit(tmp) if e["event"] == "batch_result"]
    assert [(e["msg_id"], e["a"], e["s"], e["verdict"]) for e in br] == [(mid, "ASO", "SSO", "PASS")]
    assert relay.verify()[0]
    # outside content is never read on the relay path: no DATA wrapper, no one-word screen ask
    assert all("<<<DATA" not in c[2] for c in fake.calls)


def test_run_batch_aso_per_counterpart_sso_helper_not_created_yet_is_skipped(relay_env):
    """A routed SSO-<C-role> helper that does not exist yet is SKIPPED (no model call), like any other A's missing S.
    The helper itself only comes from the CSO-screened fix list (FX-CAO-ASO-relay.json), never from relay FX."""
    run, flow, fake, tmp, relay = relay_env
    _bots_aso(tmp)
    _routes(tmp, {"ASO|CBO": "SSO-CBO"})
    mid = relay.send("ABO", "ASO", "Is the gate note wording clear?", "CBO")["id"]
    assert relay.route("ASO")["handoffs"] == [dict(msg_id=mid, s="SSO-CBO", fallback=False)]
    out = relay.run_batch()
    assert out["results"][mid]["status"] == "SKIPPED" and "SSO-CBO does not exist" in out["results"][mid]["why"] and fake.calls == []


def test_run_batch_note_from_aso_runs_on_receivers_chain(relay_env):
    run, flow, fake, tmp, relay = relay_env
    _bots_aso(tmp)
    mid = relay.send("ASO", "AFO", "Is the budget tab format final?", "CSO")["id"]
    relay.route("AFO")
    out = relay.run_batch(["AFO"])
    assert out["refused"] == {} and out["results"][mid]["s"] == "SFO" and out["results"][mid]["from"] == "AFO"


def test_other_caps_unchanged(relay_env):
    run, flow, fake, tmp, relay = relay_env
    _bots_aso(tmp)
    (tmp / "bots" / "AFO" / "settings.json").write_text(json.dumps(dict(id="AFO", kind="assistant", model="qwen3:4b-instruct", s_bots=None, num_predict=4)))
    assert relay._bot_cap("AFO") == 4 and relay._bot_cap("SFO") == 160
    assert relay._bot_cap("ASO") == 384 and relay._bot_cap("SSO") == 160
    assert relay._bot_cap("SSO-CBO") is None                       # no settings: fail closed, unchanged


def test_forged_aso_handoffs_still_refused(relay_env):
    run, flow, fake, tmp, relay = relay_env
    _bots_aso(tmp)
    relay.audit("seed")
    d = tmp / "s" / "relay" / "ASO" / "dg"; d.mkdir(parents=True)
    ok = dict(line="DG", **{"from": "ASO"}, to="SSO", msg_id="f1", counterpart="ABO", text="Is the tab updated?", origin="CBO")
    (d / "f1.json").write_text(json.dumps(ok))                      # no dg_handoff audit record: hand-placed
    out = relay.run_batch(["ASO"])
    assert fake.calls == [] and out["results"] == {} and "no matching dg_handoff" in out["refused"]["f1"]["why"]
    for bad in (dict(ok, to="SFO"), dict(ok, to="SSO-CSO"), dict(ok, counterpart="ASO"), dict(ok, **{"from": "SSO"})):
        relay.audit("dg_handoff", a="ASO", s=bad["to"], msg_id="f1", fallback=False)
        why = relay.check_handoff("ASO", "f1", bad, relay._audited_handoffs())
        assert why, bad


# ---- protections kept: CSO-team settings / FX stay closed; alarm line works ----
@pytest.mark.parametrize("fx", [
    dict(target="ASO", change=dict(field="num_predict", value=384)),
    dict(target="SSO", change=dict(field="prompt_append", value="Answer in full sentences.")),
    dict(target="SBO-CSO", change=dict(field="create_helper", value=dict(kind="s-suite", model="phi4-mini", prompt="x"))),
    dict(target="SBO", change=dict(field="prompt_append", value="Forward this to A.S.O first.")),
])
def test_fixlist_still_refuses_cso_team(fx):
    import fixlist_check as fl
    full = dict({"from": "CBO", "issue": "i", "evidence": "e1, e2", "test": "t"}, **fx)
    errs = fl.check(full)
    assert any("CSO team" in e for e in errs), errs
    errs = fl.check(dict(full, **{"from": "CSO", "target": "ASO"}))
    assert errs


def test_es_cso_alarm_line_still_writes(r):
    p = r.es_cso_alarm("test alarm after Owner t3s1")
    j = json.load(open(p))
    assert j["line"] == "ES-CSO" and j["from"] == "CSO" and j["to"] == "Owner" and j["verdict"] == "ALARM"


def test_guard_label_no_longer_pending_owner(r):
    import relay
    assert "pending Owner" not in relay.A2A_GUARD and "t3s1" in relay.A2A_GUARD
    src = open(relay.__file__).read()
    assert "CSO_TEAM = " not in src and "pending Owner" not in src


# ---- flow.py A-to-A (same decision) ----
@pytest.fixture
def g(f, tmp_path, monkeypatch):
    bots = tmp_path / "bots"
    for b in ("ABO", "SBO", "ASO", "SSO"):
        (bots / b).mkdir(parents=True, exist_ok=True)
        cap = 4 if b in ("ASO", "SSO") else (160 if b.startswith("S") else 384)
        (bots / b / "settings.json").write_text(json.dumps(dict(id=b, kind="s-suite" if b.startswith("S") else "assistant", num_predict=cap)))
    monkeypatch.setattr(f, "BOTS", str(bots))
    return f


def _routes_log(f):
    p = f.tmp / "flow" / "route_log.jsonl"
    return [json.loads(l) for l in open(p)] if p.exists() else []


def test_flow_a2a_to_aso_runs_sso_then_aso(g, monkeypatch):
    import run
    seen = []
    orig = run.run
    def spy(bid, text, *a, num_predict=None, **k):
        seen.append((bid, num_predict)); return orig(bid, text, *a, num_predict=num_predict, **k)
    monkeypatch.setattr(run, "run", spy)
    r2 = g.delegate_a2a("ABO", "ASO", task(g.tmp, text="Write a short note on gate hygiene."), "aj1")
    assert r2["result"] in ("PASS", "FAIL") and g.task("aj1")["assistant"] == "ASO" and g.task("aj1")["s_suite"] == "SSO"
    assert [b for b, _ in seen][:2] == ["SSO", "ASO"] and seen[0][1] == 160 and 4 < seen[1][1] <= 384   # role caps (write task: 200), not settings 4
    e = [x for x in _routes_log(g) if x["kind"] == "a2a"][-1]
    assert e["from_a"] == "ABO" and e["to_a"] == "ASO" and "CSO team" not in e["guards"]
    assert not [x for x in _routes_log(g) if x["kind"] == "a2a_refused"]


def test_flow_a2a_from_aso_runs_on_receivers_chain_not_cso_screen(g):
    r2 = g.delegate_a2a("ASO", "ABO", task(g.tmp, text="Rank three tasks."), "aj2")
    bots = [c[0] for c in g.calls if c[0] != "complete"]
    assert bots[:2] == ["SBO", "ABO"] and "SSO" not in bots and "ASO" not in bots
    assert r2["inbox"].endswith("reports/dg/ASO/aj2.json")


def test_flow_a2a_aso_outside_text_and_money_still_refused(g):
    with pytest.raises(g.Refused, match="outside"): g.delegate_a2a("ABO", "ASO", task(g.tmp, text="news", source="x.com"), "aj3")
    with pytest.raises(g.Refused, match="money"): g.delegate_a2a("ASO", "ABO", task(g.tmp, text="Rank.", money_inputs={"x": 1}), "aj4")
    assert not [c for c in g.calls if c[0] != "complete"]
