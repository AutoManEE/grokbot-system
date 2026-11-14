"""Proposed text_guard fix (CSO screen pending): relay retest refused the AIO A reply with
'looks encoded: short base64 next to a secret word'. The reply text is not stored (refused replies keep no text); AIO's
own report-format prompt trips the same rule the same way: 'PASS/FAIL' (has '/') + the word 'code' ('the code checks
those'). Only the exact token 'PASS/FAIL' is exempt; every look-alike and every earlier leak still refuses."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests")
import text_guard as g
from test_text_guard import protected_list   # noqa: F401  (autouse fixture re-exported)
from test_text_guard_rescreen3 import LEAKS_R3, EXTRA

FP = ("List required conditions as PASS/FAIL; any required FAIL makes the result FAIL. Never add conditions about your "
      "own report's format; the code checks those.")


def test_report_format_pass_fail_with_code_word_passes():
    assert g.check(FP) == (True, "")


def test_relay_accepts_report_format_reply():
    import relay
    relay.guard_text(FP, "internal")


@pytest.mark.parametrize("text", ["the code is PASS/FAIl", "token pass/fail", "code PASS/FAIL+", "code PASS/FAIL=", "the code checks PASS/FAIL1",
                                  "key ABcd/EFgh", "code PASS/FALL", "login Pa55/Fail", "otp AB/CD/EF/GH", "code PASS+FAIL",
                                  "code PASS/FAIL aB3dE9xQ", "secret PASSFAIL/x"])
def test_lookalikes_still_refused(text):
    ok, why = g.check(text); assert not ok and why, text


@pytest.mark.parametrize("name", sorted(LEAKS_R3))
def test_rescreen3_leaks_still_refused_with_pass_fail_nearby(name):
    assert not g.check(LEAKS_R3[name] + " PASS/FAIL")[0], name


@pytest.mark.parametrize("name", sorted(EXTRA))
def test_rescreen3_related_still_refused_with_pass_fail_nearby(name):
    assert not g.check(EXTRA[name] + " PASS/FAIL")[0], name
