"""#36 C8 gap plus the re-screen:
queue_read close writes done/ through line_guard.escalate_write (exclusive O_CREAT|O_EXCL|O_NOFOLLOW), never a
raw json.dump; refuses a symlinked or out-of-tree queue/done; --legacy-fill is narrowly gated. tmp GB_STATE
only; no real alarm is closed or filled."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import importlib, inspect, json, os, sys, time, pytest
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, GB_ROOT + "/scripts")


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path))
    import queue_read, line_guard
    importlib.reload(line_guard); importlib.reload(queue_read)
    queue_read.tmp = tmp_path
    return queue_read


def _item(q, sub, name, rec):
    d = q.tmp / "queue" / sub; d.mkdir(parents=True, exist_ok=True)
    p = d / name; p.write_text(json.dumps(rec)); return p


ES = dict(line="ES-CSO", **{"from": "CSO"}, to="Owner", kind="relay audit broken", verdict="ALARM", reason="test", ts=1.0)
RQ = dict(line="RQ-CFO", **{"from": "CFO"}, to="CBO", kind="loss floor", ts=1.0)
CUTOFF = datetime(2034, 1, 13, 0, 0, tzinfo=ZoneInfo("UTC"))
# a ts clearly before the cutoff, as a unix timestamp
PRE_TS = datetime(2034, 1, 12, 21, 24, tzinfo=ZoneInfo("UTC")).timestamp()
LEGACY = dict(line="ES-CSO", to=["CSO", "Owner"], raised_by="CAO", kind="alignment team FAIL",
              verdict="ALARM", reason="test legacy", ts=PRE_TS)


@pytest.mark.parametrize("sub,rec", [("es_cso", ES), ("rq", RQ)])
def test_close_goes_through_guarded_writer(q, monkeypatch, sub, rec):
    import line_guard
    seen = []
    real = line_guard.escalate_write
    def wrap(path, r, lines=None, exclusive=False):
        seen.append((path, r, exclusive))
        return real(path, r, lines, exclusive=exclusive)
    monkeypatch.setattr(line_guard, "escalate_write", wrap)
    p = _item(q, sub, "x.json", rec)
    dst = q.close(str(p), "Owner t999u: test answer")
    assert seen and seen[0][0] == dst and seen[0][2] is True
    assert json.load(open(dst))["answer"] == "Owner t999u: test answer"
    assert not p.exists() and os.path.dirname(dst) == str(q.tmp / "queue" / "done")


def test_bad_line_refused_original_kept_fallback_dropped(q):
    import line_guard
    p = _item(q, "es_cso", "bad.json", dict(ES, **{"from": "CBO"}))   # ES-CSO must be CSO -> Owner
    with pytest.raises(line_guard.LineRefused): q.close(str(p), "Owner t999u: x")
    assert p.exists() and not (q.tmp / "queue" / "done" / "bad.json").exists()
    assert list((q.tmp / "alarms" / "fallback").glob("*-bad.json"))


@pytest.mark.parametrize("case", ["no_answer", "blank_answer", "outside_queue", "done_dir", "symlink", "existing_done", "not_json_name"])
def test_close_refusals(q, tmp_path, case):
    p = _item(q, "es_cso", "a.json", ES); ans = "Owner t999u: x"
    if case == "no_answer": ans = None
    elif case == "blank_answer": ans = "  "
    elif case == "outside_queue": p = tmp_path / "elsewhere.json"; p.write_text(json.dumps(ES))
    elif case == "done_dir": p = _item(q, "done", "d.json", ES)
    elif case == "symlink": s = q.tmp / "queue" / "es_cso" / "s.json"; os.symlink(p, s); p = s
    elif case == "existing_done":
        _item(q, "done", "a.json", dict(ES, answer="earlier"))
    elif case == "not_json_name": p = _item(q, "es_cso", "a.txt", ES)
    with pytest.raises(ValueError, match="REFUSED"): q.close(str(p), ans)
    if case == "existing_done": assert json.load(open(q.tmp / "queue" / "done" / "a.json"))["answer"] == "earlier"
    assert (q.tmp / "queue" / "es_cso" / "a.json").exists()


def test_close_has_no_raw_json_dump():
    import queue_read
    src = inspect.getsource(queue_read.close)
    assert "escalate_write" in src and "exclusive=True" in src and "json.dump(" not in src


def test_done_dir_symlink_refused_outside_untouched(q, tmp_path):
    victim = tmp_path / "outside"; victim.mkdir(); marker = victim / "keep.txt"; marker.write_text("safe")
    (q.tmp / "queue").mkdir(parents=True, exist_ok=True)
    os.symlink(victim, q.tmp / "queue" / "done")
    p = _item(q, "es_cso", "a.json", ES)
    with pytest.raises(ValueError, match="queue/done"): q.close(str(p), "Owner t999u: x")
    assert p.exists() and marker.read_text() == "safe" and not (victim / "a.json").exists()


def test_exclusive_write_flags_and_symlink_not_followed(q, tmp_path):
    import line_guard
    src = inspect.getsource(line_guard.guarded_write)
    assert "O_EXCL" in src and "O_NOFOLLOW" in src and "O_CREAT" in src
    victim = tmp_path / "victim.json"; victim.write_text('{"keep": true}')
    dest = tmp_path / "dest.json"; os.symlink(victim, dest)
    rec = dict(ES)
    with pytest.raises(OSError):
        line_guard.guarded_write(str(dest), rec, exclusive=True)
    assert json.loads(victim.read_text()) == {"keep": True} and dest.is_symlink()
    # existing regular file is not overwritten
    regular = tmp_path / "reg.json"; regular.write_text('{"keep": true}')
    with pytest.raises(OSError):
        line_guard.guarded_write(str(regular), rec, exclusive=True)
    assert json.loads(regular.read_text()) == {"keep": True}


def test_exclusive_write_succeeds_on_fresh_path(q, tmp_path):
    import line_guard
    dest = tmp_path / "fresh.json"
    line_guard.guarded_write(str(dest), dict(ES), exclusive=True)
    assert json.load(open(dest))["line"] == "ES-CSO" and not dest.is_symlink()


# ---- legacy-fill option (b) ----
def _legacy(q, name="alignment-TEAM-deadbeef.json", **kw):
    rec = dict(LEGACY, **kw)
    return _item(q, "es_cso", name, rec), rec


def test_legacy_fill_happy_path(q, monkeypatch):
    import line_guard
    seen = []
    real = line_guard.escalate_write
    def wrap(path, r, lines=None, exclusive=False):
        seen.append((path, r, exclusive))
        return real(path, r, lines, exclusive=exclusive)
    monkeypatch.setattr(line_guard, "escalate_write", wrap)
    p, rec = _legacy(q)
    out = q.legacy_fill(str(p))
    assert out == str(p) and p.exists()
    got = json.load(open(p))
    assert got["from"] == "CSO" and got["to"] == "Owner"
    assert got["legacy_filled"] == ["from", "to"]
    assert got["legacy_original"] == rec
    assert got["legacy_original"].get("from") is None and "from" not in rec
    assert seen and seen[0][2] is True
    assert not list((q.tmp / "queue" / "done").glob("*"))   # not closed
    # staging file must not remain
    assert not (q.tmp / "queue" / "es_cso" / (p.name + ".legacy-fill")).exists()


@pytest.mark.parametrize("case,kwargs,name,sub", [
    ("not_es_cso", {}, "alignment-TEAM-deadbeef.json", "rq"),
    ("bad_name", {}, "other-TEAM-deadbeef.json", "es_cso"),
    ("line", {"line": "ES-CBO"}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("from_present", {"from": "CBO"}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("from_present_cso", {"from": "CSO"}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("to_string", {"to": "Owner"}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("to_swapped", {"to": ["Owner", "CSO"]}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("to_short", {"to": ["CSO"]}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("raised_by", {"raised_by": "CSO"}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("kind", {"kind": "relay audit broken"}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("ts_at_cutoff", {"ts": CUTOFF.timestamp()}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("ts_after", {"ts": CUTOFF.timestamp() + 1}, "alignment-TEAM-deadbeef.json", "es_cso"),
    ("ts_missing", {"ts": None}, "alignment-TEAM-deadbeef.json", "es_cso"),
])
def test_legacy_fill_each_gate_fails(q, tmp_path, case, kwargs, name, sub):
    rec = dict(LEGACY, **kwargs)
    if case == "ts_missing":
        rec.pop("ts", None)
    p = _item(q, sub, name, rec)
    original = p.read_text()
    with pytest.raises(ValueError, match="REFUSED"):
        q.legacy_fill(str(p))
    assert p.read_text() == original   # untouched


def test_legacy_fill_accepts_string_ts_before_cutoff(q):
    p, _ = _legacy(q, ts="2034-01-12 21:24:00 UTC")
    q.legacy_fill(str(p))
    assert json.load(open(p))["from"] == "CSO"


def test_legacy_fill_from_absent_ok_but_empty_string_refused(q):
    p, rec = _legacy(q)
    assert "from" not in rec
    # empty string is present and not None
    p2, _ = _legacy(q, name="alignment-TEAM-emptyfrom.json", **{"from": ""})
    with pytest.raises(ValueError, match="from"):
        q.legacy_fill(str(p2))
