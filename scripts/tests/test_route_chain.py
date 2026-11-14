"""CSO re-screen 2 (non-blocking): route_log.jsonl is hash-chained; takeover verifies the chain and counts only
chained a_report records. tmp files only."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests"); sys.path.insert(0, GB_ROOT + "/bots")
import route_chain as rc
from test_fx_cto_flow import g, log, GOOD, task   # noqa: F401
from test_delegate import f   # noqa: F401


def test_append_verify_and_legacy_prefix(tmp_path):
    p = str(tmp_path / "rl.jsonl")
    assert rc.verify(p)[1].startswith("NO-LOG")
    open(p, "w").write(json.dumps(dict(kind="old")) + "\n")             # a pre-chain line stays as legacy prefix
    for i in range(3): rc.append(p, dict(kind="k", i=i))
    ok, msg, n = rc.verify(p); assert ok and n == 3 and "1 legacy" in msg


@pytest.mark.parametrize("tamper", ["edit", "delete_middle", "append_unchained", "append_forged_hash", "malformed"])
def test_tamper_detected(tmp_path, tamper):
    p = tmp_path / "rl.jsonl"
    for i in range(3): rc.append(str(p), dict(kind="k", i=i))
    L = p.read_text().splitlines()
    if tamper == "edit": e = json.loads(L[1]); e["i"] = 9; L[1] = json.dumps(e)
    elif tamper == "delete_middle": del L[1]
    elif tamper == "append_unchained": L.append(json.dumps(dict(kind="a_report", task="t", sha256="x")))
    elif tamper == "append_forged_hash": L.append(json.dumps(dict(kind="a_report", task="t", sha256="x", prev="0" * 64, hash="1" * 64)))
    elif tamper == "malformed": L.append("{bad")
    p.write_text("\n".join(L) + "\n")
    assert not rc.verify(str(p))[0]


def test_all_writers_chain(g, tmp_path, monkeypatch):
    g.delegate("CBO", task(g.tmp, text="Rank three tasks."), "rc1")
    p = str(g.tmp / "flow" / "route_log.jsonl")
    ok, msg, n = rc.verify(p); assert ok and n >= 1 and all("hash" in r for r in log(g))


def test_takeover_refuses_on_broken_chain_and_self_appended_report(g):
    g.delegate("CBO", task(g.tmp, text="Rank three tasks."), "rc2")             # PASS report, logged and chained
    rp = g.tmp / "flow" / "rc2" / "03_a_report.json"
    json.dump({"result": "FAIL"}, open(rp, "w"))                                # forged FAIL ...
    p = g.tmp / "flow" / "route_log.jsonl"
    with open(p, "a") as fh: fh.write(json.dumps(dict(kind="a_report", task="rc2", sha256=g._file_sha(str(rp)))) + "\n")  # ... + self-appended line
    with pytest.raises(g.Refused, match="route_log chain not intact"): g.takeover("rc2", "C did the work.")
    assert g.task("rc2")["state"] == "OPEN"


def test_takeover_ignores_unchained_legacy_a_report(g):
    p = g.tmp / "flow" / "route_log.jsonl"; os.makedirs(p.parent, exist_ok=True)
    g.new("rc3", "CBO", "Rank three tasks."); rp = g.tmp / "flow" / "rc3" / "03_a_report.json"
    json.dump({"result": "FAIL"}, open(rp, "w"))
    p.write_text(json.dumps(dict(kind="a_report", task="rc3", sha256=g._file_sha(str(rp)))) + "\n")   # legacy-prefix line
    assert rc.verify(str(p))[0] and g.logged_report_sha("rc3") is None
    with pytest.raises(g.Refused, match="only after a FAIL report"): g.takeover("rc3", "C did the work.")


# ---- CSO re-screen 2 (non-blocking): batch adjusts count only logged ADJUST records ----
def _round(g, t, decision, b):
    rp = g.tmp / "flow" / t / "03_a_report.md"; m = os.path.getmtime(rp) + 5; os.utime(rp, (m, m))
    g.c_review(t, decision, "fix it", batch_id=b)


def _forge_batch(g, b, t):
    bs = g.batch(b); bs.update(rounds=3, adjusts=3); json.dump(bs, open(g._batch_path(b), "w"))
    tk = json.load(open(g.tmp / "flow" / t / "01_task.json")); tk["batch"] = b; json.dump(tk, open(g.tmp / "flow" / t / "01_task.json", "w"))


def test_real_adjust_plus_forged_batch_adjusts_3_refused(g):
    g.delegate("CTO", task(g.tmp, text="Explain the lock."), "ba1", batch_id="BA1"); _round(g, "ba1", "ADJUST", "BA1")
    _forge_batch(g, "BA1", "ba1")
    assert g.batch("BA1")["adjusts"] == 3 and g.logged_adjust_rounds("BA1") == 1
    with pytest.raises(g.Refused, match="only after a FAIL report or review round 3"): g.takeover("ba1", "C did the work.")


def test_forged_batch_without_any_adjust_refused(g):
    g.delegate("CTO", task(g.tmp, text="Explain the lock."), "ba2")
    os.makedirs(os.path.dirname(g._batch_path("BA2")), exist_ok=True)
    json.dump(dict(batch="BA2", tasks=["ba2"], rounds=3, adjusts=3, reviews=[]), open(g._batch_path("BA2"), "w"))
    tk = json.load(open(g.tmp / "flow" / "ba2" / "01_task.json")); tk["batch"] = "BA2"; json.dump(tk, open(g.tmp / "flow" / "ba2" / "01_task.json", "w"))
    assert g.logged_adjust_rounds("BA2") == 0
    with pytest.raises(g.Refused, match="only after a FAIL report or review round 3"): g.takeover("ba2", "C did the work.")


def test_three_logged_adjust_rounds_allow_takeover(g):
    g.delegate("CTO", task(g.tmp, text="Explain the lock."), "ba3", batch_id="BA3")
    for i in range(3):
        _round(g, "ba3", "ADJUST", "BA3")
        if i < 2: g.delegate("CTO", task(g.tmp, text="x"), "ba3", batch_id="BA3")
    assert g.logged_adjust_rounds("BA3") == 3
    g.takeover("ba3", "C did the work: wrote the note directly.")
    assert g.task("ba3")["state"] == "CLOSED"
    assert [r for r in log(g) if r["kind"] == "takeover"][-1]["rounds"] == 3


def test_unchained_review_lines_do_not_count(g):
    p = g.tmp / "flow" / "route_log.jsonl"; os.makedirs(p.parent, exist_ok=True)
    p.write_text("".join(json.dumps(dict(kind="review", batch="BA4", decision="ADJUST", round=r)) + "\n" for r in (1, 2, 3)))
    assert g.logged_adjust_rounds("BA4") == 0
