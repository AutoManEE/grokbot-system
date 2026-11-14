GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import os, sys, json, shutil, importlib, pytest
sys.path.insert(0, GB_ROOT + "/scripts")
T = json.load(open(GB_ROOT + "/templates/fixlist_template.json"))
@pytest.fixture
def fl(tmp_path, monkeypatch):
    shutil.copytree(GB_ROOT + "/bots/AFO", tmp_path / "bots" / "AFO")
    monkeypatch.setenv("GB_BOTS", str(tmp_path / "bots")); monkeypatch.setenv("GB_ACTION_LOG", str(tmp_path / "log.jsonl"))
    import fixlist_check; importlib.reload(fixlist_check); return fixlist_check
def test_template_valid(fl): assert fl.check(T) == []
def test_other_roles_bot_rejected(fl): assert fl.check({**T, "target": "ATO"})
def test_malformed_rejected(fl):
    assert fl.check({k: v for k, v in T.items() if k != "evidence"})
    assert fl.check({**T, "change": {"field": "tools", "value": ["shell"]}})
    assert fl.check({**T, "change": {"field": "outside_content", "value": True}})
def test_apply_only_cao_and_logs(fl, tmp_path):
    real_v = json.load(open(GB_ROOT + "/bots/AFO/settings.json"))["version"]
    with pytest.raises(PermissionError): fl.apply(T, "COO")
    fl.apply(T, "CAO")
    s = json.load(open(tmp_path / "bots" / "AFO" / "settings.json")); assert s["version"] == real_v + 1
    assert "Exact text to add" in open(tmp_path / "bots" / "AFO" / "prompt.md").read()
    import action_log as al; assert al.verify(str(tmp_path / "log.jsonl"))[0]
    assert json.load(open(GB_ROOT + "/bots/AFO/settings.json"))["version"] == real_v  # real config untouched

def test_cao_may_apply_sao_without_fx_cao(tmp_path, monkeypatch):
    """No FX-CAO in the brief; CAO still applies AD-SAO itself."""
    shutil.copytree(GB_ROOT + "/bots/SAO", tmp_path / "bots" / "SAO")
    monkeypatch.setenv("GB_BOTS", str(tmp_path / "bots"))
    monkeypatch.setenv("GB_ACTION_LOG", str(tmp_path / "log.jsonl"))
    import fixlist_check; importlib.reload(fixlist_check)
    fx = {"from": "CAO", "target": "SAO", "issue": "shared model", "evidence": "item 10",
          "change": {"field": "model", "value": "phi4-mini"}, "test": "pair_independence PASS"}
    assert fixlist_check.check(fx) == []
    with pytest.raises(PermissionError, match="--cosign CTO"):
        fixlist_check.apply(fx, "CAO")                                   # CIO: CTO cosign required
    _ruling(tmp_path, monkeypatch, fixlist_check)
    fixlist_check.apply(fx, "CAO", "CTO", "cto_ruling_test.md")
    s = json.load(open(tmp_path / "bots" / "SAO" / "settings.json"))
    assert s["model"] == "phi4-mini"



# ---- CIO: AAO/SAO fix lists need --cosign CTO --cosign-ref <real CTO message or ruling id>

def _ruling(tmp_path, monkeypatch, mod):
    d = tmp_path / "rulings"; d.mkdir(exist_ok=True)
    (d / "cto_ruling_test.md").write_text("# CTO ruling (test fixture)\nCTO: AAO/SAO model change approved.\n")
    monkeypatch.setattr(mod, "RULINGS", str(d))


@pytest.fixture
def cao_fl(tmp_path, monkeypatch):
    for b in ("AAO", "SAO"):
        shutil.copytree(f"{GB_ROOT}/bots/{b}", tmp_path / "bots" / b)
    monkeypatch.setenv("GB_BOTS", str(tmp_path / "bots")); monkeypatch.setenv("GB_ACTION_LOG", str(tmp_path / "log.jsonl"))
    import fixlist_check; importlib.reload(fixlist_check)
    _ruling(tmp_path, monkeypatch, fixlist_check)
    return fixlist_check


def _fx(t):
    return {"from": "CAO", "target": t, "issue": "x", "evidence": "e", "change": {"field": "temperature", "value": 0.1}, "test": "t"}


@pytest.mark.parametrize("t", ["AAO", "SAO"])
@pytest.mark.parametrize("co,ref", [(None, None), ("CTO", None), ("CTO", ""), ("CIO", "cto_ruling_test.md"),
                                    ("CTO", "t999u-made-up"), ("CTO", "../../etc/passwd")])
