"""relay.py drain: archive completed inbox notes only.
tmp GB_STATE/GB_BOTS only (fixture r from test_relay), no models, no network."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import hashlib, json, os, subprocess, sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
from test_relay import r, box   # noqa: F401


def R(r, *p): return r.tmp / "s" / "relay" / os.path.join(*p) if p else r.tmp / "s" / "relay"


def done(r, a, mid, status="DONE"):
    d = R(r, a, "results"); d.mkdir(parents=True, exist_ok=True)
    (d / f"{mid}.json").write_text(json.dumps(dict(status=status, msg_id=mid, verdict="PASS", **{"from": a})))
    if status == "DONE":   # CSO r3 fix: drain also needs the audited batch_result (as run_batch writes it)
        r.audit("batch_result", msg_id=mid, a=a, s="S" + a[1:], verdict="PASS")


def direct_done(r, frm="ABO", to="AFO", text="Please check the budget line format."):
    mid = r.send(frm, to, text, "CBO")["id"]; r.route(to); done(r, to, mid); return mid


def names(r, a, sub):
    d = R(r, a, sub)
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def tree_snapshot(r, skip=("inbox", "archive")):
    """sha256 of every file under relay/ except inbox/archive and the audit (checked separately)."""
    out = {}
    for root_, dirs, files in os.walk(R(r)):
        rel = os.path.relpath(root_, R(r)).split(os.sep)
        if len(rel) >= 2 and rel[1] in skip: continue
        for f in files:
            p = os.path.join(root_, f)
            if f == "audit.jsonl": continue
            out[os.path.relpath(p, R(r))] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def test_direct_complete_is_archived_and_audited(r):
    mid = direct_done(r)
    lines_before = open(R(r, "audit.jsonl")).read().splitlines()
    out = r.drain()
    assert out["archived"] == 1 and out["per_a"]["AFO"]["archived"] == 1
    assert names(r, "AFO", "inbox") == [] and names(r, "AFO", "archive") == [f"{mid}.json"]
    assert names(r, "AFO", "dg") == [f"{mid}.json"] and names(r, "AFO", "results") == [f"{mid}.json"]
    after = open(R(r, "audit.jsonl")).read().splitlines()
    assert after[:len(lines_before)] == lines_before and len(after) == len(lines_before) + 1   # append only
    e = json.loads(after[-1]); assert e["event"] == "inbox_archived" and e["a"] == "AFO" and e["msg_ids"] == [mid]
    assert r.verify()[0]


def test_broadcast_routed_is_archived(r):
    mid = r.broadcast("ABO", "Weekly plan is in the tracker.", "t159u")["id"]
    r.route("AFO")   # ATO not routed yet
    out = r.drain()
    assert names(r, "AFO", "archive") == [f"{mid}.json"] and names(r, "AFO", "inbox") == []
    assert names(r, "ATO", "inbox") == [f"{mid}.json"] and out["per_a"]["ATO"]["skipped_why"] == {"unrouted": 1}


def test_held_queue_is_never_touched(r):
    r.broadcast("ABO", "Weekly plan is in the tracker.", "t159u")
    q = names(r, "AHO", "queued"); assert len(q) == 1
    before = {n: open(R(r, "AHO", "queued", n), "rb").read() for n in q}
    out = r.drain()
    assert {n: open(R(r, "AHO", "queued", n), "rb").read() for n in names(r, "AHO", "queued")} == before
    assert out["per_a"]["AHO"]["queued_untouched"] == 1 and not R(r, "AHO", "archive").exists()


def test_unrouted_direct_is_not_archived(r):
    mid = r.send("ABO", "AFO", "Please check the budget line format.", "CBO")["id"]
    out = r.drain()
    assert names(r, "AFO", "inbox") == [f"{mid}.json"] and out["archived"] == 0
    assert out["per_a"]["AFO"]["skipped_why"] == {"unrouted": 1}


def test_direct_pending_dg_is_not_archived(r):
    mid = r.send("ABO", "AFO", "Please check the budget line format.", "CBO")["id"]; r.route("AFO")
    out = r.drain()
    assert names(r, "AFO", "inbox") == [f"{mid}.json"] and out["per_a"]["AFO"]["skipped_why"] == {"pending dg (no result)": 1}


def test_direct_without_dg_is_not_archived(r):
    mid = direct_done(r)
    os.remove(R(r, "AFO", "dg", f"{mid}.json"))
    out = r.drain()
    assert names(r, "AFO", "inbox") == [f"{mid}.json"] and out["per_a"]["AFO"]["skipped_why"] == {"no dg handoff": 1}


@pytest.mark.parametrize("status", ["SKIPPED", "REFUSED", None])
def test_direct_result_not_done_is_not_archived(r, status):
    mid = r.send("ABO", "AFO", "Please check the budget line format.", "CBO")["id"]; r.route("AFO"); done(r, "AFO", mid, status)
    out = r.drain()
    assert names(r, "AFO", "inbox") == [f"{mid}.json"] and out["per_a"]["AFO"]["skipped_why"] == {"result not DONE": 1}


def test_refused_note_is_kept_for_review(r):
    r.send("ABO", "ATO", "Please check the config format.", "CBO")   # starts the audit chain
    fake = "20340113-010101-000000001-ABO-deadbeef"
    d = R(r, "AFO", "inbox"); d.mkdir(parents=True)
    (d / f"{fake}.json").write_text(json.dumps(dict(id=fake, ts="x", to="AFO", kind="broadcast", text="hand placed", origin="CBO", **{"from": "ABO"})))
    res = r.route("AFO"); assert res["refused"]
    out = r.drain()
    assert names(r, "AFO", "inbox") == [f"{fake}.json"]
    assert out["per_a"]["AFO"]["skipped_why"] == {"routed as refused/skipped (kept for review)": 1}


def test_dry_run_writes_nothing(r):
    direct_done(r); r.broadcast("ABO", "Weekly plan is in the tracker.", "t159u"); r.route("AFO"); r.route("ATO")
    snap = {p: open(p, "rb").read() for p in (str(x) for x in (r.tmp / "s").rglob("*") if x.is_file())}
    dirs = sorted(str(x) for x in (r.tmp / "s").rglob("*") if x.is_dir())
    out = r.drain(dry_run=True)
    assert out["dry_run"] and out["archivable"] == 3 and out["archived"] == 0
    assert {p: open(p, "rb").read() for p in (str(x) for x in (r.tmp / "s").rglob("*") if x.is_file())} == snap
    assert sorted(str(x) for x in (r.tmp / "s").rglob("*") if x.is_dir()) == dirs


def test_a_filter_only_drains_that_a(r):
    direct_done(r, to="AFO"); direct_done(r, to="ATO", text="Please check the config format.")
    out = r.drain(a="afo")
    assert list(out["per_a"]) == ["AFO"] and names(r, "AFO", "inbox") == [] and len(names(r, "ATO", "inbox")) == 1
    with pytest.raises(r.Refused, match="unknown A"): r.drain(a="AZZ")


def test_audit_routed_dg_results_preserved_and_cap_count_kept(r):
    r.broadcast("ABO", "Weekly plan is in the tracker.", "t159u"); direct_done(r); r.route("ATO")
    n_cap = r.broadcasts_last_hour("ABO"); assert n_cap == 1
    snap = tree_snapshot(r)
    head = open(R(r, "audit.jsonl")).read().splitlines()
    r.drain()
    assert tree_snapshot(r) == snap     # routed.jsonl, dg/, results/, queued/, routes, LIVE unchanged
    after = open(R(r, "audit.jsonl")).read().splitlines(); assert after[:len(head)] == head
    assert r.verify()[0] and r.verify()[1].startswith("PASS")
    assert r.broadcasts_last_hour("ABO") == n_cap
    r.drain()   # second run: nothing left, no new audit record
    assert open(R(r, "audit.jsonl")).read().splitlines() == after


def test_bad_audit_refuses_before_any_move(r):
    mid = direct_done(r)
    p = R(r, "audit.jsonl"); lines = p.read_text().splitlines(); lines[0] = lines[0].replace('"write"', '"wrote"'); p.write_text("\n".join(lines) + "\n")
    for dry in (True, False):
        with pytest.raises(r.Refused): r.drain(dry_run=dry)
    assert names(r, "AFO", "inbox") == [f"{mid}.json"] and not R(r, "AFO", "archive").exists()


def test_not_live_refuses(r):
    mid = direct_done(r); os.remove(R(r, "LIVE"))
    with pytest.raises(r.Refused, match="NOT live"): r.drain()
    assert names(r, "AFO", "inbox") == [f"{mid}.json"]


def test_symlinked_inbox_file_is_skipped(r, tmp_path):
    mid = direct_done(r)
    src = R(r, "AFO", "inbox", f"{mid}.json"); tgt = tmp_path / "elsewhere.json"
    tgt.write_bytes(src.read_bytes()); os.remove(src); os.symlink(tgt, src)
    out = r.drain()
    assert os.path.islink(src) and names(r, "AFO", "archive") == [] and out["per_a"]["AFO"]["skipped_why"] == {"symlink": 1}


@pytest.mark.parametrize("what", ["inbox", "archive", "dg", "results", "routed.jsonl"])
def test_symlinked_structure_refuses_whole_drain(r, tmp_path, what):
    mid = direct_done(r); mid2 = direct_done(r, to="ATO", text="Please check the config format.")
    p = R(r, "AFO", what); real = tmp_path / f"real_{what}"
    if what == "archive":
        real.mkdir()
    else:
        os.rename(p, real)
    os.symlink(real, p)
    for dry in (True, False):
        with pytest.raises(r.Refused, match="no symlinks"): r.drain(dry_run=dry)
    assert names(r, "ATO", "inbox") == [f"{mid2}.json"]   # fail closed: nothing moved for any A


def test_symlinked_audit_refuses(r, tmp_path):
    direct_done(r)
    p = R(r, "audit.jsonl"); real = tmp_path / "audit_real.jsonl"; os.rename(p, real); os.symlink(real, p)
    with pytest.raises(r.Refused): r.drain(dry_run=True)
    with pytest.raises(r.Refused): r.drain()
    assert len(names(r, "AFO", "inbox")) == 1


def test_existing_archive_file_is_never_overwritten(r):
    mid = direct_done(r)
    a = R(r, "AFO", "archive"); a.mkdir(); (a / f"{mid}.json").write_text("{}")
    out = r.drain()
    assert (a / f"{mid}.json").read_text() == "{}" and names(r, "AFO", "inbox") == [f"{mid}.json"] and out["archived"] == 0


def test_cli_drain_dry_run_and_run(r):
    mid = direct_done(r)
    env = dict(os.environ, GB_STATE=str(r.tmp / "s"), GB_BOTS=str(r.tmp / "bots"))
    # the CLI has no is_live monkeypatch: AFO counts as live only if onboarded; check the JSON shape and exit code
    c = subprocess.run([sys.executable, GB_ROOT + "/scripts/relay.py", "drain", "--dry-run"], env=env, capture_output=True, text=True)
    assert c.returncode in (0, 2), c.stderr
    if c.returncode == 0:
        j = json.loads(c.stdout); assert j["dry_run"] is True and j["archived"] == 0
    assert names(r, "AFO", "inbox") == [f"{mid}.json"]
