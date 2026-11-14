"""CSO r3 fix: relay drain hardening.
Audit-backed archiving only, link -> audit -> unlink with rollback/resume, full file identity, O_NOFOLLOW fd chain.
tmp GB_STATE/GB_BOTS only (fixture r from test_relay), no models, no network."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
from test_relay import r, box   # noqa: F401
from test_relay_drain import R, done, direct_done, names


def _forge_routed(r, a, mid):
    with open(R(r, a, "routed.jsonl"), "a") as f: f.write(json.dumps(dict(id=mid, ts="x")) + "\n")


def _result_file(r, a, mid):
    d = R(r, a, "results"); d.mkdir(parents=True, exist_ok=True)
    (d / f"{mid}.json").write_text(json.dumps(dict(status="DONE", msg_id=mid, verdict="PASS", **{"from": a})))


def _archived_ids(r):
    return [m for l in open(R(r, "audit.jsonl")) if '"inbox_archived"' in l for m in json.loads(l)["msg_ids"]]


def test_forged_routed_dg_and_result_archive_nothing(r):
    mid = r.send("ABO", "AFO", "Please check the budget line format.", "CBO")["id"]   # never routed
    _forge_routed(r, "AFO", mid)
    d = R(r, "AFO", "dg"); d.mkdir(parents=True, exist_ok=True); (d / f"{mid}.json").write_text("{}")
    _result_file(r, "AFO", mid)
    out = r.drain()
    assert out["archived"] == 0 and names(r, "AFO", "inbox") == [f"{mid}.json"]
    assert out["per_a"]["AFO"]["skipped_why"] == {"no audited dg_handoff": 1}


def test_routed_but_hand_written_result_is_not_archived(r):
    mid = r.send("ABO", "AFO", "Please check the budget line format.", "CBO")["id"]; r.route("AFO")
    _result_file(r, "AFO", mid)   # no batch_result record
    out = r.drain()
    assert out["archived"] == 0 and out["per_a"]["AFO"]["skipped_why"] == {"no audited batch_result": 1}


def test_hand_placed_note_with_forged_routed_line_is_not_archived(r):
    r.send("ABO", "ATO", "Please check the config format.", "CBO")   # starts the chain
    mid = "20340113-120000-000000001-ABO-deadbeef"
    d = R(r, "AFO", "inbox"); d.mkdir(parents=True, exist_ok=True)
    (d / f"{mid}.json").write_text(json.dumps({"id": mid, "kind": "broadcast", "from": "ABO", "to": "ALL", "text": "x", "origin": "CBO", "ts": "x"}))
    _forge_routed(r, "AFO", mid)
    out = r.drain()
    assert out["archived"] == 0 and names(r, "AFO", "inbox") == [f"{mid}.json"]
    assert list(out["per_a"]["AFO"]["skipped_why"]) == ["not authentic (no matching audit write record (hand-placed inbox file?))"]


def test_audit_failure_rolls_back_every_link(r, monkeypatch):
    mid = direct_done(r); mid2 = r.broadcast("ABO", "Weekly plan is in the tracker.", "t159u")["id"]; r.route("AFO")
    real = r.audit
    def boom(ev, *a, **k):
        if ev == "inbox_archived": raise r.Refused("simulated audit failure")
        return real(ev, *a, **k)
    monkeypatch.setattr(r, "audit", boom)
    with pytest.raises(r.Refused): r.drain(a="AFO")
    assert names(r, "AFO", "inbox") == sorted([f"{mid}.json", f"{mid2}.json"]) and names(r, "AFO", "archive") == []
    assert _archived_ids(r) == []


def test_interrupted_drain_is_finished_and_audited_on_rerun(r, monkeypatch):
    mid = direct_done(r)
    real_unlink = os.unlink
    def crash(p, *a, **k):
        if str(p).endswith(".json") and k.get("dir_fd") is not None: raise KeyboardInterrupt("simulated crash")
        return real_unlink(p, *a, **k)
    monkeypatch.setattr(os, "unlink", crash)
    with pytest.raises(KeyboardInterrupt): r.drain()
    monkeypatch.setattr(os, "unlink", real_unlink)
    assert names(r, "AFO", "inbox") == names(r, "AFO", "archive") == [f"{mid}.json"]   # audited, unlink not done
    out = r.drain()
    assert out["archived"] == 1 and names(r, "AFO", "inbox") == [] and names(r, "AFO", "archive") == [f"{mid}.json"]
    assert mid in _archived_ids(r) and r.verify()[0]


def test_different_file_in_archive_is_still_never_overwritten(r):
    mid = direct_done(r)
    a = R(r, "AFO", "archive"); a.mkdir(); (a / f"{mid}.json").write_text("{}")
    out = r.drain()
    assert (a / f"{mid}.json").read_text() == "{}" and names(r, "AFO", "inbox") == [f"{mid}.json"] and out["archived"] == 0
    assert out["per_a"]["AFO"]["skipped_why"] == {"already in archive (kept in inbox)": 1}


def test_broadcast_cap_not_reset_by_drain(r):
    for i in range(5): r.broadcast("ABO", f"Plan update number {i} is in the tracker.", "t159u")
    r.route("AFO"); r.route("ATO")
    assert r.drain()["archived"] == 10
    assert r.broadcasts_last_hour("ABO") == 5
    with pytest.raises(r.Refused, match="broadcast cap"): r.broadcast("ABO", "Sixth plan update is in the tracker.", "t159u")


def test_hwm_advances_one_line_per_drained_a_and_head_is_kept(r):
    direct_done(r, to="AFO"); direct_done(r, to="ATO", text="Please check the config format.")
    head = open(R(r, "audit.jsonl")).read().splitlines()
    hw0 = json.load(open(r.hwm_path()))["lines"]
    r.drain()
    after = open(R(r, "audit.jsonl")).read().splitlines()
    assert after[:len(head)] == head and len(after) == len(head) + 2
    assert json.load(open(r.hwm_path()))["lines"] == hw0 + 2 and r.verify()[0]


def test_delete_and_recreate_between_plan_and_move_is_detected(r, monkeypatch):
    mid = direct_done(r)
    real_plan = r._drain_plan
    def plan_then_swap(al):
        p = real_plan(al)
        f = R(r, "AFO", "inbox", f"{mid}.json"); b = f.read_bytes(); os.remove(f); f.write_bytes(b.replace(b"budget", b"BUDGET"))
        return p
    monkeypatch.setattr(r, "_drain_plan", plan_then_swap)
    out = r.drain()
    assert out["archived"] == 0 and out["per_a"]["AFO"]["skipped_why"].get("changed before move") == 1


def test_a_dir_swapped_for_symlink_after_plan_refuses(r, monkeypatch, tmp_path):
    mid = direct_done(r)
    real_plan = r._drain_plan
    ev = tmp_path / "evil"; ev.mkdir()
    def plan_then_swap(al):
        p = real_plan(al)
        os.rename(R(r, "AFO"), tmp_path / "AFO_real"); os.symlink(ev, R(r, "AFO"))
        return p
    monkeypatch.setattr(r, "_drain_plan", plan_then_swap)
    with pytest.raises((r.Refused, OSError)): r.drain()
    assert os.listdir(ev) == [] and (tmp_path / "AFO_real" / "inbox" / f"{mid}.json").exists()


def test_archive_swapped_for_symlink_after_plan_refuses(r, monkeypatch, tmp_path):
    mid = direct_done(r)
    real_plan = r._drain_plan
    ev = tmp_path / "evil_arch"; ev.mkdir()
    def plan_then_swap(al):
        p = real_plan(al)
        os.rmdir(R(r, "AFO", "archive")) if R(r, "AFO", "archive").exists() else None
        os.symlink(ev, R(r, "AFO", "archive"))
        return p
    monkeypatch.setattr(r, "_drain_plan", plan_then_swap)
    with pytest.raises(r.Refused, match="not a plain dir"): r.drain()
    assert os.listdir(ev) == [] and names(r, "AFO", "inbox") == [f"{mid}.json"]


@pytest.mark.parametrize("a", ["../AFO", "AFO/../ABO", "afo\n", "*", "AFO/inbox"])
def test_a_argument_cannot_traverse(r, a):
    direct_done(r)
    with pytest.raises(r.Refused, match="unknown A"): r.drain(a=a)


def test_fifo_and_dir_named_like_a_message_are_skipped(r):
    mid = direct_done(r)
    os.mkfifo(R(r, "AFO", "inbox", "20340113-120000-000000002-ABO-deadbeef.json"))
    (R(r, "AFO", "inbox") / "20340113-120000-000000003-ABO-deadbeef.json").mkdir()
    out = r.drain()
    assert out["archived"] == 1 and names(r, "AFO", "archive") == [f"{mid}.json"]


def test_result_for_another_msg_id_is_not_done(r):
    mid = r.send("ABO", "AFO", "Please check the budget line format.", "CBO")["id"]; r.route("AFO")
    d = R(r, "AFO", "results"); d.mkdir(parents=True, exist_ok=True)
    (d / f"{mid}.json").write_text(json.dumps(dict(status="DONE", msg_id="20340113-120000-000000009-ABO-deadbeef")))
    r.audit("batch_result", msg_id=mid, a="AFO", s="SFO", verdict="PASS")
    out = r.drain()
    assert out["archived"] == 0 and out["per_a"]["AFO"]["skipped_why"] == {"result not DONE": 1}


def test_dry_run_refuses_when_not_live_and_writes_nothing(r):
    direct_done(r); os.remove(R(r, "LIVE"))
    snap = sorted(str(p) for p in (r.tmp / "s").rglob("*"))
    with pytest.raises(r.Refused, match="NOT live"): r.drain(dry_run=True)
    assert sorted(str(p) for p in (r.tmp / "s").rglob("*")) == snap


def test_dry_run_refuses_on_a_shadow_module_in_bots(r):
    direct_done(r)
    (r.tmp / "bots" / "flow.py").write_text("# shadow\n")
    with pytest.raises(r.Refused, match="shadow"): r.drain(dry_run=True)
    assert len(names(r, "AFO", "inbox")) == 1
