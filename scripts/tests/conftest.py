"""#36: same guard as scripts/conftest.py, for runs with cwd <repo>/scripts/tests (pytest does not load the
parent conftest from that root). Audit log to tmp; Ollama lock may move only in explicit test mode.
Accident guard plus detection, not security."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os
import pytest

# Tests never reach a real model server: point every Ollama client at a closed port.
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _tmp_open_items_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("GB_LOG_AUDIT", str(tmp_path / "_conftest_open_items_audit.jsonl"))
    # FI-pytest-log-guards: route_log writers (flow.log_model_call / log_cap_override, relay._log) no longer skip
    # under pytest, so a test that sets no GB_STATE logs to a per-test tmp state dir, never the live route_log.
    if not os.environ.get("GB_STATE"):
        monkeypatch.setenv("GB_STATE", str(tmp_path / "_conftest_gb_state"))
    monkeypatch.setenv("GB_OLLAMA_LOCK_TEST", "1")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_git_check: keep run._tracked_and_clean real (the test points OVERRIDE_GIT at a tmp repo)")


@pytest.fixture(autouse=True)
def _override_git_check_monkeypatched(request, monkeypatch):
    """(3c) CSO: run.py has no pytest deferral. A screen tree bind-mounted over <repo> is dirty vs HEAD by
    construction, so tests monkeypatch the git-tracked/unmodified check here. Tests of the check itself use the
    real_git_check marker and point OVERRIDE_GIT at a tmp repo, never at <repo>."""
    if request.node.get_closest_marker("real_git_check"):
        return
    import sys
    m = sys.modules.get("run")
    if m is None:
        import importlib
        m = importlib.import_module("run") if GB_ROOT + "/bots" in sys.path else None
    if m is not None and hasattr(m, "_tracked_and_clean"):
        monkeypatch.setattr(m, "_tracked_and_clean", lambda path: (True, "test: git check monkeypatched by conftest"))


@pytest.fixture(autouse=True)
def _protected_list_for_tests(tmp_path, monkeypatch):
    """A fresh clone has no private protected-name list, and text_guard fails closed without one. Tests get a
    tmp list holding one made-up name (pytest-only test mode); tests of the fail-closed path override it."""
    p = tmp_path / "_conftest_protected_names.txt"
    p.write_text("# test list\nZorblax Builders\n")
    monkeypatch.setenv("GB_PROTECTED_LIST", str(p))
    monkeypatch.setenv("GB_PROTECTED_LIST_TEST", "1")
