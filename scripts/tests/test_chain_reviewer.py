"""3c: per-chain reviewer, same-model label, reviewer never overrides a code FAIL."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys
import pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/bots")
import flow, run, alignment_check as ac


def _bots(tmp, a_model, s_model, reviewer=None):
    for hid, m in (("AZZ", a_model), ("SZZ", s_model)):
        d = tmp / hid; d.mkdir(parents=True, exist_ok=True)
        s = dict(id=hid, model=m, tools=[], outside_content=False)
        if reviewer and hid == "SZZ": s["reviewer_model"] = reviewer
        (d / "settings.json").write_text(json.dumps(s)); (d / "prompt.md").write_text(f"{hid} prompt\n")
    return tmp


def _rep(result="PASS", conds=None):
    return dict(item="DG-AZZ t", bot="AZZ", result=result, objective="o", options=[], decision="84",
                reasoning="r", confidence=90, conditions=conds or [dict(name="c", required=True, result=result)])


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, "BOTS", str(tmp_path / "bots"))
    monkeypatch.setattr(ac, "BOTS", tmp_path / "bots")
    monkeypatch.setattr(flow, "tdir", lambda t: str(tmp_path))
    calls = []
    monkeypatch.setattr(run, "complete", lambda m, p: (calls.append(m), "VERDICT: PASS\nREASON: fine")[1])
    return tmp_path / "bots", calls


def test_phi4_s_chain_gets_llama_reviewer(env):
    bots, calls = env
    _bots(bots, "qwen3:4b-instruct", "phi4-mini")
    assert flow.chain_reviewer("AZZ") == ("llama3.2:3b", False)
    assert ac.chain_reviewer("AZZ", "SZZ") == "llama3.2:3b"
    r = _rep(); flow.apply_independent_review(r, "t", "task", "84")
    assert calls == ["llama3.2:3b"] and r["independent_reviewer_model"] == "llama3.2:3b"
    assert not r.get("same_model_review")
    assert ac.pair_independence("AZZ", "SZZ")[0] == "PASS"


def test_default_reviewer_when_chain_has_no_phi4(env):
    bots, _ = env
    _bots(bots, "qwen3:4b-instruct", "llama3.2:3b")
    assert flow.chain_reviewer("AZZ") == ("phi4-mini", False)


def test_settings_reviewer_wins(env):
    bots, _ = env
    _bots(bots, "qwen3:4b-instruct", "phi4-mini", reviewer="granite3:2b")
    assert flow.chain_reviewer("AZZ")[0] == "granite3:2b" == ac.chain_reviewer("AZZ", "SZZ")


def test_same_family_reviewer_is_labeled_and_fails_alignment(env):
    bots, calls = env
    _bots(bots, "qwen3:4b-instruct", "phi4-mini", reviewer="qwen3:1.7b")
    rv, same = flow.chain_reviewer("AZZ")
    assert same
    r = _rep(); flow.apply_independent_review(r, "t", "task", "84")
    assert r["same_model_review"] is True and "Same-model review: yes" in flow._md(r)
    v, why = ac.pair_independence("AZZ", "SZZ")
    assert v == "FAIL" and "same-model review" in why


def test_barred_reviewer_not_called(env):
    bots, calls = env
    _bots(bots, "qwen3:4b-instruct", "phi4-mini", reviewer="gemma3:4b")
    r = _rep(); flow.apply_independent_review(r, "t", "task", "84")
    assert calls == [] and r["independent_review_verdict"] == "N/A"
    assert ac.pair_independence("AZZ", "SZZ")[0] == "FAIL"


def test_reviewer_pass_cannot_override_code_fail(env):
    bots, calls = env
    _bots(bots, "qwen3:4b-instruct", "phi4-mini")
    r = _rep("FAIL", [dict(name="arith_recheck", required=True, result="FAIL", note="3 x 4 x 7 = 74")])
    flow.apply_independent_review(r, "t", "task", "74")
    assert calls == ["llama3.2:3b"] and r["independent_review_verdict"] == "PASS"
    assert r["result"] == "FAIL"
    assert any(c["name"] == "arith_recheck" and c["result"] == "FAIL" for c in r["conditions"])


def test_reviewer_fail_still_fails_a_pass(env, monkeypatch):
    bots, _ = env
    _bots(bots, "qwen3:4b-instruct", "phi4-mini")
    monkeypatch.setattr(run, "complete", lambda m, p: "VERDICT: FAIL\nREASON: wrong")
    r = _rep(); flow.apply_independent_review(r, "t", "task", "84")
    assert r["result"] == "FAIL"
