"""C9: Owner quotes are verifiable (message or card id in alignment/c9_quotes.jsonl) or UNVERIFIED."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import importlib, json, sys
sys.path.insert(0, GB_ROOT + "/scripts")
import owner_override as mo, validate_work as vw
OK = dict(by="CAO", result="PASS")


def test_directive_quote_without_id_unverified():
    m = mo.mark_quote("Go ahead and run it.")
    assert m["status"] == "UNVERIFIED" and m["approval"] is False and m["text"].startswith("UNVERIFIED:")
    assert mo.mark_quote("x", "t-unknown")["status"] == "UNVERIFIED"
    assert mo.mark_quote("Example: approve one purchase on gate SAMPLE-1.", "t1s1")["approval"] is True


def test_v4_rechecks_id_at_gate_time():
    j = dict(id="x", origin="self_started", cao_validation=OK, owner_answer="yes", owner_quote_id="made-up")
    assert any("UNVERIFIED" in x for x in vw.gate(j))

