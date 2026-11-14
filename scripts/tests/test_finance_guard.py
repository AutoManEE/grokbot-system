import os, sys, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import finance_guard as fg

S = dict(read_only=False, per_request_limit=50, period_limit=100, period_days=30, allowed_categories=["software"])
D = dict(action="purchase", amount=20, category="software")


def test_default_read_only_refuses(tmp_path):
    r = fg.check(D, settings=dict(S, read_only=True), ledger_path=str(tmp_path / "l.jsonl"))
    assert not r["allowed"] and "F1 read_only_off" in r["failed"]


def test_missing_settings_fail_closed(tmp_path):
    r = fg.check(D, settings="nope", ledger_path=str(tmp_path / "l.jsonl"))
    assert not r["allowed"] and r["failed"] == ["settings"]


def test_unapproved_limits_refuse(tmp_path):
    r = fg.check(D, settings=dict(S, per_request_limit=None, period_limit=None), ledger_path=str(tmp_path / "l.jsonl"))
    assert {"F3 per_request", "F4 period_total"} <= set(r["failed"])


def test_pass_and_period_window(tmp_path):
    led = str(tmp_path / "l.jsonl")
    assert fg.check(D, settings=S, ledger_path=led)["allowed"]
    for i in range(5):
        fg.record_spend(D, f"g{i}", ledger_path=led)
    r = fg.check(D, settings=S, ledger_path=led)
    assert not r["allowed"] and r["failed"] == ["F4 period_total"]
    assert fg.check(D, settings=S, ledger_path=led, now=time.time() + 31 * 86400)["allowed"]


def test_category_destination_and_amount(tmp_path):
    led = str(tmp_path / "l.jsonl")
    assert "F5 category" in fg.check(dict(D, category="travel"), settings=S, ledger_path=led)["failed"]
    assert "F6 no_destination" in fg.check(dict(D, destination="acct"), settings=S, ledger_path=led)["failed"]
    assert "F2 amount_valid" in fg.check(dict(D, amount=float("nan")), settings=S, ledger_path=led)["failed"]
    assert "F3 per_request" in fg.check(dict(D, amount=60), settings=S, ledger_path=led)["failed"]


def test_shipped_settings_are_read_only():
    s = fg.load_settings()
    assert s is not None and s["read_only"] is True
    assert not fg.check(D)["allowed"]
