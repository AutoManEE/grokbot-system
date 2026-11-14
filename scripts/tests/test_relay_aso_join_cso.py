"""CSO r4: A-to-A into ASO keeps the CSO work-mode refusals
(URL / fetched / --from outside text and money math), with no model call; other A's are unchanged."""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import sys, pytest
sys.path.insert(0, GB_ROOT + "/scripts"); sys.path.insert(0, GB_ROOT + "/scripts/tests"); sys.path.insert(0, GB_ROOT + "/bots")
from test_delegate import f, task   # noqa: F401
from test_relay_aso_join import g   # noqa: F401  (CAO's flow A-to-A fixture)


@pytest.mark.parametrize("kw", [dict(text="Summarize https://example.com/page for me."),
                                dict(text="Summarize the page.", fetched=True),
                                dict(text="Summarize the page.", fetched_text="page body")])
def test_a2a_to_aso_refuses_outside_text(g, kw):
    with pytest.raises(g.Refused, match="ASO refuses raw outside text"):
        g.delegate_a2a("ABO", "ASO", task(g.tmp, **kw), "ajx")
    assert not [c for c in g.calls if c[0] != "complete"]


def test_a2a_to_aso_refuses_money_math(g):
    with pytest.raises(g.Refused, match="ASO refuses money math"):
        g.delegate_a2a("ABO", "ASO", task(g.tmp, text="Compute the net P&L on $500 profit minus $20 fee."), "ajm")
    assert not [c for c in g.calls if c[0] != "complete"]


def test_a2a_from_aso_and_other_as_unchanged(g):
    r2 = g.delegate_a2a("ASO", "ABO", task(g.tmp, text="Summarize https://example.com/page for me."), "ajo")
    assert r2["result"] in ("PASS", "FAIL")


CHECK = "Check these facts: budget 1000, spend 1%. (1) Spend per request is at or under 1%. (2) Vendor is set."


def _spy_items(g, monkeypatch):
    seen = []
    orig = g.checklist_items
    monkeypatch.setattr(g, "checklist_items", lambda text: (seen.append(text), orig(text))[1])
    return seen


def test_q3_check_mode_on_for_a2a_into_aso(g, monkeypatch):
    seen = _spy_items(g, monkeypatch)
    try: g.delegate_a2a("ABO", "ASO", task(g.tmp, text=CHECK), "q3a")
    except g.Refused: pass
    assert seen, "A-to-A into ASO must use CSO work mode (checklist) for CHECK tasks"


def test_q3_check_mode_off_for_a2a_from_aso(g, monkeypatch):
    seen = _spy_items(g, monkeypatch)
    try: g.delegate_a2a("ASO", "ABO", task(g.tmp, text=CHECK), "q3b")
    except g.Refused: pass
    assert not seen, "A-to-A from ASO runs on the receiver's chain, never in CSO work mode"


class _SpyRe:
    def __init__(self, rx): self.rx, self.calls = rx, 0
    def match(self, s): self.calls += 1; return self.rx.match(s)


@pytest.mark.parametrize("fa,ta,want", [("ABO", "ASO", True), ("ASO", "ABO", False)])
def test_q3_write_mode_keyed_on_chain_a(g, monkeypatch, fa, ta, want):
    spy = _SpyRe(g.WRITE_TASK_RE); monkeypatch.setattr(g, "WRITE_TASK_RE", spy)
    try: g.delegate_a2a(fa, ta, task(g.tmp, text="Write a short note on gate hygiene."), f"q3w{fa}")
    except g.Refused: pass
    assert (spy.calls > 0) == want
