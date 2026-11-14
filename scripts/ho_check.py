#!/usr/bin/env python3
"""ho_check.py (P2-05, COO, code by CAO): HOST lines. Check Ollama and every assistant/S-suite; mark READY or PTO.
Zero AI. A bot is READY when Ollama answers, its configured model is installed, and its config has no tools;
otherwise it is on PTO and its C-role (for an assistant) or assistant (for an S bot) finishes the work.
Writes $GB_STATE/ho/status.json {checked_at, ollama, bots: {ID: READY|PTO}}. Never changes a bot's settings.
Run at each COO wake (on a schedule the Owner approves, or on demand).
Usage: ho_check.py [--url http://127.0.0.1:11434]          quick check (status.json)
       ho_check.py wake [--url ...]                         COO AT EACH WAKE (scheduled or
          on demand): check + PTO handling. For every bot on PTO it writes $GB_STATE/ho/pto/<ID>.json naming who
          finishes its work (assistant -> its C-role on the cloud model; S bot -> its assistant), clears PTO files for bots that
          are READY again, appends one line to $GB_STATE/ho/log.jsonl, and exits 0 (all READY) or 4 (any PTO).
          flow.py delegate also refuses (exit 3, PTO) when the assistant or S-suite it needs is not READY.
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, json, os, sys, time, urllib.request
sys.path.insert(0, GB_ROOT + "/bots"); import run
STATE = os.environ.get("GB_STATE", GB_ROOT + "/gb_state")
IDS = ["ABO","AIO","AFO","ATO","AMO","ASO","AAO","APO","AOO","ARO","SBO","SIO","SFO","STO","SMO","SSO","SAO","SPO","SOO","SRO"]


def model_installed(name, models):
    """True if Ollama has this model. 'phi4-mini' matches a tags name of 'phi4-mini:latest' and vice versa."""
    if name in models or name + ":latest" in models:
        return True
    if name.endswith(":latest") and name[:-7] in models:
        return True
    return False

def check(url):
    try:
        models = {m["name"] for m in json.load(urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=5))["models"]}
        up = True
    except Exception:
        models, up = set(), False
    bots = {}
    for b in IDS:
        _, s = run.load(b)
        bots[b] = "READY" if up and model_installed(s["model"], models) and not s["tools"] else "PTO"
    st = dict(checked_at=time.strftime("%Y-%m-%d %H:%M:%S %Z"), ollama="UP" if up else "DOWN", bots=bots)
    os.makedirs(os.path.join(STATE, "ho"), exist_ok=True)
    json.dump(st, open(os.path.join(STATE, "ho", "status.json"), "w"), indent=1)
    return st

def wake(url):
    st = check(url); d = os.path.join(STATE, "ho", "pto"); os.makedirs(d, exist_ok=True)
    pto = sorted(b for b, v in st["bots"].items() if v == "PTO")
    for b in IDS:
        f = os.path.join(d, f"{b}.json")
        if b in pto:
            cover = ("C" + b[1:] + " (C-role, on the cloud model within the CTO's cost limits)") if b[0] == "A" else ("A" + b[1:] + " (assistant)")
            json.dump(dict(bot=b, line="HO-" + b, status="PTO", since=st["checked_at"], ollama=st["ollama"], covered_by=cover), open(f, "w"), indent=1)
        elif os.path.exists(f):
            os.remove(f)
    rec = dict(at=st["checked_at"], ollama=st["ollama"], ready=len(IDS) - len(pto), pto=pto)
    try:
        import alignment_check
        al = alignment_check.collect()
        rec["alignment"] = {"result": al.get("result"), "bot_fail": al.get("bot_fail"),
                            "aso_sso_independence": al.get("aso_sso_independence")}
        rec["alignment"]["alarms"] = len(alignment_check.write_alarms(al, state=STATE))   # C10 routing
    except Exception as e:
        rec["alignment"] = {"result": "UNVERIFIABLE", "note": str(e)}
    with open(os.path.join(STATE, "ho", "log.jsonl"), "a") as fh: fh.write(json.dumps(rec) + "\n")
    return rec

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("cmd", nargs="?", default="check", choices=["check", "wake"])
    p.add_argument("--url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    a = p.parse_args()
    if a.cmd == "wake":
        r = wake(a.url); print(json.dumps(r)); sys.exit(4 if r["pto"] else 0)
    st = check(a.url); print(json.dumps({"ollama": st["ollama"], "READY": sum(v == "READY" for v in st["bots"].values()), "PTO": sum(v == "PTO" for v in st["bots"].values())}))
