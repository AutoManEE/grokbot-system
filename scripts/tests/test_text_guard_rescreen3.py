"""CSO relay re-screen 3: the 8 text_guard leaks the CSO found, closed by the CSO's own patch
(C takeover, round 3; the text_guard.py pin moved after the PASS/FAIL false-positive fix). Each must be refused in relay notes and takeover lessons;
ordinary notes still pass. Made-up protected name in a tmp list (pytest-only sentinel)."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests")
import text_guard as g
from test_text_guard import protected_list, FALSE_POSITIVES, OK   # noqa: F401  (autouse fixture re-exported)

LEAKS_R3 = {
    "secret word + digits": "the code is 4471",
    "pwd= value": "pwd=Hunter2!",
    "leet secret word": "the p@$$ is hunter2",
    "cashapp handle": "cashapp $OwnerO22",
    "date of birth": "DOB 03/14/1988",
    "mixed digit/word run": "four 4 seven 1",
    "tens words": "forty-four seventy-one",
    "combining acute": "the pa\u0301ssword is hunter2",
}
EXTRA = {"venmo": "venmo me tonight", "zelle": "use zelle for it", "paypal": "my paypal is open", "cashtag alone": "send to $annaB77",
         "combining on partner": "tell my pa\u0301rtner the plan", "pin is value": "pin is 2468x"}


@pytest.mark.parametrize("name", sorted(LEAKS_R3))
def test_rescreen3_leaks_refused(name):
    ok, why = g.check(LEAKS_R3[name]); assert not ok and why, name


@pytest.mark.parametrize("name", sorted(EXTRA))
def test_rescreen3_related_refused(name):
    assert not g.check(EXTRA[name])[0], name


def test_eight_leaks():
    assert len(LEAKS_R3) == 8


@pytest.mark.parametrize("text", FALSE_POSITIVES + OK)
def test_ordinary_notes_still_pass(text):
    assert g.check(text) == (True, ""), text


def test_relay_refuses_rescreen3_leaks():
    import relay
    for t in LEAKS_R3.values():
        with pytest.raises(relay.Refused): relay.guard_text(t, "internal")

