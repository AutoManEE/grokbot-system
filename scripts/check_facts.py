#!/usr/bin/env python3
"""check_facts.py: code does every numeric comparison for CSO CHECK tasks. Zero AI.

A CHECK task gives facts ('Facts: budget 1000, spent 40, ...') and a numbered checklist
('(1) ... (2) ...'). For each item, code finds every threshold ('at or under 100', 'at or under 10% of budget',
'at 3x', ...), finds the matching fact by name and unit, and compares with Decimal. ASO only writes reasons;
if ASO's PASS/FAIL differs from code's, code wins and flow.py flags the difference.

Rules (fail closed):
- a needed fact is missing -> item FAIL 'fact missing: <metric>'.
- 'N% of X' -> limit = N/100 * fact X (both from the task; no hand math, no rule changed).
- a bare 'at' is read as a minimum for 'x' (savings at 3x means at least 3x) and as a maximum for '%' and '$'
  (spend at 1% means no more than 1%).
- an item with no threshold is a yes/no item: a fact sharing 2+ words with it decides it ('not', 'no',
  'missing', 'unset' in the fact -> FAIL); no such fact -> FAIL 'fact missing'.
- code cannot tell (two facts match equally) -> result None; flow leaves that item to ASO and flags it.
Only compares numbers the task states; it never computes totals (that stays in finance_facts.py).
"""
import re
from decimal import Decimal, InvalidOperation

OPS = [("at or under", "le"), ("at or below", "le"), ("no more than", "le"), ("not more than", "le"), ("at most", "le"),
       ("under", "lt"), ("below", "lt"), ("less than", "lt"), ("at or over", "ge"), ("at or above", "ge"),
       ("at least", "ge"), ("no less than", "ge"), ("over", "gt"), ("above", "gt"), ("more than", "gt"), ("at", "at")]
VAL = r"(\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%|\d[\d,]*(?:\.\d+)?\s?x\b)"
CMP_RE = re.compile(r"\b(" + "|".join(re.escape(o) for o, _ in OPS) + r")\s+" + VAL + r"(?:\s+of\s+([a-z][a-z ]*?))?(?=\s*(?:$|[,;.()])|\s+(?:with|and|including)\b)", re.I)
STOP = {"the", "a", "an", "of", "on", "per", "total", "with", "and", "both", "is", "are", "at", "to", "new", "including", "request", "s"}
NEG_RE = re.compile(r"\b(not|no|missing|unset|none)\b", re.I)
OPNAME = dict(le="<=", lt="<", ge=">=", gt=">")


def _norm(w):
    w = w.lower()
    for suf in ("es", "s"):
        if w.endswith(suf) and len(w) > 4:
            return w[: -len(suf)]
    return w


def _words(s, stop=True):
    return {_norm(w) for w in re.findall(r"[a-z]+", (s or "").lower()) if not stop or w not in STOP}


def _val(tok):
    t = tok.replace(",", "").replace(" ", "")
    unit = "$" if t.startswith("$") else "%" if t.endswith("%") else "x"
    try:
        return Decimal(t.strip("$%x")), unit
    except InvalidOperation:
        return None, unit


def parse_facts(text):
    """Facts after 'Facts:' up to the first '(1)'; comma/semicolon separated. Each: phrase, value, unit."""
    m = re.search(r"\bfacts?\s*:\s*(.+?)(?=\(1\)|$)", text or "", re.I | re.S)
    if not m:
        return []
    out = []
    for part in re.split(r"[,;]", m.group(1)):
        p = " ".join(part.split()).strip(" .")
        if not p:
            continue
        v = re.search(VAL, p, re.I)
        val, unit = _val(v.group(1)) if v else (None, None)
        out.append(dict(phrase=p, name=(p[: v.start()] if v else p).strip(), value=val, unit=unit))
    return out


def _find_fact(metric, unit, facts):
    mw = _words(metric)
    if not mw:
        return None, "no metric name"
    # every word of the metric must be in the fact's name; the fact with the fewest extra words wins
    scored = sorted((len(_words(f["name"]) - mw), i) for i, f in enumerate(facts)
                    if f["value"] is not None and f["unit"] == unit and mw <= _words(f["name"]))
    if not scored:
        return None, f"fact missing: {metric.strip()}"
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return "AMBIGUOUS", f"two facts match {metric.strip()}"
    return facts[scored[0][1]], None


def _fmt(v, unit):
    q = v.normalize()
    t = f"{q:f}"
    return f"${t}" if unit == "$" else f"{t}%" if unit == "%" else f"{t}x"


def _cmp(op, val, lim):
    return dict(le=val <= lim, lt=val < lim, ge=val >= lim, gt=val > lim)[op]


def check_item(item, facts):
    """-> dict(item, result PASS/FAIL/None, numbers=[...], why)."""
    cmps = list(CMP_RE.finditer(item))
    if not cmps:   # yes/no item
        iw = _words(item)
        best = max(((len(iw & _words(f["phrase"])), f) for f in facts if f["value"] is None), key=lambda x: x[0], default=(0, None))
        if best[0] < 2:
            return dict(item=item, result="FAIL", numbers=[], why="fact missing")
        neg = bool(NEG_RE.search(best[1]["phrase"]))
        return dict(item=item, result="FAIL" if neg else "PASS", numbers=[], why=f"fact: {best[1]['phrase']}")
    numbers, results, start = [], [], 0
    for m in cmps:
        metric = re.sub(r"^\s*(with|and)\s+", "", item[start:m.start()], flags=re.I)
        start = m.end()
        op = dict(OPS)[m.group(1).lower()]
        lim, unit = _val(m.group(2))
        if op == "at":
            op = "ge" if unit == "x" else "le"
        of = m.group(3)
        if of and unit == "%":   # 'N% of X': limit in X's unit
            base, err = _find_fact(of, "$", facts)
            if err:
                return dict(item=item, result=None if base == "AMBIGUOUS" else "FAIL", numbers=numbers, why=err)
            lim, unit = lim / Decimal(100) * base["value"], "$"
            numbers.append(dict(fact=base["phrase"], role="base"))
        fact, err = _find_fact(metric, unit, facts)
        if err:
            return dict(item=item, result=None if fact == "AMBIGUOUS" else "FAIL", numbers=numbers, why=err)
        ok = _cmp(op, fact["value"], lim)
        numbers.append(dict(metric=metric.strip(), value=_fmt(fact["value"], unit), op=OPNAME[op], limit=_fmt(lim, unit),
                            result="PASS" if ok else "FAIL"))
        results.append(ok)
    bad = [n for n in numbers if n.get("result") == "FAIL"]
    why = "; ".join(f"{n['metric']} {n['value']} is not {n['op']} {n['limit']}" for n in bad) or \
          "; ".join(f"{n['metric']} {n['value']} {n['op']} {n['limit']}" for n in numbers if "metric" in n)
    return dict(item=item, result="PASS" if all(results) else "FAIL", numbers=numbers, why=why)


def check(text, items):
    facts = parse_facts(text)
    return [check_item(it, facts) for it in items]
