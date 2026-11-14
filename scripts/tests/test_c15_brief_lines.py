"""C15: CRO brief lines and FX-CAO / TK-CPO / RP-CPO exemptions are in brief.txt."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from pathlib import Path
import sys
sys.path.insert(0, GB_ROOT + "/scripts")
import alignment_check as ac

BRIEF = Path(GB_ROOT + "/alignment/brief.txt").read_text(encoding="utf-8")

def test_cro_table_rows_in_brief():
    for row in (
        "TK-CRO | CPO → CRO | TASK",
        "RP-CRO | CRO → CPO | REPORT",
        "DG-CRO | CRO ↔ ARO | DELEGATE",
        "FX-CRO | CRO → CAO | FIX",
        "DG-ARO | ARO ↔ SRO | DELEGATE",
    ):
        assert row in BRIEF, row

def test_exemption_sentences_next_to_tk_coo():
    i = BRIEF.index("there is no TK-COO.")
    window = BRIEF[i:i+800]
    assert "there is no FX-CAO." in window
    assert "there is no TK-CPO." in window
    assert "there is no RP-CPO." in window

def test_alignment_check_cro_brief_lines_pass():
    payload = ac.collect(role="CRO")
    row = payload["bots"][0]
    assert row["brief_lines"] == "PASS", row.get("brief_lines_note")
