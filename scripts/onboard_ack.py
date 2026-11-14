#!/usr/bin/env python3
"""onboard_ack.py (CAO): write a new role's ack with code-graded quiz results.

  onboard_ack.py --role CRO --answers /path/answers.json [--dry-run]
  onboard_ack.py --role CRO --retake CTO --answers retake.json   (one retake: {"2": "shorter answer"})
  onboard_ack.py --role CRO --regrade                            (after owners fix their keys)

answers.json: {"CBO": ["answer 1", "answer 2", ...], "CIO": [...], ...} with one
list per quiz in <repo>/gb_state/onboarding/packets/<ROLE>.quiz.json, in
question order. The bot under onboarding writes ONLY its answers; this script
hashes every packet, grades every quiz in code (quiz_grade.py), and writes
<repo>/gb_state/onboarding/acks/<ROLE>.json with each question's given answer,
matched rule, pass/fail and the quiz score. alignment_check re-grades those answers
and never trusts a stored quiz_result. Exit 0 if every quiz PASSes, 1 otherwise,
2 on bad input. Accident guard plus detection, not security.
"""
from __future__ import annotations
GB_ROOT = __import__("os").environ.get("GB_ROOT") or __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alignment_check as ac
import quiz_grade as qg


def build(role: str, answers: dict) -> dict:
    role = role.upper()
    pk = ac.list_role_packets()
    quizzes = ac.list_role_quizzes()
    if not isinstance(answers, dict):
        raise SystemExit("REFUSED: answers must be an object keyed by role")
    missing = [r for r in quizzes if r not in answers]
    if missing:
        raise SystemExit(f"REFUSED: answers missing for quizzes: {', '.join(missing)}")
    live16 = qg.brief16(ac.BRIEF)
    rec = dict(role=role, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"), graded_by="onboard_ack.py",
               grader=qg.GRADER, brief_sha256=qg.file_sha256(ac.BRIEF), brief16=live16,
               packets={r: {"sha256": ac.sha256_file(p)} for r, p in sorted(pk.items())},
               quizzes={})
    try:
        pin = json.loads(ac.CHARTER_PIN.read_text(encoding="utf-8"))
        rec["charter_rev"] = dict(revisionId=pin.get("revisionId"), body_sha256=pin.get("body_sha256"))
    except (OSError, ValueError):
        rec["charter_rev"] = None
    for r in quizzes:
        rec["quizzes"][r] = _grade(r, role, [dict(kind="original", ts=rec["ts"], given=list(answers.get(r) or []))], live16)
    rec["result"] = _overall(rec)
    return rec


def _grade(r, role, attempts, live16):
    qp = ac.PACKETS / f"{r}.quiz.json"
    quiz = qg.load_quiz(qp)
    corpus = qg.corpus_for(r, ac.PACKETS, ac.BRIEF, ac.DEFAULT_CHARTER)
    _raw, g = qg.grade_taken(quiz, role, r, corpus_toks=corpus, attempts=attempts)   # same code as alignment_check
    g.update(quiz_file=qp.name, quiz_sha256=qg.file_sha256(qp),
             quiz_brief_sha256=quiz.get("brief_sha256"), stale=qg.stale(quiz, live16))
    return g


def _overall(rec):
    rs = [q["result"] for q in rec["quizzes"].values()]
    return "PASS" if all(x == "PASS" for x in rs) else ("WAIT" if "FAIL" not in rs else "FAIL")


def _load_ack(role):
    path = ac.ACKS / f"{role.upper()}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"REFUSED: no readable ack at {path}: {e}")


def regrade(role: str) -> dict:
    """Item 17: re-grade every stored attempt against the current quiz keys (after owners fix them)."""
    rec = _load_ack(role)
    live16 = qg.brief16(ac.BRIEF)
    for r, g in list(rec.get("quizzes", {}).items()):
        attempts = g.get("attempts")
        if not attempts:  # v1 ack: rebuild attempt 1 from the stored given answers
            attempts = [dict(kind="original", given=[(a.get("first") or a).get("given", "") for a in g.get("answers") or []])]
        rec["quizzes"][r] = _grade(r, rec["role"], attempts, live16)
    rec.setdefault("regrades", []).append(dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"), grader=qg.GRADER))
    rec["result"] = _overall(rec)
    return rec


def retake(role: str, quiz_role: str, answers: dict) -> dict:
    """Item 17: one retake per quiz. answers = {question_index (0-based): answer}. Only failed
    questions may be retaken: too_long needs a shortening; a content miss needs the question's
    replacement. Every attempt is kept."""
    rec = _load_ack(role); quiz_role = quiz_role.upper()
    g = rec.get("quizzes", {}).get(quiz_role)
    if not g:
        raise SystemExit(f"REFUSED: {quiz_role} quiz not in the ack yet")
    attempts = g.get("attempts") or []
    if len(attempts) >= 2:
        raise SystemExit(f"REFUSED: {quiz_role} quiz already has its one retake")
    failed = {a["q_index"] for a in g["answers"] if not a.get("pass") and not a.get("excluded")}
    extra = sorted(int(k) for k in answers if int(k) not in failed)
    if extra:
        raise SystemExit(f"REFUSED: retake only failed questions; not failed: {extra}")
    attempts = attempts + [dict(kind="retake", ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                given={str(int(k)): v for k, v in answers.items()})]
    rec["quizzes"][quiz_role] = _grade(quiz_role, rec["role"], attempts, qg.brief16(ac.BRIEF))
    rec["result"] = _overall(rec)
    return rec


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--role", required=True); p.add_argument("--answers")
    p.add_argument("--retake", metavar="QUIZ_ROLE", help="one retake of that quiz; --answers is {index: answer}")
    p.add_argument("--regrade", action="store_true", help="re-grade stored attempts against current keys")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    answers = None
    if not a.regrade:
        if not a.answers:
            print("REFUSED: --answers required", file=sys.stderr); return 2
        try:
            answers = json.loads(Path(a.answers).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"REFUSED: unreadable answers: {e}", file=sys.stderr); return 2
    if a.regrade:
        rec = regrade(a.role)
    elif a.retake:
        rec = retake(a.role, a.retake, answers)
    else:
        rec = build(a.role, answers)
    for r, q in rec["quizzes"].items():
        print(f"{r}: {q['result']} {q['passed']}/{q['n']} ({q['threshold']})" + (f" {q['stale']}" if q.get("stale") else ""))
    if not a.dry_run:
        import packet_guard   # CIO: packets are write-once; never ack against a packet changed outside the build
        bad = packet_guard.problems(packets=ac.PACKETS)
        if bad:
            print("REFUSED: onboarding packets differ from the committed manifest (fail closed):\n  "
                  + "\n  ".join(bad), file=sys.stderr)
            return 2
        out = ac.ACKS / f"{rec['role']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    print(f"result: {rec['result']}")
    return 0 if rec["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
