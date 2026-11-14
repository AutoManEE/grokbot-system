"""Side task (CIO option A): Charter integrity uses the committed pin, never local .sha256 files."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import hashlib, json, shutil, sys
from pathlib import Path
import pytest
sys.path.insert(0, GB_ROOT + "/scripts")
import alignment_check as ac

HDR = b"<!-- meta\nrevisionId: X\n-->\r\n\n"
BODY = b"# Charter\n\n## Roles added after the brief\n\nCRO \xc2\xb7 Chief Recruiting Officer\n"


def _setup(tmp_path):
    snap = tmp_path / "charter_snapshot_2034-01-09.md"; snap.write_bytes(HDR + BODY)
    pin = tmp_path / "charter_pin.json"
    pin.write_text(json.dumps({"snapshot": snap.name,
                               "body_sha256": hashlib.sha256(BODY).hexdigest(),
                               "file_sha256": hashlib.sha256(HDR + BODY).hexdigest()}))
    return snap, pin


def test_pin_match_passes(tmp_path):
    snap, pin = _setup(tmp_path)
    r = ac.load_charter_snapshot(snap, pin)
    assert r["integrity"] == "PASS" and r["added_roles"] == {"CRO"}


def test_edited_local_sha256_cannot_make_it_pass(tmp_path):
    snap, pin = _setup(tmp_path)
    snap.write_bytes(HDR + BODY + b"\nedited\n")
    forged = hashlib.sha256(BODY + b"\nedited\n").hexdigest()
    (tmp_path / "charter_snapshot_2034-01-09.sha256").write_text(f"{forged}  {snap.name}\n")
    (tmp_path / "charter_snapshot_2034-01-09.file.sha256").write_text(
        f"{hashlib.sha256(snap.read_bytes()).hexdigest()}  {snap.name}\n")
    r = ac.load_charter_snapshot(snap, pin)
    assert r["integrity"] == "FAIL" and "pin" in r["note"]


def test_header_only_change_fails_on_file_hash(tmp_path):
    snap, pin = _setup(tmp_path)
    snap.write_bytes(b"<!-- other\n-->\n" + BODY)
    assert ac.load_charter_snapshot(snap, pin)["integrity"] == "FAIL"


def test_missing_snapshot_or_pin_is_unverifiable(tmp_path):
    snap, pin = _setup(tmp_path)
    pin.unlink()
    assert ac.load_charter_snapshot(snap, pin)["integrity"] == "UNVERIFIABLE"
    snap, pin = _setup(tmp_path); snap.unlink()
    assert ac.load_charter_snapshot(snap, pin)["integrity"] == "UNVERIFIABLE"


def test_pin_for_other_snapshot_is_unverifiable(tmp_path):
    snap, pin = _setup(tmp_path)
    other = tmp_path / "charter_snapshot_2034-02-01.md"; shutil.copy(snap, other)
    assert ac.load_charter_snapshot(other, pin)["integrity"] == "UNVERIFIABLE"


def test_live_pin_matches_local_snapshot():
    if not ac.DEFAULT_CHARTER.is_file():
        pytest.skip("no local snapshot (it is never committed)")
    pin = json.loads(ac.CHARTER_PIN.read_text())
    raw = ac.DEFAULT_CHARTER.read_bytes()
    match = hashlib.sha256(raw).hexdigest() == pin["file_sha256"]
    # A re-export without a new committed pin must FAIL, never PASS.
    assert ac.load_charter_snapshot(ac.DEFAULT_CHARTER)["integrity"] == ("PASS" if match else "FAIL")
