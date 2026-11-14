"""CIO: onboarding packets are write-once; only the CAO packet build (with an action_log line) changes one.
Temp dirs only. Accident guard plus detection, not security."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, stat, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import packet_guard as pg
import action_log


@pytest.fixture
def env(tmp_path):
    d = tmp_path / "packets"; d.mkdir()
    (d / "ZZO.md").write_text("v1\n")
    m, log = tmp_path / "manifest.json", tmp_path / "action_log.jsonl"
    r = pg.build("ZZO.md", by="CAO", keep=True, item="pin ZZO", reason="test", packets=d, manifest=m, log=str(log))
    return d, m, log, r


def test_build_pins_with_action_log_line_and_read_only(env):
    d, m, log, r = env
    assert pg.problems(d, m) == []
    lines = [json.loads(l) for l in open(log)]
    assert len(lines) == 1 and lines[0]["packet"] == "ZZO.md" and lines[0]["hash"] == r["action_log_hash"]
    assert action_log.verify(str(log))[0]
    assert not (os.stat(d / "ZZO.md").st_mode & stat.S_IWUSR)
    assert json.loads(m.read_text())["packets"]["ZZO.md"]["action_log_hash"] == r["action_log_hash"]


def test_direct_write_is_refused_and_detected(env):
    d, m, log, _ = env
    with pytest.raises(PermissionError):
        (d / "ZZO.md").write_text("edited by someone else\n")
    os.chmod(d / "ZZO.md", 0o644); (d / "ZZO.md").write_text("edited after chmod\n")      # deliberate bypass
    assert any("changed outside the CAO packet build" in p for p in pg.problems(d, m))


def test_new_or_removed_packet_detected(env):
    d, m, _, _ = env
    (d / "NEW.quiz.json").write_text("{}")
    assert any("NEW.quiz.json: not in the packet manifest" in p for p in pg.problems(d, m))
    (d / "NEW.quiz.json").unlink(); os.chmod(d / "ZZO.md", 0o644); (d / "ZZO.md").unlink()
    assert any("pinned packet missing" in p for p in pg.problems(d, m))


@pytest.mark.parametrize("by", ["CXO", "CIO", "", "cxo executor"])
def test_other_writers_refused(env, tmp_path, by):
    d, m, log, _ = env
    src = tmp_path / "new.md"; src.write_text("v2\n")
    with pytest.raises(SystemExit, match="only the CAO packet build"):
        pg.build("ZZO.md", by=by, src=src, item="x", reason="y", packets=d, manifest=m, log=str(log))
    assert (d / "ZZO.md").read_text() == "v1\n"


def test_cao_build_changes_packet_with_line(env, tmp_path):
    d, m, log, _ = env
    src = tmp_path / "new.md"; src.write_text("v2\n")
    r = pg.build("ZZO.md", by="CAO", src=src, item="rebuild ZZO", reason="test", packets=d, manifest=m, log=str(log))
    assert (d / "ZZO.md").read_text() == "v2\n" and pg.problems(d, m) == []
    assert [json.loads(l)["hash"] for l in open(log)][-1] == r["action_log_hash"]


def test_pending_and_missing_manifest_fail_closed(env, tmp_path):
    d, m, _, _ = env
    mm = json.loads(m.read_text()); mm["pending"] = {"ZZO.md": {"reason": "unreviewed"}}; m.write_text(json.dumps(mm))
    assert any("pending" in p for p in pg.problems(d, m))
    assert "fail closed" in pg.problems(d, tmp_path / "gone.json")[0]


def test_build_refuses_without_item_or_bad_name(env, tmp_path):
    d, m, log, _ = env
    with pytest.raises(SystemExit):
        pg.build("ZZO.md", by="CAO", keep=True, item="", reason="r", packets=d, manifest=m, log=str(log))
    with pytest.raises(SystemExit):
        pg.build("../x.md", by="CAO", keep=True, item="i", reason="r", packets=d, manifest=m, log=str(log))

