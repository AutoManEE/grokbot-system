#!/usr/bin/env python3
"""make_bots.py: create bots/<ID>/{prompt.md,settings.json,README.md} for every assistant and S-suite from bots/templates.

  make_bots.py            create missing bot folders (never overwrites an existing one)
  make_bots.py --force    rewrite every generated folder from the templates
The C-role list comes from scripts/croles.py; titles from TITLES below. Per-counterpart S helpers are listed in HELPERS.
Only the CAO changes bot folders in a running team (through scripts/fixlist_check.py).
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
from croles import CROLES_ORDER

TITLES = {"CBO": "Chief Bot Officer", "CIO": "Chief Intelligence Officer", "CFO": "Chief Financial Officer",
          "CTO": "Chief Token Officer", "CMO": "Chief Media Officer", "CSO": "Chief Safety Officer",
          "CAO": "Chief Automation Officer", "CPO": "Chief Production Officer", "COO": "Chief Operations Officer",
          "CRO": "Chief Recruiting Officer", "CCO": "Chief Code Officer", "CXO": "Chief Outreach Officer",
          "CLO": "Chief Leads Officer", "CGO": "Chief Git Officer", "CHO": "Chief Hub Officer"}
HELPERS = {"SBO-CFO": "CFO", "SIO-CBO": "CBO", "SFO-CBO": "CBO", "SSO-CBO": "CBO", "SAO-CBO": "CBO", "SOO-CBO": "CBO"}
A_MODEL, S_MODEL, REVIEWER = "qwen3:4b-instruct", "phi4-mini", "llama3.2:3b"


def _t(name):
    return open(os.path.join(HERE, "templates", name)).read()


def _write(bid, prompt, settings, readme, force):
    d = os.path.join(HERE, bid)
    if os.path.isdir(d) and not force:
        return False
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "prompt.md"), "w").write(prompt)
    open(os.path.join(d, "settings.json"), "w").write(json.dumps(settings, indent=1) + "\n")
    open(os.path.join(d, "README.md"), "w").write(readme)
    return True


def build(force=False):
    made = []
    for c in CROLES_ORDER:
        a, s, title = "A" + c[1:], "S" + c[1:], TITLES[c]
        screen = c == "CSO"
        ap = _t("screen_assistant.prompt.md" if screen else "assistant.prompt.md").format(ID=a, C_ROLE=c, TITLE=title)
        sp = _t("screen_worker.prompt.md" if screen else "s_suite.prompt.md").format(ID=s, PARENT=a)
        aset = dict(id=a, kind="assistant", c_role=c, parent=c, model=A_MODEL, temperature=0,
                    num_predict=4 if screen else 1024, tools=[], outside_content=screen, version=1, s_bots=None)
        sset = dict(id=s, kind="s-suite", c_role=c, parent=a, model=S_MODEL, temperature=0, num_predict=160, tools=[],
                    outside_content=screen, version=1, s_bots=[s + "-1"], reviewer_model=REVIEWER)
        if _write(a, ap, aset, f"# {a} — assistant of {c} ({title})\nTakes every task from {c} (DG-{c}), hands it to {s} (DG-{a}), reviews what comes back, and rolls it up into a Section 5 report.\n", force):
            made.append(a)
        if _write(s, sp, sset, f"# {s} — S-suite of {c} ({title})\nDoes the work {a} hands over (DG-{a}) and returns it for {a}'s review. No tools, no sends, no money.\n", force):
            made.append(s)
    for h, cp in HELPERS.items():
        base = h.split("-")[0]; parent = "A" + base[1:]
        hp = _t("s_helper.prompt.md").format(ID=h, PARENT=parent, COUNTERPART=cp)
        hset = dict(id=h, kind="s-suite", c_role="C" + base[1:], parent=parent, model=S_MODEL, temperature=0,
                    num_predict=160, tools=[], outside_content=False, version=1, s_bots=[h + "-1"], reviewer_model=REVIEWER)
        if _write(h, hp, hset, f"# {h} — dedicated S helper of {parent} for {cp}\n", force):
            made.append(h)
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--force", action="store_true")
    print("created:", " ".join(build(ap.parse_args().force)) or "nothing (all present)")
