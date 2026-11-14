#!/usr/bin/env python3
"""alignment_check.py.

Evolved from the CRO huddle step 3 draft. Wired into flow.py (new/delegate/close),
gate.py request, cost_gate.check (new-role paid-fallback), and ho_check.wake.
This layer is an accident guard plus detection, not security: --role is not a
verified caller identity on the shared box.

READ-ONLY against profiles, brief.txt, brief_amendments.md, a Charter snapshot,
flow.py CROLES, <repo>/bots helper dirs, and onboarding packets/acks.

Checks, per bot discovered from $GB_PROFILES/*/profile.json (default <repo>/samples/profiles):
  1. name format: emoji + space + 3-letter acronym + ' · ' + full role (FAIL on mismatch)
  2. title: UNVERIFIABLE and advisory unless a self-report registry file is present
     (CSO spot-checks; profile.json is never the source; a missing title never
     FAILs the bot). The huddle names the
     registry default path is <repo>/alignment/title_registry.jsonl (CAO-owned,
     append-only JSONL). Override with --titles. Never affects PASS/FAIL or exit.
  3. acronym is in <repo>/scripts/flow.py CROLES
  4. A- and S-helper dirs exist in <repo>/bots (A{acro[1:]}/S{acro[1:]})
  5. role is in brief.txt's C-role list OR the Charter snapshot section
     'Roles added after the brief'. Default snapshot:
     the file named in <repo>/alignment/charter_pin.json (sample: charter_sample.md)
     Override with --charter. alignment_check hashes the local snapshot itself
     and compares body_sha256 (bytes after the header's closing '-->', leading
     CR/LF stripped) and file_sha256 with the committed pin
     <repo>/alignment/charter_pin.json. Local .sha256 files are never
     trusted. Mismatch = FAIL; missing snapshot or pin = UNVERIFIABLE.
     charter_v2_draft.md
     is never used for PASS.
  6. brief lines: brief.txt is canonical; brief_amendments.md is staging only.
     A formal table row `DG-<ROLE> |` (and TK/RP/FX) in brief.txt is PASS.
     The same row or a draft mention found ONLY in amendments is WAIT (DRAFT),
     never PASS. WAIT does not fail the exit code by itself, but the bot
     result is WAIT, not PASS (PASS < WAIT < FAIL).
     Exemptions (`there is no FX-CAO / TK-CPO / RP-CPO`, or `there is no
     TK-COO` at brief.txt:246): in brief.txt = N/A (PASS); only in
     amendments = WAIT (DRAFT); nowhere, and the line is missing = FAIL.
  7. packet file packets/<ROLE>.md is present for every existing C-role.
     Quiz files packets/<ROLE>.quiz.json are tolerated and are not packets.
     Other markdown (e.g. a gap witness) is ignored.
  8. acknowledgement at <repo>/gb_state/onboarding/acks/<ROLE>.json for
     new bots (roles not in brief.txt's original C-role list). Missing, incomplete
     or stale hash = FAIL for new bots. Original brief C-roles get ack=N/A.

Usage:
  alignment_check.py
  alignment_check.py --role CRO
  alignment_check.py --json
  alignment_check.py --charter /path/to/snapshot.md
  alignment_check.py --titles /path/to/registry.jsonl

Exit 1 on any FAIL (bot FAIL, missing packet, or Charter hash mismatch). WAIT does not fail the exit code on its own.
"""
from __future__ import annotations
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))

import argparse
import hashlib
import json
import os
import time
import re
import sys
from pathlib import Path

AGENTS = Path(__import__("os").environ.get("GB_PROFILES") or (GB_ROOT + "/samples/profiles"))
FLOW = Path(GB_ROOT + "/scripts/flow.py")
BOTS = Path(GB_ROOT + "/bots")
BRIEF = Path(GB_ROOT + "/alignment/brief.txt")
AMENDMENTS = Path(GB_ROOT + "/alignment/brief_amendments.md")
PACKETS = Path(__import__("os").environ.get("GB_PACKETS") or (GB_ROOT + "/gb_state/onboarding/packets"))
ACKS = Path(GB_ROOT + "/gb_state/onboarding/acks")
REQUIRED_QUIZZES = Path(GB_ROOT + "/alignment/onboarding/required_quizzes.json")  # #36: committed required quiz set
CHARTER_PIN = Path(GB_ROOT + "/alignment/charter_pin.json")  # committed; local .sha256 files are never trusted


def _pinned_charter_path() -> Path:
    """Default snapshot = the one named in the committed pin (Sync 6+); falls back to name."""
    try:
        name = json.loads(CHARTER_PIN.read_text()).get("snapshot") or ""
        if name and "/" not in name and name.endswith(".md"):
            return CHARTER_PIN.parent / name
    except Exception:
        pass
    return Path(GB_ROOT + "/alignment/charter_sample.md")


DEFAULT_CHARTER = _pinned_charter_path()
DEFAULT_TITLES = Path(GB_ROOT + "/alignment/title_registry.jsonl")
NAME_RE = re.compile(r"^(\S+)\s+([A-Z]{3}) · (.+)$")
CROLES_RE = re.compile(r"^CROLES\s*=\s*\{([^}]+)\}", re.M)
BRIEF_ROLES_RE = re.compile(
    r"C-role\s*=\s*one of the \d+ chief roles:\s*([A-Z]{3}(?:,\s*[A-Z]{3})*)",
    re.M,
)
ADDED_HEADING_RE = re.compile(r"^#{1,6}\s*Roles added after the brief\s*$", re.I | re.M)
ADDED_ROLE_RE = re.compile(r"\b([A-Z]{3})\s*·")
LINE_KINDS = ("DG", "TK", "RP", "FX")
TABLE_ROW_RE = re.compile(r"^(?:\|\s*)?(DG|TK|RP|FX)-([A-Z]{3})\s*\|", re.M)
NO_LINE_RE = re.compile(r"there is no (DG|TK|RP|FX)-([A-Z]{3})\b", re.I)
EXEMPT_LIST_RE = re.compile(
    r"there is no\s+((?:DG|TK|RP|FX)-[A-Z]{3}(?:\s*(?:/|,|&|and)\s*(?:DG|TK|RP|FX)-[A-Z]{3})*)",
    re.I,
)
DRAFT_ID_RE = re.compile(r"\b(DG|TK|RP|FX)-([A-Z]{3})\b")
SHA256SUM_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")


