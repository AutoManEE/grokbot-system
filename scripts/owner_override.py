#!/usr/bin/env python3
"""Verify a Owner-signed override that carries a C9 quote id.

Accident guard plus detection, not security: signed_by is a field on a
file, not a cryptographic signature, and is not a verified caller identity
on the shared box. A CSO-signed override is never accepted (C13 edit 2:
CSO does not sign overrides for content it is not allowed to read).

A quote id is verifiable only when it appears in the C9 quotes registry
(message id or card id). Missing or unknown ids are UNVERIFIED and cannot
be treated as approval (C9 / brief.txt:254).

#36 C9 scoping: registry rows carry "scope" (a string or list). Any approval use passes the scope(s) it
needs; a quote is VERIFIED for that use only when one of its row scopes matches. A row with no scope
never approves a scoped use. check_override always needs a scope.  is scoped to the brake carve-out
(closes and SL tightening) only. quote_status without a scope only says the quote exists (display, C9
marking) and is never used for approval by a gate.
"""
from __future__ import annotations
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))

import json, re
import os
from pathlib import Path

DEFAULT_QUOTES = Path(os.environ.get("GB_C9_QUOTES", GB_ROOT + "/alignment/c9_quotes.jsonl"))


def load_quotes(path: Path | str | None = None) -> dict[str, dict]:
    p = Path(path) if path is not None else DEFAULT_QUOTES
    out: dict[str, dict] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        qid = str(rec.get("id") or rec.get("quote_id") or "").strip()
        if qid:
            out[qid] = rec
    return out


# #36 C9 retest: money:<kind>:<action> names no particular request, so it never matches (replay guard).
GENERIC_MONEY_SCOPE = re.compile(r"money:[^:]*:[^:]*")


def quote_scopes(rec) -> set[str]:
    s = rec.get("scope") if isinstance(rec, dict) else None
    if isinstance(s, str):
        return {s.strip()} if s.strip() else set()
    if isinstance(s, list):
        return {str(x).strip() for x in s if str(x).strip()}
    return set()


def quote_status(quote_id: str, quotes: dict[str, dict] | None = None, scope=None) -> tuple[str, str]:
    qid = (quote_id or "").strip()
    if not qid:
        return "UNVERIFIED", "missing C9 quote id"
    recs = quotes if quotes is not None else load_quotes()
    if qid not in recs:
        return "UNVERIFIED", f"C9 quote id {qid!r} is not in the quotes registry"
    if scope is not None:
        want = {scope} if isinstance(scope, str) else {str(x) for x in scope}
        have = quote_scopes(recs[qid])
        generic = {s for s in have if GENERIC_MONEY_SCOPE.fullmatch(s)}
        if generic and not ((have - generic) & want):
            return "UNVERIFIED", (f"C9 quote id {qid!r} has only generic money scopes {sorted(generic)}; a money "
                                  "quote must name gate:<id> only")
        if not ((have - generic) & want):
            return "UNVERIFIED", f"C9 quote id {qid!r} is not scoped for {sorted(want)}"
    return "VERIFIED", qid


def mark_quote(quote, quote_id=None, quotes: dict[str, dict] | None = None) -> dict:
    """C9: a Owner quote in a briefing, directive, gate or money_settings provenance field. Without a
    verifiable message or card id it is marked UNVERIFIED and approval is False."""
    st, why = quote_status(quote_id or "", quotes)
    return dict(quote=quote, quote_id=quote_id or None, status=st, approval=st == "VERIFIED",
                text=quote if st == "VERIFIED" else f"UNVERIFIED: {quote}", note=why)


def check_override(src, quotes_path: Path | str | None = None, scope=None) -> tuple[bool, str, dict]:
    """Return (ok, reason, record). ok is True only for a Owner-signed
    override that carries a verifiable C9 quote id.
    src is a path, a Path, or an already-loaded dict.
    """
    if isinstance(src, (str, Path)):
        path = Path(src)
        if not path.is_file():
            return False, f"override file missing: {path}", {}
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return False, f"override unreadable: {e}", {}
    elif isinstance(src, dict):
        rec = src
    else:
        return False, "override is not a file or object", {}
    if not isinstance(rec, dict):
        return False, "override is not an object", {}

    signed = str(rec.get("signed_by") or rec.get("by") or "").strip()
    if signed != "Owner":
        if signed == "CSO":
            return False, "CSO cannot sign an override (no CSO closer-look; Owner-signed only)", rec
        return False, f"override signed_by must be Owner, not {signed or '(empty)'}", rec

    qid = str(rec.get("quote_id") or rec.get("c9_quote_id") or rec.get("card_id") or rec.get("message_id") or "").strip()
    if not scope:
        return False, "override scope required (#36 C9): a quote approves only the action it is scoped to", rec
    st, note = quote_status(qid, load_quotes(quotes_path), scope=scope)
    if st != "VERIFIED":
        return False, note, rec
    return True, f"Owner override with verified C9 quote id {qid}", rec
