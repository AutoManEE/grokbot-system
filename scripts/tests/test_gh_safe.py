"""gh_safe.py DRAFT: mocks only. Never runs real gh; only local tmp repos and OWNER/example-system slugs; no network.

The real protected name is stored in gh_safe.py only as a salted SHA-256. These tests add a stand-in protected
name (PNAME) to PROTECTED_NAMES so every denylist path is exercised without writing the real name here.
Set GH_SAFE_TEST_PROTECTED_NAME to also check that the shipped digest blocks the real name."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import json, os, subprocess, sys
from pathlib import Path
import pytest
sys.path.insert(0, GB_ROOT + "/scripts")
import gh_safe as g, action_log

SSH = "git" + "@github.com"   # split so repo_guard does not read it as an email
PNAME = "WorkRepoStandIn"     # stand-in protected repo name (added to PROTECTED_NAMES by the env fixture)
FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    allow = tmp_path / "allow.json"; allow.write_text(json.dumps({"allow": ["OWNER/example-system"]}))
    quotes = tmp_path / "q.jsonl"; quotes.write_text('{"id": "t999s1", "kind": "card", "scope": "gh"}\n')
    fake = tmp_path / "fakegh"; fake.write_text(f"#!/bin/sh\necho \"ran $@ token {FAKE_TOKEN}\"\necho \"Token: {FAKE_TOKEN}\" >&2\n")
    fake.chmod(0o755)
    monkeypatch.setattr(g, "LOG", str(log)); monkeypatch.setattr(g, "ALLOWLIST", allow)
    monkeypatch.setattr(g, "WORK_LIST", tmp_path / "work.txt")
    monkeypatch.setenv("GB_C9_QUOTES", str(quotes))
    import owner_override; monkeypatch.setattr(owner_override, "DEFAULT_QUOTES", quotes)
    monkeypatch.setenv("GH_SAFE_GH", str(fake)); monkeypatch.setenv("GH_SAFE_GIT", str(fake))
    monkeypatch.setattr(g, "PROTECTED_NAMES", g.PROTECTED_NAMES | {g._digest(PNAME)})
    return dict(tmp=tmp_path, log=log, allow=allow)


def _repo(tmp, name, remote):
    d = tmp / name; d.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)
    run("init", "-q"); run("config", "user.email", "t@users.noreply.github.com"); run("config", "user.name", "t")
    if remote: run("remote", "add", "origin", remote)
    (d / "a.txt").write_text("hello\n"); run("add", "."); run("commit", "-qm", "init")
    return d, run


def _rc(env, *argv, cwd=None):
    return g.main(["--by", "CAO", "--cwd", str(cwd or env["tmp"]), *argv])


@pytest.mark.parametrize("argv", [
    ["gh", "repo", "clone", f"OWNER/{PNAME}"], ["gh", "repo", "view", f"owner/{PNAME.lower()}"],
    ["gh", "repo", "clone", PNAME], ["gh", "repo", "clone", PNAME.upper() + ".git"],
    ["gh", "pr", "list", "-R", f"OWNER/{PNAME.upper()}"], ["gh", "issue", "list", f"--repo=OWNER/{PNAME}"],
    ["gh", "api", f"repos/OWNER/{PNAME}/contents"],
    ["git", "clone", f"https://github.com/OWNER/{PNAME}.git"], ["git", "clone", SSH + f":owner/{PNAME.lower()}.git"],
    ["git", "fetch", "ssh://" + SSH + f"/OWNER/{PNAME}"],
    ["git", "commit", "-m", f"see OWNER/{PNAME}-notes"]])
def test_denylist_by_name_and_url(env, argv, capsys):
    assert _rc(env, *argv) == 2
    assert "denylisted repo " + g.PROTECTED_LABEL in capsys.readouterr().err
    assert "ran" not in capsys.readouterr().out


def test_denylist_by_checkout_remote(env, capsys):
    d, _ = _repo(env["tmp"], "prot", f"https://github.com/OWNER/{PNAME}.git")
    for argv in (["git", "status"], ["git", "log"], ["gh", "pr", "list"]):
        assert _rc(env, *argv, cwd=d) == 2
    assert "remote of" in capsys.readouterr().err


def test_denylist_even_if_allowlisted(env):
    env["allow"].write_text(json.dumps({"allow": ["OWNER/example-system", f"OWNER/{PNAME}"]}))
    assert _rc(env, "gh", "repo", "view", f"OWNER/{PNAME}") == 2


def test_allowlist(env, capsys):
    assert _rc(env, "gh", "repo", "view", "OWNER/example-system") == 0
    assert _rc(env, "gh", "repo", "view", "OWNER/other") == 2
    assert "not on" in capsys.readouterr().err
    d, _ = _repo(env["tmp"], "ok", "https://github.com/OWNER/example-system.git")
    assert _rc(env, "git", "status", cwd=d) == 0
    d2, _ = _repo(env["tmp"], "other", "https://github.com/someone/else.git")
    assert _rc(env, "git", "status", cwd=d2) == 2
    d3, _ = _repo(env["tmp"], "noremote", None)
    assert _rc(env, "git", "status", cwd=d3) == 2              # no determinable repo
    assert _rc(env, "gh", "auth", "status") == 0               # account-level read is allowed
    env["allow"].write_text("{broken")
    assert _rc(env, "gh", "repo", "view", "OWNER/example-system") == 2   # unreadable = fail closed


def test_commit_message_slug_is_not_a_repo(env):
    d, _ = _repo(env["tmp"], "ok", "https://github.com/OWNER/example-system.git")
    assert _rc(env, "git", "commit", "-m", "and/or tweak", cwd=d) == 0


def test_scan_blocks_push(env, capsys):
    d, run = _repo(env["tmp"], "ok", "https://github.com/OWNER/example-system.git")
    (d / "cfg.txt").write_text(f"key = {FAKE_TOKEN}\n"); run("add", "."); run("commit", "-qm", "oops")
    assert _rc(env, "git", "push", "origin", "main", cwd=d) == 2
    err = capsys.readouterr().err
    assert "push blocked by scan" in err and "secret: token" in err and FAKE_TOKEN not in err


def test_scan_blocks_work_content_and_protected_path(env, capsys):
    d, run = _repo(env["tmp"], "ok", "https://github.com/OWNER/example-system.git")
    (env["tmp"] / "work.txt").write_text("# protected names\nAcmeCorp\n")
    (d / "n.md").write_text("notes from AcmeCorp standup\n"); run("add", "."); run("commit", "-qm", "n")
    assert _rc(env, "git", "push", cwd=d) == 2
    assert "protected name" in capsys.readouterr().err
    run("reset", "-q", "--hard", "HEAD~1")
    (d / PNAME.upper()).mkdir(); (d / PNAME.upper() / "x.py").write_text("x = 1\n"); run("add", "."); run("commit", "-qm", "b")
    assert _rc(env, "gh", "pr", "create", "--fill", cwd=d) == 2
    assert "protected-name path" in capsys.readouterr().err
    run("reset", "-q", "--hard", "HEAD~1")
    (d / "m.md").write_text(f"ref OWNER/{PNAME}\n"); run("add", "."); run("commit", "-qm", "m")
    assert _rc(env, "git", "push", cwd=d) == 2
    assert "protected work repo slug" in capsys.readouterr().err


def test_protected_name_not_in_source_and_lookalikes_pass(env):
    assert len(g.PROTECTED_NAMES) >= 1 and all(len(x) == 64 for x in g.PROTECTED_NAMES)
    assert g.is_protected_name(PNAME) and g.is_protected_name(PNAME.lower() + ".git")
    assert not g.is_protected_name(PNAME + "x") and not g.is_protected_name("example-system")
    assert _rc(env, "gh", "repo", "view", "OWNER/example-system") == 0


@pytest.mark.skipif(not os.environ.get("GH_SAFE_TEST_PROTECTED_NAME"), reason="real protected name not supplied")
def test_shipped_digest_blocks_real_name(tmp_path, monkeypatch, capsys):
    real = os.environ["GH_SAFE_TEST_PROTECTED_NAME"]
    assert g._digest(real) in g.PROTECTED_NAMES
    monkeypatch.setattr(g, "LOG", str(tmp_path / "log.jsonl"))
    for argv in (["gh", "repo", "view", f"OWNER/{real}"], ["gh", "repo", "clone", real.lower()],
                 ["git", "clone", f"https://github.com/OWNER/{real}.git"]):
        assert g.main(["--by", "CAO", "--cwd", str(tmp_path), *argv]) == 2
    assert real.lower() not in (tmp_path / "log.jsonl").read_text().lower()


def test_clean_push_runs(env):
    d, run = _repo(env["tmp"], "ok", "https://github.com/OWNER/example-system.git")
    assert _rc(env, "git", "push", "origin", "main", cwd=d) == 0


@pytest.mark.parametrize("argv", [
    ["gh", "repo", "edit", "OWNER/example-system", "--visibility", "public"],
    ["gh", "repo", "edit", "OWNER/example-system", "--visibility=public"],
    ["gh", "repo", "create", "OWNER/example-system", "--public"],
    ["gh", "api", "-X", "PATCH", "repos/OWNER/example-system", "-f", "private=false"]])
def test_public_needs_c9_quote(env, argv, capsys):
    assert _rc(env, *argv) == 2
    assert "C9 quote id" in capsys.readouterr().err
    assert g.main(["--by", "CAO", "--c9", "nope", "--cwd", str(env["tmp"]), *argv]) == 2
    assert g.main(["--by", "CAO", "--c9", "t999s1", "--cwd", str(env["tmp"]), *argv]) == 0


@pytest.mark.parametrize("extra", [["--force"], ["-f"], ["--force-with-lease"], ["--mirror"], ["origin", "+main"]])
def test_force_push_refused(env, extra, capsys):
    d, _ = _repo(env["tmp"], "ok", "https://github.com/OWNER/example-system.git")
    assert _rc(env, "git", "push", *extra, cwd=d) == 2
    assert "force-push refused" in capsys.readouterr().err


def test_gh_auth_changes_refused(env):
    for sub in ("login", "logout", "refresh", "token", "switch", "setup-git"):
        assert _rc(env, "gh", "auth", sub) == 2


def test_token_redaction_in_output_and_log(env, capsys):
    assert _rc(env, "gh", "repo", "view", "OWNER/example-system", "--json", f"x{FAKE_TOKEN}") in (0, 2)
    assert _rc(env, "gh", "repo", "view", "OWNER/example-system") == 0
    out = capsys.readouterr()
    assert FAKE_TOKEN not in out.out + out.err and "[REDACTED]" in out.out and "[REDACTED]" in out.err
    text = env["log"].read_text()
    assert FAKE_TOKEN not in text
    for s in ("https://user:hunter2" + "@" + "github.com/a/b", "Authorization: token abc123", "GITHUB_TOKEN=xyz"):
        r = g.redact(s); assert "hunter2" not in r and "abc123" not in r and "xyz" not in r


def test_every_call_logged(env):
    _rc(env, "gh", "repo", "view", "OWNER/example-system")
    _rc(env, "gh", "repo", "view", f"OWNER/{PNAME}")
    rows = action_log.read(str(env["log"]))
    assert [r["result"] for r in rows] == ["PASS", "FAIL"] and all(r["bot"] == "CAO" for r in rows)
    assert action_log.verify(str(env["log"]))[0]
    assert PNAME.lower() not in env["log"].read_text().lower()     # protected name is masked in the log


def test_docstring_says_not_security():
    assert "NOT SECURITY" in g.__doc__ and "condition 6" in g.__doc__ and "DRAFT" not in g.__doc__
    assert "reaches only OWNER/example-system" in g.__doc__


REPO = "OWNER/example-system"
API = f"repos/{REPO}"


def _c9(env, *argv):
    return g.main(["--by", "CAO", "--c9", "t999s1", "--cwd", str(env["tmp"]), *argv])


def _no(env, *argv):
    return g.main(["--by", "CAO", "--cwd", str(env["tmp"]), *argv])


def _badc9(env, *argv):
    return g.main(["--by", "CAO", "--c9", "nope", "--cwd", str(env["tmp"]), *argv])


@pytest.mark.parametrize("sub", ["delete", "archive", "rename", "transfer"])
def test_repo_delete_archive_rename_transfer_need_c9(env, sub, capsys):
    argv = ["gh", "repo", sub, REPO]
    if sub == "rename":
        argv = ["gh", "repo", "rename", REPO, "new-name"]
    if sub == "transfer":
        argv = ["gh", "repo", "transfer", REPO, "SomeoneElse"]
    assert _no(env, *argv) == 2
    assert "C9 quote id" in capsys.readouterr().err
    assert _badc9(env, *argv) == 2
    assert _c9(env, *argv) == 0


def test_repo_edit_beyond_description_needs_c9(env, capsys):
    base = ["gh", "repo", "edit", REPO]
    # --description alone is allowed without C9
    assert _no(env, *base, "--description", "ok") == 0
    assert _no(env, *base, "--description=ok") == 0
    for extra in (["--homepage", "https://example.com"], ["--default-branch", "main"],
                  ["--description", "ok", "--homepage", "https://example.com"]):
        assert _no(env, *base, *extra) == 2
        assert "C9 quote id" in capsys.readouterr().err
        assert _badc9(env, *base, *extra) == 2
        assert _c9(env, *base, *extra) == 0


@pytest.mark.parametrize("argv", [
    ["gh", "api", "-X", "DELETE", API],
    ["gh", "api", "--method", "PATCH", API],
    ["gh", "api", "--method=PUT", f"{API}/settings"],
    ["gh", "api", "-XDELETE", f"{API}/collaborators/alice"],
    ["gh", "api", "-X", "PUT", f"{API}/collaborators/alice"],
    ["gh", "api", "-X", "PUT", f"{API}/branches/main/protection"],
    ["gh", "api", "-X", "POST", f"{API}/hooks", "-f", "name=web"],
    ["gh", "api", "-X", "POST", f"{API}/keys", "-f", "key=ssh-rsa"],
    ["gh", "api", "-X", "DELETE", f"{API}/hooks/1"],
    ["gh", "api", "-X", "DELETE", f"{API}/keys/1"],
])
def test_api_mutating_repo_settings_need_c9(env, argv, capsys):
    assert _no(env, *argv) == 2
    err = capsys.readouterr().err
    assert "C9 quote id" in err
    assert _badc9(env, *argv) == 2
    assert _c9(env, *argv) == 0


@pytest.mark.parametrize("argv", [
    ["gh", "api", "-X", "PUT", f"{API}/collaborators/bob"],
    ["gh", "api", "-X", "DELETE", f"{API}/collaborators/bob"],
    ["gh", "api", "-X", "PUT", f"{API}/teams/devs"],
    ["gh", "api", "-X", "DELETE", f"{API}/teams/devs"],
    ["gh", "api", f"{API}/collaborators/bob", "-F", "permission=push"],  # implied POST
])
def test_collaborator_or_team_add_remove_need_c9(env, argv, capsys):
    assert _no(env, *argv) == 2
    assert "C9 quote id" in capsys.readouterr().err
    assert _c9(env, *argv) == 0


def test_collaborator_cli_add_remove_need_c9(env, capsys):
    # no first-class command required: any gh collaborator/team add/remove is gated
    for argv in (["gh", "repo", "collaborators", "add", "-R", REPO, "bob"],
                 ["gh", "repo", "collaborators", "remove", "--repo", REPO, "bob"]):
        assert _no(env, *argv) == 2
        assert "C9 quote id" in capsys.readouterr().err
        assert _c9(env, *argv) == 0


@pytest.mark.parametrize("argv", [
    ["gh", "release", "create", "v1.0.0"],
    ["gh", "release", "delete", "v1.0.0"],
    ["gh", "release", "edit", "v1.0.0", "--title", "x"],
    ["gh", "release", "upload", "v1.0.0", "dist.tgz"],
    ["gh", "api", "-X", "POST", f"{API}/releases", "-f", "tag_name=v1"],
    ["gh", "api", "-X", "DELETE", f"{API}/releases/1"],
])
def test_release_create_and_touch_need_c9(env, argv, capsys):
    d, _ = _repo(env["tmp"], "okrel", "https://github.com/OWNER/example-system.git")
    # release subcommands have no repo slug in args; use the checkout remote
    rc = g.main(["--by", "CAO", "--cwd", str(d), *argv])
    assert rc == 2
    assert "C9 quote id" in capsys.readouterr().err
    assert g.main(["--by", "CAO", "--c9", "t999s1", "--cwd", str(d), *argv]) == 0


def test_release_list_and_view_do_not_need_c9(env):
    d, _ = _repo(env["tmp"], "okrel2", "https://github.com/OWNER/example-system.git")
    assert g.main(["--by", "CAO", "--cwd", str(d), "gh", "release", "list"]) == 0
    assert g.main(["--by", "CAO", "--cwd", str(d), "gh", "release", "view", "v1"]) == 0


def test_api_non_get_default_denied_unless_write_list(env, capsys):
    # POST to create an issue is not on the write list (comments only)
    argv = ["gh", "api", "-X", "POST", f"{API}/issues", "-f", "title=hi"]
    assert _no(env, *argv) == 2
    err = capsys.readouterr().err
    assert "default-denied" in err and "write list" in err
    # a verified C9 does not punch through the default-deny
    assert _c9(env, *argv) == 2
    # GET stays allowed
    assert _no(env, "gh", "api", f"{API}") == 0
    assert _no(env, "gh", "api", "-X", "GET", f"{API}/issues") == 0


@pytest.mark.parametrize("argv,method", [
    (["gh", "api", "--method", "POST", f"{API}/issues", "-f", "title=x"], "POST"),
    (["gh", "api", "-X", "POST", f"{API}/issues"], "POST"),
    (["gh", "api", f"{API}/issues", "-f", "title=x"], "POST"),   # implied POST from -f
    (["gh", "api", f"{API}/issues", "-F", "title=x"], "POST"),   # implied POST from -F
    (["gh", "api", "--method=POST", f"{API}/issues"], "POST"),
    (["gh", "api", "-XPOST", f"{API}/issues"], "POST"),
])
def test_api_method_dash_x_and_implied_post_are_equivalent(env, argv, method, capsys):
    assert g.gh_api_method(argv[1:]) == method
    assert _no(env, *argv) == 2
    assert "default-denied" in capsys.readouterr().err


def test_api_issue_and_pr_comments_on_allowlisted_repo_are_on_write_list(env):
    for path in (f"{API}/issues/1/comments", f"{API}/pulls/2/comments",
                 f"{API}/issues/comments/9", f"{API}/pulls/comments/9"):
        assert _no(env, "gh", "api", "-X", "POST", path, "-f", "body=hi") == 0
        assert _no(env, "gh", "api", "--method", "PATCH", path, "-f", "body=hi") == 0
    # not allowlisted: still refused
    assert _no(env, "gh", "api", "-X", "POST", "repos/OWNER/other/issues/1/comments", "-f", "body=hi") == 2
