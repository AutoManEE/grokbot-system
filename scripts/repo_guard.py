#!/usr/bin/env python3
"""repo_guard.py (A1, CAO): refuse a commit if any staged file looks private or secret. Zero AI.
Checks staged paths (git diff --cached --name-only) for: paths under private/, mail/gold data, and content
patterns: email addresses (except noreply/example), private keys, API-key/token shapes, card/SSN-like digits,
and personal keywords (personal sender names, financial institution names, a family name). Exit 1 = blocked.
Usage: repo_guard.py [--all]  (--all scans every tracked+staged file)  Install: .git/hooks/pre-commit
"""
import re, subprocess, sys
PATH_BLOCK = re.compile(r"(^|/)(private|mail|gold)(/|$)|_snapshot|\.jsonl\.bak$")
PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com|example\.(com|org))[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})\b"),
    "card/ssn": re.compile(r"\b(\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}|\d{3}-\d{2}-\d{4})\b"),
    # Placeholder tokens in the public copy; the live list of personal keywords is kept privately.
    "personal keyword": re.compile(r"(?i)\b(privkwa|privkwb|privkwc|privkwd|privkwe|privkwf|privkwg|privkwh|privkwi)\b"),
}
ALLOW_FILES = {"scripts/repo_guard.py", "scripts/tests/test_repo_guard.py", "scripts/tests/test_paid_job_log_cso.py"}

def files(all_):
    cmd = ["git", "ls-files"] if all_ else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    return [f for f in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines() if f]

def scan(path, text):
    hits = []
    if PATH_BLOCK.search(path):
        hits.append("blocked path")
    if path in ALLOW_FILES:
        return hits
    for name, rx in PATTERNS.items():
        m = rx.search(text)
        if m:
            hits.append(f"{name}: {m.group(0)[:40]!r}")
    return hits

def main():
    bad = 0
    for f in files("--all" in sys.argv):
        try:
            text = open(f, errors="ignore").read()
        except (IsADirectoryError, FileNotFoundError):
            continue
        h = scan(f, text)
        if h:
            bad += 1; print(f"BLOCK {f}: {', '.join(h)}")
    print("repo_guard: PASS" if not bad else f"repo_guard: FAIL ({bad} files)")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
