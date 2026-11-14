"""The shipped synthetic samples stay valid: the sample action log chain verifies, every sample profile name
parses as '<emoji> <ACR> · <title>' for a known C-role, and the sample charter matches its committed pin."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, sys
from pathlib import Path
sys.path.insert(0, GB_ROOT + "/scripts")
import action_log, alignment_check as ac
from croles import CROLES_ORDER


def test_sample_action_log_chain_verifies():
    ok, msg = action_log.verify(GB_ROOT + "/samples/action_log.sample.jsonl")[:2]
    assert ok, msg


def test_sample_profiles_cover_every_c_role():
    got = set()
    for p in Path(GB_ROOT, "samples", "profiles").glob("*/profile.json"):
        m = ac.NAME_RE.match(json.loads(p.read_text())["name"]); assert m, p
        got.add(m.group(2))
    assert got == set(CROLES_ORDER)


def test_sample_charter_matches_pin():
    r = ac.load_charter_snapshot(Path(GB_ROOT, "alignment", "charter_sample.md"))
    assert r["integrity"] == "PASS", r["note"]
