#!/usr/bin/env python3
"""gh_safe.py (CAO, for the coming CGO · Chief Git Officer).

ACCIDENT GUARD PLUS DETECTION, NOT SECURITY. Any bot on the box can still call `gh` or `git` directly and
skip this wrapper. Real enforcement is CSO condition 6: a fine-grained credential Owner created himself
that reaches only OWNER/example-system. That is Owner's call. This file never changes a gh login, token or account.

Usage:  gh_safe.py [--by ROLE] [--c9 QUOTE_ID] [--cwd DIR] gh|git ARGS...
Rules (each refusal exits 2 and is logged):
  1. Hard denylist: the protected work repo, stored only as a salted SHA-256 of its name (PROTECTED_NAMES),
     matched case-insensitively by owner/repo slug, a bare repo-name arg, URL (https/ssh/git@), `gh api
     repos/...` path, or because the checkout's git remote points at it. It is reported as PROTECTED_LABEL.
     The denylist cannot be turned off by config.
  2. Allowlist mode: every target repo (named in args, or the checkout's remotes) must be on
     alignment/gh_safe_allowlist.json (now: OWNER/example-system). No determinable repo = refused,
     except `gh --version`, `git --version` and `gh auth status`.
  3. Push (`git push`, `gh pr create`, `gh repo sync`): before running, scan every unpushed commit
     (`git log -p HEAD --not --remotes`) for secrets (repo_guard patterns) and work content (protected
     names from alignment/protected_names.txt if it exists, protected-name path parts and protected slugs). A hit blocks.
  4. Visibility: `gh repo edit --visibility public`, `gh repo create --public`, or `gh api` setting
     private=false / visibility=public are refused unless --c9 names a VERIFIED Owner quote in
     alignment/c9_quotes.jsonl (owner_override.quote_status == VERIFIED).
  9. The same C9 check is required for: `gh repo delete|archive|rename|transfer`; `gh repo edit` beyond
     --description; `gh api` DELETE/PATCH/PUT on repos/<r> (including settings, collaborators, branch
     protection, hooks and deploy keys); collaborator or team adds and removes; `gh release create` and
     any command that touches a release.
  10. `gh api` with a method other than GET is default-denied unless it is on an explicit write list.
      The list starts with issue and PR comments on allowlisted repos only. --method, -X, and an implied
      POST from -f/-F/--field/--raw-field/--input are treated the same way.
  5. Force-push refused: --force, -f, --force-with-lease, --force-if-includes, --mirror, a '+refspec',
     and `gh repo sync --force`.
  6. gh auth/login/token changes are refused (auth login/logout/refresh/token/switch/setup-git), and
     `gh secret`/`gh ssh-key`/`gh gpg-key` too. The wrapper never passes or reads a token itself.
  7. Output and logged args are redacted: ghp_/gho_/ghu_/ghs_/ghr_/github_pat_ tokens, Bearer/token headers,
     'Token: ...' lines, and user:secret@ in URLs become [REDACTED].
  8. Every call (allowed or refused) appends a Section 5 entry to the action log (GB_ACTION_LOG).
Env: GH_SAFE_GH / GH_SAFE_GIT (binaries to run; tests use mocks), GH_SAFE_ALLOWLIST, GH_SAFE_WORK_LIST,
GB_C9_QUOTES, GB_ACTION_LOG.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import hashlib, json, os, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, GB_ROOT + "/scripts")
import action_log, repo_guard

# Protected work repo: only a salted SHA-256 of the lowercased repo name is kept here, so the name itself is
# never written into this repo. Hard; config cannot remove it. Owner is the logged-in account.
PROTECTED_SALT = "gh_safe/protected/v1:"
PROTECTED_OWNER = "owner"
PROTECTED_NAMES = frozenset()   # add salted digests of repos that must never be touched
PROTECTED_LABEL = "protected-work-repo"
DENY = {PROTECTED_LABEL}
ALLOWLIST = Path(os.environ.get("GH_SAFE_ALLOWLIST", GB_ROOT + "/alignment/gh_safe_allowlist.json"))
WORK_LIST = Path(os.environ.get("GH_SAFE_WORK_LIST", GB_ROOT + "/alignment/protected_names.txt"))
LOG = os.environ.get("GB_ACTION_LOG", GB_ROOT + "/alignment/action_log.jsonl")
REFUSED = 2

URL_RE = re.compile(r"(?:https?://|ssh://|git://)(?:[^@/\s]+@)?github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/?#]|$)"
                    r"|git@github\.com:([\w.-]+)/([\w.-]+?)(?:\.git)?$", re.I)
API_RE = re.compile(r"^/?repos/([\w.-]+)/([\w.-]+)", re.I)
SLUG_RE = re.compile(r"^([\w.-]+)/([\w.-]+?)(?:\.git)?$")
TOKEN_RES = [
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,})"),   # no \\b: also inside a word
    re.compile(r"(?i)(authorization:\s*(?:bearer|token|basic)\s+)\S+"),
    re.compile(r"(?i)(\btoken:\s*)\S+"),
    re.compile(r"(?i)(\b(?:GH|GITHUB)_TOKEN=)\S+"),
    re.compile(r"(?<=://)[^/@\s:]+:[^/@\s]+(?=@)"),
    re.compile(r"(?<=://)(?:x-access-token|oauth2)[^@\s]*(?=@)", re.I),
]
FORCE_FLAGS = {"--force", "-f", "--force-with-lease", "--force-if-includes", "--mirror"}
AUTH_DENY = {"login", "logout", "refresh", "token", "switch", "setup-git"}
NO_REPO_OK = (("gh", "--version"), ("git", "--version"), ("gh", "auth", "status"))


class Refused(Exception):
    pass


def redact(text):
    s = str(text or "")
    s = TOKEN_RES[0].sub("[REDACTED]", s)
    for rx in TOKEN_RES[1:4]:
        s = rx.sub(lambda m: m.group(1) + "[REDACTED]", s)
    for rx in TOKEN_RES[4:]:
        s = rx.sub("[REDACTED]", s)
    return s


def _digest(name):
    return hashlib.sha256((PROTECTED_SALT + str(name).lower()).encode("utf-8")).hexdigest()


def is_protected_name(name):
    """True when a bare repo name (optionally .git, or followed by -suffix/.ext/path) is the protected one.
    Same reach as a case-insensitive word match on the name: the leading word run is compared."""
    n = str(name or "").lower().removesuffix(".git")
    m = re.match(r"\w+", n)
    return bool(n) and (_digest(n) in PROTECTED_NAMES or bool(m and _digest(m.group(0)) in PROTECTED_NAMES))


SLUG_ANY_RE = re.compile(r"(?i)\b" + PROTECTED_OWNER + r"/([\w.-]+)")


def norm(owner, repo):
    if owner.lower() == PROTECTED_OWNER and is_protected_name(repo):
        return PROTECTED_LABEL
    return f"{owner}/{repo}".lower().removesuffix(".git")


def _url_repos(text):
    out = set()
    for part in re.split(r"[=\s'\"]", text):
        m = URL_RE.search(part)
        if m:
            o, r = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            out.add(norm(o, r))
        for m in SLUG_ANY_RE.finditer(part):
            if is_protected_name(m.group(1)):
                out.add(PROTECTED_LABEL)
    return out


def repos_in_args(args, tool="gh"):
    """Repos a command names. URLs and the protected owner/name slug are found anywhere (even inside a message).
    owner/repo slugs and the bare protected name count only where gh takes a repo: -R/--repo values,
    `gh repo <sub> X`, and `gh api repos/...` paths (so a commit message with 'a/b' is not a repo)."""
    out = set()
    for a in args:
        out |= _url_repos(a)
    slots = []
    for i, a in enumerate(args):
        if a in ("-R", "--repo") and i + 1 < len(args):
            slots.append(args[i + 1])
        elif a.startswith("--repo="):
            slots.append(a.split("=", 1)[1])
    if tool == "gh" and args[:1] == ["repo"] and len(args) > 2:
        slots += [x for x in args[2:] if not x.startswith("-")][:1]
    if tool == "gh" and args[:1] == ["api"]:
        slots += [x for x in args[1:] if API_RE.match(x)]
    for x in slots:
        m = API_RE.match(x)
        if m:
            out.add(norm(m.group(1), m.group(2))); continue
        if "/" not in x and _digest(x.lower().removesuffix(".git")) in PROTECTED_NAMES:
            out.add(PROTECTED_LABEL); continue           # bare name resolves to the logged-in owner
        m = SLUG_RE.match(x)
        if m:
            out.add(norm(m.group(1), m.group(2)))
    return out


def remote_repos(cwd):
    git = os.environ.get("GH_SAFE_GIT_READ", "git")
    try:
        r = subprocess.run([git, "-C", str(cwd), "remote", "-v"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    out = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            got = _url_repos(parts[1])
            out |= got if got else {"unknown:" + redact(parts[1])}
    return out


def load_allow():
    try:
        d = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        return {str(x).lower() for x in d.get("allow") or []}
    except (OSError, ValueError):
        return set()                                         # unreadable = nothing allowed (fail closed)


def is_push(tool, args):
    a = [x for x in args if not x.startswith("-")]
    return (tool == "git" and "push" in a[:1]) or (tool == "gh" and a[:2] in (["pr", "create"], ["repo", "sync"]))


def is_force(tool, args):
    if tool == "git" and args[:1] == ["push"]:
        return any(x in FORCE_FLAGS or x.startswith("--force") for x in args[1:]) or \
            any(x.startswith("+") for x in args[1:] if not x.startswith("-"))
    if tool == "gh" and args[:2] == ["repo", "sync"]:
        return "--force" in args
    return False


def is_public(tool, args):
    if tool != "gh":
        return False
    j = " ".join(args).lower()
    if args[:2] == ["repo", "create"] and "--public" in args:
        return True
    if args[:2] == ["repo", "edit"] and re.search(r"--visibility[= ]public", j):
        return True
    if args[:1] == ["api"] and re.search(r"\bprivate=false\b|\bvisibility=public\b|\"private\"\s*:\s*false", j):
        return True
    return False


SECRET_KEYS = ("private key", "token", "card/ssn")


def scan_unpushed(cwd):
    """Return a list of hits in commits not on any remote: secrets and work content."""
    git = os.environ.get("GH_SAFE_GIT_READ", "git")
    r = subprocess.run([git, "-C", str(cwd), "log", "-p", "--format=COMMIT %H", "HEAD", "--not", "--remotes"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return [f"scan could not read unpushed commits: {redact(r.stderr.strip())[:200]}"]
    names = []
    if WORK_LIST.is_file():
        names = [l.strip() for l in WORK_LIST.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    hits, path = [], None
    for line in r.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if any(_digest(part) in PROTECTED_NAMES for part in path.split("/")):
                hits.append(f"work content: protected-name path {path}")
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        if path in repo_guard.ALLOW_FILES or path in ("scripts/gh_safe.py", "scripts/tests/test_gh_safe.py"):
            continue
        for k in SECRET_KEYS:
            if repo_guard.PATTERNS[k].search(body):
                hits.append(f"secret: {k} in {path}")
        if any(is_protected_name(m.group(1)) for m in SLUG_ANY_RE.finditer(body)):
            hits.append(f"work content: protected work repo slug in {path}")
        for n in names:
            if re.search(r"(?i)\b" + re.escape(n) + r"\b", body):
                hits.append(f"work content: protected name in {path}")
    return sorted(set(hits))



def require_verified_c9(c9, action):
    """Same check visibility uses: --c9 must name a VERIFIED id in alignment/c9_quotes.jsonl."""
    import owner_override
    st, why = owner_override.quote_status(c9 or "", scope="gh")   # #36 C9: quote must be scoped "gh"
    if st != "VERIFIED":
        raise Refused(f"{action} needs a Owner C9 quote id ({why})")


def _skip_flag_value(args, i):
    """Advance past a flag that takes a value (--flag VALUE, --flag=VALUE, or -XVALUE)."""
    a = args[i]
    if "=" in a and not a.startswith("-X"):
        return i + 1
    if a.startswith("-X") and a != "-X":
        return i + 1
    return i + 2


def gh_api_method(args):
    """HTTP method gh api will use. --method/-X win; -f/-F/--field/--raw-field/--input imply POST."""
    rest = args[1:] if args[:1] == ["api"] else []
    method, implied_post, i = None, False, 0
    while i < len(rest):
        a = rest[i]
        if a in ("-X", "--method"):
            if i + 1 < len(rest):
                method = rest[i + 1].upper()
            i += 2
            continue
        if a.startswith("--method="):
            method = a.split("=", 1)[1].upper(); i += 1
            continue
        if a.startswith("-X") and a != "-X":
            method = a[2:].upper(); i += 1
            continue
        if a in ("-f", "-F", "--raw-field", "--field", "--input") or a.startswith(("--raw-field=", "--field=", "--input=")):
            implied_post = True
        elif len(a) > 2 and a[:2] in ("-f", "-F") and not a.startswith("--"):
            implied_post = True
        i += 1
    if method:
        return method
    return "POST" if implied_post else "GET"


def gh_api_path(args):
    """First positional path after `gh api`, minus a query string."""
    rest = args[1:] if args[:1] == ["api"] else []
    takes_val = {"-X", "--method", "-H", "--header", "-f", "-F", "--raw-field", "--field", "--input",
                 "--jq", "-q", "--hostname", "--cache", "-p", "--preview"}
    skip = False
    for a in rest:
        if skip:
            skip = False
            continue
        if a in takes_val:
            skip = True
            continue
        if a.startswith("-"):
            continue
        return a.lstrip("/").split("?", 1)[0]
    return ""


_COMMENT_WRITE = (
    re.compile(r"(?i)^repos/[\w.-]+/[\w.-]+/issues(?:/\d+)?/comments(?:/\d+)?$"),
    re.compile(r"(?i)^repos/[\w.-]+/[\w.-]+/pulls(?:/\d+)?/comments(?:/\d+)?$"),
)
_SENSITIVE_API = re.compile(
    r"(?i)/(collaborators|teams|releases|hooks|keys|environments|actions/secrets|"
    r"branches/[^/]+/protection)(/|$)|^repos/[\w.-]+/[\w.-]+/settings(/|$)"
)


def api_on_write_list(path):
    """Issue and PR comments only (allowlist is enforced separately)."""
    return any(rx.match(path or "") for rx in _COMMENT_WRITE)


def api_needs_c9(method, path):
    """DELETE/PATCH/PUT on repos/<r>..., and any non-GET on settings/collaborators/protection/hooks/keys/releases/teams."""
    pth = path or ""
    if method in ("DELETE", "PATCH", "PUT") and re.match(r"(?i)^repos/", pth):
        return True
    if method != "GET" and _SENSITIVE_API.search(pth):
        return True
    return False


def repo_edit_beyond_description(args):
    """True when `gh repo edit` has any flag other than --description (and repo selectors)."""
    if args[:2] != ["repo", "edit"]:
        return False
    i = 2
    while i < len(args):
        a = args[i]
        if a in ("-R", "--repo", "--description"):
            i = _skip_flag_value(args, i)
            continue
        if a.startswith("--repo=") or a.startswith("--description="):
            i += 1
            continue
        if a.startswith("-"):
            return True
        i += 1
    return False


def is_release_touch(tool, args):
    """gh release create, and anything that mutates or otherwise touches a release."""
    if tool != "gh" or args[:1] != ["release"]:
        return False
    sub = args[1] if args[1:] else ""
    return sub not in ("list", "view", "download")


def is_collaborator_or_team_mutation(tool, args):
    if tool != "gh" or args[:1] == ["api"]:
        return False
    words = [a for a in args if not a.startswith("-")]
    joined = " ".join(words).lower()
    if re.search(r"\bcollaborators?\b", joined) and any(w in {"add", "remove", "delete", "set"} for w in words):
        return True
    if re.search(r"\bteams?\b", joined) and any(w in {"add", "remove", "delete", "set"} for w in words):
        return True
    return False


def decide(tool, args, cwd, c9=None):
    """Raise Refused(reason) or return the list of conditions that PASSed."""
    if tool not in ("gh", "git"):
        raise Refused("only gh and git are wrapped")
    if tool == "gh" and args[:1] == ["auth"] and (args[1:2] and args[1] in AUTH_DENY):
        raise Refused(f"gh auth {args[1]} changes or prints a login/token; never through this wrapper")
    if tool == "gh" and args[:1] and args[0] in ("secret", "ssh-key", "gpg-key"):
        raise Refused(f"gh {args[0]}: security settings need Owner's own card (GATE-CGO)")
    named = repos_in_args(args, tool)
    remotes = remote_repos(cwd) if (Path(cwd) / ".git").exists() or tool == "git" else set()
    targets = named | remotes
    bad = sorted(t for t in targets if t in DENY)
    if bad:
        raise Refused(f"denylisted repo {bad[0]} ({'remote of ' + str(cwd) if bad[0] in remotes and bad[0] not in named else 'named in the command'})")
    if is_force(tool, args):
        raise Refused("force-push refused (GATE-CGO: Owner's own card)")
    if tool == "gh" and args[:2] in (["repo", "delete"], ["repo", "archive"], ["repo", "rename"], ["repo", "transfer"]):
        require_verified_c9(c9, f"gh repo {args[1]}")
    if repo_edit_beyond_description(args):
        require_verified_c9(c9, "gh repo edit beyond --description")
    if is_public(tool, args):
        require_verified_c9(c9, "making a repo public")
    if is_collaborator_or_team_mutation(tool, args):
        require_verified_c9(c9, "collaborator or team add/remove")
    if is_release_touch(tool, args):
        require_verified_c9(c9, "gh release (create or any command that touches a release)")
    if tool == "gh" and args[:1] == ["api"]:
        method, path = gh_api_method(args), gh_api_path(args)
        if method != "GET":
            if api_on_write_list(path):
                pass  # issue/PR comments on allowlisted repos; allowlist checked below
            elif api_needs_c9(method, path):
                require_verified_c9(c9, f"gh api {method} {path or 'repos/<r>'}")
            else:
                raise Refused(
                    f"gh api {method} is default-denied unless on the write list "
                    "(issue and PR comments on allowlisted repos)"
                )
    allow = load_allow()
    if not targets and tuple([tool] + args[:2]) not in NO_REPO_OK and tuple([tool] + args[:1]) not in NO_REPO_OK:
        raise Refused("allowlist mode: no target repo could be determined")
    off = sorted(t for t in targets if t not in allow)
    if off:
        raise Refused(f"allowlist mode: {off[0]} is not on {ALLOWLIST.name}")
    if is_push(tool, args):
        hits = scan_unpushed(cwd)
        if hits:
            raise Refused("push blocked by scan: " + "; ".join(hits[:5]))
    return sorted(targets)


def mask_protected(text):
    """Replace any word that is the protected name with PROTECTED_LABEL, so the committed log never spells it."""
    return re.sub(r"\w+", lambda m: PROTECTED_LABEL if _digest(m.group(0)) in PROTECTED_NAMES else m.group(0), str(text or ""))


def _log(by, tool, args, cwd, result, note, refused=False):
    cmd = mask_protected(redact(" ".join([tool] + list(args))))[:300]
    note = mask_protected(note)
    try:
        action_log.append(LOG, dict(
            item=f"GH-SAFE {tool} {(args[:1] or [''])[0]}", bot=by or "unknown", result=result,
            objective="Wrapped gh/git call (accident guard plus detection, not security)",
            options=[dict(name="run" if result == "PASS" else "refuse", deltas=cmd, pros="logged", cons="any bot can bypass")],
            decision="ran" if result == "PASS" else "refused", reasoning=redact(note)[:400], confidence=90,
            conditions=[dict(name="gh_safe rules", required=True, result="FAIL" if refused else "PASS"),
                        dict(name="command exit 0", required=False, result="N/A" if refused else result)]))
    except (OSError, ValueError):
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    by = c9 = None; cwd = os.getcwd()
    while argv and argv[0] in ("--by", "--c9", "--cwd"):
        k, v = argv[0], argv[1]; argv = argv[2:]
        by, c9, cwd = (v, c9, cwd) if k == "--by" else (by, v, cwd) if k == "--c9" else (by, c9, v)
    if not argv:
        print(__doc__); return REFUSED
    tool, args = argv[0], argv[1:]
    try:
        targets = decide(tool, args, cwd, c9)
    except Refused as e:
        msg = f"REFUSED: {e}"
        print(redact(msg), file=sys.stderr); _log(by, tool, args, cwd, "FAIL", msg, refused=True); return REFUSED
    exe = os.environ.get("GH_SAFE_GH" if tool == "gh" else "GH_SAFE_GIT", tool)
    r = subprocess.run([exe] + args, cwd=cwd, capture_output=True, text=True)
    sys.stdout.write(redact(r.stdout)); sys.stderr.write(redact(r.stderr))
    _log(by, tool, args, cwd, "PASS" if r.returncode == 0 else "FAIL",
         f"targets {targets}; exit {r.returncode}" + ("" if r.returncode == 0 else f"; stderr {r.stderr[-200:]}"))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
