GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, sys
from pathlib import Path
sys.path.insert(0, GB_ROOT + "/scripts")
import owner_override as mo

def test_owner_verified_quote_ok(tmp_path):
    q = tmp_path / "q.jsonl"
    q.write_text(json.dumps({"id": "t19s1", "kind": "card", "scope": ["gate:g1"]}) + "\n")
    ov = {"signed_by": "Owner", "quote_id": "t19s1"}
    ok, why, rec = mo.check_override(ov, quotes_path=q, scope=["gate:g1"])
    assert ok and "t19s1" in why

def test_cso_signed_refused(tmp_path):
    q = tmp_path / "q.jsonl"
    q.write_text(json.dumps({"id": "t19s1"}) + "\n")
    ok, why, _ = mo.check_override({"signed_by": "CSO", "quote_id": "t19s1"}, quotes_path=q)
    assert not ok and "CSO" in why

def test_missing_or_unknown_quote_unverified(tmp_path):
    q = tmp_path / "q.jsonl"
    q.write_text(json.dumps({"id": "t-known", "scope": "x"}) + "\n")
    ok, why, _ = mo.check_override({"signed_by": "Owner"}, quotes_path=q, scope="x")
    assert not ok and "missing" in why
    ok, why, _ = mo.check_override({"signed_by": "Owner", "quote_id": "nope"}, quotes_path=q, scope="x")
    assert not ok and "not in the quotes registry" in why


def test_quotes_are_scoped(tmp_path):                                                  # #36 C9, probe P11
    q = tmp_path / "q.jsonl"
    q.write_text(json.dumps({"id": "t19s1", "scope": ["legacy-scope:a", "legacy-scope:b"]}) + "\n"
                 + json.dumps({"id": "t-noscope"}) + "\n")
    ov = {"signed_by": "Owner", "quote_id": "t19s1"}
    ok, why, _ = mo.check_override(ov, quotes_path=q)
    assert not ok and "scope required" in why
    for sc in (["gate:g1", "money:spend:sample"], "cso-emit", "gh", "cco-paid", "money-settings", "selfstart:SS-1"):
        ok, why, _ = mo.check_override(ov, quotes_path=q, scope=sc)
        assert not ok and "not scoped" in why, sc
    assert mo.check_override(ov, quotes_path=q, scope="legacy-scope:a")[0]
    assert not mo.check_override({"signed_by": "Owner", "quote_id": "t-noscope"}, quotes_path=q, scope="gh")[0]

