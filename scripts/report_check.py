#!/usr/bin/env python3
"""report_check.py (P3-01, CAO): validate a Section 5 reasoning report. Zero AI.
Report JSON = the action-log entry shape: {item (task id), bot (role), result, objective, options[{name, deltas,
pros, cons}], decision, reasoning, confidence (int 0-100), conditions[{name, required, result PASS|FAIL|N/A}]}.
Rules (brief section 5): all fields present; confidence is a number 0-100 in every report; every condition is
named; if any REQUIRED condition FAILs the result must be FAIL, whatever the confidence; at least one condition
(measurable PASS/FAIL logic). Exit 0 valid, 1 invalid (errors printed).
Also applies code checks (do not move these into a model prompt):
  arith_recheck — explicit a op b [op c...] = r claims in reasoning, decision, and optional S-out are
    computed with a precedence parser (never eval). A mismatch adds required condition arith_recheck=FAIL
    naming the expression and forces result FAIL. No arithmetic: add nothing.
  decision_contains_s_out_number — when the task asks for a number (or S-out is mainly numeric), the
    decision must contain the S-suite output's number; otherwise a required FAIL condition is added.
  content_recheck — banned characters (at least U+2014 em dash; other dashes only if the task forbids
    them), placeholders, truncation markers, and empty required sections force a required FAIL
    condition and force result FAIL. independent_review cannot override a code FAIL.
The money-numbers rule is unchanged and is not implemented here.
Usage: report_check.py -f report.json
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, json, os, re, sys
sys.path.insert(0, GB_ROOT + "/scripts")
import action_log as al

NUM_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
ASK_NUMBER_RE = re.compile(
    r"\b(compute|calculate|calculation|how many|how much|what is|give the number|reply with the number|the total)\b",
    re.I,
)
ARITH_HINT_RE = re.compile(r"[\d×x*/+\-−]|turns|sum|product|total", re.I)
ANSWER_RE = re.compile(r"\banswer\s*[:\-]\s*(" + NUM_RE.pattern + r")", re.I)
MUL_OPS = {"×", "*", "x", "X"}
ADD_OPS = {"+", "−", "-"}
DIV_OPS = {"/"}
ALL_OPS = MUL_OPS | ADD_OPS | DIV_OPS


def parse_number(s):
    return float(str(s).replace(",", "").strip())


def _is_times_x(text, i):
    """True if text[i] is a standalone 'x'/'X' used as multiplication, not a letter in a word."""
    if i >= len(text) or text[i] not in "xX":
        return False
    prev = text[i - 1] if i > 0 else " "
    nxt = text[i + 1] if i + 1 < len(text) else " "
    return (prev.isspace() or prev.isdigit() or prev in ",.)") and (nxt.isspace() or nxt.isdigit() or nxt in ".,+-−")


def _next_nonspace(text, i):
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _prev_emitted_is_num(tokens):
    return bool(tokens) and tokens[-1][0] == "num"


def _number_starts_at(text, i):
    if i < len(text) and text[i] in "+-" or (i < len(text) and text[i] == "−"):
        # signed number only when this is a new term (start, after op/=, or whitespace after op already handled)
        j = i + 1
        if j < len(text) and NUM_RE.match(text, j):
            return True
        return False
    return bool(NUM_RE.match(text, i))


def tokenize_arith(text):
    """Tokenize numbers, ops, and equals, skipping words. '/' and '-' are ops only when both
    sides are numbers (so '4 runs/day' is not division). 'x' is multiplication only as a standalone token.
    Never uses eval."""
    tokens, i, n = [], 0, len(text or "")
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        # equals
        if text[i] == "=":
            tokens.append(("eq", "=", i))
            i += 1
            continue
        # number (optional leading sign after op/eq/start)
        sign = ""
        j = i
        if text[j] in "+-" or text[j] == "−":
            allow_sign = (not tokens) or tokens[-1][0] in ("op", "eq")
            if allow_sign and NUM_RE.match(text, j + 1):
                sign = "-" if text[j] in "-−" else "+"
                j += 1
        m = NUM_RE.match(text, j)
        if m and (sign or not (j > 0 and (text[j - 1].isalnum() or text[j - 1] == "."))):
            raw = (sign + m.group(0)) if sign else m.group(0)
            # reject if the unsigned part is glued to a preceding letter (e.g. T3) when no sign
            tokens.append(("num", parse_number(raw), raw, i))
            i = m.end()
            continue
        # multiplication x
        if _is_times_x(text, i):
            tokens.append(("op", "×", i))
            i += 1
            continue
        # slash: only if previous emitted token is num and next nonspace starts a number
        if text[i] == "/":
            k = _next_nonspace(text, i + 1)
            if _prev_emitted_is_num(tokens) and k < n and (NUM_RE.match(text, k) or (text[k] in "+-−" and NUM_RE.match(text, k + 1))):
                # also require only whitespace between previous number end and this slash
                # (the prev token's end is not stored separately; if words were skipped, we still
                # require the character before '/' after skipping spaces to be the end of that number.)
                # If words were skipped, the previous non-space char is not part of a number.
                p = i - 1
                while p >= 0 and text[p].isspace():
                    p -= 1
                if p >= 0 and (text[p].isdigit() or text[p] in ".,"):
                    tokens.append(("op", "/", i))
                    i += 1
                    continue
            i += 1
            continue
        # plus, unicode times, asterisk, unicode minus, hyphen-minus.
        # Minus is only an op when both neighboring numbers sit on the SAME line
        # (a markdown/list dash after a newline is not subtraction).
        if text[i] in "×*+":
            tokens.append(("op", "×" if text[i] in "×*" else "+", i))
            i += 1
            continue
        if text[i] in "-−" and _prev_emitted_is_num(tokens):
            k = _next_nonspace(text, i + 1)
            p = i - 1
            while p >= 0 and text[p].isspace():
                p -= 1
            ends_num = p >= 0 and (text[p].isdigit() or (text[p] in ",." and p > 0 and text[p - 1].isdigit()))
            nxt_num = k < n and (NUM_RE.match(text, k) or (text[k] in "+-−" and NUM_RE.match(text, k + 1)))
            same_line = p >= 0 and k < n and "\n" not in text[p:k]
            if ends_num and nxt_num and same_line:
                tokens.append(("op", "-", i))
            i += 1
            continue
        # skip a word or single other char
        if text[i].isalnum():
            while i < n and (text[i].isalnum() or text[i] in "'’"):
                i += 1
        else:
            i += 1
    return tokens


def _eval_ops(items):
    """items is a list alternating number, op, number, ...  Precedence: * / × before + -. Left-associative."""
    if not items or len(items) % 2 == 0:
        raise ValueError("bad expression")
    # first pass: * /
    out = [items[0]]
    i = 1
    while i < len(items):
        op, rhs = items[i], items[i + 1]
        if op in MUL_OPS or op in DIV_OPS:
            lhs = out.pop()
            if op in DIV_OPS:
                if rhs == 0:
                    raise ZeroDivisionError("division by zero")
                out.append(lhs / rhs)
            else:
                out.append(lhs * rhs)
        else:
            out.extend([op, rhs])
        i += 2
    # second pass: + -
    val = out[0]
    i = 1
    while i < len(out):
        op, rhs = out[i], out[i + 1]
        if op == "+":
            val = val + rhs
        else:
            val = val - rhs
        i += 2
    return val


def _tok_end(tok):
    return tok[-1] + (len(tok[2]) if tok[0] == "num" else len(tok[1]))


def _side(tokens, start, step, text=None):
    """Collect num (op num)* from tokens[start] going right (step=1) or left (step=-1). Returns token list in order.
    Going right (an expression after '='), stop at any letter between tokens, so words never join two lines."""
    if start < 0 or start >= len(tokens) or tokens[start][0] != "num":
        return []
    seq, k = [tokens[start]], start
    while 0 <= k + 2 * step < len(tokens) and tokens[k + step][0] == "op" and tokens[k + 2 * step][0] == "num":
        if step == 1 and text is not None and (re.search(r"[A-Za-z]", text[_tok_end(tokens[k]):tokens[k + 1][-1]])
                                               or re.search(r"[A-Za-z]", text[_tok_end(tokens[k + 1]):tokens[k + 2][-1]])):
            break
        seq += [tokens[k + step], tokens[k + 2 * step]]; k += 2 * step
    return seq if step == 1 else seq[::-1]


def find_arith_claims(text):
    """Return list of {expr, lhs, rhs, raw_lhs, raw_rhs} for each explicit arithmetic '=' claim.
    D1: every '=' segment of a chain is checked against its neighbours, and either side
    may be an expression: '3 * 4 * 7 = 12 * 7 = 84' is two claims (84 vs 84, 84 vs 84) and passes;
    '3 * 4 * 7 = 12 * 6' is 84 vs 72 and flags. The left side must contain an operator."""
    tokens = tokenize_arith(text or "")
    claims = []
    for i, tk in enumerate(tokens):
        if tk[0] != "eq":
            continue
        left, right = _side(tokens, i - 1, -1), _side(tokens, i + 1, 1, text)
        if len(left) < 3 or not right:   # the left side must be an expression; the right may be one
            continue
        try:
            lhs = _eval_ops([x[1] for x in left]); rhs = _eval_ops([x[1] for x in right])
        except (ValueError, ZeroDivisionError):
            continue
        raw = lambda seq: " ".join(x[2] if x[0] == "num" else x[1] for x in seq)
        claims.append(dict(expr=f"{raw(left)} = {raw(right)}", lhs=lhs, rhs=rhs, raw_lhs=raw(left), raw_rhs=raw(right)))
    return claims


EM_DASH = "\u2014"
# Other unicode dashes, checked only when the task text forbids dashes.
OTHER_DASHES = ("\u2013", "\u2212", "\u2012", "\u2010")
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME)\b|\[INSERT\]|\[placeholder\]|<placeholder>", re.I)
TRUNCATION_RE = re.compile(r"\[truncated\]|\(truncated\)", re.I)
DASH_FORBID_RE = re.compile(r"em\s*dash|en\s*dash|no[_\s-]?dash|U\+201[34]|forbid\w* dash", re.I)
REQUIRED_TEXT_FIELDS = ("objective", "decision", "reasoning")


def _collect_text_parts(obj, prefix=""):
    """Yield (path, text) for every string in a JSON-like object."""
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield from _collect_text_parts(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]" if prefix else f"[{i}]"
            yield from _collect_text_parts(v, p)


def _snippet_around(text, needle, width=24):
    i = text.find(needle)
    if i < 0:
        return needle
    a, b = max(0, i - width), min(len(text), i + len(needle) + width)
    return text[a:b]


def _set_required_fail(rep, name, note):
    conds = [c for c in (rep.get("conditions") or []) if not (isinstance(c, dict) and c.get("name") == name)]
    conds.append(dict(name=name, required=True, result="FAIL", note=note))
    rep["conditions"] = conds
    rep["result"] = "FAIL"


def apply_content_recheck(rep, s_out=None, task_text=None):
    """Force required FAIL (and report result FAIL) for banned content. Does not let
    independent_review override a code FAIL. Returns the list of condition names added."""
    if not isinstance(rep, dict):
        return []
    added = []
    parts = list(_collect_text_parts(rep))
    if s_out:
        parts.append(("s_out", str(s_out)))
    blob = "\n".join(t for _, t in parts)

    if EM_DASH in blob:
        snip = None
        for _, t in parts:
            if EM_DASH in t:
                snip = _snippet_around(t, EM_DASH)
                break
        note = f"banned U+2014 em dash in {snip!r}"
        _set_required_fail(rep, "no_em_dashes", note)
        added.append("no_em_dashes")

    task = task_text or ""
    if DASH_FORBID_RE.search(task):
        hits = [d for d in OTHER_DASHES if d in blob]
        if hits:
            names = ", ".join(f"U+{ord(d):04X}" for d in hits)
            _set_required_fail(rep, "no_forbidden_dashes", f"task forbids dashes; found {names}")
            added.append("no_forbidden_dashes")

    ph = PLACEHOLDER_RE.search(blob)
    if ph:
        _set_required_fail(rep, "no_placeholders", f"placeholder {ph.group(0)!r}")
        added.append("no_placeholders")

    tr = TRUNCATION_RE.search(blob)
    if tr:
        _set_required_fail(rep, "no_truncation", f"truncation marker {tr.group(0)!r}")
        added.append("no_truncation")

    empty = []
    for f in REQUIRED_TEXT_FIELDS:
        v = rep.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            empty.append(f)
    opts = rep.get("options")
    if not opts:
        empty.append("options")
    if empty:
        _set_required_fail(rep, "no_empty_required_sections", "empty required sections: " + ", ".join(empty))
        added.append("no_empty_required_sections")

    return added


def arith_mismatch(claim):
    """True if |lhs − r| > max(0.01, 0.001×|r|)."""
    tol = max(0.01, 0.001 * abs(claim["rhs"]))
    return abs(claim["lhs"] - claim["rhs"]) > tol


def collect_arith_text(rep, s_out=None):
    parts = [str(rep.get("reasoning") or ""), str(rep.get("decision") or "")]
    if s_out:
        parts.append(str(s_out))
    return "\n".join(parts)


def apply_arith_recheck(rep, s_out=None):
    """On a mismatch, add required arith_recheck=FAIL naming the expression and force result FAIL.
    Correct claims or no arithmetic: add nothing. Returns the list of mismatch claims."""
    if not isinstance(rep, dict):
        return []
    claims = find_arith_claims(collect_arith_text(rep, s_out))
    mismatches = [c for c in claims if arith_mismatch(c)]
    if not mismatches:
        return []
    exprs = ", ".join(c["expr"] for c in mismatches)
    note = "; ".join(f"{c['expr']} (computed {c['lhs']})" for c in mismatches)
    conds = list(rep.get("conditions") or [])
    # idempotent: replace any prior arith_recheck
    conds = [c for c in conds if c.get("name") != "arith_recheck"]
    conds.append(dict(name="arith_recheck", required=True, result="FAIL", note=note or exprs))
    rep["conditions"] = conds
    rep["result"] = "FAIL"
    return mismatches


def s_out_mainly_numeric(s_out):
    t = (s_out or "").strip()
    if not t:
        return False
    if re.fullmatch(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", t):
        return True
    # a single explicit equation of numbers and ops
    compact = re.sub(r"\s+", " ", t).strip()
    eq = re.fullmatch(
        r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
        r"(?:\s*[×xX*/+\-−]\s*[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)+"
        r"\s*=\s*[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?",
        compact,
    )
    return bool(eq)


def task_asks_for_number(task_text):
    t = task_text or ""
    if not ASK_NUMBER_RE.search(t):
        return False
    # 'what is' / compute / etc. should be tied to a numeric or arithmetic task
    return bool(ARITH_HINT_RE.search(t) or re.search(r"\d", t))


def extract_s_out_number(s_out):
    """The S-suite output's number: the last Answer: N, else the last equation whose RHS is a plain number, else a mainly-numeric value."""
    t = (s_out or "").strip()
    if not t:
        return None
    ms = ANSWER_RE.findall(t)
    if ms:
        return ms[-1]          # the final 'Answer: N' line wins (STO: show work, then 'Answer: <number>')
    claims = [c for c in find_arith_claims(t) if NUM_RE.fullmatch(c["raw_rhs"].strip())]
    if claims:
        return claims[-1]["raw_rhs"]   # D1: a right side may be an expression; only a plain number counts
    if re.fullmatch(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", t):
        return t
    return None


def decision_contains_number(decision, raw_num):
    if raw_num is None or decision is None:
        return False
    try:
        target = parse_number(raw_num)
    except ValueError:
        return False
    for m in NUM_RE.finditer(str(decision)):
        try:
            if abs(parse_number(m.group(0)) - target) <= 1e-9:
                return True
        except ValueError:
            continue
    return False


def apply_s_out_number_in_decision(rep, s_out=None, task_text=None):
    """If the task asks for a number or S-out is mainly numeric, the decision must contain the
    S-suite output's number. Otherwise add required decision_contains_s_out_number=FAIL and force FAIL.
    Returns the missing number (raw) or None if the rule does not apply or passes."""
    if not isinstance(rep, dict):
        return None
    applies = task_asks_for_number(task_text) or s_out_mainly_numeric(s_out)
    raw = extract_s_out_number(s_out)
    if not applies or raw is None:
        return None
    if decision_contains_number(rep.get("decision"), raw):
        return None
    conds = [c for c in (rep.get("conditions") or []) if c.get("name") != "decision_contains_s_out_number"]
    conds.append(dict(
        name="decision_contains_s_out_number", required=True, result="FAIL",
        note=f"decision must contain S-suite output number {raw}",
    ))
    rep["conditions"] = conds
    rep["result"] = "FAIL"
    return raw


VERDICT_WORDS = {"SAFE", "SUSPECT", "BLOCK", "UNSAFE"}
WHOLE_VERDICT = VERDICT_WORDS | {"PASS", "FAIL", "PASS OVERALL", "FAIL OVERALL", "OVERALL PASS", "OVERALL FAIL"}
BAD_DELTA_RE = re.compile(r"[{\[]|:\[")


def is_work_mode(rep, task_text=None, work_mode=None):
    """CSO work mode: the ASO report of a delegated task. The SCREEN path calls check(rep) with no task text,
    so a task text on an ASO report means work mode. An explicit work_mode wins."""
    if work_mode is not None:
        return bool(work_mode)
    bot = str((rep or {}).get("bot") or ""); item = str((rep or {}).get("item") or "")
    return task_text is not None and (bot == "ASO" or item.startswith("DG-ASO "))


def apply_work_mode_checks(rep, task_text=None, work_mode=None):
    """FX-CSO-ASO-workmode-v3 code backstop (no model judgment): in a work-mode task the decision may not be a
    verdict word, and every option delta must be a plain string without '{', '[' or ':['. Returns names added."""
    if not isinstance(rep, dict) or not is_work_mode(rep, task_text, work_mode):
        return []
    added = []
    dec = " ".join(str(rep.get("decision") or "").split()).strip(".").upper()
    # a whole-report verdict (SAFE, UNSAFE, PASS overall ...) or a screen word anywhere FAILs; per-item 'x: PASS' is fine
    if dec in WHOLE_VERDICT or set(re.findall(r"[A-Z]+", dec)) & VERDICT_WORDS or re.search(r"\b(PASS|FAIL) OVERALL\b|\bOVERALL:? (PASS|FAIL)\b", dec):
        _set_required_fail(rep, "work_decision_not_verdict", f"work-mode decision is a verdict word: {rep.get('decision')!r}")
        added.append("work_decision_not_verdict")
    bad = [o.get("deltas") for o in (rep.get("options") or []) if isinstance(o, dict)
           and (not isinstance(o.get("deltas"), str) or BAD_DELTA_RE.search(o.get("deltas")))]
    if bad:
        _set_required_fail(rep, "work_deltas_plain_text", f"option deltas not plain text: {[str(b)[:20] for b in bad[:3]]}")
        added.append("work_deltas_plain_text")
    return added


# ---- (C) task-declared deterministic checks: no model judgment ----
DECL_RE = lambda key: re.compile(r"^\s*" + key + r"\s*:\s*(.+)$", re.I | re.M)
RULE_RE = re.compile(r"^\s*-?\s*([\w.-]+)\s*:\s*IF\s+([\w.]+)\s*(<=|>=|==|!=|<|>)\s*(-?[\d.]+|[A-Za-z][\w.]*)\s+THEN\s+(.+?)\s*$", re.I | re.M)
FACT_RE = re.compile(r"\b([a-z][\w]*)\s*=\s*(-?\d+(?:\.\d+)?)", re.I)
MONEY_LABELS = {"net": "net_usd", "p&l": "net_usd", "pnl": "net_usd", "gross": "gross_usd", "fee": "fees_usd", "fees": "fees_usd"}
OPS = {"<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _decl_list(task_text, key):
    m = DECL_RE(key).search(task_text or "")
    return [x.strip() for x in re.split(r"[|,]", m.group(1)) if x.strip()] if m else None


def task_facts(task_text, rep=None):
    """FACTS from code (rep['facts_from_code']) else the task's 'FACTS:' block (key=value)."""
    f = (rep or {}).get("facts_from_code")
    if isinstance(f, dict) and f:
        return {k: float(v) for k, v in f.items() if isinstance(v, (int, float))}
    m = re.search(r"FACTS\s*:\s*(.+)", task_text or "", re.I | re.S)
    return {k: float(v) for k, v in FACT_RE.findall(m.group(1))} if m else {}


def check_required_conditions(rep, task_text):
    """08: 'REQUIRED_CONDITIONS: id1, id2' in the task; FAIL if any id is not a condition name in the report."""
    ids = _decl_list(task_text, "REQUIRED_CONDITIONS")
    if not ids: return None
    have = {str(c.get("name")) for c in rep.get("conditions") or [] if isinstance(c, dict)}
    miss = [i for i in ids if i not in have]
    if miss: _set_required_fail(rep, "declared_conditions_present", f"missing required condition ids: {miss}")
    return miss


def check_allowed_decisions(rep, task_text):
    """09: 'ALLOWED_DECISIONS: A | B'; the decision must equal one exactly (whitespace trimmed only)."""
    ok = _decl_list(task_text, "ALLOWED_DECISIONS")
    if not ok: return None
    dec = str(rep.get("decision") or "").strip()
    if dec not in ok: _set_required_fail(rep, "decision_in_allowed_words", f"decision {dec!r} not one of {ok}")
    return dec in ok


def check_money_labels(rep, task_text):
    """10 (CFO chains only: bot AFO): every number in decision/reasoning that follows a money label
    (net, P&L, gross, fee) must equal the FACTS value under that label (sign-insensitive, 0.005)."""
    if str(rep.get("bot") or "") != "AFO": return None
    facts = task_facts(task_text, rep)
    if not facts: return None
    bad = []
    text = " ".join(str(rep.get(k) or "") for k in ("decision", "reasoning"))
    for m in re.finditer(r"(?i)\b(net|p&l|pnl|gross|fees?)\b[^\d\-.]{0,30}?(-?\d+(?:\.\d+)?)", text):
        key = MONEY_LABELS[m.group(1).lower()]; v = float(m.group(2))
        if key in facts and abs(abs(v) - abs(facts[key])) > 0.005:
            bad.append(f"{m.group(1)} {m.group(2)} != {key} {facts[key]}")
    if bad: _set_required_fail(rep, "money_number_matches_labeled_fact", "; ".join(bad[:3]))
    return bad


def check_priority_rules(rep, task_text):
    """11: 'RULES:' lines '- id: IF fact op (number|fact) THEN decision', highest priority first, evaluated on FACTS.
    The decision must equal the THEN of the first rule that fires. No rule fires: no check."""
    rules = RULE_RE.findall(task_text or "")
    if not rules: return None
    facts = task_facts(task_text, rep)
    for rid, key, op, num, then in rules:
        rhs = facts.get(num) if re.match(r"[A-Za-z]", num) else float(num)
        if key in facts and rhs is not None and OPS[op](facts[key], rhs):
            dec = str(rep.get("decision") or "").strip()
            if dec != then.strip():
                _set_required_fail(rep, "decision_is_top_fired_rule", f"rule {rid} fired first: expected {then.strip()!r}, got {dec!r}")
            return rid
    return None


def _is_flow_task(st, h):
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]*", h) or ".." in h:
        return False
    root = os.path.realpath(os.path.join(st, "flow"))
    d = os.path.realpath(os.path.join(root, h))
    if os.path.dirname(d) != root:
        return False
    return os.path.isfile(os.path.join(d, "01_task.json"))


def check_handoff_ids(r, state=None):
    """C7: handoff_id is the flow task id that `flow.py delegate --id` stamps; missing or
    not a task under gb_state/flow/ -> reject. open_item_id is optional: missing -> WARN, except a report that
    marks an Open Items row Done (open_item_status Done) -> reject. Returns (errors, warnings)."""
    errs, warns = [], []
    st = state or os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
    h = str(r.get("handoff_id") or "").strip()
    if not h:
        errs.append("C7: handoff_id missing (the flow task id from delegate --id)")
    elif not _is_flow_task(st, h):   # #36 C7: no '.', '..', traversal; the task must have 01_task.json
        errs.append(f"C7: handoff_id {h!r} is not a flow task id")
    oi = str(r.get("open_item_id") or "").strip()
    done = str(r.get("open_item_status") or "").strip().lower() == "done"
    if not oi:
        (errs if done else warns).append("C7: open_item_id missing" + (" on a report that marks an Open Items row Done" if done else ""))
    elif not re.fullmatch(r"\d+", oi):
        errs.append(f"C7: open_item_id {oi!r} is not an Open Items ID")
    return errs, warns


def check(r, s_out=None, task_text=None, work_mode=None):
    if isinstance(r, dict):
        for fn in (check_required_conditions, check_allowed_decisions, check_money_labels, check_priority_rules):
            fn(r, task_text)
        apply_work_mode_checks(r, task_text=task_text, work_mode=work_mode)
        apply_arith_recheck(r, s_out=s_out)
        apply_s_out_number_in_decision(r, s_out=s_out, task_text=task_text)
        apply_content_recheck(r, s_out=s_out, task_text=task_text)
    errs = al.validate(r) if isinstance(r, dict) else ["report is not an object"]
    if not errs and "note" in r and not isinstance(r["note"], str):
        errs.append("note must be a plain string")   # optional field (CSO round-3 ruling)
    if not errs and not r["conditions"]: errs.append("at least one PASS/FAIL condition required (measurable)")
    if not errs and r["result"] not in ("PASS", "FAIL", "PENDING"): errs.append("report result must be PASS, FAIL or PENDING")
    if not errs and r["result"] == "FAIL" and not any(c["result"] == "FAIL" for c in r["conditions"]):
        errs.append("FAIL must name the condition that failed")
    return errs


def scan_report_dir(root=GB_ROOT + "/gb_state/reports/dg"):
    """Re-check every existing DG report. Returns {scanned, with_claims, false_flags, hits}."""
    import glob
    files = sorted(glob.glob(os.path.join(root, "*", "*.json")))
    hits, false_flags, with_claims = [], [], 0
    for path in files:
        try:
            rep = json.load(open(path))
        except (ValueError, OSError):
            continue
        # optional S-out from the matching flow folder (filename is the task id)
        s_out = None
        tid = os.path.splitext(os.path.basename(path))[0]
        sdir = os.path.join(GB_ROOT + "/gb_state/flow", tid, "02_s_out")
        if os.path.isdir(sdir):
            parts = []
            for fn in sorted(os.listdir(sdir)):
                if fn.endswith(".txt"):
                    parts.append(open(os.path.join(sdir, fn)).read())
            if parts:
                s_out = parts[-1]
        claims = find_arith_claims(collect_arith_text(rep, s_out))
        if claims:
            with_claims += 1
        for c in claims:
            if arith_mismatch(c):
                rec = dict(file=path, expr=c["expr"], lhs=c["lhs"], rhs=c["rhs"])
                # false flag: an independent re-eval of the expression string agrees lhs==rhs
                if _independent_expr_correct(c["expr"]):
                    rec["false_flag"] = True
                    false_flags.append(rec)
                hits.append(rec)
    return dict(scanned=len(files), with_claims=with_claims, false_flags=len(false_flags),
                false_flag_list=false_flags, hits=hits)


def _independent_expr_correct(expr):
    """Second, narrower evaluator used only to classify false flags. Parses 'a op b ... = r' with
    no word-skipping (expression already extracted). Returns True if the equation is actually correct."""
    if "=" not in expr:
        return False
    left, right = expr.rsplit("=", 1)
    try:
        rhs = parse_number(right.strip())
    except ValueError:
        try:
            rt = tokenize_arith(right.strip())
            if any(x[0] not in ("num", "op") for x in rt):
                return False
            rhs = _eval_ops([x[1] for x in rt])
        except (ValueError, ZeroDivisionError, IndexError):
            return False
    # tokenize left as number/op only (whitespace-separated, plus glued ops)
    pieces, buf = [], left.strip()
    tok = tokenize_arith(buf)
    items = []
    for t in tok:
        if t[0] == "num":
            items.append(t[1])
        elif t[0] == "op":
            items.append(t[1])
        else:
            return False
    try:
        lhs = _eval_ops(items)
    except (ValueError, ZeroDivisionError):
        return False
    return abs(lhs - rhs) <= max(0.01, 0.001 * abs(rhs))


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("-f", "--file", required=True)
    e = check(json.load(open(p.parse_args().file))); print(json.dumps({"valid": not e, "errors": e})); sys.exit(0 if not e else 1)
