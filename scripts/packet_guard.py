#!/usr/bin/env python3
"""packet_guard.py (CAO): onboarding packets are write-once.

Every file in gb_state/onboarding/packets/ (<ROLE>.md, <ROLE>.quiz.json) must match the committed manifest
alignment/onboarding/packet_manifest.json. Only the CAO packet build (`packet_guard.py build`) may change a
packet, and it always appends an action_log line whose hash it records in the manifest. Built packets are
left read-only (0444), so a stray editor write fails; any change outside the build (new, edited, removed or
unpinned file) is a problem, and onboard_ack.py refuses to write an ack while one exists (fail closed).
A file listed under "pending" (e.g. CXO.md, rewritten outside the build at, waiting on a CIO
ruling) is a problem until the build pins it.

  packet_guard.py check                                        exit 0 clean, 1 problems
  packet_guard.py build --by CAO --file CXO.md --src NEW.md --item TEXT --reason TEXT [--ref ID]
  packet_guard.py build --by CAO --file CXO.md --keep --item TEXT --reason TEXT   (pin current bytes)

Zero AI. Accident guard plus detection, not security (one Linux user; a deliberate chmod + write is still
possible, but it is detected).
"""
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, hashlib, json, os, stat, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKETS = Path(os.environ.get("GB_PACKETS", GB_ROOT + "/gb_state/onboarding/packets"))
MANIFEST = Path(os.environ.get("GB_PACKET_MANIFEST", GB_ROOT + "/alignment/onboarding/packet_manifest.json"))
LOG = os.environ.get("GB_ACTION_LOG", GB_ROOT + "/alignment/action_log.jsonl")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_manifest(path: Path | None = None):
    path = path or MANIFEST
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"packet manifest unreadable: {path}: {e}"
    if not isinstance(m, dict) or not isinstance(m.get("packets"), dict):
        return None, f"packet manifest malformed: {path}"
    return m, ""


def _files(d: Path):
    return sorted(p.name for p in d.glob("*") if p.is_file() and (p.name.endswith(".md") or p.name.endswith(".quiz.json")))


def problems(packets: Path | None = None, manifest: Path | None = None) -> list[str]:
    d = packets or PACKETS
    m, err = load_manifest(manifest)
    if m is None:
        return [err + " (fail closed)"]
    pins, pending = m["packets"], m.get("pending") or {}
    out = []
    for name in _files(d):
        if name in pending:
            out.append(f"{name}: pending ({pending[name].get('reason', 'unreviewed change')}); only the CAO packet build can pin it")
        elif name not in pins:
            out.append(f"{name}: not in the packet manifest (written outside the CAO packet build)")
        elif _sha(d / name) != pins[name].get("sha256"):
            out.append(f"{name}: changed outside the CAO packet build (sha256 differs from the manifest)")
    for name in pins:
        if not (d / name).is_file():
            out.append(f"{name}: pinned packet missing")
    return out


def _entry(name, sha, item, reason, ref):
    return dict(item=item, bot="CAO", result="PASS",
                objective=f"CAO packet build: pin onboarding packet {name}",
                options=[dict(name="1. packet_guard.py build", pros="only sanctioned packet writer; manifest + action_log line",
                              cons="accident guard plus detection, not security", deltas=f"{name} sha256 {sha}")],
                decision="1", reasoning=reason + (f" (ref {ref})" if ref else ""), confidence=90,
                conditions=[dict(name="packet pinned with an action_log line", required=True, result="PASS")],
                packet=name, packet_sha256=sha)


def build(name, *, by, item, reason, src=None, keep=False, ref=None, packets=None, manifest=None, log=None):
    if str(by or "").strip().upper() != "CAO":
        raise SystemExit("REFUSED: only the CAO packet build may change an onboarding packet")
    if not (name.endswith(".md") or name.endswith(".quiz.json")) or "/" in name or name.startswith("."):
        raise SystemExit(f"REFUSED: not a packet file name: {name!r}")
    if not str(item or "").strip() or not str(reason or "").strip():
        raise SystemExit("REFUSED: --item and --reason are required (they become the action_log line)")
    d, mpath = packets or PACKETS, manifest or MANIFEST
    m, err = load_manifest(mpath)
    if m is None:
        if Path(mpath).exists():
            raise SystemExit(f"REFUSED: {err}")
        m = dict(packets={}, pending={})
    target = d / name
    if keep:
        if not target.is_file():
            raise SystemExit(f"REFUSED: --keep but {target} is missing")
        data = target.read_bytes()
    else:
        if not src:
            raise SystemExit("REFUSED: --src or --keep required")
        data = Path(src).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    sys.path.insert(0, str(HERE))
    import action_log
    h = action_log.append(log or LOG, _entry(name, sha, item, reason, ref))   # line first: no line, no write
    if not keep:
        if target.exists():
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        tmp = target.with_name(target.name + ".build-tmp")
        tmp.write_bytes(data); os.replace(tmp, target)
    os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    m["packets"][name] = dict(sha256=sha, action_log_hash=h, built_by="CAO",
                              ts=time.strftime("%Y-%m-%d %H:%M:%S %Z"), **({"ref": ref} if ref else {}))
    (m.get("pending") or {}).pop(name, None)
    Path(mpath).write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return dict(file=name, sha256=sha, action_log_hash=h)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    b = sub.add_parser("build")
    b.add_argument("--by", required=True); b.add_argument("--file", required=True)
    b.add_argument("--src"); b.add_argument("--keep", action="store_true")
    b.add_argument("--item", required=True); b.add_argument("--reason", required=True); b.add_argument("--ref")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        p = problems()
        print(json.dumps(dict(result="FAIL" if p else "PASS", problems=p), indent=1))
        return 1 if p else 0
    print(json.dumps(build(a.file, by=a.by, item=a.item, reason=a.reason, src=a.src, keep=a.keep, ref=a.ref), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