def pf(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sibling_sha256_path(path: Path) -> Path:
    """Return the sibling digest file, accepting either common name.

    Prefer <stem>.sha256 if it
    exists, else <name>.md.sha256.
    If neither is present, return the preferred <stem>.sha256 path so the
    missing-file note names the expected sibling.
    """
    candidates = [path.with_suffix(".sha256"), path.with_name(path.name + ".sha256")]
    seen: list[Path] = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    for c in seen:
        if c.is_file():
            return c
    return seen[0]


def snapshot_body_bytes(data: bytes) -> tuple[bytes, str]:
    """Split a Charter snapshot into metadata header vs markdown body.

    The official .sha256 covers ONLY the markdown body. If `data` starts
    (after optional leading whitespace) with an HTML comment `<!-- ... -->`,
    that comment is the metadata header and is excluded. The body starts at
    the first byte after the closing `-->` with leading CR/LF stripped, so
    a blank line between the comment and the first markdown heading is not
    hashed. If no such header is present, the whole file is the body.
    Returns (body, note_about_split).
    """
    start = data.find(b"<!--")
    prefix = data[:start] if start >= 0 else data
    if start >= 0 and prefix.strip() == b"":
        end = data.find(b"-->", start)
        if end >= 0:
            body = data[end + 3:].lstrip(b"\r\n")
            return body, "hashed markdown body only; metadata header excluded"
    return data, "no metadata header found; hashed whole file"


def load_croles(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    m = CROLES_RE.search(text)
    if not m:
        return set()
    return set(re.findall(r'"([A-Z]{3})"', m.group(1)))


def load_brief_roles(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    m = BRIEF_ROLES_RE.search(text)
    if not m:
        return set()
    return set(re.findall(r"[A-Z]{3}", m.group(1)))


ONBOARDED = Path(GB_ROOT + "/alignment/onboarded_roles.json")


def load_onboarded_roles(path: Path | None = None) -> set[str]:
    """Roles that skip the ack. Missing/unreadable/invalid = empty set
    (fail closed: everyone needs a code-graded ack). brief.txt line 18 and flow.py
    CROLES never count as onboarded."""
    path = Path(path) if path else ONBOARDED
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, list) or not all(isinstance(r, str) and re.fullmatch(r"[A-Z]{3}", r) for r in roles):
        return set()
    return set(roles)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def parse_sha256sum(path: Path) -> str | None:
    """Return the first 64-hex digest in a sha256sum-format file, or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = SHA256SUM_RE.search(text)
    return m.group(1).lower() if m else None


def parse_added_roles(text: str) -> set[str] | None:
    m = ADDED_HEADING_RE.search(text)
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^#{1,6}\s+", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    return set(ADDED_ROLE_RE.findall(body))


def load_charter_snapshot(path: Path, pin_path: Path | None = None) -> dict:
    """Hash the LOCAL snapshot itself and compare with the committed pin
    (alignment/charter_pin.json: body_sha256 and file_sha256 for that snapshot name).

    Local .sha256 sibling files are never read for PASS.
    Missing snapshot, missing pin, or no pin for this snapshot name = UNVERIFIABLE.
    Body or whole-file mismatch = FAIL. added_roles is parsed only after PASS.
    charter_v2_draft.md is never consulted.
    """
    pin_path = Path(pin_path) if pin_path else CHARTER_PIN
    base = dict(source=str(path), sha_path=str(pin_path), added_roles=None)
    missing = [str(p) for p in (path, pin_path) if not p.is_file()]
    if missing:
        return dict(base, integrity="UNVERIFIABLE", note="missing " + ", ".join(missing))
    try:
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return dict(base, integrity="UNVERIFIABLE", note=f"unreadable pin {pin_path}: {e}")
    if not isinstance(pin, dict) or pin.get("snapshot") != path.name:
        return dict(base, integrity="UNVERIFIABLE", note=f"{pin_path} has no pin for {path.name}")
    want_body = str(pin.get("body_sha256") or "").lower()
    want_file = str(pin.get("file_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", want_body) or not re.fullmatch(r"[0-9a-f]{64}", want_file):
        return dict(base, integrity="UNVERIFIABLE", note=f"{pin_path} lacks body_sha256/file_sha256")
    raw = path.read_bytes()
    body, split_note = snapshot_body_bytes(raw)
    got_body = hashlib.sha256(body).hexdigest()
    got_file = hashlib.sha256(raw).hexdigest()
    probs = []
    if got_body != want_body:
        probs.append(f"body hashes to {got_body}, pin has {want_body} ({split_note})")
    if got_file != want_file:
        probs.append(f"file hashes to {got_file}, pin has {want_file}")
    if probs:
        return dict(base, integrity="FAIL",
                    note="hash mismatch vs committed pin: " + "; ".join(probs)
                         + "; a rewrite may have been in progress")
    text = path.read_text(encoding="utf-8")
    added = parse_added_roles(text)
    if added is None:
        return dict(base, integrity="PASS", note=f"{path} has no 'Roles added after the brief' section")
    return dict(base, integrity="PASS", added_roles=added, note=f"matches pin (revisionId {str(pin.get('revisionId',''))[:16]}...)")


def load_line_sources() -> tuple[str, str]:
    return read_text(BRIEF), read_text(AMENDMENTS)


def table_rows(text: str) -> set[tuple[str, str]]:
    return {(k.upper(), r.upper()) for k, r in TABLE_ROW_RE.findall(text or "")}


def exemption_ids(text: str) -> set[tuple[str, str]]:
    """Parse 'there is no TK-COO' and list forms 'there is no FX-CAO / TK-CPO / RP-CPO'."""
    found: set[tuple[str, str]] = set()
    for m in EXEMPT_LIST_RE.finditer(text or ""):
        for k, r in re.findall(r"(DG|TK|RP|FX)-([A-Z]{3})", m.group(1), re.I):
            found.add((k.upper(), r.upper()))
    for k, r in NO_LINE_RE.findall(text or ""):
        found.add((k.upper(), r.upper()))
    return found


def draft_ids(text: str) -> set[tuple[str, str]]:
    return {(k.upper(), r.upper()) for k, r in DRAFT_ID_RE.findall(text or "")}


def worst_status(*statuses: str) -> str:
    """PASS < WAIT < FAIL. N/A and UNVERIFIABLE do not raise the rank."""
    rank = {"PASS": 0, "N/A": 0, "UNVERIFIABLE": 0, "WAIT": 1, "FAIL": 2}
    worst = "PASS"
    for s in statuses:
        key = s if s in rank else (
            "FAIL" if str(s).startswith("FAIL") else
            "WAIT" if str(s).startswith("WAIT") else
            "PASS"
        )
        if rank[key] > rank[worst]:
            worst = key
    return worst


def check_brief_lines(acro: str, brief: str, amendments: str) -> tuple[str, str]:
    """Score DG/TK/RP/FX-<ROLE> against canonical brief.txt and staging amendments.

    Table row in brief.txt = PASS. Exemption in brief.txt = N/A (PASS).
    The same found only in amendments = WAIT (DRAFT). Missing everywhere = FAIL.
    """
    if not acro:
        return "FAIL", "no acronym"
    brief_rows, brief_exempt = table_rows(brief), exemption_ids(brief)
    amend_rows, amend_exempt = table_rows(amendments), exemption_ids(amendments)
    amend_draft = draft_ids(amendments)
    parts = []
    statuses = []
    for k in LINE_KINDS:
        tag = f"{k}-{acro}"
        key = (k, acro)
        if key in brief_rows:
            parts.append(f"{tag} ok (brief.txt)")
            statuses.append("PASS")
        elif key in brief_exempt:
            parts.append(f"{tag} N/A (brief.txt)")
            statuses.append("PASS")
        elif key in amend_exempt:
            parts.append(f"{tag} WAIT (DRAFT exemption in amendments)")
            statuses.append("WAIT")
        elif key in amend_rows or key in amend_draft:
            parts.append(f"{tag} WAIT (DRAFT in amendments)")
            statuses.append("WAIT")
        else:
            parts.append(f"{tag} missing")
            statuses.append("FAIL")
    return worst_status(*statuses), "; ".join(parts)



def helper_ids(acro: str) -> tuple[str, str]:
    rest = acro[1:]
    return "A" + rest, "S" + rest


def role_packet_path(acro: str) -> Path:
    return PACKETS / f"{acro}.md"


def list_role_packets() -> dict[str, Path]:
    out = {}
    if not PACKETS.is_dir():
        return out
    for p in sorted(PACKETS.glob("*.md")):
        if re.fullmatch(r"[A-Z]{3}", p.stem):
            out[p.stem] = p
    return out


def required_quiz_roles() -> tuple[list[str], str]:
    """#36: the committed required quiz set. Missing or unreadable manifest -> ([], error) (fail closed)."""
    try:
        data = json.loads(REQUIRED_QUIZZES.read_text(encoding="utf-8"))
        roles = data["roles"]
        if not isinstance(roles, list) or not all(isinstance(r, str) and re.fullmatch(r"[A-Z]{3}", r) for r in roles):
            raise ValueError("roles must be a list of 3-letter acronyms")
        return sorted(set(roles)), ""
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [], f"required quiz manifest {REQUIRED_QUIZZES} missing or unreadable: {e}"


def list_role_quizzes() -> list[str]:
    """Quiz files present in PACKETS plus every role in the committed required set (#36: deleting a peer's
    quiz file no longer drops that peer from the requirement)."""
    present = set()
    if PACKETS.is_dir():
        present = {
            p.stem[:-5] if p.stem.endswith(".quiz") else p.stem
            for p in PACKETS.glob("*.quiz.json")
            if re.fullmatch(r"[A-Z]{3}\.quiz", p.stem) or re.fullmatch(r"[A-Z]{3}", p.stem)
        }
    return sorted(present | set(required_quiz_roles()[0]))


def discover_bots() -> list[dict]:
    rows = []
    if not AGENTS.is_dir():
        return rows
    for d in sorted(p for p in AGENTS.iterdir() if p.is_dir()):
        prof = d / "profile.json"
        if not prof.exists():
            continue
        try:
            data = json.loads(prof.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            rows.append(dict(
                id=d.name, name="(unreadable)", acro="", error=str(e), data=None,
            ))
            continue
        rows.append(dict(id=d.name, name=str(data.get("name") or ""), acro="",
                         error=None, data=data))
    return rows


def self_approved(acro: str, rec) -> str | None:
    """#36 C16: no role approves its own hire ack (CSO for CSO included). Checks approved_by, approver
    and by, normalized by token; a "self" token also counts. Returns the offending field or None."""
    if not isinstance(rec, dict):
        return None
    for k in ("approved_by", "approver", "by"):
        toks = set(re.findall(r"[A-Z0-9]+", str(rec.get(k) or "").upper()))
        if acro.upper() in toks or "SELF" in toks:
            return f"{k}={rec.get(k)!r}"
    return None


def check_ack(acro: str, required: bool, role_packets: dict[str, Path]) -> tuple[str, str]:
    """New roles only. PASS needs fresh packet hashes AND every quiz file present in
    PACKETS graded in code (onboard_ack.py) and PASSing when re-graded now
    (own 100%; peers >=80% with every critical correct). Packet hashes alone never
    PASS (FX-CSO-alignment-ackquiz; CIO ruling items 3, 8, 12). A self-written
    quiz_result string is refused. A quiz whose brief_sha256 is stale is WAIT.
    """
    if not required:
        # #36 C16: the self-approval block runs for every role, onboarded ones (CSO) included.
        p0 = ACKS / f"{acro}.json"
        if p0.is_file():
            try:
                bad = self_approved(acro, json.loads(p0.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                bad = None
            if bad:
                return "FAIL", f"{bad} on its own hire ack is refused"
        return "N/A", "ack not required for brief.txt C-roles"
    import quiz_grade as qg
    path = ACKS / f"{acro}.json"
    if not path.is_file():
        return "FAIL", f"missing {path}"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "FAIL", f"unreadable {path}: {e}"
    packets = rec.get("packets") if isinstance(rec, dict) else None
    if not isinstance(packets, dict):
        return "FAIL", f"{path} missing packets object"
    misses, waits = [], []
    for pkt, pth in sorted(role_packets.items()):
        want = sha256_file(pth)
        entry = packets.get(pkt)
        got = entry.get("sha256") if isinstance(entry, dict) else None
        if got != want:
            misses.append(f"{pkt} hash missing or stale")
    if rec.get("role") != acro:
        misses.append(f"role field {rec.get('role')!r} != {acro}")
    _self = self_approved(acro, rec)   # C16: no role approves its own hire/packet (CSO for CSO)
    if _self:
        misses.append(f"approved_by {acro} on its own hire ack is refused ({_self})")
    graded = rec.get("quizzes") if isinstance(rec.get("quizzes"), dict) else {}
    self_written = [k for k, v in packets.items() if isinstance(v, dict) and "quiz_result" in v]
    if not graded and self_written:
        misses.append("self-written quiz_result refused (" + ", ".join(sorted(self_written))
                      + "); grade with onboard_ack.py")
    if graded and rec.get("graded_by") != "onboard_ack.py":
        misses.append("quizzes not written by onboard_ack.py")
    try:
        live16 = qg.brief16(BRIEF)
    except OSError:
        live16 = None
    _req, _req_err = required_quiz_roles()
    if _req_err:
        misses.append(_req_err)
    for r in list_role_quizzes():
        g = graded.get(r)
        if not isinstance(g, dict) or not isinstance(g.get("answers"), list):
            misses.append(f"{r} quiz ungraded or missing in ack")
            continue
        qp = PACKETS / f"{r}.quiz.json"
        if not qp.is_file():
            misses.append(f"{r} quiz file missing from {PACKETS} (required set)"); continue
        try:
            quiz = qg.load_quiz(qp)
        except (OSError, ValueError) as e:
            misses.append(f"{r} quiz unreadable: {e}"); continue
        attempts = g.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            first = [a.get("first", a).get("given", "") if isinstance(a, dict) else "" for a in g["answers"]]
            attempts = [dict(kind="original", given=first)]
        corpus = qg.corpus_for(r, PACKETS, BRIEF, DEFAULT_CHARTER)
        # Shared with onboard_ack.py: raw = plain code grade, now = with CIO reviews (alignment/quiz_reviews.jsonl).
        # The stored ack must equal one of them (older acks hold raw; onboard_ack now stores the reviewed grade).
        raw, now = qg.grade_taken(quiz, acro, r, corpus_toks=corpus, attempts=attempts)
        if not qg.stored_matches(g, raw, now):
            misses.append(f"{r} stored quiz result differs from code grading (self-written?)")
        if now["result"] == "WAIT":
            waits.append(f"{r} quiz WAIT ({now['why']})")
        elif now["result"] != "PASS":
            misses.append(f"{r} quiz FAIL ({now['why']})")
        st = qg.stale(quiz, live16) if live16 else "brief.txt unreadable"
        if st:
            waits.append(f"{r} {st}")
    if misses:
        return "FAIL", "; ".join(misses)
    if waits:
        return "WAIT", "; ".join(waits)
    return "PASS", f"{path} (packets fresh; every quiz code-graded PASS)"


def title_status(acro: str, registry: dict | None) -> tuple[str, str]:
    """Advisory only. Registry missing, unreported, or reported: UNVERIFIABLE. Never FAIL."""
    if not acro:
        return "UNVERIFIABLE", "no acronym"
    if registry is None:
        return "UNVERIFIABLE", (
            "self-report title registry not present; default "
            f"{DEFAULT_TITLES}; CSO spot-checks; profile.json is not the source"
        )
    entry = registry.get(acro)
    if entry is None:
        return "UNVERIFIABLE", f"{acro} has no registry row (unreported; never FAIL)"
    if isinstance(entry, dict):
        status = str(entry.get("status") or "").strip().lower()
        title = str(entry.get("title") or "").strip()
        src = str(entry.get("source") or "").strip()
        if status == "unreported" or not title:
            return "UNVERIFIABLE", (
                f"{acro} unreported in {DEFAULT_TITLES.name}; waiting for the bot to "
                "confirm its title (never FAIL; profile.json is not the source)"
            )
        note = f"registry title {title!r} (advisory; CSO spot-check, not a PASS/FAIL)"
        if src:
            note += f"; source: {src}"
        return "UNVERIFIABLE", note
    if isinstance(entry, str) and entry.strip():
        return "UNVERIFIABLE", f"registry title {entry.strip()!r} (advisory; CSO spot-check, not a PASS/FAIL)"
    return "UNVERIFIABLE", f"{acro} unreported in the title registry (never FAIL)"


def load_title_registry(path: Path | None = None) -> tuple[dict | None, str]:
    """Load the title registry. Default: DEFAULT_TITLES (JSONL or JSON)."""
    if path is None:
        path = DEFAULT_TITLES
    path = Path(path)
    if not path.is_file():
        return None, f"title registry missing: {path}"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"title registry unreadable: {path}: {e}"

    def _parse_jsonl(text: str) -> dict:
        by_role: dict = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rec = json.loads(line)
            if not isinstance(rec, dict):
                continue
            role = str(rec.get("role") or "").strip().upper()
            if role:
                by_role[role] = rec
        return by_role

    use_jsonl = path.suffix == ".jsonl" or raw.lstrip().startswith("#")
    if use_jsonl:
        try:
            return _parse_jsonl(raw), str(path)
        except ValueError as e:
            return None, f"title registry unreadable: {path}: {e}"
    try:
        data = json.loads(raw)
    except ValueError:
        try:
            return _parse_jsonl(raw), str(path)
        except ValueError as e:
            return None, f"title registry unreadable: {path}: {e}"
    if isinstance(data, dict) and isinstance(data.get("titles"), dict):
        data = data["titles"]
    if not isinstance(data, dict):
        return None, f"title registry {path} is not an object"
    return data, str(path)


def helper_settings_status(helper_id: str) -> tuple[str, str]:
    """Dir, settings.json, configured model, and no tools. Accident-guard check."""
    if not helper_id:
        return "FAIL", "no helper id"
    d = BOTS / helper_id
    if not d.is_dir():
        return "FAIL", f"missing {helper_id}"
    settings = d / "settings.json"
    if not settings.is_file():
        return "FAIL", f"{helper_id} missing settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "FAIL", f"{helper_id} unreadable settings.json: {e}"
    model = str(data.get("model") or "").strip()
    if not model:
        return "FAIL", f"{helper_id} has no configured model"
    tools = data.get("tools", [])
    if tools:
        return "FAIL", f"{helper_id} has tools (must be none)"
    return "PASS", f"{helper_id} model={model} no tools"


def description_status(data: dict, acro: str, role: str) -> tuple[str, str]:
    """Non-empty description. Does not read title. Never uses profile.json title."""
    desc = str((data or {}).get("description") or "").strip()
    if not desc:
        return "FAIL", "empty description"
    return "PASS", "description present"


def check_quiz(acro: str, required: bool) -> tuple[str, str]:
    """Quiz schema allows 5-9 questions. Required for new roles; existing C-roles
    with a present quiz outside 5-9 are WAIT, not FAIL (CBO's live quiz has 4).
    Missing quiz on a new role is FAIL. Missing quiz on an existing role is N/A.
    """
    if not acro:
        return "FAIL", "no acronym"
    path = PACKETS / f"{acro}.quiz.json"
    if not path.is_file():
        if required:
            return "FAIL", f"missing {path}"
        return "N/A", "no quiz file"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "FAIL", f"unreadable {path}: {e}"
    if not isinstance(data, dict):
        return "FAIL", f"{path} is not an object"
    qs = data.get("questions")
    if qs is None:
        qs = data.get("answers")
    if not isinstance(qs, list):
        return "FAIL", f"{path} has no questions/answers list"
    n = len(qs)
    import quiz_grade as qg
    probs = qg.schema_problems(data)
    if probs:  # 5-9 range and schema errors are WAIT for every quiz (Addendum 4)
        return "WAIT", "quiz schema: " + "; ".join(probs[:3])
    try:
        st = qg.stale(data, qg.brief16(BRIEF))
    except OSError:
        st = "brief.txt unreadable"
    if st:
        return "WAIT", st
    corpus = qg.corpus_for(acro, PACKETS, BRIEF, DEFAULT_CHARTER)
    flagged = [f"q{i}:{'|'.join(map(str, bad))}" for i, q in enumerate(qs, 1)
               if isinstance(q, dict) and (bad := qg.untraceable_facts(q, corpus))]
    if flagged:
        return "WAIT", "KEY_UNTRACEABLE (owner fixes the key; excluded from takers' scores): " + "; ".join(flagged)
    return "PASS", f"{n} questions, key_facts schema, brief hash current, keys traceable"


def _helper_model_prompt(hid: str) -> tuple[str | None, str | None, str]:
    settings = BOTS / hid / "settings.json"
    if not settings.is_file():
        return None, None, f"{hid} missing settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, None, f"{hid} unreadable settings.json: {e}"
    model = str(data.get("model") or "").strip() or None
    prompt = BOTS / hid / "prompt.md"
    ph = sha256_file(prompt) if prompt.is_file() else None
    return model, ph, ""


def model_family(model: str | None) -> str:
    m = (model or "").lower().split(":")[0]
    m = re.sub(r"[\d.]+$", "", m)          # qwen3 -> qwen, llama3.2 -> llama, gemma3 -> gemma
    return m.rstrip("-_") or (model or "")


def default_reviewer_model() -> str | None:
    """flow.py INDEPENDENT_MODEL (read as text; flow.py is not imported here)."""
    m = re.search(r'^INDEPENDENT_MODEL\s*=\s*["\']([^"\']+)["\']', read_text(FLOW), re.M)
    return m.group(1) if m else None


def chain_reviewer(a_id: str, s_id: str) -> str | None:
    """Per-chain reviewer: S-helper settings reviewer_model, else A's, else flow's default."""
    for hid in (s_id, a_id):
        try:
            rv = json.loads((BOTS / hid / "settings.json").read_text(encoding="utf-8")).get("reviewer_model")
        except (OSError, ValueError):
            rv = None
        if rv:
            return str(rv)
    # mirror flow.chain_reviewer: the default reviewer steps aside for llama3.2:3b when A or S runs it
    dflt = default_reviewer_model()
    m = re.search(r'^ALT_REVIEWER_MODEL\s*=\s*["\']([^"\']+)["\']', read_text(FLOW), re.M)
    alt = m.group(1) if m else None
    if dflt and alt and dflt in (_helper_model_prompt(a_id)[0], _helper_model_prompt(s_id)[0]):
        return alt
    return dflt


def pair_independence(a_id: str, s_id: str) -> tuple[str, str]:
    """A, S and the chain's reviewer must be three different model families (FX-CSO-SSO-model)."""
    am, ap, ae = _helper_model_prompt(a_id)
    sm, sp, se = _helper_model_prompt(s_id)
    rv = chain_reviewer(a_id, s_id)
    problems = [x for x in (ae, se) if x]
    if rv and "gemma3" in rv:
        problems.append(f"reviewer {rv} is barred for safety review")
    for hid, m in ((a_id, am), (s_id, sm)):
        if rv and m and model_family(m) == model_family(rv):
            problems.append(f"{hid} ({m}) and reviewer ({rv}) are the same model family: same-model review")
    if am and sm and am == sm:
        problems.append(f"{a_id} and {s_id} share model {am}")
    elif am and sm and model_family(am) == model_family(sm):
        problems.append(f"{a_id} ({am}) and {s_id} ({sm}) are the same model family")
    if ap and sp and ap == sp:
        problems.append(f"{a_id} and {s_id} share a prompt hash")
    if not problems and (not am or not sm):
        problems.append(f"{a_id} or {s_id} has no configured model")
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", f"A {a_id} {am} / S {s_id} {sm} / reviewer {rv}: three different models"


def helper_pairs() -> list[tuple[str, str]]:
    if not BOTS.is_dir():
        return []
    out = []
    for d in sorted(BOTS.glob("A??")):
        s = "S" + d.name[1:]
        if d.is_dir() and (BOTS / s).is_dir():
            out.append((d.name, s))
    return out


def helper_independence() -> dict:
    """CIO ruling item 10: every A/S pair, not only ASO/SSO."""
    return {f"{a}/{s}": pair_independence(a, s) for a, s in helper_pairs()}


def aso_sso_independence() -> tuple[str, str]:
    """Team-level summary over every A/S pair (name kept for older callers)."""
    pairs = helper_independence()
    if not pairs:
        return pair_independence("ASO", "SSO")
    bad = [f"{k}: {v[1]}" for k, v in pairs.items() if v[0] != "PASS"]
    if bad:
        return "FAIL", f"{len(bad)}/{len(pairs)} A/S pairs not independent: " + "; ".join(bad)
    return "PASS", f"all {len(pairs)} A/S pairs use distinct model families and prompts"


def is_new_role(acro: str, onboarded: set[str] | None = None) -> bool:
    """New = not in alignment/onboarded_roles.json (never brief line 18, never CROLES)."""
    acro = (acro or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", acro):
        return True
    roles = onboarded if onboarded is not None else load_onboarded_roles()
    return acro not in roles


HELD_ROLES = Path(GB_ROOT + "/alignment/held_roles.json")
HOLD_CLEARERS = ("CIO", "CSO")
# Holds on the CSO role: the Owner, "CIO co-signs holds on the CSO"
# (quote verified by CAO in CSO's transcript). A CSO hold clears with a CIO cosign only; CSO never cosigns its
# own hold and no other role can clear it. Every other hold keeps the CIO + CSO dual cosign.
CSO_HOLD_QUOTE = "t2s1"
HOLD_CLEARERS_BY_ROLE = {"CSO": ("CIO",)}


def norm_role(x) -> str:
    """#36 C16 retest: role strings compare case- and whitespace-insensitively ("cso " == "CSO")."""
    return str(x or "").strip().upper()


def known_roles() -> set[str]:
    return load_croles(FLOW) | load_brief_roles(BRIEF)


def valid_hold_role(x, known: set[str] | None = None) -> str | None:
    """#36 retest-2: a hold role must be exactly three ASCII letters (surrounding whitespace and case ignored)
    naming a known C-role (flow.py CROLES or brief line 18). Zero-width/format characters, inner spaces
    ('C S O') or an unknown acronym -> None. No known-role list -> None (fail closed)."""
    s = str(x or "").strip()
    if not re.fullmatch(r"[A-Za-z]{3}", s):
        return None
    k = known if known is not None else known_roles()
    return s.upper() if s.upper() in k else None


def hold_clearers(role) -> tuple[str, ...]:
    return HOLD_CLEARERS_BY_ROLE.get(norm_role(role), HOLD_CLEARERS)


def load_held_roles(path: Path | None = None) -> tuple[dict | None, str]:
    """Return (data, error). Missing or unreadable = error (fail closed, #36: a one-line rm is no bypass).
    An empty registry is an explicit {"holds": [], "cosigns": []} file."""
    path = path or HELD_ROLES
    if not path.is_file():
        return None, f"missing {path} (fail closed)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"unreadable {path}: {e}"
    if not isinstance(data, dict) or not isinstance(data.get("holds", []), list) \
            or not isinstance(data.get("cosigns", []), list):
        return None, f"{path} must be an object with holds and cosigns lists"
    return data, ""


def hold_cleared(hold: dict, cosigns: list) -> tuple[bool, list[str]]:
    """A hold clears only when BOTH a CIO and a CSO cosign name its hold id
    with a non-empty ref. One cosigner alone never clears it. Exception (Owner t2s1): a hold on the CSO
    role clears with a CIO cosign alone; CSO and every other role are ignored."""
    hid = hold.get("id")
    clearers = hold_clearers(hold.get("role"))
    have = set()
    for c in cosigns:
        if not isinstance(c, dict) or c.get("hold_id") != hid:
            continue
        by = norm_role(c.get("by"))
        if by == norm_role(hold.get("role")):
            continue  # #36 C16: a role never cosigns its own hold (CSO on a CSO hold)
        if by in clearers and str(c.get("ref") or "").strip():
            have.add(by)
    missing = [r for r in clearers if r not in have]
    return (not missing), missing


def is_held(acro: str, path: Path | None = None) -> tuple[bool, str]:
    """Hard hold, checked before anything else. Accident guard plus detection,
    not security: --by on a cosign is not a verified caller identity."""
    acro = (acro or "").strip().upper()
    data, err = load_held_roles(path)
    if data is None:
        return True, f"held_roles unreadable, failing closed: {err}"
    known = known_roles()
    bad = [h.get("id") for h in data.get("holds", []) if not isinstance(h, dict) or valid_hold_role(h.get("role"), known) is None]
    if bad:
        return True, f"held_roles has hold(s) with an invalid or unknown role {bad}, failing closed"
    for h in data.get("holds", []):
        if not isinstance(h, dict) or norm_role(h.get("role")) != acro:
            continue
        ok, missing = hold_cleared(h, data.get("cosigns", []))
        if not ok:
            return True, (
                f"{acro} is on hard hold {h.get('id')} ({h.get('reason')}); "
                f"clears only with recorded CIO+CSO cosigns, missing {', '.join(missing)}"
            )
    return False, f"{acro} not on hard hold"


def is_quarantined(acro: str) -> tuple[bool, str]:
    """Refuse a NEW role whose onboarding is not PASS.

    Existing brief.txt C-roles are not quarantined (huddle: flow/gate/cost_gate
    refuse a new role until onboarding is PASS; a new bot may chat before PASS).
    This is an accident guard plus detection, not security: --role/--by are not
    a verified caller identity on the shared box.
    """
    acro = (acro or "").strip().upper()
    held, why = is_held(acro)
    if held:
        return True, why
    if not re.fullmatch(r"[A-Z]{3}", acro):
        return True, (
            f"{acro or '(empty)'} is not a 3-letter C-role; "
            "quarantine blocks chain, gate, and paid work "
            "(accident guard plus detection, not identity)"
        )
    if acro in load_onboarded_roles():
        return False, f"{acro} is an onboarded original C-role (onboarded_roles.json); not quarantined"
    payload = collect(role=acro, allow_missing=True)
    bots = payload.get("bots") or []
    if not bots:
        return True, (
            f"new role {acro} has no profile and is not onboarding PASS; "
            "quarantine blocks chain, gate, and paid work"
        )
    result = bots[0].get("result") or "FAIL"
    if result == "PASS":
        return False, f"new role {acro} onboarding is PASS"
    return True, (
        f"new role {acro} onboarding is {result}; "
        "quarantine blocks chain, gate, and paid work"
    )


def refuse_if_quarantined(acro: str, raise_cls: type[Exception] | None = None) -> None:
    held, why = is_quarantined(acro)
    if not held:
        return
    msg = f"quarantine: {why}"
    if raise_cls is not None:
        raise raise_cls(msg)
    raise SystemExit(f"REFUSED: {msg}")


def evaluate_bot(entry: dict, croles: set[str], brief_roles: set[str],
                 charter_added: set[str] | None, role_packets: dict[str, Path],
                 brief_text: str, amend_text: str, title_reg: dict | None) -> dict:
    if entry["error"] or entry["data"] is None:
        return dict(
            id=entry["id"], name=entry["name"], acro="—",
            name_format="FAIL", title="UNVERIFIABLE", title_note=entry["error"] or "",
            croles="FAIL", a_helper="FAIL", s_helper="FAIL",
            brief_or_charter="FAIL", brief_lines="FAIL",
            packet="FAIL", ack="FAIL",
            result="FAIL", note=entry["error"] or "unreadable profile",
        )
    data = entry["data"]
    name = str(data.get("name") or "")
    m = NAME_RE.match(name)
    acro = m.group(2) if m else ""
    role = m.group(3).strip() if m else ""
    name_ok = bool(m and role)
    in_croles = acro in croles
    a_id, s_id = helper_ids(acro) if acro else ("", "")
    a_st, a_note = helper_settings_status(a_id) if a_id else ("FAIL", "no helper id")
    s_st, s_note = helper_settings_status(s_id) if s_id else ("FAIL", "no helper id")
    a_ok = a_st == "PASS"
    s_ok = s_st == "PASS"

    in_brief = acro in brief_roles
    if in_brief:
        loc, loc_note = "PASS", "in brief.txt C-role list"
    elif charter_added is None:
        loc, loc_note = "UNVERIFIABLE", "not in brief.txt; Charter snapshot unread or unverified"
    elif acro in charter_added:
        loc, loc_note = "PASS", "in Charter snapshot 'Roles added after the brief'"
    else:
        loc, loc_note = "FAIL", "in neither brief.txt nor the Charter snapshot added-roles section"

    lines_st, lines_note = check_brief_lines(acro, brief_text, amend_text)
    title_st, title_note = title_status(acro, title_reg)
    pkt_path = role_packet_path(acro) if acro else None
    pkt_ok = bool(pkt_path) and pkt_path.is_file()
    is_new = bool(acro) and is_new_role(acro)  # onboarded_roles.json, not brief line 18 / CROLES
    ack_st, ack_note = check_ack(acro, is_new, role_packets) if acro else ("FAIL", "no acronym")
    desc_st, desc_note = description_status(data, acro, role)
    quiz_st, quiz_note = check_quiz(acro, required=is_new) if acro else ("FAIL", "no acronym")

    # Title never enters overall. UNVERIFIABLE charter does not FAIL.
    # WAIT does not become PASS; FAIL outranks WAIT (PASS < WAIT < FAIL).
    # Quiz WAIT on an existing role (count outside 5-9) does not FAIL the bot.
    result = worst_status(
        pf(name_ok), pf(in_croles), pf(a_ok), pf(s_ok),
        "PASS" if loc == "UNVERIFIABLE" else loc,
        lines_st, pf(pkt_ok),
        "PASS" if ack_st == "N/A" else ack_st,
        desc_st,
        "PASS" if quiz_st in ("N/A", "WAIT") else quiz_st,
    )
    return dict(
        id=entry["id"],
        name=name or "(empty)",
        acro=acro or "—",
        name_format=pf(name_ok),
        title=title_st,
        title_note=title_note,
        croles=pf(in_croles),
        a_helper=f"{a_st} {a_id or '—'}",
        a_helper_note=a_note,
        s_helper=f"{s_st} {s_id or '—'}",
        s_helper_note=s_note,
        brief_or_charter=loc,
        brief_or_charter_note=loc_note,
        brief_lines=lines_st,
        brief_lines_note=lines_note,
        packet=pf(pkt_ok),
        ack=ack_st,
        ack_note=ack_note,
        description=desc_st,
        description_note=desc_note,
        quiz=quiz_st,
        quiz_note=quiz_note,
        result=result,
        note="",
        new=is_new,
    )



def collect(*, role: str | None = None, charter_path: Path | None = None,
            titles_path: Path | None = None, allow_missing: bool = False) -> dict:
    """Run the alignment checks and return a payload. Never prints.

    If role is set and no matching profile is found: exit=2, unless
    allow_missing, in which case bots=[] and result=FAIL, exit=1.
    """
    croles = load_croles(FLOW)
    brief_roles = load_brief_roles(BRIEF)
    charter = load_charter_snapshot(Path(charter_path) if charter_path else DEFAULT_CHARTER)
    charter_added = charter["added_roles"]
    title_reg, title_src = load_title_registry(Path(titles_path) if titles_path else DEFAULT_TITLES)
    brief_text, amend_text = load_line_sources()
    quizzes = list_role_quizzes()
    aso_st, aso_note = aso_sso_independence()

    discovered = discover_bots()
    for e in discovered:
        if e["data"]:
            m = NAME_RE.match(str(e["data"].get("name") or ""))
            e["acro"] = m.group(2) if m else ""

    missing_role = False
    if role:
        discovered = [e for e in discovered if e.get("acro") == role]
        if not discovered:
            missing_role = True
            held_now, held_why = is_held(role)
            if held_now and not allow_missing:
                # Pre-creation hard hold (e.g. HOLD-CXO-1): show HELD, never crash.
                row = dict(id="(no profile)", name=f"(no profile.json for {role})", acro=role,
                           name_format="N/A", title="UNVERIFIABLE", title_note="no profile",
                           croles="N/A", a_helper="N/A", s_helper="N/A", brief_or_charter="N/A",
                           brief_lines="N/A", packet="N/A", ack="N/A", quiz="N/A",
                           held="HELD", held_note=held_why, result="HELD", note=held_why, new=True)
                return dict(error=None, exit=1, result="FAIL", bots=[row], bots_checked=1, bot_fail=1,
                            bot_wait=0, packet_fail=0, held_without_profile=True,
                            note=f"{role} has no profile.json yet and is on hard hold")
            if not allow_missing:
                return dict(
                    error=f"no profile.json with name acronym {role}",
                    exit=2,
                    result="FAIL",
                    bots=[],
                    bots_checked=0,
                    bot_fail=0,
                    bot_wait=0,
                    packet_fail=0,
                )

    existing = set(croles) | set(brief_roles)
    for e in discover_bots():
        if e["data"]:
            m = NAME_RE.match(str(e["data"].get("name") or ""))
            if m:
                existing.add(m.group(2))
    if charter_added is not None:
        existing |= charter_added

    role_packets = list_role_packets()
    packet_status = {r: ("PASS" if role_packet_path(r).is_file() else "FAIL") for r in sorted(existing)}

    rows = [evaluate_bot(e, croles, brief_roles, charter_added, role_packets,
                         brief_text, amend_text, title_reg)
            for e in discovered]
    # Hard hold (alignment/held_roles.json) overrides any row result.
    for r in rows:
        h, why = is_held(r.get("acro") or "")
        r["held"] = "HELD" if h and r.get("acro") not in ("", "—") else "-"
        r["held_note"] = why if r["held"] == "HELD" else ""
        if r["held"] == "HELD":
            r["result"] = "HELD"
            r["note"] = (r.get("note") + "; " if r.get("note") else "") + why

    note = (
        "self-report registry spot-checked by CSO; profile.json is not the source; "
        "UNVERIFIABLE always (advisory); a missing or unreported title never FAILs"
    )
    bot_fails = sum(1 for r in rows if r["result"] in ("FAIL", "HELD"))
    bot_waits = sum(1 for r in rows if r["result"] == "WAIT")
    packet_fails = sum(1 for st in packet_status.values() if st == "FAIL")
    integrity_fail = charter["integrity"] == "FAIL"
    try:   # C8: null W7 cost limits -> FAIL naming the missing W4 record
        import line_guard
        cost_lim = line_guard.cost_limits(state=os.environ.get("GB_STATE", GB_ROOT + "/gb_state"))
    except Exception as e:
        cost_lim = dict(result="FAIL", note=f"cost limits unreadable: {type(e).__name__}: {e}")
    any_fail = bot_fails + packet_fails > 0 or integrity_fail or aso_st == "FAIL" or missing_role or cost_lim["result"] == "FAIL"
    summary = "FAIL" if any_fail else ("WAIT" if bot_waits else "PASS")

    bot_keys = (
        "id", "name", "acro", "name_format", "title", "title_note", "croles",
        "a_helper", "a_helper_note", "s_helper", "s_helper_note",
        "brief_or_charter", "brief_or_charter_note",
        "brief_lines", "brief_lines_note",
        "packet", "ack", "ack_note", "held", "held_note",
        "description", "description_note", "quiz", "quiz_note",
        "result", "new",
    )
    payload = dict(
        note=note,
        ack_path=str(ACKS / "<ROLE>.json"),
        charter_source=charter["source"],
        charter_integrity=charter["integrity"],
        charter_integrity_note=charter["note"],
        charter_added_roles=sorted(charter_added) if charter_added is not None else None,
        title_registry=title_src,
        brief_roles=sorted(brief_roles),
        flow_croles=sorted(croles),
        quizzes=quizzes,
        packets=packet_status,
        aso_sso_independence=aso_st,
        aso_sso_independence_note=aso_note,
        helper_independence={k: dict(status=v[0], note=v[1]) for k, v in helper_independence().items()},
        bots=[{k: r[k] for k in bot_keys if k in r} for r in rows],
        bots_checked=len(rows),
        bot_fail=bot_fails,
        bot_wait=bot_waits,
        packet_fail=packet_fails,
        result=summary,
        exit=1 if any_fail else 0,
        charter=charter,
        integrity_fail=integrity_fail,
        packet_status=packet_status,
        missing_role=missing_role,
        cost_limits=cost_lim,
    )
    return payload


def _team_fails(payload: dict) -> list[tuple[str, str]]:
    """#36 C10: team-level FAILs (not tied to one bot row)."""
    out = []
    if payload.get("integrity_fail") or payload.get("charter_integrity") == "FAIL":
        out.append(("charter_integrity", str(payload.get("charter_integrity_note") or "charter integrity FAIL")))
    if payload.get("aso_sso_independence") == "FAIL":
        out.append(("aso_sso_independence", str(payload.get("aso_sso_independence_note") or "ASO/SSO share a model")))
    for pair, v in sorted((payload.get("helper_independence") or {}).items()):
        if isinstance(v, dict) and v.get("status") == "FAIL":
            out.append((f"helper_independence {pair}", str(v.get("note") or "FAIL")))
    for role, st in sorted((payload.get("packet_status") or payload.get("packets") or {}).items()):
        if st == "FAIL":
            out.append((f"packet {role}", f"onboarding packet missing for {role}"))
    if payload.get("missing_role"):
        out.append(("missing_role", "a brief role has no bot row"))
    cl = payload.get("cost_limits") or {}
    if isinstance(cl, dict) and cl.get("result") == "FAIL":
        out.append(("cost_limits", str(cl.get("note") or "cost limits FAIL")))
    return out


def write_alarms(payload: dict, state: str | None = None) -> list[str]:
    """C1/C10 alarm writer: each bot row with result FAIL files an RQ-CAO alarm (CAO -> CBO). When the
    failing role or the subject is CBO, the alarm goes to ES-CSO and a Owner alarm file instead, so
    escalation never depends on CBO alone. #36 C10: HELD rows also file a (deduped) notice on the same
    routing, and team-level FAILs (charter integrity, helper/ASO-SSO independence, packets, missing role,
    cost limits) file RQ-CAO plus ES-CSO. One file per (role, note) so a repeated wake does not spam."""
    st = Path(state or os.environ.get("GB_STATE", GB_ROOT + "/gb_state"))
    out = []

    def _put(targets, name):
        for sub, body in targets:
            d = st / "queue" / sub; d.mkdir(parents=True, exist_ok=True)
            f = d / name
            if not f.exists():
                import line_guard   # #36 C8: queue files only through line_guard (fallback alarm + raise if refused)
                line_guard.escalate_write(str(f), body); out.append(str(f))

    for r in payload.get("bots") or []:
        res = r.get("result")
        if res not in ("FAIL", "HELD"):
            continue
        role = str(r.get("acro") or r.get("id") or "?")
        note = str(r.get("note") or r.get("title_note") or f"alignment {res}")
        key = hashlib.sha256(f"{role}|{res}|{note}".encode() if res == "HELD" else f"{role}|{note}".encode()).hexdigest()[:12]
        rec = dict(kind=f"alignment {res}", subject=role, note=note, raised_by="CAO", ts=time.time())
        if role == "CBO" or "CBO" in (r.get("subject"), r.get("failing_role")):
            targets = [("es_cso", dict(rec, line="ES-CSO", **{"from": "CSO"}, to="Owner", cc="CSO")), ("owner", dict(rec, line="ES-CSO", **{"from": "CSO"}, to="Owner"))]
        else:
            targets = [("rq", dict(rec, line="RQ-CAO", **{"from": "CAO"}, to="CBO"))]
        _put(targets, f"alignment-{role}-{'held-' if res == 'HELD' else ''}{key}.json")
    for what, note in _team_fails(payload):
        key = hashlib.sha256(f"TEAM|{what}|{note}".encode()).hexdigest()[:12]
        rec = dict(kind="alignment team FAIL", subject="TEAM", check=what, note=note, raised_by="CAO", ts=time.time())
        _put([("rq", dict(rec, line="RQ-CAO", **{"from": "CAO"}, to="CBO")),
              ("es_cso", dict(rec, line="ES-CSO", **{"from": "CSO"}, to="Owner", cc="CSO"))], f"alignment-TEAM-{key}.json")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="C1 onboarding alignment check (accident guard plus detection, not security)."
    )
    ap.add_argument("--role", metavar="X", help="check a single 3-letter acronym")
    ap.add_argument("--json", action="store_true", help="print a JSON object instead of the table")
    ap.add_argument("--charter", metavar="PATH", default=str(DEFAULT_CHARTER),
                    help="Charter snapshot path (default: %(default)s)")
    ap.add_argument("--titles", metavar="PATH", default=str(DEFAULT_TITLES),
                    help="self-report title registry (default: %(default)s)")
    args = ap.parse_args(argv)

    if args.role and not re.fullmatch(r"[A-Z]{3}", args.role):
        print("error: --role must be a 3-letter acronym", file=sys.stderr)
        return 2

    payload = collect(
        role=args.role,
        charter_path=Path(args.charter) if args.charter else None,
        titles_path=Path(args.titles) if args.titles else None,
    )
    if payload.get("exit") == 2:
        print(f"error: {payload.get('error')}", file=sys.stderr)
        return 2
    if payload.get("held_without_profile"):
        row = payload["bots"][0]
        if args.json:
            print(json.dumps({k: v for k, v in payload.items() if k != "exit"}, indent=2))
        else:
            print("alignment_check.py (C1; accident guard plus detection, not security)")
            print(f"{row['acro']}: HELD ({row['held_note']})")
            print(f"{row['acro']}: no profile.json yet; RESULT HELD")
        return 1

    public = {k: v for k, v in payload.items()
              if k not in ("charter", "integrity_fail", "packet_status", "missing_role", "exit")}
    if args.json:
        print(json.dumps(public, indent=2))
        return payload["exit"]

    charter = payload["charter"]
    charter_added = payload["charter_added_roles"]
    croles = payload["flow_croles"]
    brief_roles = payload["brief_roles"]
    quizzes = payload["quizzes"]
    packet_status = payload["packet_status"]
    bot_fails = payload["bot_fail"]
    bot_waits = payload["bot_wait"]
    packet_fails = payload["packet_fail"]
    rows = payload["bots"]
    integrity_fail = payload["integrity_fail"]
    note = payload["note"]

    print("alignment_check.py (C1; accident guard plus detection, not security)")
    print(f"profiles: {AGENTS}/*/profile.json")
    print(f"CROLES source: {FLOW} ({len(croles)} roles: {', '.join(croles) or 'NONE'})")
    print(f"brief.txt C-roles: {', '.join(brief_roles) or 'NONE'}")
    print(f"brief lines: {BRIEF} canonical; {AMENDMENTS} staging only (WAIT/DRAFT, never PASS)")
    print(f"Charter snapshot: {payload['charter_source']}")
    print(f"Charter integrity: {payload['charter_integrity']}"
          + (f" ({payload['charter_integrity_note']})" if payload['charter_integrity_note'] else ""))
    if charter_added is None:
        print("Charter added-roles: UNVERIFIABLE")
    else:
        added = ", ".join(charter_added) or "(section empty)"
        print(f"Charter added-roles: {added}")
    print(f"helpers root: {BOTS}")
    print(f"packets: {PACKETS}/<ROLE>.md")
    qnote = ", ".join(quizzes) if quizzes else "none present"
    print(f"note: packets/<ROLE>.quiz.json files are tolerated (not packets); present: {qnote}")
    print(f"acks: {ACKS}/<ROLE>.json (required for new bots only)")
    print(f"A/S helper independence (every pair): {payload['aso_sso_independence']} ({payload['aso_sso_independence_note'][:160]})")
    print(f"titles: {note}")
    print()

    cols = [
        ("name", "name", 38),
        ("name_format", "name", 10),
        ("title", "title", 13),
        ("acro", "acro", 4),
        ("croles", "CROLES", 6),
        ("a_helper", "A-helper", 10),
        ("s_helper", "S-helper", 10),
        ("brief_or_charter", "brief/charter", 13),
        ("brief_lines", "brief lines", 11),
        ("packet", "packet", 6),
        ("ack", "ack", 6),
        ("quiz", "quiz", 6),
        ("held", "held", 4),
        ("result", "RESULT", 6),
    ]

    def fmt(row):
        return "  ".join(str(row.get(k, ""))[:w].ljust(w) for k, _h, w in cols)

    header = "  ".join(h.ljust(w) for _k, h, w in cols)
    rule = "  ".join("-" * w for _k, _h, w in cols)
    print(header)
    print(rule)
    for r in rows:
        print(fmt(r))
        if r.get("note"):
            print(f"  note: {r['note']}")
    print()
    print(f"bots checked: {len(rows)}")
    print(f"bot FAIL: {bot_fails}  bot WAIT: {bot_waits}  bot PASS: {len(rows) - bot_fails - bot_waits}")
    missing_pkts = [r for r, st in packet_status.items() if st == "FAIL"]
    print(f"role packets: {len(packet_status) - packet_fails}/{len(packet_status)} present"
          + (f"  missing: {', '.join(missing_pkts)}" if missing_pkts else ""))
    failed = [r for r in rows if r["result"] == "FAIL"]
    waiting = [r for r in rows if r["result"] == "WAIT"]
    if failed or missing_pkts or integrity_fail or payload["aso_sso_independence"] == "FAIL":
        print()
        print("FAIL detail:")
        if integrity_fail:
            print(f"  Charter integrity: {charter['note']}")
        if payload["aso_sso_independence"] == "FAIL":
            for k, v in payload.get("helper_independence", {}).items():
                if v["status"] != "PASS":
                    print(f"  {k}: {v['note']}")
        for r in failed:
            misses = []
            if r.get("name_format") == "FAIL":
                misses.append("name_format")
            if r.get("description") == "FAIL":
                misses.append(r.get("description_note") or "description")
            if r.get("croles") == "FAIL":
                misses.append("not in flow.py CROLES")
            if str(r.get("a_helper", "")).startswith("FAIL"):
                misses.append(r.get("a_helper_note") or f"missing {str(r.get('a_helper',''))[5:].strip()}")
            if str(r.get("s_helper", "")).startswith("FAIL"):
                misses.append(r.get("s_helper_note") or f"missing {str(r.get('s_helper',''))[5:].strip()}")
            if r.get("brief_or_charter") == "FAIL":
                misses.append(r.get("brief_or_charter_note") or "brief/charter")
            if r.get("brief_lines") == "FAIL":
                misses.append("brief lines: " + (r.get("brief_lines_note") or "missing"))
            if r.get("packet") == "FAIL":
                misses.append(f"missing packet {r['acro']}.md")
            if r.get("ack") == "FAIL":
                misses.append(r.get("ack_note") or "ack")
            if r.get("quiz") == "FAIL":
                misses.append(r.get("quiz_note") or "quiz")
            print(f"  {r['name']}: {', '.join(misses)}")
        if missing_pkts:
            print(f"  system: missing packets for {', '.join(missing_pkts)}")
    if waiting:
        print()
        print("WAIT detail (DRAFT in amendments; does not fail the exit code alone):")
        for r in waiting:
            print(f"  {r['name']}: {r.get('brief_lines_note') or 'WAIT'}")
    notes = [r for r in rows if r.get("brief_lines_note") and (
        "N/A" in r["brief_lines_note"] or "WAIT" in r["brief_lines_note"] or "DRAFT" in r["brief_lines_note"]
    )]
    if notes:
        print()
        print("brief-lines notes (N/A in brief.txt, or WAIT/DRAFT in amendments):")
        for r in notes:
            print(f"  {r['acro']}: {r['brief_lines_note']}")
    return payload["exit"]


if __name__ == "__main__":
    sys.exit(main())
