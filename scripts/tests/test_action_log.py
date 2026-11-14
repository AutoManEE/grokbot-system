import json, os, sys, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import action_log as al

def entry(**kw):
    e = dict(item="T", bot="CAO", result="PASS", objective="o", options=[dict(name="a", deltas="d", pros="p", cons="c")],
             decision="a", reasoning="r", confidence=90, conditions=[dict(name="x", required=True, result="PASS")])
    e.update(kw); return e

def test_append_verify_tamper(tmp_path):
    log = str(tmp_path / "l.jsonl")
    al.append(log, entry()); al.append(log, entry(item="T2"))
    assert al.verify(log)[0]
    s = open(log).read(); i = s.index('"r"')
    open(log, "w").write(s[:i] + '"R"' + s[i+3:])  # tamper one byte in line 1
    ok, msg = al.verify(log)
    assert not ok and "line 1" in msg

def test_delete_line_detected(tmp_path):
    log = str(tmp_path / "l.jsonl")
    for n in range(3): al.append(log, entry(item=f"T{n}"))
    lines = open(log).readlines(); open(log, "w").writelines(lines[:1] + lines[2:])
    assert not al.verify(log)[0]

def test_required_fail_cannot_pass():
    errs = al.validate(entry(conditions=[dict(name="x", required=True, result="FAIL")], confidence=100))
    assert any("cannot be PASS" in e for e in errs)

def test_missing_fields_and_confidence():
    e = entry(); del e["reasoning"]
    assert al.validate(e)
    assert al.validate(entry(confidence=101)) and al.validate(entry(confidence=50.5))
