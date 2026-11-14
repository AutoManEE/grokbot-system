"""C1 alignment_check: accident guard plus detection, not security."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, GB_ROOT + "/scripts")
import alignment_check as ac
import cost_gate
import flow
import gate


@pytest.fixture
def cro_rehold(tmp_path, monkeypatch):
    """Live HOLD-CRO-1 was released. To keep testing the
    quarantine path, re-hold CRO in a tmp held_roles.json."""
    import held_roles as _hr
    import alignment_check as _ac
    p = tmp_path / "held_rehold.json"; p.write_text('{"holds": [], "cosigns": []}')
    _hr.hold("CRO", "test: CRO re-held to exercise quarantine", "CAO", p)
    monkeypatch.setattr(_ac, "HELD_ROLES", p); return p


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _charter(path: Path, body: str) -> Path:
    data = body.encode()
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    # Committed-pin model (CIO option A): alignment_check compares with charter_pin.json only.
    (path.parent / "charter_pin.json").write_text(json.dumps({
        "snapshot": path.name, "body_sha256": digest, "file_sha256": digest}), encoding="utf-8")
    return path


def _settings(root: Path, hid: str, model="qwen3:4b-instruct") -> None:
    _write(root / hid / "settings.json", json.dumps({
        "id": hid, "kind": "assistant", "model": model, "tools": [],
    }))
    _write(root / hid / "prompt.md", f"prompt for {hid}\n")


def _isolate(monkeypatch, tmp: Path):
    agents = tmp / "agents"
    bots = tmp / "bots"
    brief = tmp / "brief.txt"
    amend = tmp / "brief_amendments.md"
    packets = tmp / "packets"
    acks = tmp / "acks"
    flowp = tmp / "flow.py"
    charter = tmp / "charter.md"
    agents.mkdir()
    bots.mkdir()
    packets.mkdir()
    acks.mkdir()
    (tmp / "no-held-roles.json").write_text('{"holds": [], "cosigns": []}')   # #36: missing now fails closed
    monkeypatch.setattr(ac, "HELD_ROLES", tmp / "no-held-roles.json")
    monkeypatch.setattr(ac, "ONBOARDED", tmp / "onboarded_roles.json")
    _write(tmp / "onboarded_roles.json", json.dumps({"roles": []}))
    # Start with no C-roles so packet_status does not require the live nine.
    _write(brief, "C-role = one of the 0 chief roles: \n")
    # The regex needs at least one acronym to parse; tests that need roles write their own list.
    _write(brief, "C-role = one of the 0 chief roles: XXX\n")
    _write(amend, "")
    _write(flowp, 'CROLES = set()\n')
    _charter(charter, "# Charter\n\n## Roles added after the brief\n\n")
    monkeypatch.setattr(ac, "AGENTS", agents)
    monkeypatch.setattr(ac, "BOTS", bots)
    monkeypatch.setattr(ac, "BRIEF", brief)
    monkeypatch.setattr(ac, "AMENDMENTS", amend)
    monkeypatch.setattr(ac, "PACKETS", packets)
    _write(tmp / "required_quizzes.json", json.dumps({"roles": []}))   # #36: committed required set (empty here)
    monkeypatch.setattr(ac, "REQUIRED_QUIZZES", tmp / "required_quizzes.json")
    monkeypatch.setattr(ac, "ACKS", acks)
    monkeypatch.setattr(ac, "FLOW", flowp)
    monkeypatch.setattr(ac, "DEFAULT_CHARTER", charter)
    monkeypatch.setattr(ac, "CHARTER_PIN", tmp / "charter_pin.json")
    monkeypatch.setattr(ac, "DEFAULT_TITLES", tmp / "no-titles.jsonl")
    return dict(agents=agents, bots=bots, brief=brief, amend=amend,
                packets=packets, acks=acks, flow=flowp, charter=charter)


def _profile(agents: Path, acro: str, name: str, description="A role that carries the brief.", title=""):
    d = agents / f"bot-{acro}"
    _write(d / "profile.json", json.dumps({
        "name": name, "description": description, "title": title,
    }))
    return d


def _complete_existing(env, acro="CZZ", name=None):
    """Add a fully passing existing brief C-role named CZZ (emoji + acronym + role)."""
    name = name or f"⚙️ {acro} · Chief Test Officer"
    _profile(env["agents"], acro, name, title="")
    env["brief"].write_text(
        f"C-role = one of the 1 chief roles: {acro}\n"
        f"DG-{acro} | do work\n"
        f"TK-{acro} | track work\n"
        f"RP-{acro} | report work\n"
        f"FX-{acro} | fix work\n",
        encoding="utf-8",
    )
    env["flow"].write_text(f'CROLES = {{"{acro}"}}\n', encoding="utf-8")
    (env["acks"].parent / "onboarded_roles.json").write_text(json.dumps({"roles": [acro]}), encoding="utf-8")
    a_id, s_id = "A" + acro[1:], "S" + acro[1:]
    _settings(env["bots"], a_id, model=f"model-{a_id}")
    _settings(env["bots"], s_id, model=f"model-{s_id}")
    pkt = _write(env["packets"] / f"{acro}.md", f"# {acro} packet\n")
    return acro, pkt


def test_short_name_fails(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    _profile(env["agents"], "CBO", "CBO Chief Bot Officer")  # missing emoji / middle-dot
    payload = ac.collect(charter_path=env["charter"])
    assert payload["bots"], payload
    row = payload["bots"][0]
    assert row["name_format"] == "FAIL"
    assert row["result"] == "FAIL"
    assert payload["result"] == "FAIL"


def test_missing_ack_new_role_fails(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    _charter(env["charter"], "# Charter\n\n## Roles added after the brief\n\nCRO · Chief Recruiting Officer\n")
    _profile(env["agents"], "CRO", "🥇 CRO · Chief Recruiting Officer")
    _write(env["packets"] / "CRO.md", "# CRO packet\n")
    payload = ac.collect(role="CRO", charter_path=env["charter"])
    row = payload["bots"][0]
    assert row["croles"] == "FAIL"
    assert row["a_helper"].startswith("FAIL")
    assert row["s_helper"].startswith("FAIL")
    assert row["ack"] == "FAIL"
    assert "missing" in row["ack_note"]
    assert row["result"] == "FAIL"


def test_stale_packet_hash_fails(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    _charter(env["charter"], "# Charter\n\n## Roles added after the brief\n\nCRO · Chief Recruiting Officer\n")
    _profile(env["agents"], "CRO", "🥇 CRO · Chief Recruiting Officer")
    pkt = _write(env["packets"] / "CRO.md", "# CRO packet\n")
    # also need a quiz 5-9 or quiz will FAIL too; that's fine
    _write(env["acks"] / "CRO.json", json.dumps({
        "role": "CRO",
        "packets": {"CRO": {"sha256": "0" * 64}},
    }))
    payload = ac.collect(role="CRO", charter_path=env["charter"])
    row = payload["bots"][0]
    assert row["ack"] == "FAIL"
    assert "stale" in row["ack_note"]
    assert row["result"] == "FAIL"
    # a fresh hash would not be stale
    _write(env["acks"] / "CRO.json", json.dumps({
        "role": "CRO",
        "packets": {"CRO": {"sha256": hashlib.sha256(pkt.read_bytes()).hexdigest()}},
    }))
    payload2 = ac.collect(role="CRO", charter_path=env["charter"])
    assert payload2["bots"][0]["ack"] == "PASS"


def test_quiz_schema_5_to_9(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    _complete_existing(env, "CZZ")
    _settings(env["bots"], "ASO", model="model-aso")
    _settings(env["bots"], "SSO", model="model-sso")
    # existing role, 4 questions: WAIT, bot still PASS
    _write(env["packets"] / "CZZ.quiz.json", json.dumps({
        "role": "CZZ", "questions": [f"q{i}" for i in range(4)],
    }))
    payload = ac.collect(role="CZZ", charter_path=env["charter"])
    row = payload["bots"][0]
    assert row["quiz"] == "WAIT"
    assert "4 questions" in row["quiz_note"]
    assert row["result"] == "PASS"
    # 7 bare questions without the key_facts schema: WAIT for an existing role
    _write(env["packets"] / "CZZ.quiz.json", json.dumps({
        "role": "CZZ", "questions": [f"q{i}" for i in range(7)],
    }))
    payload = ac.collect(role="CZZ", charter_path=env["charter"])
    assert payload["bots"][0]["quiz"] == "WAIT"
    assert "schema" in payload["bots"][0]["quiz_note"]
    # 7 questions in the key_facts schema with the current brief hash and traceable keys: PASS
    import quiz_grade as qg
    _write(env["packets"] / "CZZ.md", "# CZZ packet\n" + " ".join(f"a{i}" for i in range(7)) + "\n")
    _write(env["packets"] / "CZZ.quiz.json", json.dumps({
        "role": "CZZ", "brief_sha256": qg.brief16(env["brief"]),
        "questions": [{"q": f"q{i}", "a": f"a{i}", "key_facts": [f"a{i}"], "critical": False} for i in range(7)],
    }))
    payload = ac.collect(role="CZZ", charter_path=env["charter"])
    assert payload["bots"][0]["quiz"] == "PASS"
    # new role with 3 questions: FAIL
    _profile(env["agents"], "CRO", "🥇 CRO · Chief Recruiting Officer")
    _write(env["packets"] / "CRO.md", "# CRO\n")
    _write(env["packets"] / "CRO.quiz.json", json.dumps({
        "role": "CRO", "questions": ["a", "b", "c"],
    }))
    payload = ac.collect(role="CRO", charter_path=env["charter"])
    assert payload["bots"][0]["quiz"] == "WAIT"   # Addendum 4: out of 5-9 is WAIT for every quiz
    assert "3 questions" in payload["bots"][0]["quiz_note"]


def test_flow_refuses_quarantined_role(cro_rehold):
    with pytest.raises(flow.Refused) as ei:
        flow.new("c1-cro-should-not-exist", "CRO", "hello")
    assert "quarantine" in str(ei.value)


def test_gate_refuses_quarantined_role(monkeypatch, tmp_path, cro_rehold):
    monkeypatch.setattr(gate, "STATE", str(tmp_path))
    (tmp_path / "gates").mkdir()
    (tmp_path / "queue" / "es_cso").mkdir(parents=True)
    with pytest.raises(SystemExit) as ei:
        gate.request("c1-q-cro", "CRO", "irreversible", "should not file")
    assert "quarantine" in str(ei.value)
    assert not (tmp_path / "gates" / "c1-q-cro.request.json").exists()


def test_cost_gate_new_role_paid_fallback_zero(cro_rehold):
    W7 = {"max_paid_turns_per_task": 5, "max_paid_turns_per_day_when_ollama_down": 5,
          "weekly_share_points": 10, "points_per_job": 4}
    v, why = cost_gate.check(3, 0, W7, used_week=0, role="XRO")
    assert v == "WAIT"
    assert "paid-fallback 0" in why
    v, why = cost_gate.check(3, 0, W7, used_week=0, role="CRO")  # CRO re-held in a tmp held_roles.json
    assert v == "WAIT" and "held role" in why
    v, why = cost_gate.check(3, 0, W7, used_week=0, role="CFO")
    assert v == "RUN"
    v, why = cost_gate.check(3, 0, W7, used_week=0)  # no role: unchanged
    assert v == "RUN"


# --- option A: onboarded_roles.json, not brief line 18, decides who skips the ack ----
def test_role_on_brief_line_18_but_not_onboarded_needs_graded_ack(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    acro, pkt = _complete_existing(env, "CZZ")
    (tmp_path / "onboarded_roles.json").write_text(json.dumps({"roles": []}))   # on line 18, not onboarded
    assert "CZZ" in ac.load_brief_roles(env["brief"])
    assert ac.is_new_role("CZZ")
    row = ac.collect(role="CZZ", charter_path=env["charter"])["bots"][0]
    assert row["ack"] == "FAIL" and "missing" in row["ack_note"] and row["result"] == "FAIL"
    assert ac.is_quarantined("CZZ")[0]
    # hash-only ack still FAILs: graded quizzes are required
    _write(env["packets"] / "CZZ.quiz.json", json.dumps({"brief_sha256": "x", "questions": []}))
    _write(env["acks"] / "CZZ.json", json.dumps({"role": "CZZ", "packets": {"CZZ": {"sha256": ac.sha256_file(pkt)}}}))
    row = ac.collect(role="CZZ", charter_path=env["charter"])["bots"][0]
    assert row["ack"] == "FAIL" and "ungraded" in row["ack_note"]
    # once onboarded, the ack is N/A
    (tmp_path / "onboarded_roles.json").write_text(json.dumps({"roles": ["CZZ"]}))
    assert ac.collect(role="CZZ", charter_path=env["charter"])["bots"][0]["ack"] == "N/A"


def test_croles_membership_is_not_onboarded(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    env["flow"].write_text('CROLES = {"CQQ"}\n', encoding="utf-8")
    assert "CQQ" in ac.load_croles(env["flow"]) and ac.is_new_role("CQQ")
    import cost_gate
    v, why = cost_gate.check(1, 0, {"max_paid_turns_per_task": 5, "max_paid_turns_per_day_when_ollama_down": 5}, role="CQQ")
    assert v == "WAIT" and "paid-fallback 0" in why


def test_onboarded_file_unreadable_fails_closed(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    (tmp_path / "onboarded_roles.json").write_text("{broken")
    assert ac.load_onboarded_roles() == set()
    assert ac.is_new_role("CAO") and ac.is_quarantined("CAO")[0]
    (tmp_path / "onboarded_roles.json").unlink()
    assert ac.is_new_role("CBO")


def test_flow_and_gate_refuse_croles_member_not_onboarded(monkeypatch, tmp_path):
    env = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(flow, "CROLES", set(flow.CROLES) | {"CQQ"})
    with pytest.raises(flow.Refused) as e:
        flow.new("cqq-should-not-exist", "CQQ", "x")
    assert "quarantine" in str(e.value)
    import gate
    with pytest.raises(SystemExit) as e2:
        gate.request("cqq1", "CQQ", "irreversible", "x")
    assert "quarantine" in str(e2.value)
