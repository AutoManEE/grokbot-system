"""text_guard OTP/secret follow-up.
Each leak must be refused by check() AND be caught by the new otp_followup() rule on its own (so the rule, not an older
one, owns the case). Ordinary notes stay clean. Made-up values only."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests")
import text_guard as g
from test_text_guard import protected_list, FALSE_POSITIVES, OK   # noqa: F401  (autouse fixture re-exported)
from test_text_guard_otp_values import LEAKS_OTP, MORE, STILL_PASS
from test_text_guard_pass_fail_fp import FP

# rule id -> leaks (rule ids match the case table in the CAO report)
LEAKS = {
    "R1_value_before_keyword": ["ANSWER: 482193 is your code", "482193 is your verification code", "482193 is the code",
                                "48 21 93 is your login code", "abc123 is your code", "482193 code", "482193: your one-time code",
                                "ANSWER: 482193 is your pin"],
    "R2_obfuscated_keyword": ["c0de 482193", "c o d e 482193", "codes 482193", "codeword 482193", "c-o-d-e 482193",
                              "C0DE: 482193", "c.o.d.e 4821", "codewords abc123", "c0d3 is 482193"],
    "R3_password_value": ["ANSWER: pw 482193", "pw 482193", "pass 482193", "pass: hunter2", "pwd hunter2", "passwd 482193",
                          "PW: hunter", "pw = abc123", "pass hunter2", "482193 is your password", "p w 482193", "pa55 482193"],
    "R4_secret_token_key_value": ["secret ab12cd", "secret: x9k2m", "secrets ab12cd", "key ab12cd", "key: 9f3k2m",
                                  "token ab12cd", "ab12cd is the secret", "the secret is hunter2!"],
    "R5_digit_run_17plus": ["12345678901234567", "id 12345678901234567", "ref 123456789012345678901 ok", "ANSWER: 99999999999999999"],
}
CLEAN = ["tests pass", "All tests pass.", "pass it to CBO", "Please pass it to CBO today.", "R2 is done", "R2 return leg is next",
         "cap S 160", "S 160 and A 384 are the role caps", "Meeting at 10:30 on 2034-01-13", "It costs $19.99", "all 1799 tests pass",
         "1783 passed, 2 failed", "pass 1799/2 and 1745/2", "pass t164u to CBO",
         "Check the code in commit abc1234 today.", "the abc1234 code review", "token limit 4096", "error code 404",
         "the secret is out", "key point: keep it short", "the key file is flow.py", "pass the review to CSO", "a free pass",
         "code review is done", "codes of conduct", "use 384 tokens at most", "batch 0645 is screened", "pw reset docs",
         "open item 6 goes to the next batch", "version 2034.01 shipped", "run 3 of 3"]
# CSO ruling 1: pass/pw + ':' or '=' + ANY value (digits or not)
LEAKS["R3_password_value"] += ["pass: hunter", "pass=hunter", "PASS: hunter", "pass : hunter", "pass:\nhunter", "pw=hunter",
                               "pass: 1745", "pass: 1799/2"]
# CSO ruling 2: counts with a value-sized number are not carved out; only 'pass N/M' (1-4 digits each) and real task ids
LEAKS["R3_password_value"] += ["pass 482193 tests", "pass 482193 of 9", "pass 1745 tests", "both suites pass 1745 tests",
                               "pass 482193/1", "pass 12345/6", "pass t1645 now", "pass t164x now", "pass t16455u now",
                               "pass 1a2b3c4d to CSO", "pass c0ffee1 to CSO"]
# CSO #tg-otp-followup: a carve-out holds at most 5 digits, so 'pass 4821/93' and 'pass t112s539' (6 digits) refuse
# (see test_text_guard_otp_cso.py).
CARVED_OUT = ["pass 1799/2 and 1745/2", "pass 1978/2", "pass t164u to CBO", "pass t19s1 to CBO",
              "pass t164r2 now", "pass t90u today"]
SHORT_NOT_A_VALUE = ["pass 12 of 14", "PASS 62 records", "PASS 187, FAIL 2", "pass 2"]
ACCEPTED_GAPS = ["pass hunter", "abc123 code"]   # CSO rulings 1 and 3: accepted, pinned so a change is noticed
ALL_LEAKS = [(rid, t) for rid, ts in LEAKS.items() for t in ts]


@pytest.mark.parametrize("rid,text", ALL_LEAKS)
def test_followup_leak_refused_by_check(rid, text):
    ok, why = g.check(text); assert not ok and why, (rid, text)


@pytest.mark.parametrize("rid,text", ALL_LEAKS)
def test_followup_leak_owned_by_new_rule(rid, text):
    hit, why = g.otp_followup(g.normalize(text)); assert hit and why.startswith("looks private"), (rid, text)


@pytest.mark.parametrize("rid,want", [("R1_value_before_keyword", "code/OTP"), ("R2_obfuscated_keyword", "code/OTP"),
                                      ("R3_password_value", "password"), ("R4_secret_token_key_value", "secret/token/key"),
                                      ("R5_digit_run_17plus", "17+ digit")])
def test_rule_reason(rid, want):
    for t in LEAKS[rid]:
        hit, why = g.otp_followup(g.normalize(t))
        assert hit and (want in why or (rid == "R1_value_before_keyword" and "password" in why)), (t, why)


@pytest.mark.parametrize("text", CLEAN)
def test_ordinary_text_still_passes(text):
    assert g.check(text) == (True, ""), text
    assert g.otp_followup(g.normalize(text)) == (False, ""), text


@pytest.mark.parametrize("text", STILL_PASS + FALSE_POSITIVES + OK + [FP])
def test_existing_clean_lists_untouched_by_new_rule(text):
    assert g.otp_followup(g.normalize(text)) == (False, ""), text


def test_sixteen_digits_not_the_17_rule():
    assert not g.LONG_DIGITS_RE.search("1234567890123456") and g.LONG_DIGITS_RE.search("12345678901234567")


def test_fullwidth_and_zero_width_leaks():
    for t in ("４８２１９３ is your code", "c\u200bo\u200bd\u200be 482193", "ｐｗ 482193", "１２３４５６７８９０１２３４５６７"):
        assert not g.check(t)[0], t


@pytest.mark.parametrize("text", LEAKS_OTP + MORE)
def test_earlier_otp_leaks_still_refused(text):
    assert not g.check(text)[0], text


def test_relay_refuses_followup_leaks():
    import relay
    for t in ("ANSWER: 482193 is your code", "ANSWER: pw 482193", "secret ab12cd", "c0de 482193"):
        with pytest.raises(relay.Refused): relay.guard_text(t, "internal")


@pytest.mark.parametrize("text", CARVED_OUT + SHORT_NOT_A_VALUE)
def test_pass_carve_outs_and_short_numbers_pass(text):
    assert g.check(text) == (True, ""), text


def test_task_id_carve_out_matches_repo_ids_only():
    for t in ("t164u", "t123u", "t90u", "t19s1", "t112s539", "t164r2", "t11"):
        assert g.OTP2_PASS_TASKID_RE.match(" " + t), t
    for t in ("t1645", "t164x", "t16455u", "t164uu", "t1s1", "t164s1234", "1a2b3c4d", "cao-0645"):
        assert not g.OTP2_PASS_TASKID_RE.match(" " + t), t


def test_count_carve_out_is_only_n_slash_m():
    for t in (" 1799/2", " 4821/93", " 1/1"):
        assert g.OTP2_PASS_COUNT_RE.match(t), t
    for t in (" 1745 tests", " 482193 of 9", " 482193/1", " 12345/6", ": 1799/2", " 1799/2/3", " 17/2.5"):
        assert not g.OTP2_PASS_COUNT_RE.match(t), t


@pytest.mark.parametrize("text", ACCEPTED_GAPS)
def test_accepted_gaps_documented(text):
    assert g.check(text) == (True, ""), text
