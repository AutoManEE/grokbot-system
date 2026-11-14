#!/usr/bin/env python3
"""text_guard.py: ONE hardened text guard, shared by relay text
(relay.guard_text) and takeover lessons (flow.takeover). Zero AI. Accident guard plus detection, not security.

check(text, source="internal") -> (ok, why). Refuses, in order:
  - empty text; a source other than internal / cso_screened (raw outside text goes through cso_screen first)
  - cso_screen.rules not SAFE (prompt injection, money commands, hidden characters), on raw and normalized text
  - a link: http(s)://, hxxp(s), [.] / (.) / [dot] obfuscation, www., or a bare domain (name.com, .net, .org ...)
  - repo_guard private patterns (email, key, token, card/SSN, personal keywords) on raw and normalized text,
    plus an obfuscated email ("name at host dot com")
  - a phone number, e.g. (312) 555-0199, 312-555-0199, +1 312 555 0199
  - a base64-looking run (16+ chars of [A-Za-z0-9+/] mixing upper, lower and digits, or ending in =)
  - structured data (a JSON object/array, e.g. an order) - notes are plain lines
  - a money move ("move/send/transfer/wire/pay/deposit/withdraw ... $<n>")
  - private words, matched after NFKC normalization with up to 3 spaces, hyphens or other non-letters between
    letters (so 'ｐａｒｔｎｅｒ', 'pass word', 'pass-word', 'p.a.r.t.n.e.r' all match): family/work/money-secret words and
    the protected names from the private, git-ignored list (see PROTECTED_LISTS; never stored in the repo)
  - quoted outside-page text: quoted text (4+ words) next to a source cue (page, site, article, news, post,
    tweet, wrote, announced, according to ...), or a "From the <name> page/site/article:" lead-in
  - re-screen 2: leet (4->a, @->a, 3->e, 1->i/l, 0->o, 5/$->s, 7->t) and a partial Cyrillic/Greek fold before word
    matching; a PIN near digits; SSN words and spaced SSNs; money in words / "move ... to ... account"; spaced or
    spelled-out digit runs of 4+; 2-2 split and international (+44 ...) phones; short base64 next to a secret word;
    32+ hex chars; bare domains (evil.example; file names like flow.py are allowed); street addresses
  - an acknowledgement: only ack words, emoji and punctuation ('Got it.', 'ok 👍', 'Thanks, noted, will do!')
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import json, os, re, sys, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PRIVATE_WORDS = ("my family", "password", "passcode", "passwd", "pin code", "account number", "routing number",
                 "seed phrase", "api key", "login code", "one time code", "salary", "my employer", "my boss", "job site", "jobsite",
                 "coworker", "co worker", "my partner", "girlfriend", "boyfriend", "fiance", "fiancee", "birthday", "date of birth",
                 "social security", "ssn", "my son", "my daughter", "my kids", "home address", "my address", "i live at", "lives at",
                 # CSO re-screen 3: payment handles and date of birth
                 "dob", "cashapp", "cash app", "venmo", "zelle", "paypal")
# Protected names are private: read at run time from git-ignored files only (gh_safe's work list if present, then
# <repo>/private/protected_names.txt). No names loaded = every check refuses (fail closed). Only tests may point
# GB_PROTECTED_LIST at a tmp file with a made-up name (protected_test_mode). Contents are never printed.
PROTECTED_LISTS = (GB_ROOT + "/alignment/protected_names.txt", GB_ROOT + "/private/protected_names.txt")
ACK_WORDS = {"ack", "acked", "acknowledged", "ok", "okay", "k", "kk", "got", "it", "received", "noted", "note", "thanks", "thank",
             "you", "thx", "ty", "roger", "will", "do", "done", "sure", "great", "cool", "perfect", "sounds", "good", "yes", "yep",
             "copy", "that", "understood", "on", "and", "all", "set", "fine", "nice", "much", "very", "a", "lot", "np"}
LINK_RE = re.compile(r"(?i)(https?://|\bhxxps?\b|\[\.\]|\(\.\)|\[dot\]|\(dot\)|\bwww\.|\b[a-z0-9-]+\.(com|net|org|io|gov|edu|co|ai|ly|me|info|biz|us|uk|ru|cn|xyz|app|dev)\b)")
PHONE_RE = re.compile(r"(?<![\w$])(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w])")
PHONE_22_RE = re.compile(r"(?<![\w$.])\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{2}[\s.-]\d{2}(?![\w])")          # 312 555 01 99
PHONE_INTL_RE = re.compile(r"(?<![\w])\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?![\w])")                  # +44 20 7946 0958
SSN_RE = re.compile(r"(?<![\w.])\d{3}[\s.-]\d{2}[\s.-]\d{4}(?![\w])")                                      # 123 45 6789
PIN_RE = re.compile(r"\bpin\b\s*(?:(?:is|:|=|#|number|no\.?|code)\s*[:=]?\s*\d|\d{4,})")                         # not bare 'pin'
DIGIT_RUN_RE = re.compile(r"(?<![\w.])\d(?:\s*[\s.,-]\s*\d){3,}(?![\w])")                                    # 4 4 7 1, 4-4-7-1
NUM_WORDS = r"(?:zero|one|two|three|four|five|six|seven|eight|nine)"
NUM_WORD_RUN_RE = re.compile(rf"\b{NUM_WORDS}(?:[\s,-]+{NUM_WORDS}){{3,}}\b")                                 # four four seven one
# CSO re-screen 3: mixed digit/word runs ('four 4 seven 1') and tens/teens ('forty-four seventy-one'), 4+ tokens
NUM_ANY = r"(?:\d|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|" \
          r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|oh)"
MIXED_RUN_RE = re.compile(rf"(?<![\w.]){NUM_ANY}(?:[\s,.-]+{NUM_ANY}){{3,}}(?![\w])")
# CSO re-screen 3: a secret word followed by is/:/= and a value holding a digit ('code is 4471', 'pwd=Hunter2!', 'p@$$ is hunter2')
SECRET_KW_RE = re.compile(r"\b(pass|pwd|passwd|password|passcode|pin|code|otp|token|secret|key|login|combo|combination)\b")
SECRET_VAL_RE = re.compile(r"\s*(?:is|was|=|:|-)\s*(?=\S*\d)\S{4,}")
# CSO relay_next_screen #tg-passfail separate finding: a code/OTP/token keyword IMMEDIATELY followed (optional : = # -) by a
# value: 4+ digits (single spaces, dots or dashes allowed between them: '48 21 93') or a 4-16 char letter+digit token
# ('abc123', 'Qm9i'). A keyword followed by a word without a digit ('code review', 'code PASS/FAIL') still passes.
OTP_KW = r"(?:code|otp|token|pin|passcode|2fa|mfa|verification|auth)"
OTP_VAL_RE = re.compile(rf"(?<![\w]){OTP_KW}\s*[:=#-]?\s*(?:\d(?:[ .-]?\d){{3,15}}|(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{{4,16}})(?![\w])")
# CSO cao_batch_0645 #otp follow-up residuals + Option Y D3: value BEFORE the keyword ('482193 is your
# code'), obfuscated/plural keywords ('c0de', 'c o d e', 'codes', 'codeword'; leet via word_variants, 1-2 non-letters
# between letters), pw/pass/passwd/pwd + value ('pw 482193', 'pass: hunter2'), secret/token/key + value ('secret ab12cd')
# and bare digit runs of 17+. Keywords are matched on word_variants (1:1 maps); the value is always read from n.
def _kw_rx(words):
    alts = sorted((r"[\W_]{0,2}".join(map(re.escape, w)) for w in words), key=len, reverse=True)
    return re.compile(r"(?<![^\W_])(?:" + "|".join(alts) + r")(?![^\W_])")
OTP2_VAL = r"(?:\d(?:[ .-]?\d){3,15}|(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{4,16})"
OTP2_CODE_KW = _kw_rx(("code", "codes", "codeword", "codewords", "otp", "otps", "pin", "pins", "passcode", "passcodes", "2fa", "mfa"))
OTP2_PASS_KW = _kw_rx(("pw", "pwd", "passwd", "password", "passwords", "pass", "passphrase", "pword"))
OTP2_SECRET_KW = _kw_rx(("secret", "secrets", "token", "key", "apikey"))
# CSO #tg-otp-followup: any 0-6 punctuation/space between keyword and value ('pw — 1234', '(pw) 1234', 'pw…1234', 'p.w. 1234').
# Digits-only values may follow any punctuation; a mixed letter+digit value keeps the narrow separator, so
# 'pass. word9' (sentence end) and 'pin (sha)' stay clean.
OTP2_SEP = r"[\W_]{0,6}(?:(?:is|was)[\W_]{1,6})?"
OTP2_AFTER_RE = re.compile(rf"(?:{OTP2_SEP}\d(?:[ .-]?\d){{3,15}}|\s{{0,8}}(?:[:=#-]|is|was)?\s{{0,8}}{OTP2_VAL})(?![\w])")
OTP2_AFTER_ANY_RE = re.compile(r"[ \t]{0,8}(?:[:=]|is)[ \t]{0,8}(?=\S{0,63}\d)\S{4}")   # bounded: linear time              # 'pass: hunter2!', 'secret is x9!k'
# CSO ruling 1: any pw/pass keyword then ':' or '=' then ANY value (digits or not) refuses: 'pass: hunter', 'pass=hunter'.
# Bare 'pass hunter' (no separator, no digit) is an accepted gap.
OTP2_PASS_SEP_RE = re.compile(r"\s{0,8}[:=]\s{0,8}\S")
# CSO ruling 2: carve-outs for the bare word 'pass' (space, no separator) are ONLY a suite count 'pass 1799/2'
# (1-4 digits / 1-4 digits) and a real repo task id. 'pass 1745 tests', 'pass 482193 of 9' now refuse.
# CSO #tg-otp-followup: a count/id carve-out holds at most OTP2_CARVE_MAX_DIGITS digits in total, so a 6-digit code split
# as a count ('pass 4821/93', 'pass 4821/9301') or hidden in an id ('pass t482s193') is not carved out.
OTP2_PASS_COUNT_RE = re.compile(r"\s+\d{1,4}\s*/\s*\d{1,4}(?![\w/.])")
# Task id shapes that are carved out: t + 2-3 digits + u (t123u), t + digits + s + 1-3 digits (t19s1),
# t + digits + r + 1 digit (t164r2), and plain t + digits (t11). Nothing else is carved out (hex/msg ids, shas refuse).
OTP2_PASS_TASKID_RE = re.compile(r"\s+t\d{2,3}(?:u|s\d{1,3}|r\d)?(?![\w])")
OTP2_CARVE_MAX_DIGITS = 5   # a carved-out count or id never carries a 6+ digit code
OTP2_FILLER = (r"(?:your|the|my|a|an|new|one[\s-]?time|temp|temporary|login|log[\s-]?in|sign[\s-]?in|verification|verify|security|"
               r"access|auth|authentication|sms|text|2fa|mfa|confirmation|reset|backup|recovery|unlock|app|wifi|wi-fi|admin|account)")
OTP2_DIGITS = r"\d(?:[ .-]?\d){3,15}"
# No-connector form takes digits only, so a commit id before the word ("fac2133 code review") stays clean.
OTP2_BEFORE_RE = re.compile(rf"(?<![\w]){OTP2_DIGITS}\s*(?:[:=#-]\s*)?(?:(?:is|was|as)\s+)?(?:{OTP2_FILLER}\s+){{0,3}}$")
OTP2_BEFORE_IS_RE = re.compile(rf"(?<![\w]){OTP2_VAL}\s*(?:[:=-]\s*|(?:is|was|as)\s+)(?:{OTP2_FILLER}\s+){{0,3}}$")
LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{17,}(?!\d)")
# keyword glued to its value: 'pw482193', 'otp482193' (4-16 digits)
OTP2_GLUED_RE = re.compile(r"(?<![^\W_])(?:pw|pwd|passwd|password|pass|passcode|code|otp|pin)\d{4,16}(?![\w])")
OTP2_BEFORE_WINDOW = 120   # value + connector + 3 filler words always fit; bounds the backward search (no quadratic scan)

CASHTAG_RE = re.compile(r"(?<![\w$])\$[a-z][a-z0-9_-]{2,}\b")                                                     # $OwnerO22
HEX_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{32,}(?![0-9a-f])")
SECRET_NEAR_RE = re.compile(r"\b(token|key|secret|password|passcode|passwd|pwd|pin|code|cred(ential)?s?|login|auth|otp)\b")
# Exact, case-sensitive tokens from the A report format (bots/A*/prompt.md) that are never a secret; nothing else exempt.
SHORT_B64_EXEMPT = frozenset({"PASS/FAIL"})
SHORT_B64_RE = re.compile(r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{4,14}={1,2}|[A-Za-z0-9+/]{8,15})(?![A-Za-z0-9+/=])")
FILE_EXT = {"py", "md", "json", "jsonl", "txt", "csv", "sh", "yaml", "yml", "toml", "pdf", "png", "jpg", "jpeg", "gif", "xlsx", "xls",
            "docx", "doc", "pptx", "ppt", "log", "lock", "html", "js", "ts", "css", "rvt", "rfa", "dwg", "dxf", "ifc", "nwd", "nwc",
            "bak", "tmp", "cfg", "ini", "xml", "sql", "zip", "gz", "tar", "pyc", "ipynb", "env", "out", "err", "rs", "go", "java", "c", "h"}
DOMAIN_RE = re.compile(r"(?<![\w@.])((?:[a-z0-9-]+\.)+)([a-z]{2,24})(?![\w.]*[\w])")
STREET_RE = re.compile(r"\b\d{1,6}\s+(?:[nsew]\.?\s+)?(?:[a-z0-9']+\s+){1,3}(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|"
                       r"ct|court|way|pl|place|pkwy|parkway|hwy|highway|ter|terrace|cir|circle|trl|trail)\b")
MONEY_WORDS_RE = re.compile(r"\b(move|send|transfer|wire|pay|deposit|withdraw|buy|sell)\b[^.\n]{0,40}\b(dollars?|usd|bucks|grand|euros?)\b")
MONEY_ACCOUNT_RE = re.compile(r"\b(move|send|transfer|wire|deposit|withdraw)\b[^.\n]{0,60}\b(to|into|from)\b[^.\n]{0,40}\baccount\b"
                              r"(?!\s+(?:review|list|lists|manager|team|tab|folder|page|settings|owner|plan|sheet|notes?|section|file|rows?|summary))")
LEET = (str.maketrans("4@3105$7", "aaeiosst"), str.maketrans("4@3105$7", "aaelosst"))
# Small Cyrillic/Greek lookalike fold (partial; a full confusables fold stays a recorded CSO condition)
CONFUSABLES = str.maketrans("аеорсухіјѕԁԛԝαεικνορτυχ", "aeopcyxijsdqwaeiknoptux")
B64_RE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
JSON_RE = re.compile(r"[{\[]\s*\"[^\"]{1,40}\"\s*:")
MONEY_RE = re.compile(r"(?i)\b(move|send|transfer|wire|pay|deposit|withdraw|buy|sell)\b[^.\n]{0,40}[$€£]\s?\d")
OBF_EMAIL_RE = re.compile(r"(?i)\b[\w.]+\s*(\(at\)|\[at\]|\sat\s)\s*[\w-]+\s*(\(dot\)|\[dot\]|\sdot\s)\s*(com|net|org|io)\b")
QUOTE_RE = re.compile(r"[\"“”«»„]([^\"“”«»„]{3,})[\"“”«»„]")
CUE_RE = re.compile(r"(?i)\b(page|site|website|web ?site|article|news|blog|post(ed)?|tweet(ed)?|thread|press release|announced|"
                    r"according to|wrote|headline|newsletter|forum|reddit|wikipedia)\b")
LEADIN_RE = re.compile(r"(?i)\b(from|per|according to|via)\s+(the\s+)?[\w .&'-]{1,40}\s+(page|site|website|article|post|blog|feed|tweet|thread|news|report)\s*[:,-]")


def normalize(text):
    """NFKC (fullwidth -> ASCII), drop format/zero-width characters (Unicode Cf), casefold."""
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Cf")
    # CSO re-screen 3: strip combining marks (pa\u0301ssword -> password), then re-compose
    t = unicodedata.normalize("NFKC", "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"))
    return t.casefold()


def word_variants(n):
    """Normalized text plus its leet maps (4->a, @->a, 3->e, 1->i or l, 0->o, 5/$->s, 7->t) and a partial lookalike fold."""
    out = [n] + [n.translate(t) for t in LEET]
    f = n.translate(CONFUSABLES)
    if f != n: out += [f] + [f.translate(t) for t in LEET]
    return out


def otp_followup(n):
    """(True, why) when n holds a one-time code / secret value in a shape the older rules miss; else (False, "")."""
    if LONG_DIGITS_RE.search(n): return True, "looks private: 17+ digit run"
    if OTP2_GLUED_RE.search(n): return True, "looks private: code/OTP value (keyword glued to value)"
    def before(rx, end):   # backward search over a bounded window ($ anchors at end): linear overall
        return rx.search(n, max(0, end - OTP2_BEFORE_WINDOW), end)
    def value_at(e):
        return OTP2_AFTER_RE.match(n, e) or OTP2_AFTER_ANY_RE.match(n, e) or OTP2_PASS_SEP_RE.match(n, e)
    seen, done = set(), set()   # values are read from n, so a keyword span is judged once across variants
    for v in word_variants(n):
        if len(v) != len(n) or v in seen: continue
        seen.add(v)
        for m in OTP2_CODE_KW.finditer(v):
            if ("c", m.span()) in done: continue
            done.add(("c", m.span()))
            if OTP2_AFTER_RE.match(n, m.end()) or before(OTP2_BEFORE_RE, m.start()) or before(OTP2_BEFORE_IS_RE, m.start()):
                return True, "looks private: code/OTP value (keyword before or after)"
        for m in OTP2_PASS_KW.finditer(v):
            if ("p", m.span()) in done: continue
            done.add(("p", m.span()))
            bare_pass = re.fullmatch(r"pass", re.sub(r"[\W_]", "", v[m.start():m.end()])) is not None
            if bare_pass:
                c = OTP2_PASS_COUNT_RE.match(n, m.end()) or OTP2_PASS_TASKID_RE.match(n, m.end())
                if c and sum(ch.isdigit() for ch in c.group()) > OTP2_CARVE_MAX_DIGITS:
                    return True, "looks private: password value"   # a 6+ digit code split as a count or id
                if c:   # one carve-out token only: a value or another id right after it is not hidden by it
                    if value_at(c.end()) or OTP2_PASS_TASKID_RE.match(n, c.end()) or OTP2_PASS_COUNT_RE.match(n, c.end()):
                        return True, "looks private: password value"
                    continue
            if value_at(m.end()) or before(OTP2_BEFORE_IS_RE, m.start()):
                return True, "looks private: password value"
        for m in OTP2_SECRET_KW.finditer(v):
            if ("s", m.span()) in done: continue
            done.add(("s", m.span()))
            if OTP2_AFTER_RE.match(n, m.end()) or OTP2_AFTER_ANY_RE.match(n, m.end()) or before(OTP2_BEFORE_IS_RE, m.start()):
                return True, "looks private: secret/token/key value"
    return False, ""


def _word_rx(word):
    letters = [c for c in word.casefold() if c.isalnum()]
    return re.compile(r"(?<![^\W_])" + r"[\W_]{0,3}".join(map(re.escape, letters)) + r"(?![^\W_])")


def protected_test_mode():
    """GB_PROTECTED_LIST is honoured ONLY in an explicit pytest run: GB_PROTECTED_LIST_TEST=1, PYTEST_CURRENT_TEST set and
    pytest actually imported. Outside tests the env var is ignored, so GB_PROTECTED_LIST=/dev/null cannot bypass the list."""
    return (os.environ.get("GB_PROTECTED_LIST_TEST") == "1" and bool(os.environ.get("PYTEST_CURRENT_TEST"))
            and "_pytest" in sys.modules)


def protected_names():
    """Names from the private list(s). Never printed or logged. Unreadable, missing, empty or comments-only files
    give no names; check() then fails closed."""
    paths = [os.environ["GB_PROTECTED_LIST"]] if protected_test_mode() and os.environ.get("GB_PROTECTED_LIST") else list(PROTECTED_LISTS)
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                out += [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
        except (OSError, UnicodeDecodeError, ValueError):
            pass
    return out


def is_ack(text):
    n = normalize(text)
    words = re.findall(r"[a-z]+", n)
    return len(words) <= 8 and all(w in ACK_WORDS for w in words)


def check(text, source="internal"):
    import cso_screen, repo_guard
    raw = text or ""
    n = normalize(raw)
    if not n.strip(): return False, "empty text"
    if (source or "internal") not in ("internal", "cso_screened"):
        return False, "raw/unscreened outside text: only the CSO team reads it; route it through cso_screen first"
    for t in (raw, n):
        v = cso_screen.rules(t)
        if v[0] != "SAFE": return False, f"raw/unscreened outside text: fails cso_screen rules ({', '.join(v[1][:2])})"
    for t in (raw, n):
        hits = repo_guard.scan("lessons.md", t)
        if hits: return False, f"looks private: {hits[0].split(':')[0]}"
    if OBF_EMAIL_RE.search(n): return False, "looks private: obfuscated email"
    if LINK_RE.search(n): return False, "has a URL or obfuscated link (outside content)"
    for m in DOMAIN_RE.finditer(n):
        if m.group(2) not in FILE_EXT and not re.fullmatch(r"[\d.]+", m.group(1)):
            return False, "has a URL or obfuscated link (bare domain)"
    if PHONE_RE.search(n) or PHONE_22_RE.search(n) or PHONE_INTL_RE.search(n): return False, "looks private: phone number"
    if SSN_RE.search(n): return False, "looks private: SSN-like number"
    if PIN_RE.search(n): return False, "looks private: PIN"
    if DIGIT_RUN_RE.search(n) or NUM_WORD_RUN_RE.search(n) or MIXED_RUN_RE.search(n): return False, "looks private: spaced or spelled-out digit run"
    if CASHTAG_RE.search(n): return False, "looks private: payment handle"
    if OTP_VAL_RE.search(n): return False, "looks private: code/OTP/token value"
    hit, why = otp_followup(n)
    if hit: return False, why
    for v in word_variants(n):   # translate() maps 1:1, so positions in v line up with n; the value is read from n
        for m in SECRET_KW_RE.finditer(v):
            if len(v) == len(n) and SECRET_VAL_RE.match(n, m.end()): return False, "looks private: secret value"
    if STREET_RE.search(n): return False, "looks private: street address"
    nr = unicodedata.normalize("NFKC", raw)
    for m in B64_RE.finditer(nr):
        s = m.group(0)
        if s.endswith("=") or (re.search(r"[A-Z]", s) and re.search(r"[a-z]", s) and re.search(r"\d", s)):
            return False, "looks encoded: base64-like run"
    if HEX_RE.search(n): return False, "looks encoded: 32+ hex characters (key or hash)"
    if SECRET_NEAR_RE.search(n):
        for m in SHORT_B64_RE.finditer(nr):
            s = m.group(0)
            if s in SHORT_B64_EXEMPT: continue   # the report-format token itself
            up, lo, dg = len(re.findall(r"[A-Z]", s)), len(re.findall(r"[a-z]", s)), len(re.findall(r"\d", s))
            if s.endswith("=") or re.search(r"[+/]", s) or (up and lo and dg) or (up >= 3 and lo >= 3) or (dg >= 2 and lo + up >= 3 and not s.isdigit()):
                return False, "looks encoded: short base64 next to a secret word"
    if JSON_RE.search(n): return False, "structured data (JSON) refused: notes are plain lines"
    if MONEY_RE.search(n) or MONEY_WORDS_RE.search(n) or MONEY_ACCOUNT_RE.search(n): return False, "money instruction refused"
    variants = word_variants(n)
    for w in PRIVATE_WORDS:
        rx = _word_rx(w)
        if any(rx.search(v) for v in variants): return False, f"looks private (family, work or money-secret word '{w}')"
    names = protected_names()
    if not names: return False, "protected-name list missing, empty or unreadable: text guard fails closed (CAO: restore <repo>/private/protected_names.txt)"
    for w in names:
        rx = _word_rx(w)
        if any(rx.search(v) for v in variants): return False, "looks private: protected name"
    if LEADIN_RE.search(n): return False, "quoted outside-page text refused"
    if any(len(q.split()) >= 4 for q in QUOTE_RE.findall(n)) and CUE_RE.search(n):
        return False, "quoted outside-page text refused"
    if is_ack(raw): return False, "no acknowledgements: a bare ack is never sent (replies are batched)"
    return True, ""


if __name__ == "__main__":
    ok, why = check(sys.stdin.read(), sys.argv[1] if len(sys.argv) > 1 else "internal")
    print("PASS" if ok else f"REFUSED: {why}"); sys.exit(0 if ok else 2)
