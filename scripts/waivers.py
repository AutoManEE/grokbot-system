#!/usr/bin/env python3
"""Recorded legacy waivers.

Waivers live in alignment/waivers/*.json and are read by item id; a hand note never counts. An item is
WAIVED only when its file has status "ruled" and a cio_ruling_ref, the item names its id (gate_id + check,
or open_item_id + field), and the event time falls inside the item's [from, until] bounds (ISO-8601 with an
offset). Anything else (unknown id, other field, later or missing time, pending file) is not waived.
Unreadable files waive nothing. Zero AI. Accident guard plus detection, not security.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import glob, json, math, os
from datetime import datetime

WAIVERS = os.environ.get("GB_WAIVERS", GB_ROOT + "/alignment/waivers")


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    return d.timestamp() if d.tzinfo else None


def load(path=None):
    """All items from all waiver files: dict(file, ruled, ref, status, item, t0, t1)."""
    out = []
    for f in sorted(glob.glob(os.path.join(path or WAIVERS, "*.json"))):
        try:
            w = json.load(open(f))
        except (OSError, ValueError):
            continue
        if not isinstance(w, dict):
            continue
        ruled = w.get("status") == "ruled" and bool(w.get("cio_ruling_ref"))
        for it in w.get("items") or []:
            if isinstance(it, dict):
                out.append(dict(file=os.path.basename(f), ruled=ruled, ref=w.get("cio_ruling_ref"), item=it,
                                t0=_ts(it.get("from")), t1=_ts(it.get("until")), status=w.get("status", "")))
    return out


def _in_bounds(w, ts):
    return (isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts)
            and w["t0"] is not None and w["t1"] is not None and w["t0"] <= ts <= w["t1"])


def listed_gate_ids(items):
    """Gate ids named in any waiver file (always audited, never age out of the window)."""
    return {w["item"]["gate_id"]: f"{w['file']}: {w['status']}" for w in items if w["item"].get("gate_id")}


def gate_waiver(gate_id, check, ts, items):
    """Waiver text when gate_id + check (e.g. 'C16 V3') is ruled and ts is in bounds; else None."""
    for w in items:
        it = w["item"]
        if w["ruled"] and it.get("gate_id") == gate_id and it.get("check") == check and _in_bounds(w, ts):
            return f"{w['file']}: {w['ref']}"
    return None


def oi_waiver(oi_id, field, t_from, t_to, items):
    """Waiver text when an Open Items edit (id, field) seen between snapshots t_from..t_to is ruled and the
    whole snapshot window lies inside the item's bounds; else None."""
    for w in items:
        it = w["item"]
        if (w["ruled"] and str(it.get("open_item_id")) == str(oi_id) and it.get("field") == field
                and it.get("check") == "C11" and _in_bounds(w, t_from) and _in_bounds(w, t_to)):
            return f"{w['file']}: {w['ref']}"
    return None
