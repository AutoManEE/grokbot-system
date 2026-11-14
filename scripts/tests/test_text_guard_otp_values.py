"""Proposed text_guard leak fix: OTP-shaped values
right after a code/otp/token keyword pass today and must refuse. Ordinary notes that mention 'code' or 'token' with no
value, and the PASS/FAIL report format, still pass. Made-up values only."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests")
import text_guard as g
from test_text_guard import protected_list, FALSE_POSITIVES, OK   # noqa: F401  (autouse fixture re-exported)
from test_text_guard_rescreen3 import LEAKS_R3, EXTRA
from test_text_guard_pass_fail_fp import FP

LEAKS_OTP = ["verification code 482193", "otp 482193", "2fa code 482193", "your code 48 21 93", "code abc123", "token Qm9i"]
MORE = ["OTP: 482193", "mfa 4821-93", "auth code 9f3k2m", "passcode 48.21.93", "token=Qm9iYQ", "Your verification code 482193.",
        "code\u200b 482193", "ｃｏｄｅ ４８２１９３", "the otp 1234567890123"]
STILL_PASS = ["Check the code in commit abc1234 today.", "The code review for JavaScript files.", "the code is ready",
              "Ask ATO: what is the token budget for tomorrow?", "error code 404", "Wire pin 2 to the terminal block.",
              "code PASS/FAIL", "Write the auth module docs.", "The verification step passed."]


@pytest.mark.parametrize("text", LEAKS_OTP + MORE)
def test_otp_values_refused(text):
    ok, why = g.check(text); assert not ok and why, text


@pytest.mark.parametrize("text", STILL_PASS + FALSE_POSITIVES + OK + [FP])
def test_ordinary_code_mentions_still_pass(text):
    assert g.check(text) == (True, ""), text


@pytest.mark.parametrize("name", sorted(LEAKS_R3))
def test_rescreen3_leaks_still_refused(name):
    assert not g.check(LEAKS_R3[name])[0], name


def test_relay_refuses_otp_values():
    import relay
    for t in LEAKS_OTP:
        with pytest.raises(relay.Refused): relay.guard_text(t, "internal")
