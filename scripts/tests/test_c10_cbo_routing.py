"""C10: an alignment FAIL involving CBO goes to ES-CSO and Owner, not only RQ-CBO."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, sys
sys.path.insert(0, GB_ROOT + "/scripts")
import alignment_check as ac


def test_cbo_fail_goes_to_es_cso_and_owner(tmp_path):
    out = ac.write_alarms({"bots": [dict(acro="CBO", result="FAIL", note="ack missing")]}, state=str(tmp_path))
    assert {os.path.basename(os.path.dirname(p)) for p in out} == {"es_cso", "owner"}
    assert not (tmp_path / "queue" / "rq").exists()
    rec = json.load(open([p for p in out if "/es_cso/" in p][0])); assert rec["line"] == "ES-CSO" and "Owner" in rec["to"]


def test_other_fail_rq_cao_held_alarmed_and_deduped(tmp_path):
    p = {"bots": [dict(acro="CMO", result="FAIL", note="x"), dict(acro="CRO", result="HELD", note="hold")]}
    out = ac.write_alarms(p, state=str(tmp_path))
    assert len(out) == 2 and all("/rq/" in o for o in out) and json.load(open(out[0]))["to"] == "CBO"
    held = [json.load(open(o)) for o in out if "-held-" in o]
    assert len(held) == 1 and held[0]["kind"] == "alignment HELD" and held[0]["subject"] == "CRO"   # #36 C10
    assert ac.write_alarms(p, state=str(tmp_path)) == []   # repeated wake: no spam


def test_rq_record_is_on_its_line(tmp_path):
    import line_guard
    out = ac.write_alarms({"bots": [dict(acro="CMO", result="FAIL", note="y")]}, state=str(tmp_path))
    assert line_guard.check_record(json.load(open(out[0]))) == []


def test_team_level_fails_alarm_rq_and_es_cso(tmp_path):                       # #36 C10
    p = {"bots": [], "integrity_fail": True, "charter_integrity_note": "pin mismatch", "aso_sso_independence": "FAIL",
         "helper_independence": {"AAO/SAO": {"status": "FAIL", "note": "share"}, "ACO/SCO": {"status": "PASS"}},
         "packet_status": {"CXO": "FAIL", "CAO": "PASS"}, "missing_role": True, "cost_limits": {"result": "FAIL", "note": "null"}}
    out = ac.write_alarms(p, state=str(tmp_path))
    rq = [o for o in out if "/rq/" in o]; es = [o for o in out if "/es_cso/" in o]
    assert len(rq) == len(es) == 6
    assert {json.load(open(o))["check"] for o in es} == {"charter_integrity", "aso_sso_independence", "helper_independence AAO/SAO",
                                                         "packet CXO", "missing_role", "cost_limits"}
    assert ac.write_alarms(p, state=str(tmp_path)) == []
