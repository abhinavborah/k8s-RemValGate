# Sanity check of the merged work from 0237c2c to b6fa293

Audit date: 2026-10-31. During the audit no code, issue or pull request was changed. One side
effect on the local clone: `git fetch --prune` deleted remote-tracking refs of branches already
deleted on GitHub (at least `origin/pr88`, `origin/pr97`, `origin/pr99`, `origin/pr103` and
`origin/pr105`; the full list was not captured). This report and the two story drafts are in
`docs/sanity-check/`.

After the drafts were approved, these changes were made on GitHub (section 16):
- issues #119 and #120 were created, as sub-issues of #25 and #36;
- the Tickets lists in the bodies of epics #3 and #5 gained one line each;
- the board items of #119 and #120 got their Priority and Size.

## 1. Scope and method

- Range: `0237c2c^..b6fa293`, that is every commit reachable from `b6fa293` (the head of `main`,
  merge of #115) and not from `e083441` (the first parent of `0237c2c`). `0237c2c` itself is
  included. 50 commits.
- Merged pull requests reviewed: 6. Five merged into `main` (#102, #107, #106, #113, #115) and one,
  #105, merged into the branch of #102, which then merged into `main`.
- Pull requests #101, #103 and #104 also merged on 2026-10-30, but before `0237c2c`, so they are
  outside the range.
- Sources of truth. What was built: the code, tests, configs and committed records at `b6fa293`.
  What was required: the acceptance criteria of the issues, `docs/brd.md` (version 1.8) and the
  proposal. The proposal was read from the submitted version 2 PDF (8 pages); this repository holds
  no proposal source file, and section numbers below follow that PDF. Pull request bodies, commit
  messages and notes were treated as claims and checked against the code where possible.
- Commands run on `b6fa293` in a clean work tree: the four CI steps (section 5), a recomputation of
  the confirmation checksums, `check_seal` on the committed seal record and touch log, greps for
  stubs and markers, an AST scan for duplicate names, and a scan of documented paths. The work tree
  was clean before the drafts were written, and the checks changed no tracked file.

## 2. Conventions found

This repository has no `.github/ISSUE_TEMPLATE` folder. The story format is the one the existing
issues use:

- Title: `[E<n>-S<n>] <phrase>`, with the next free story number in the epic. A split story uses
  `a`, `b`, `c`, `d` (for example `[E3-S2a]`). Epics are `[E<n>] <name>` with `## Goal` and
  `## Tickets`.
- Body sections, in order:
  - `## User Story`: "As a group member, I want ..., so that ...".
  - `## Acceptance Criteria`: experiment and gate stories open with an `Eval block:` line, then
    `Scenario`, `Given`, `When`, `Then` and `And` bullets.
  - `## Dependencies`: one `Blocked by:` line with a comma list of `#N Title`, or `none` in
    lowercase. Closed blockers are kept. Gate stories add a `**Gate:**` line.
  - `## Notes`: `Source: BRD R<n>, proposal <roman section>.`, then bullets.
  - `## Meta`: Epic, Phase, Priority, Size, Type.
- Scales: Size XS, S, M, L, XL. Priority P0, P1. Phase M1, M2, M3.
- Labels: one epic label `E<n>: <epic name>` and one type label `type: <infra | harness |
  experiment | gate | decision | delivery>`. There are no priority or size labels.
- Milestones: `M1: Mid-submission` (due 2026-10-30), `M2: Final submission` (due 2026-11-15) and
  `M3: Project evaluations` (due 2026-11-19). No milestone is titled `M1` alone.
- Links: each story is a native sub-issue of its epic, and a split story's parts are sub-issues of
  the story. Each `Blocked by:` entry is mirrored by a native blocked-by link. The epic's `Tickets`
  list carries one line per story. Its checkboxes stay unticked, also in the closed epics #1 and #2.
- Project board: the fields Status, Priority and Size mirror the Meta section. The in-range stories
  agree: the closed ones are Done, and Priority and Size equal their Meta values.

Where the requested template differs from the repository:

- No existing story has a `Gap of:` line. The drafts add it, as requested, under `Blocked by:`.
  GAP-01 spans several stories, so its Dependencies also carry a `Related:` line naming the others
  (#110, #111, #112, #117). Existing stories hold only the `Blocked by:` line there.
- The requested Notes line has an em dash before the file reference. Committed files here carry no
  em dash (BRD 7.4), so the drafts use a comma there.
- Notes cite the BRD by requirement and the proposal by roman section number, as existing stories
  do.
- The two gap stories extend closed stories, so they follow the pattern of the parts of #24. Their
  titles take the parent's number with a letter (`[E3-S3a]`, `[E5-S1a]`). Each is a native
  sub-issue of its parent story, nested under the parent's line in the epic's Tickets list. Each
  keeps `Blocked by: none`, since every prerequisite is closed.

## 3. Traceability

| Issue | Pull request (merge) | Commits | Files changed (the pull request's own list) |
|---|---|---|---|
| #71 [E3-S10] Author the three confirmation problems, and #25 [E3-S3] Seal the confirmation slice (gate G1) | #102 (`0237c2c`), `Closes #71` and `Closes #25` | its own: `e4cb86c`, `2dbc2b1`, `edf088d`, `cc99e2c`, `c781c34`, `27a8240`, `48bf03b`, `71ccc18`; with #105: `4634af7` and the commits below | 16: `.gitignore`, `README.md`, `docs/confirmation_problems.md`, `docs/development_problems.md`, `runs/confirmation_seal.json`, `runs/touch_log.jsonl`, `scripts/seal_confirmation.py`, `scripts/validate_overlay_problems.py`, `src/remvalgate/harness/manifest.py`, `src/remvalgate/harness/slices.json`, `src/remvalgate/overlay/__init__.py`, `src/remvalgate/overlay/confirmation.py`, `src/remvalgate/overlay/dev_problems.py`, `src/remvalgate/overlay/fixes.py`, `tests/harness/test_manifest.py`, `tests/test_overlay.py` |
| #25 [E3-S3] Seal the confirmation slice (gate G1) | #105 (`4634af7`, into the branch of #102; GitHub recorded no closing reference) | `ac14b95`, `469de57`, `03d5d7b`, `441aa8e`, `e726ed3`, `e0fc94b`, `4c6ebbc`, `41b6d86`, `4a89bbf` | 9: `.gitignore`, `README.md`, `docs/confirmation_problems.md`, `runs/confirmation_seal.json`, `runs/touch_log.jsonl`, `scripts/seal_confirmation.py`, `src/remvalgate/harness/manifest.py`, `src/remvalgate/harness/slices.json`, `tests/harness/test_manifest.py` |
| #50 [E8-S2] Snapshot, apply, readiness check, restore | #107 (`6c3ff53`), `Closes #50` | `1998006`, `09a17bb`, `ae05a66` | 5: `README.md`, `docs/brd.md`, `scripts/run_execution_check.py`, `src/remvalgate/harness/execute.py`, `tests/harness/test_execute.py` |
| #36 [E5-S1] Planner that retrieves and diagnoses | #106 (`78d67c7`), `Closes #36` | `e3b9081`, `7351e9d`, `b7350c3`, `0301932`, `b01d18b`, `ca3bd79`, `ee7706b`, `76bb837`, `e2e39d0`, `8371cba`, `5fd0ccf`, `881acd2`, `0c512af`, `e338bba` | 12: `README.md`, `docs/compute.md`, `scripts/run_planner_dev.py`, `src/remvalgate/overlay/fixes.py`, `src/remvalgate/planner/__init__.py`, `src/remvalgate/planner/chat.py`, `src/remvalgate/planner/loop.py`, `src/remvalgate/planner/telemetry.py`, `tests/planner/test_chat.py`, `tests/planner/test_loop.py`, `tests/planner/test_telemetry.py`, `tests/planner/test_tool_call.py` |
| #110 [E3-S2a] Add three TargetPortMisconfig problems to the pool | #113 (`8219849`), `Closes #110` | `2c5b68f`, `17fdff7`, `efccb32`, `50244b7`, `26b43ff` | 9: `README.md`, `docs/pool_extension.md`, `scripts/validate_overlay_problems.py`, `src/remvalgate/harness/slices.json`, `src/remvalgate/overlay/__init__.py`, `src/remvalgate/overlay/dev_problems.py`, `src/remvalgate/overlay/fixes.py`, `tests/harness/test_manifest.py`, `tests/test_overlay.py` |
| #111 [E3-S2b] Add two ScalePod problems to the pool | #115 (`b6fa293`), `Closes #111` | `32409d6`, `4cde92d`, `458df22`, `df4b27d`, `ecea271` | 8: `README.md`, `docs/pool_extension.md`, `src/remvalgate/harness/slices.json`, `src/remvalgate/overlay/__init__.py`, `src/remvalgate/overlay/dev_problems.py`, `src/remvalgate/overlay/fixes.py`, `tests/harness/test_manifest.py`, `tests/test_overlay.py` |

Counts: 19 commits for #102 with #105, 4 for #107, 15 for #106, 6 for #113 and 6 for #115, each
including its merge commit, 50 in all.

Flags:

- Merged pull requests linked to no issue: none. GitHub recorded no closing reference for #105,
  whose base was the branch of #102; #102 carries `Closes #25`, and #25 lists #102 as its closing
  pull request.
- Issues closed by a pull request whose diff does not touch the relevant code: none.
- Closed issues with no linked pull request: none. Every issue closed on or after 2026-10-30 has
  a closing pull request.
- Open issues whose work appears to have landed already: none. #24 is open with 5 of its 7 problems
  merged; #112 (open pull request #118) and #117 (validation) remain.
- CI: the head commit of each of the six pull requests and each of the five merges into `main`
  passed `test-and-lint`.
- Review: each pull request has review comments from at least one other group member, as BRD 7.4
  asks. None has a formal approval, and the repository requires none.
- Board: #26 is "In progress" while its blocker #24 is open. Its pull request #108 is not merged,
  so no merged code depends on the unfinished pool.

## 4. Acceptance criteria, line by line

MET means the code, tests or committed records at `b6fa293` satisfy the line. Live-cluster steps
were not rerun (section 13); for those the evidence is the committed record.

### #71 [E3-S10] Author the three confirmation problems (closed)

| Criterion | Result | Evidence |
|---|---|---|
| Eval block: Slice: confirmation (created here); threshold: none; touch logged: yes | MET | `src/remvalgate/harness/slices.json` lines 19 to 23; `runs/touch_log.jsonl` lines 5 to 7 |
| Given one new TargetPortMisconfig, ScalePod and AssignNonExistentNode problem each | MET | `overlay/__init__.py` lines 41 to 46; `overlay/dev_problems.py` lines 78 to 91; `overlay/confirmation.py` lines 25 to 38 |
| When each is created and validated with a scripted known-fix command only, never a system run | MET | `scripts/validate_overlay_problems.py` lines 56 to 77 (known fix only) and lines 88 to 89 (refuses `qualitative_eval`); the touch log has only `create-` and `seal-` entries for the three ids |
| Then each is logged as a creation touch and its identifier and checksum are recorded | MET | `runs/touch_log.jsonl` lines 5 to 7; checksum table in `docs/confirmation_problems.md` lines 23 to 27; `tests/test_overlay.py` lines 306 to 308 |
| And each definition lives in the overlay, is registered through the registration step, with no change inside the AIOpsLab submodule | MET | `overlay/__init__.py` lines 25 to 63; `git diff 0237c2c^ b6fa293 -- external/AIOpsLab` is empty; `tests/test_overlay.py` lines 262 to 299 |

### #25 [E3-S3] Seal the confirmation slice (gate G1) (closed)

| Criterion | Result | Evidence |
|---|---|---|
| Eval block: Slice: confirmation (sealed here); threshold: none, qualitative only; touch logged: yes | MET | `runs/touch_log.jsonl` lines 8 to 10 (`seal-` touches at 2026-10-30T11:51:21Z) |
| Given the three authored confirmation problems | MET | as for #71 |
| When their identifiers and checksums are recorded and the slice is marked sealed | MET | `runs/confirmation_seal.json` (pin `576c3f0`, commit `41b6d86`, sealed 2026-10-30T11:51:21Z); the three checksums recomputed at `b6fa293` equal the record |
| Then no system run has touched them | MET | the touch log has no run entry for a `conf-` id; `harness/manifest.py` lines 297 to 299 refuse every confirmation run; test `test_confirmation_run_refused_even_when_sealed` |
| And the touch log shows the slice as sealed, to be opened once at the end | MET | `seal-` touches; `check_seal` requires them (`manifest.py` lines 416 to 423); opening is left to #59 |
| And the pool extension (#24) starts only after this story | MET | first commits of #113 (`2c5b68f`, 2026-10-30 17:03 UTC) and #115 (`32409d6`, 2026-10-31) are after the seal and after #102 merged (12:09 UTC); pool runs are refused until the seal holds (`manifest.py` lines 317 to 318) |
| (no line) The committed seal is checked in CI | not a criterion | see GAP-01: `check_seal` passes on the committed files at `b6fa293`, but no test runs it on them |

### #50 [E8-S2] Snapshot, apply, readiness check, restore (closed)

| Criterion | Result | Evidence |
|---|---|---|
| Failed check restores: Given a snapshot and an applied command; When the readiness check or a problem probe fails; Then the snapshot is restored and the case is escalated | MET | `harness/execute.py` lines 94 to 96 and 110 to 116; `Execution.escalated` lines 54 to 57; test `test_a_failed_check_restores_then_escalates` (`tests/harness/test_execute.py` lines 91 to 104) |
| Out-of-scope fault: Given RevokeAuth or UserUnregistered; When a check fails; Then the system escalates without restoring and harm is still measured | MET | `harness/snapshot.py` lines 34 to 35; `execute.py` lines 94 to 99; test lines 107 to 115. No development problem exists for these types, so the unit test is the only check |
| Passing check keeps the change: Given the checks pass; When the loop ends; Then the change stays and the outcome is recorded | MET | `execute.py` lines 111 to 125; test lines 82 to 88. "Recorded" means the returned `Execution.result`; writing it to a run log is left to the run scripts |
| Design gap in the Notes (an applied, restored and escalated candidate could not be recorded) | resolved | `applied_result(..., restore_attempted=...)` in `harness/outcome.py` lines 55 to 81, per the #84 decision in BRD R2.4 |

### #36 [E5-S1] Planner that retrieves and diagnoses (closed)

| Criterion | Result | Evidence |
|---|---|---|
| Eval block: Slice: none yet; threshold: none; touch logged: only when run on the pool | MET | no planner entry in `runs/touch_log.jsonl`; `docs/compute.md` lines 261 to 265 |
| Given an injected fault on a development problem and the retriever interface (stub in development, configuration (iii) before any pool run) | MET | `scripts/run_planner_dev.py` lines 64 to 68 and 182; `retrieval/stub.py` lines 89 to 95 refuse any problem outside development, so no pool run can use the stub; test `test_a_problem_outside_development_is_refused_before_any_model_call` |
| When the planner runs with exec_shell, get_logs, get_metrics and get_traces | MET | `planner/loop.py` line 38; four-tool runs on `8371cba` recorded in `docs/compute.md` lines 340 to 404. Their trace summaries were not filtered by time (lines 381 to 390); see unverified concern 3 |
| Then it returns a diagnosis and one candidate command | MET | `loop.py` lines 229 to 239; test `test_diagnoses_with_a_read_only_call_then_returns_one_command`; 7 of 9 four-tool runs returned one (2 hit the turn cap) |
| And the planner size is fixed here and recorded | MET | `docs/compute.md` lines 242 to 252 (Qwen3.5-4B, marked as one member's choice); `run_planner_dev.py` line 63; manifests record the model |
| And prompt iteration uses development problems only | MET | `run_planner_dev.py` line 199 limits `--problem` to the three development ids; stub refusal as above |
| (record) `docs/compute.md` compares the planner time with the per-rollout budget | wrong | see GAP-02 |

### #110 [E3-S2a] Add three TargetPortMisconfig problems to the pool (closed)

| Criterion | Result | Evidence |
|---|---|---|
| Eval block: Descriptive only; pool creation touches no result | MET | no pool touch was added |
| Given the pinned AIOpsLab commit and the sealed confirmation slice (#25) | MET | pin `576c3f0` unchanged; seal holds at `b6fa293` |
| When three problems are added, one each on compose-post-service, home-timeline-service and user-timeline-service | MET | `overlay/dev_problems.py` lines 94 to 103; `overlay/__init__.py` lines 36 to 38; `tests/test_overlay.py` lines 171 to 195 |
| Then the pool gains `-4`, `-5`, `-6` and holds 6 problems of this type | MET | `harness/slices.json` lines 13 to 15; `tests/harness/test_manifest.py` line 176 |
| And each is an overlay entry built from `K8STargetPortMisconfigMitigation` with the service as argument, with a known fix and a test entry, and the submodule is unchanged | MET | `overlay/fixes.py` lines 41 to 43; tests as above; submodule pointer unchanged |
| And no edit touches `checks.py` or a class or factory the checksum covers, so the checksums are unchanged and gate #25 stays open | MET at `b6fa293` | no commit after `41b6d86` touches `checks.py` or `confirmation.py`; recomputed checksums equal the seal. Checked by hand only; see GAP-01 |

### #111 [E3-S2b] Add two ScalePod problems to the pool (closed)

| Criterion | Result | Evidence |
|---|---|---|
| Eval block: Descriptive only | MET | no pool touch was added |
| Given the pinned commit and the sealed slice | MET | as for #110 |
| When two problems are added, each a small subclass of `ScalePodSocialNetMitigation` that sets `faulty_service` after the base constructor, on a service no other ScalePod problem uses | MET | `overlay/dev_problems.py` lines 33 to 42 (`text-service`, `post-storage-service`, recorded as group-confirmed in a comment on #111, 2026-10-31); `tests/test_overlay.py` lines 198 to 214 and 231 to 248. The base task reads `faulty_service` only when it injects, recovers and evaluates, so setting it after the constructor is sound |
| Then the pool gains `-2` and `-3` and holds 3 problems of this type | MET | `harness/slices.json` lines 16 to 17; `tests/harness/test_manifest.py` line 177 |
| And each is an overlay entry with a known fix and a test entry, submodule unchanged | MET | `overlay/fixes.py` lines 44 to 51 |
| And no edit touches what the checksum covers | MET at `b6fa293` | as for #110 |

### Related open stories

- #24 [E3-S2] Extend the problem pool to 16 (open). "The primary pool has 16 problems across 5
  types": NOT MET, 14 at `b6fa293` (TargetPortMisconfig 6, RevokeAuth 2, UserUnregistered 2,
  ScalePod 3, AssignNonExistentNode 1); #112 adds the last 2. "Each new problem injects, is solvable
  by a known command, and resets cleanly": NOT MET yet, moved to #117. The overlay and submodule
  criteria are MET for the 5 merged problems. Tracked by open issues, so not a gap.
- #117 [E3-S2d] Validate the pool extension problems on a cluster (open): not started; no creation
  touch exists for the five merged pool problems, as expected.
- #26 [E3-S4] Grouped 4-fold assignment with invariant check (open, blocked by #24). No fold code
  is merged (pull request #108 is open), so at `b6fa293` each requested check is NOT MET: 4 problems
  per fold, TargetPortMisconfig split 2, 2, 1, 1, RevokeAuth and UserUnregistered instances in
  different folds, ScalePod and AssignNonExistentNode instances in three different folds, all
  candidates of one incident on one side, and an automated invariant check. This is unstarted work
  under an open issue, so not a gap.

## 5. Tests and build

| Command (as in `.github/workflows/ci.yml`) | Result on `b6fa293` |
|---|---|
| `uv sync --locked` | ok, 107 packages resolved |
| `uv run pytest` | 828 passed in 22.30 s |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 68 files already formatted |

- No type checker is configured: `pyproject.toml` defines pytest and ruff only, and CI runs the four
  commands above.
- Local uv was 0.11.12; CI pins 0.13.5. Both accept the lockfile.
- Criteria with no automated test, by design: the live validation of the confirmation problems
  (#71), the order of the pool extension after the seal (#25, a process criterion), the planner size
  record (#36), and the live hand checks of #50 and #36. BRD 7.2 keeps cluster rollouts out of CI.
- Criterion checked by hand only although a test could check it: "the confirmation checksums are
  unchanged and the gate of #25 stays open" (#110, #111, #112, #117). See GAP-01.

## 6. Merge integrity

- Duplicate or parallel implementations: none found. The planner's `tool_call` reuses `exec_shell`
  and `action` in `overlay/fixes.py`.
- Conflicting values for one parameter: none. The settle window is 120 s in
  `harness/harm.py` line 24 and in `overlay/dev_problems.py` line 50; Rmax is 3 in
  `harness/manifest.py` line 46; the target port is one constant in `overlay/fixes.py` line 11.
- Dead code: none. `fixes.select` lost its `created` argument in #102, and its only caller was
  updated.
- Broken imports: none. Every file compiles; `scripts/run_planner_dev.py` imports names that exist
  in `scripts/run_dev_end_to_end.py` (lines 52 to 77). The cluster scripts need
  `aiopslab/config.yml` to import, as their docstrings say.
- Renamed paths: every path written in the README, the docs and the code exists.
- Conflict markers, TODO, FIXME, NotImplementedError and bare `pass` stubs in added lines: none.
- Duplicate top-level names, which would hide an earlier test after a merge: none.
- The two pool pull requests (#113, #115) edited the same files; the merge commit `ecea271` kept
  both, and the README, `slices.json`, the overlay, the fixes and the tests agree on 14 pool
  problems.

## 7. Consistency with the proposal and the BRD

| Item | Finding |
|---|---|
| Fault types and problem counts | Pool 14 of 16 planned (section 4); confirmation 3; development 3; singletons not yet listed (R3.6, M2). Matches proposal section IX as far as it is built |
| Primary pool, singletons, sealed confirmation slice | Confirmation slice sealed and checked before every pool run; singleton list empty until R3.6 |
| K, R and Rmax | Rmax 3 enforced by the manifest (`manifest.py` lines 46 and 160), as proposal section XII. K is not fixed yet (#72); the planner runs record K = 1 |
| 4-fold grouped CV, calibration split from each fold's training problems at incident level | Not built yet (#26 open; calibration is M2) |
| Allow-list and sandbox applied identically to every arm | The planner (`loop.py` line 236), the execution loop (`snapshot.py` lines 91 to 93) and the earlier baselines load the same policy file |
| Rollback scope | Snapshot and restore only for TargetPortMisconfig, ScalePod and AssignNonExistentNode; RevokeAuth and UserUnregistered escalate without a restore (`snapshot.py` lines 34 to 35), as proposal section VII and BRD R2.2 |
| Ineffective and harmful rates kept apart | Separate outcome classes `APPLIED_INEFFECTIVE` and `APPLIED_HARMFUL` (`outcome.py`) |
| Harm by pre and post pod-status differencing | `detect_harm` with a pre-action status, read after the 120 s window and before any restore (`execute.py` lines 98 to 112), as BRD R2.3 and section 9 |
| Baseline 5 trained on the same triples as the gate | Not built yet (M2) |
| No LLM judge in labelling | Every script that calls an AIOpsLab eval refuses to run with `qualitative_eval` on |
| Escalation scoring | An escalation is never recovered (`execute.py` lines 114 to 116), as proposal section XI; BRD section 9 records the case with nothing restored as proposed |

## 8. Leakage and evaluation hygiene

- The confirmation slice is sealed, every confirmation run is refused, and the only code that reads
  its ids is the seal, the checksum, the validation script and the registry. No training or tuning
  code exists yet.
- The planner, its prompt iteration and the E8-S2 hand check used development problems only; no
  touch was logged, and none was due.
- Tests that name pool or confirmation ids check definitions statically and build no problem, so
  they are not evaluation-set touches (BRD 7.2).
- Incident-level splits and the R rule are not built yet.

## 9. Dependencies

Every blocker of the six in-range stories was closed before the story's pull request merged: #71
after #13, #15, #23 and #65; #25 after #15, with #71 closed by the same merge, after the authored
problems; #36 after #13, #16, #66 and #75; #50 after #17, #18 and #84; #110 and #111 after #13,
#23, #25 and #65. No merged code assumes the 16-problem pool. The fold work that needs it (#26,
pull request #108) is unmerged.

## 10. Gaps

| ID | Title | Category | Parent | Epic | Priority | Size | Type | Fix needed |
|---|---|---|---|---|---|---|---|---|
| GAP-01 | [E3-S3a] Test that the committed confirmation seal matches the definitions and the pin | Missing test | #25 | E3 | P0 | S | harness | yes, created as #119 |
| GAP-02 | [E5-S1a] Correct the planner time against the per-rollout budget in the compute record | Docs | #36 | E5 | P0 | XS | experiment | yes, created as #120 |
| GAP-03 | README status says the system is not written | Docs | #61 (open) | E10 | P0 | | | yes, covered by open #61, no new story |
| GAP-04 | Pool extension validation commands differ from the plan in #117 | Inconsistency between merged docs and an open story | #117 (open) | E3 | P0 | | | yes, covered by open #117, no new story |

### GAP-01: the committed seal is never checked in CI

- Evidence:
  - The seal tests build a synthetic seal record with a stand-in checksum
    (`tests/harness/test_manifest.py` lines 120 to 133).
  - The only test on the real definitions compares them with the table in
    `docs/confirmation_problems.md` (`tests/test_overlay.py` lines 301 to 308).
  - `scripts/seal_confirmation.py` (lines 43 to 50) compares the table with the definitions only at
    sealing time.
  - No test reads `runs/confirmation_seal.json`. The record is checked only when `start_run`
    begins a pool run (`harness/manifest.py` lines 317 to 318 and 424 to 427).
- Failure scenario: a pull request edits a class the checksum covers, for example
  `AssignNonExistentNodeAvailableMitigation`, and updates the table to match.
  - CI passes.
  - The next pool run is refused with "changed since the seal (gate G1)".
  - Reopening the gate then needs a reseal, which is a group decision on #25.
- Current state: at `b6fa293`, `check_seal` passes on the committed files, so gate G1 is open now.
  Pull request #118 (open) edits `overlay/dev_problems.py` and `overlay/fixes.py`, two of the files
  the checksum reads.
- Fixed looks like: a CI test that runs `check_seal` with the shipped registry, the committed touch
  log and the pinned commit. It passes at `b6fa293`, fails on a temporary copy with one sealed
  checksum changed, and builds no confirmation problem.
- Size S: one test of about 20 lines. It reuses `check_seal` and `pinned_commit`, plus one
  negative case on copies in a temporary folder.
- Draft: `docs/sanity-check/stories/GAP-01-seal-record-ci-test.md`, created as #119.

### GAP-02: `docs/compute.md` says the planner's mean time is over the budget when it is under

- Evidence: commit `5fd0ccf` (pull request #106), `docs/compute.md` lines 391 to 392: "Each planner
  run now takes 140 s of model time on average, which is over the 2.6 minutes (156 s) the plan
  leaves per rollout".
  - The table on line 367 gives a mean of 140 s and a slowest run of 261 s.
  - The 156 s is the plan's 5.0 minutes per rollout (lines 201 to 204) minus the measured harness
    mean of 144.8 s (line 177).
  - 140 s is under 156 s. With the about 45 s workload of the same sentence (about 185 s), and for
    the slowest run alone, the time is over.
  - The two-tool section states the same comparison correctly for its slowest run (line 304).
- Impact: low. The conclusion for #72 (about 19 minutes for eight separate runs at K = 8) still
  holds. But the K and R decisions (#53, #72) read this record.
- Fixed looks like: the sentence states that the mean is under 156 s, and that the mean plus the
  workload, and the slowest run, are over it. Every number stays the same.
- Size XS: one sentence in one file.
- Draft: `docs/sanity-check/stories/GAP-02-compute-record-budget-comparison.md`, created as #120.

### GAP-03: README status (covered by open #61)

- Evidence: `README.md` lines 10 to 14 say "Planning. ... the system itself is not written yet".
  The range merged the planner (#106) and the execution loop (#107), after the harness and
  baselines 1 and 2 merged earlier.
- The line was not changed in the range. Separately, the Layout block lists `scripts/` twice
  (`README.md` lines 21 and 24), since `597c646` (2026-10-27, outside the range).
- Why no new story: #61 [E10-S2] covers the README that reviewers follow for the submission. A
  criterion could be added to #61, with approval.

### GAP-04: pool extension validation plan (covered by open #117)

- Evidence: the Validation section of `docs/pool_extension.md` (lines 51 to 65, written in #113 and
  #115) gives one command per sub-issue. It also says the results are recorded "once each run is
  done".
  - The notes of #110 and #111 say validation moved to #117.
  - #117's criterion runs `scripts/validate_overlay_problems.py` once with all seven ids, after
    #112 merges.
  - Following the doc now would add five creation touches, which #117's run would then repeat.
- Why no new story: #117 requires its result to be recorded in that same section, which settles
  the text.

## 11. False alarms checked

| Suspected problem | Why it is not a gap |
|---|---|
| #106 edited `overlay/fixes.py` after the seal | The checksum covers only the known fixes of the three confirmation ids (`overlay/confirmation.py` lines 66 to 79); the recomputed checksums equal the seal |
| The confirmation definitions changed between validation and seal | `git diff e4cb86c 41b6d86` shows no change to `dev_problems.py`, `checks.py` or `overlay/__init__.py`; `fixes.py` changed only in the `select` helper |
| #105 closes nothing | Its base was the branch of #102; #102 carries `Closes #25` |
| The E8-S2 hand check breaks `text-service` (`scripts/run_execution_check.py` lines 53 to 56), a pool ScalePod target since #115 | The check builds development problems only and logs no touch; a touch is a use of a pool problem, not of a service (BRD section 2 and 7.2) |
| "Escalation is never recovered" departs from the plan | It follows proposal section XI ("escalations counted as not recovered") |
| A passing check that reads harmful keeps the change | Proposal section VII and BRD R8.2 restore only on a failed check |
| `k` means two things in one planner run | The manifest's `k = 1` is K, the candidates (`run_planner_dev.py` line 235); the transcript's `config.k = 3` is the retrieval depth (`loop.py` lines 195 and 207), as the proposal's lowercase k in section VI. No criterion is broken; worth naming apart when #72 records K |
| No property-based test in the new test files | BRD 7.2 asks for a few such tests across the repository; `tests/test_overlay.py` and `tests/harness/test_manifest.py` have them |
| Unticked epic checkboxes | Repository convention, also in the closed epics |
| No formal pull request approvals | Not required here; each pull request was reviewed in comments by another group member |
| A path scan hit on `runs/touch_log.json` | An artifact of the scan pattern, which matched inside `runs/touch_log.jsonl` |

## 12. Unverified concerns

1. The confirmation validation ran at commit `e6e599b` (`docs/confirmation_problems.md` line 101).
   That commit is in no history that can be fetched. That the validated and the sealed definitions
   are identical rests on that statement. From `e4cb86c` onwards the definitions did not change.
2. The live output of the E8-S2 hand check (2026-10-30) exists only in the body of #107. No log on
   shared storage is referenced from the repository.
3. The nine four-tool planner runs used trace summaries that were not filtered by time
   (`docs/compute.md` lines 381 to 390), and they were not repeated.
   - The filter added afterwards (`planner/telemetry.py` line 21) is unit tested
     (`tests/planner/test_telemetry.py` line 55) but has not run live.
   - The filter compares the injection time in microseconds (`scripts/run_planner_dev.py` line
     159) with the `start_time` column, which AIOpsLab fills from each span's `startTime`
     (`external/AIOpsLab/aiopslab/observer/trace_api.py` line 282). That the two use the same unit
     was not checked.
4. `scripts/run_planner_dev.py` line 238 writes `"nodes": 2` into the manifest instead of reading
   it from the cluster, as `scripts/run_pilot.py` lines 229 and 253 do. There is no evidence that
   the value was wrong.
5. The planner accepts only the five pool fault types (`planner/loop.py` lines 30 to 36 and 176 to
   177), so it can never name a singleton type correctly. This matters for the exploratory
   singleton check (R3.6, R9.5), which has to decide how to treat it.
6. The planner's retrieval query is its first `kubectl get deployments,pods` listing
   (`run_planner_dev.py` line 173, `loop.py` line 207). #29 has not fixed the form of the stored
   query, so it is open whether the intrinsic retrieval metrics will use the query the system uses.
7. The SHA-256 values of the run archives on shared storage (`docs/compute.md`) were not checked
   against the files.

## 13. Group decisions that merged work carries as unconfirmed

These are marked as unconfirmed in the repository, so they are not gaps. They are listed so the
group can confirm or change them.

1. All three confirmation problems share `social-graph-service`, which keeps services free for the
   pool extension but makes the three outcomes less independent (#102,
   `docs/confirmation_problems.md` lines 32 to 41).
2. An escalation is never recovered, also when nothing was restored (#107, BRD section 9).
3. A check that passes while harm reads harmful keeps the change, recorded as `APPLIED_HARMFUL`
   (#107, open decision 2). No test pins this case.
4. The planner is Qwen3.5-4B with thinking on by default, one member's choice (#106,
   `docs/compute.md` lines 244 and 286).

The ScalePod services of #111 are recorded as group-confirmed in a comment on #111 (2026-10-31).

## 14. Outside the range (noted, not audited)

- #27 has milestone M2 but Phase M1; #54 has milestone M1 but Phase M2.
- The note in `src/remvalgate/harness/toolcall_schemas.json` still says the AIOpsLab commit is not
  pinned (#69, closed 2026-10-27), and `tests/harness/test_toolcall.py` line 59 asserts that text.
  The signatures in the file match `external/AIOpsLab/aiopslab/orchestrator/actions/base.py` at the
  pin (lines 34, 80, 115 and 168).
- Open pull requests #108 and #109 reference their stories with "Part of", which closes nothing.
- Milestone `M1: Mid-submission` was due on 2026-10-30 and has 19 open issues. The final
  submission is due on 2026-11-15.

## 15. Could not run or verify

- The cluster hand scripts (`validate_overlay_problems.py`, `run_execution_check.py`,
  `run_planner_dev.py`): no cluster and no `aiopslab/config.yml` here.
- Model runs: no model server.
- Files on shared storage and their checksums.
- Commit `e6e599b`.
- Whether tests were written before the code they test: code and tests land in the same commits,
  so history cannot show the order.

## 16. Issues created

Created on 2026-10-31, after the drafts were approved:

| Gap | Issue | Milestone | Labels | Parent | Board |
|---|---|---|---|---|---|
| GAP-01 | #119 [E3-S3a] Test that the committed confirmation seal matches the definitions and the pin | `M1: Mid-submission` | `E3: Dataset and problem pool`, `type: harness` | native sub-issue of #25, nested under #25 in the Tickets list of #3 | Backlog, P0, S |
| GAP-02 | #120 [E5-S1a] Correct the planner time against the per-rollout budget in the compute record | `M1: Mid-submission` | `E5: Planner and tool calling`, `type: experiment` | native sub-issue of #36, nested under #36 in the Tickets list of #5 | Backlog, P0, XS |

- The bodies are the two drafts in `docs/sanity-check/stories/`.
- Neither story has a blocker, so no blocked-by link was added.
- No comment was posted on #25 or #36.
