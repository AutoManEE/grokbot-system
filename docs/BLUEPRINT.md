# Blueprint: how to stand up this team

A step-by-step outline. Each step lists what to do and how to check it worked. Placeholders look like `<THIS>`.

## Phase 0: Decide the shape

1. **Name the Owner.** One human approves anything irreversible and is the only one who releases money.
2. **Pick the chiefs.** Start with the core six: CBO, CSO, CIO, CAO, CPO and COO. Add the rest as the workload grows.
   Each role needs a one-line job description.
3. **Pick the models.**
   - A main assistant model for the C-roles.
   - A small local instruct model for the assistants.
   - Small local models for the workers.
   - A reviewer model **from a different family**.

   **Check:** each role, with its tier and model, is written down.

## Phase 1: Write the brief

4. Copy `alignment/brief.txt`. Then fill in:
   - Section 0: the term list and the C-role list (`C-role = one of the N chief roles: ...`).
   - Section 2: authority, meaning who the Owner talks to and who answers directly to the Owner.
   - Section 3.2: the line table, one row per line, header exactly `ID | From → To | Type | Carries | Source`.
   - Section 3.3: the rules, including an explicit "there is no <LINE>" sentence for every line you leave out on purpose.
   - Section 7: the financial guardrails.
5. List the roles in `scripts/croles.py` (`CROLES_ORDER`) in the same order as the brief.

   **Check:** `python3 -m pytest -q scripts/tests/test_line_guard*.py scripts/tests/test_c15_brief_lines.py` passes.

## Phase 2: Generate the bots

6. Adjust the prompt patterns in `bots/templates/`, then set `TITLES` and `HELPERS` in `bots/make_bots.py`.
7. Run `python3 bots/make_bots.py`. Each role gets `A<x>O/` and `S<x>O/` folders with `prompt.md`, `settings.json` and `README.md`.
8. Commit the bot folders. Their prompts are part of the reviewed code.

   **Check:** `python3 scripts/alignment_check.py` shows the A- and S-helpers for every role.

## Phase 3: Turn on the guards

9. **Protected names.** List the names that must never leave the team, one per line, in
   `private/protected_names.txt`. The `private/` folder is git-ignored.
10. **Repository allowlist.** Put the repositories the Git role may touch in `alignment/gh_safe_allowlist.json`.
11. **Finance.** Leave `alignment/finance_settings.json` read-only until the Owner chooses limits on purpose:
    `per_request_limit`, `period_limit`, `period_days` and `allowed_categories`.
12. **Owner quotes.** Register each Owner approval in `alignment/c9_quotes.jsonl` with an id and a narrow `scope`,
    for example `gate:<ID>`.
13. **Cost limits.** Fill in `templates/context/cto_cost_limits.json` (or a live copy in `gb_state/context/`); null limits mean WAIT.

    **Check:** `python3 -m pytest -q` passes. A test spend request auto-HOLDs, and an unscoped quote is refused.

## Phase 4: Charter and onboarding

14. Write the charter, pin it in `alignment/charter_pin.json` (body and file sha256), and point the pin at the file.
15. For each new role, write a packet and a 5-9 question quiz (see `samples/onboarding/`). Pin the packets with
    `packet_guard.py`, because packets are write-once.
16. Have the role answer the quiz. `onboard_ack.py` grades it in code, and a different role approves the ack.

    **Check:** `alignment_check.py` shows PASS for the new role.

## Phase 5: Run work

17. Start the local model server and run `scripts/ho_check.py`, which marks every bot READY or PTO.
18. Hand out a first task: `python3 scripts/flow.py delegate --role CTO --task task.json`.
19. Review the report, then `c-review --decision ACCEPT` and `close`.
20. Append the result with `scripts/action_log.py append` and check it with `verify`.

    **Check:** the task folder has all four stages, and `action_log.py verify` prints PASS.

## Phase 6: Open the relay (optional)

21. Run the relay tests, have the CSO screen the relay modules, and run the cost check.
22. Write `gb_state/relay/LIVE` from `relay.py live-template --pins`. The relay refuses every write until this file is valid.

    **Check:** a broadcast from one assistant reaches every live inbox, and the audit chain verifies.

## Phase 7: Keep it honest

23. Every week:
    - run `verdict_audit.py` and `report_audit.py`;
    - verify every hash chain;
    - re-run `alignment_check.py`.
24. Change structure in the brief first, then regenerate, re-pin and re-test. Never hand-edit runtime state.
