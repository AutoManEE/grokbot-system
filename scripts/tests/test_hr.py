GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, importlib, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
@pytest.fixture
def hr(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import hr; importlib.reload(hr); return hr
def test_mediate_unresolved_to_owner(hr, tmp_path):
    hr.file("c1", "CTO", "budget disagreement with CFO"); c = hr.mediate("c1", "CSO", "talked", "UNRESOLVED")
    assert c["state"] == "UNRESOLVED" and os.path.exists(tmp_path / "queue/es_cso/HR-c1.json")
def test_resolved_stays_internal(hr, tmp_path):
    hr.file("c2", "CMO", "x"); hr.mediate("c2", "CSO", "ok", "RESOLVED"); assert not os.path.exists(tmp_path / "queue/es_cso/HR-c2.json")
def test_about_cso_goes_direct(hr, tmp_path):
    assert hr.file("c3", "CFO", "CSO vetoes too much", about_cso=True)["line"] == "OD-CFO"
    assert hr.file("c4", "CBO", "CSO issue", about_cso=True)["line"] == "ES-CBO"
    with pytest.raises(ValueError): hr.mediate("c3", "CSO", "x", "RESOLVED")
def test_only_croles_and_cso_mediates(hr):
    for who in ("ASO", "SBO", "CSO"):
        with pytest.raises(PermissionError): hr.file("x" + who, who, "x")
    hr.file("c5", "CAO", "x")
    with pytest.raises(PermissionError): hr.mediate("c5", "CBO", "x", "RESOLVED")
