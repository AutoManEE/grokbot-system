"""Canonical C-role set. Single source for flow, title_registry, and the other known-role lists.

alignment_check.load_croles still parses a literal CROLES = {...} in flow.py
(tests write fixture flow.py files that way). flow.py asserts that literal
equals this set at import time. Accident guard plus detection, not security.
"""
from __future__ import annotations

# Stable display order. Keep in sync with the frozenset.
CROLES_ORDER = (
    "CBO", "CIO", "CFO", "CTO", "CMO", "CSO", "CAO", "CPO", "COO", "CRO",
    "CCO", "CXO", "CLO", "CGO", "CHO",
)
CROLES = frozenset(CROLES_ORDER)
