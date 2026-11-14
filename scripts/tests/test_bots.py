GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, json, pytest
sys.path.insert(0, GB_ROOT + "/bots"); import run
IDS = ["ABO","AIO","AFO","ATO","AMO","ASO","AAO","APO","AOO","SBO","SIO","SFO","STO","SMO","SSO","SAO","SPO","SOO"]
def test_all_18_configs():
    for b in IDS:
        p, s = run.load(b); assert s["tools"] == [] and s["id"] == b and p
        assert s["outside_content"] == (b in ("ASO", "SSO"))
def test_outside_refused_for_non_cso_team():
    with pytest.raises(PermissionError): run.run("AMO", "x", outside=True)

def test_aro_sro_configs():
    for b in ("ARO", "SRO"):
        p, s = run.load(b)
        assert s["tools"] == [] and s["id"] == b and p
        assert s["outside_content"] is False
        # CIO ruling item 10: SRO moved to phi4-mini; ARO stays qwen3:4b-instruct
        assert s["model"] == ("phi4-mini" if b == "SRO" else "qwen3:4b-instruct") and s["temperature"] == 0
        assert s["c_role"] == "CRO"
