GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, importlib
sys.path.insert(0, GB_ROOT + "/scripts")
def test_down_all_pto(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import ho_check; importlib.reload(ho_check)
    st = ho_check.check("http://127.0.0.1:9"); assert st["ollama"] == "DOWN" and set(st["bots"].values()) == {"PTO"} and len(st["bots"]) == len(ho_check.IDS)
def test_up_all_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import ho_check; importlib.reload(ho_check)
    st = ho_check.check("http://127.0.0.1:11434"); assert st["ollama"] == "UP" and set(st["bots"].values()) == {"READY"}


def test_wake_pto_files(tmp_path, monkeypatch):
    import importlib, json, os
    monkeypatch.setenv("GB_STATE", str(tmp_path)); import ho_check; importlib.reload(ho_check)
    w = ho_check.wake("http://127.0.0.1:9")  # nothing listens: Ollama DOWN -> all PTO
    assert w["ollama"] == "DOWN" and len(w["pto"]) == len(ho_check.IDS)
    a = json.load(open(tmp_path / "ho/pto/AFO.json")); s = json.load(open(tmp_path / "ho/pto/SFO.json"))
    assert a["covered_by"].startswith("CFO") and s["covered_by"].startswith("AFO")
    monkeypatch.setattr(ho_check, "check", lambda url: {"checked_at": "t", "ollama": "UP", "bots": {b: "READY" for b in ho_check.IDS}})
    assert ho_check.wake("x")["pto"] == [] and not os.listdir(tmp_path / "ho/pto")
    assert len(open(tmp_path / "ho/log.jsonl").read().splitlines()) == 2

def test_model_installed_latest_alias():
    import ho_check
    models = {"phi4-mini:latest", "qwen3:4b-instruct"}
    assert ho_check.model_installed("phi4-mini", models)
    assert ho_check.model_installed("phi4-mini:latest", models)
    assert ho_check.model_installed("qwen3:4b-instruct", models)
    assert not ho_check.model_installed("missing", models)
    assert ho_check.model_installed("qwen3:4b-instruct", {"qwen3:4b-instruct:latest"})
    assert ho_check.model_installed("foo:latest", {"foo"})