def test_aao_sao_refused_without_valid_cto_cosign(cao_fl, tmp_path, t, co, ref):
    before = open(tmp_path / "bots" / t / "settings.json").read()
    with pytest.raises(PermissionError):
        cao_fl.apply(_fx(t), "CAO", co, ref)
    assert open(tmp_path / "bots" / t / "settings.json").read() == before
    assert not (tmp_path / "log.jsonl").exists()


@pytest.mark.parametrize("t", ["AAO", "SAO"])
def test_aao_sao_pass_with_cto_cosign_and_recorded(cao_fl, tmp_path, t):
    h = cao_fl.apply(_fx(t), "CAO", "CTO", "cto_ruling_test.md")
    e = [json.loads(l) for l in open(tmp_path / "log.jsonl")][-1]
    assert e["hash"] == h and e["cosign"]["by"] == "CTO" and e["cosign"]["ref"] == "cto_ruling_test.md"
    assert any("CTO cosign" in c["name"] for c in e["conditions"])


def test_cto_ref_found_in_action_log(cao_fl, tmp_path):
    (tmp_path / "log.jsonl").write_text(json.dumps({"item": "CTO ruling A: a round is one review pass", "bot": "CAO"}) + "\n")
    assert cao_fl.cto_ref_ok("CTO ruling A")[0] and not cao_fl.cto_ref_ok("CTO ruling Z")[0]


def test_cli_refuses_without_flags(tmp_path):
    import subprocess
    f = tmp_path / "fx.json"; f.write_text(json.dumps(_fx("SAO")))
    env = dict(os.environ, GB_BOTS=str(tmp_path / "nobots"), GB_ACTION_LOG=str(tmp_path / "log.jsonl"))
    r = subprocess.run([sys.executable, GB_ROOT + "/scripts/fixlist_check.py", "apply", "-f", str(f), "--by", "CAO"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "--cosign CTO" in r.stderr


# ---- the Owner: per-counterpart S-suites S<XX>-<ROLE> are a C-role's own S
def _mk(c, t, field="temperature", value=0.1):
    return {"from": c, "target": t, "issue": "x", "evidence": "e", "change": {"field": field, "value": value}, "test": "t"}


@pytest.mark.parametrize("c,t", [("CBO", "SBO-CFO"), ("CBO", "SBO-CIO"), ("CFO", "SFO-CBO"), ("CMO", "SMO-CXO"),
                                 ("CHO", "SHO-CRO"), ("CFO", "SFO"), ("CFO", "AFO")])
def test_per_counterpart_s_accepted(fl, c, t):
    assert fl.is_own_target(c, t)
    assert not [e for e in fl.check(_mk(c, t)) if "own" in e], fl.check(_mk(c, t))


@pytest.mark.parametrize("c,t", [
    ("CBO", "SBO-XYZ"),     # ROLE not a known C-role
    ("CBO", "SBO-CBO"),     # its own role is not another agent
    ("CBO", "SFO-CBO"),     # another C-role's S
    ("CBO", "ABO-CFO"),     # per-counterpart ids are S-suites only
    ("CBO", "SBO-cfo"), ("CBO", "SBO-CFO "), ("CBO", "SBO-CFO-1"), ("CBO", "SBO_CFO"), ("CBO", "SBO-AFO"),
    ("CBO", "SBO-"), ("CBO", "SBOCFO"), ("CBO", "SBO-CSO\u200b"),
])
def test_per_counterpart_s_refused(fl, c, t):
    assert not fl.is_own_target(c, t)
    assert any("own" in e for e in fl.check(_mk(c, t)))


def test_counterpart_s_create_pairs_with_own_a(fl, tmp_path):
    fx = _mk("CFO", "SFO-CBO", "create_helper", dict(model="qwen-test:1b", reviewer_model="llama-test:1b", prompt="p"))
    am = json.load(open(tmp_path / "bots" / "AFO" / "settings.json"))["model"]
    assert fl.check(fx) == [] or all("own" not in e for e in fl.check(fx))
    bad = _mk("CFO", "SFO-CBO", "create_helper", dict(model=am, reviewer_model="llama-test:1b", prompt="p"))
    assert any("share model" in e for e in fl.check(bad))     # still three distinct models vs AFO


def test_cao_counterpart_s_needs_cto_cosign(cao_fl):
    fx = _mk("CAO", "SAO-CFO")
    assert cao_fl.check(fx) == []
    with pytest.raises(PermissionError, match="--cosign CTO"):
        cao_fl.apply(fx, "CAO")
