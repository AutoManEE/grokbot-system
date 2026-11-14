"""CSO #tg-otp-followup (v2): probes beyond CAO's suite. Carve-outs ('pass N/M', 'pass <task id>') hold at most 5 digits
and cover one token only (no chaining, no value right after); punctuation/lookalike separators; glued keyword+digits;
ReDoS bound for text_guard.otp_followup on adversarial 10 KB inputs."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, time
import pytest
sys.path.insert(0, GB_ROOT + "/bots"); sys.path.insert(0, GB_ROOT + "/scripts")
import text_guard as g

MUST = [
    # rulings
    "pass: hunter", "pass=hunter", "PASS: hunter", "pass : hunter", "pass:hunter", "pass\uff1a hunter", "p\u0430ss: hunter",
    "pw: x", "pass: 61", "pass 482193 tests/of", "pass 482193 tests",
    # carve-out leaks (count / task id)
    "pass 4821/9301", "pass 4821 / 9301", "pass 4821/93", "pass 482/193", "pass 99/9999", "pass t482s193", "pass t112s539",
    "pass t164u 482193", "pass t11: hunter2", "pass t164u t19s1", "pass 1799/2 4821", "pass 1799/2: hunter",
    "code 4821/93",
    # Option Y D3
    "ANSWER: 482193 is your code", "ANSWER: pw 482193",
    # shapes
    "482193 is your code", "c0de 482193", "c o d e 482193", "codes 482193", "codeword 482193", "pwd 482193",
    "secret ab12cd", "token 5e6f7a8b", "key: k9x2m4", "12345678901234567",
    # punctuation / unicode
    "p.w. 482193", "(pw) 482193", "pw \u2014 482193", "pw\u2026482193", "\u2018pw\u2019 482193", "pw\u2192482193", "PW482193",
    "otp482193", "\uff50\uff57 \uff14\uff18\uff12\uff11\uff19\uff13", "\u0441\u0030de 482193", "p\u200bw 482193", "co\u200dde 482193",
    "c\u00a0o\u00a0d\u00a0e 482193", "pw\n482193", "pw\t482193", "482193\nis your code", "482193\u2028is your code",
    "pw:\t\thunter2", "secret\u2009ab12cd",
]
ACCEPTED_GAPS = ["pass hunter", "abc123 code"]
CLEAN = ["pass 1799/2", "pass 1799/2 and 1745/2", "pass 12/14", "pass t164u to CBO", "pass t19s1", "pass t164r2", "pass t90u",
         "pass t11", "tests pass", "1745 passed, 2 failed", "code review done", "the key point is clear", "token cap 384",
         "abc1234 code review", "pass the ball", "tests pass. Commit 1234abcd next"]


@pytest.mark.parametrize("t", MUST)
def test_must_catch(t):
    ok, why = g.check(t, "internal")
    assert not ok and why, t


@pytest.mark.parametrize("t", ACCEPTED_GAPS + CLEAN)
def test_accepted_gaps_and_clean_pass(t):
    assert g.check(t, "internal")[0], t


N = 10240
ADVERSARIAL = {
    "code": ("code " * N)[:N], "c o d e": ("c o d e " * N)[:N], "c-o-d-e": ("c-o-d-e " * N)[:N],
    "pass 1": ("pass 1 " * N)[:N], "pass 1/": ("pass 1/ " * N)[:N], "pass t1": ("pass t12 " * N)[:N],
    "pass 1/2": ("pass 1/2 " * N)[:N], "secret is": ("secret is " * N)[:N], "secret:": ("secret:" * N)[:N],
    "key=": ("key=x " * N)[:N], "digits before code": ("1 " * N)[:N - 5] + " code",
    "your ... code": ("your " * N)[:N - 5] + "code", "one token": "a" * (N - 10) + " secret:",
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_otp_followup_linear_time(name):
    n = g.normalize(ADVERSARIAL[name])
    best = min(_t(n) for _ in range(5))
    assert best < 0.050, f"{name}: {best * 1000:.1f} ms"


def _t(n):
    t0 = time.perf_counter(); g.otp_followup(n); return time.perf_counter() - t0
