# Workflow — Social Nexa Agent

> STATUS: DRAFT — needs human review before build starts.

## Manager / Worker build-and-test workflow

This project is built module-by-module, one at a time, using a manager role
and a worker role (both performed by Claude Code in this session, kept
behaviorally distinct — see below).

### Roles

**Manager**
- Owns this Workflow.md and the overall build goal.
- Assigns exactly one module (from TRD.md §2) to the worker at a time, in
  the build order below.
- After the worker reports a module "done," the manager actually **runs**
  it (executes the code / test suite / live check specified in that
  module's DoD) — never approves based on reading the code alone.
- On failure, sends the worker back with the **specific failure output**
  (stack trace, wrong value, mismatched field) — never a vague "fix it."
- Maintains a visible status checklist for all 6 modules:
  `not started / building / testing / failed (reason) / passed`.
- Does not begin Integration until all 6 modules individually show
  `passed`.
- **Retry cap: 4 fix-retest cycles per module.** If a module still fails
  after its 4th retest, the manager stops, reports the exact blocker, and
  asks the human for input — it does not keep looping silently.

**Worker**
- Builds one assigned module at a time, in Ponytail **lite** mode —
  minimal code, no speculative abstractions, no unused generality.
- Writes basic tests as part of building the module, not as a follow-up
  task.
- Reports back: what was built, how it was tested, and the result,
  including any files created/changed.

### Debugging stage (Ponytail ultra)

- Triggered only when the manager sends a module back after a failed test
  run.
- For that fix-retest cycle only, switch to Ponytail **ultra**.
- After the fix passes the manager's test, run `/ponytail-review` on the
  diff before marking the debugging cycle complete.
- Switch back to Ponytail **lite** before starting the next module.
- Ponytail ultra is never used for first-pass building of a new module —
  only for fixing a module that already failed its DoD check once.

### Build order

1. **Module 1 — Session Manager**: everything else depends on a working,
   reusable Instagram session, so it must exist and pass first.
2. **Module 2 — Business Discovery**: produces the candidate list that
   Module 3 needs as input; does not depend on Module 1 (uses
   Nominatim/Maps, not Instagram), so it can be built in parallel with
   Module 1 if desired, but is sequenced second here for simplicity.
3. **Module 3 — Data Collector**: depends on Module 1 (session) and
   Module 2 (candidate list) both passing, since its DoD test needs real
   handles and a real session.
4. **Module 4 — Rule-Based Analyzer**: depends only on the data *shape*
   Module 3 produces (tested with mocks per its own DoD), not on Modules
   1–3 actually running, but is sequenced after Module 3 so the real
   shape is confirmed first.
5. **Module 5 — Storage & Notification**: depends on Module 4's output
   shape to define CSV columns.
6. **Module 6 — Scheduler**: depends on knowing the full pipeline's
   entry point (built after 1–5 so it can call the real orchestrator path
   in its dry-run simulation).

### Test-fix loop (per module)

1. Manager assigns module N with its DoD (from TRD.md) restated explicitly.
2. Worker builds module N + its tests in lite mode, reports back.
3. Manager runs the module's test suite and/or live check per its DoD.
4. **Pass** → mark `passed` on the checklist, move to module N+1.
   **Fail** → mark `failed (reason)`, switch to ultra, send worker the
   exact failure output, increment that module's retry counter.
5. Worker fixes in ultra mode; manager re-runs the same DoD check;
   `/ponytail-review` runs on the diff before the cycle is marked complete.
6. Repeat steps 3–5 until pass or until 4 fix-retest cycles are used for
   that module — at cycle 4's failure, stop and escalate to the human with
   the exact blocker instead of attempting a 5th cycle.
7. Switch back to lite before starting the next module.

### Integration phase

- Begins only when every module's checklist entry reads `passed`.
- Worker wires all modules into `orchestrator.py` per Architecture.md's
  data flow, wrapping every external call in try/except per TRD.md §3.
- Manager runs one full end-to-end dry test (real discovery + real
  Instagram calls against the confirmed test city, or a controlled subset)
  before declaring the project done.
- Project-level DoD (from PRD.md): full pipeline runs without crashing,
  produces a correctly structured CSV, sends a completion Telegram
  notification, and every `unavailable`/`needs_manual_review` field from
  the run is visible in the log and/or notification.

### Final deliverable

- Code organized one file per module (see Architecture.md §2 file list),
  under version control locally (git init in this directory, even without
  a remote) so the build has real commit history per module.
- `README.md` covering: setup steps, one-time manual Instagram login
  instructions, cron install commands, how to check logs, how to
  re-trigger login after session expiry, how to switch Ponytail modes.
- A final manager summary at project completion: what was built, what
  passed, what's flagged for manual review, known limitations, and total
  fix-retest cycles used per module.
