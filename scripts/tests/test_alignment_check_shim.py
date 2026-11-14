"""The stale gb_state/onboarding/alignment_check.py must delegate to scripts/alignment_check.py."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import subprocess, sys, textwrap
from pathlib import Path

SHIM = textwrap.dedent("""    #!/usr/bin/env python3
    import os, sys
    os.execv(sys.executable, [sys.executable, GB_ROOT + "/scripts/alignment_check.py", *sys.argv[1:]])
""")
REAL = GB_ROOT + "/scripts/alignment_check.py"


def test_shim_exit_matches_real_checker_against_bad_pin(tmp_path):
    """Both fail (same exit code) when --charter points at a snapshot that does not match the pin."""
    shim = tmp_path / "alignment_check.py"
    shim.write_text(SHIM, encoding="utf-8")
    bad = tmp_path / "charter_snapshot_2034-01-09.md"
    bad.write_bytes(b"<!-- meta\n-->\n# not the pinned charter\n")
    args_tail = ["--charter", str(bad)]
    real = subprocess.run([sys.executable, REAL, *args_tail], capture_output=True, text=True)
    sh = subprocess.run([sys.executable, str(shim), *args_tail], capture_output=True, text=True)
    assert real.returncode == sh.returncode
    assert real.returncode != 0
