"""gate.py: GT lines, auto-rules for money, Owner release with a scoped quote + finance_guard PASS. Temp state only."""
import importlib, json, os, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

OK_SETTINGS = dict(read_only=False, per_request_limit=50, period_limit=100, period_days=30, allowed_categories=["software"])


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path))
    q = tmp_path / "quotes.jsonl"
    q.write_text(json.dumps({"id": "q1", "kind": "card", "scope": ["gate:s1"]}) + "\n"
                 + json.dumps({"id": "q2", "kind": "card", "scope": ["money:spend:purchase"]}) + "\n")
    monkeypatch.setenv("GB_C9_QUOTES", str(q))
    import gate, owner_override, finance_guard
    importlib.reload(owner_override); importlib.reload(finance_guard); importlib.reload(gate)
    gate._quotes = q
    return gate


def _override(tmp_path, qid, by="Owner"):
    p = tmp_path / f"ov-{qid}.json"; p.write_text(json.dumps({"signed_by": by, "quote_id": qid})); return str(p)


def test_transfer_is_auto_veto(g):
    g.request("t1", "CFO", "money", "move", {"action": "transfer", "amount": 5, "destination": "x"})
    ok, why = g.require("t1")
    assert not ok and "VETO" in why


def test_unknown_money_action_is_veto(g):
    g.request("t2", "CFO", "money", "odd", {"action": "something-else", "amount": 5})
    assert "VETO" in g.require("t2")[1]


def test_spend_auto_holds_and_cso_cannot_pass_it(g):
    g.request("s1", "CFO", "money", "buy a tool", {"action": "purchase", "amount": 20, "category": "software"})
    ok, why = g.require("s1")
    assert not ok and "HOLD" in why
    with pytest.raises(SystemExit, match="cannot be released by a gate verdict"):
        g.verdict("s1", "PASS", "CSO", "looks fine")


def test_only_cfo_files_money_and_cco_never(g):
    with pytest.raises(SystemExit, match="no GATE line"):
        g.request("m1", "CAO", "money", "x", {"action": "purchase", "amount": 1})
    with pytest.raises(SystemExit, match="no money"):
        g.request("m2", "CCO", "money", "x", {"action": "purchase", "amount": 1})


def test_only_cso_answers(g):
    g.request("i1", "CAO", "irreversible", "delete temp dir")
    with pytest.raises(SystemExit, match="only the CSO"):
        g.verdict("i1", "PASS", "CBO", "x")
    g.verdict("i1", "PASS", "CSO", "ok")
    assert g.require("i1") == (True, "PASS")


def test_changed_request_after_verdict_refused(g, tmp_path):
    g.request("i2", "COO", "irreversible", "rotate logs")
    g.verdict("i2", "PASS", "CSO", "ok")
    p = tmp_path / "gates" / "i2.request.json"; r = json.loads(p.read_text()); r["summary"] = "something else"; p.write_text(json.dumps(r))
    assert g.require("i2") == (False, "request changed after verdict")


def test_release_needs_owner_scoped_quote_and_finance_pass(g, tmp_path):
    g.request("s1", "CFO", "money", "buy a tool", {"action": "purchase", "amount": 20, "category": "software"})
    ok, why, _ = g.release("s1", _override(tmp_path, "q1", by="CSO"), settings=OK_SETTINGS)
    assert not ok and "CSO cannot sign" in why
    ok, why, _ = g.release("s1", _override(tmp_path, "q2"), settings=OK_SETTINGS)
    assert not ok and "generic money scopes" in why
    ok, why, _ = g.release("s1", _override(tmp_path, "q1"), settings=dict(OK_SETTINGS, read_only=True))
    assert not ok and "finance_guard failed" in why
    ok, why, _ = g.release("s1", _override(tmp_path, "q1"), settings=OK_SETTINGS)
    assert ok, why
    assert g.require("s1") == (True, "PASS (Owner release, verified quote)")


def test_quote_releases_one_request_only(g, tmp_path):
    g.request("s1", "CFO", "money", "buy", {"action": "purchase", "amount": 10, "category": "software"})
    assert g.release("s1", _override(tmp_path, "q1"), settings=OK_SETTINGS)[0]
    led = g.used_quotes_path()
    assert json.loads(open(led).read().splitlines()[0])["quote_id"] == "q1"
    ok, why = g._use_quote("q1", "other", "h")
    assert not ok and "already used" in why


def test_truncated_quote_ledger_refuses_every_release(g, tmp_path):
    g.request("s1", "CFO", "money", "buy", {"action": "purchase", "amount": 10, "category": "software"})
    assert g.release("s1", _override(tmp_path, "q1"), settings=OK_SETTINGS)[0]
    open(g.used_quotes_path(), "w").close()
    ok, why = g.require("s1")
    assert not ok and "not intact" in why


def test_read_access_only(g):
    assert g.access("CFO", "finance_readonly", "read")[0]
    assert not g.access("CFO", "finance_readonly", "write")[0]
    assert not g.access("CMO", "finance_readonly", "read")[0]
