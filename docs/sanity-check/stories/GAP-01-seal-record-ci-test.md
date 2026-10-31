## User Story
As a group member, I want a test that checks the committed confirmation seal against the current definitions and the AIOpsLab pin on every pull request, so that a change that closes gate G1 fails in continuous integration and not at the next pool run.

## Acceptance Criteria
- Scenario: The committed seal holds
- Given `runs/confirmation_seal.json`, `runs/touch_log.jsonl` and `src/remvalgate/harness/slices.json` as committed
- When `uv run pytest` runs in continuous integration
- Then a test calls `check_seal` with the shipped registry, the committed touch log and the pinned AIOpsLab commit, and it passes
- And the test checks that the `aiopslab` field of the seal record equals the pinned commit
- Scenario: A changed sealed checksum fails the suite
- Given copies of the committed seal record and touch log in a temporary folder, with one sealed checksum changed
- When the same check runs on the copies
- Then it fails with the gate G1 refusal that names the changed problem id
- And neither scenario builds, injects or runs a confirmation problem, so the test is not an evaluation-set touch (BRD 7.2)
- And the committed seal record, the touch log and the confirmation definitions are not edited

## Dependencies
Blocked by: none
Gap of: #25 Seal the confirmation slice (gate G1)
Related: #110 Add three TargetPortMisconfig problems to the pool, #111 Add two ScalePod problems to the pool, #112 Add two AssignNonExistentNode problems to the pool, #117 Validate the pool extension problems on a cluster

## Notes
Source: BRD 7.1 and R3.3, proposal IX. Sub-issue of #25, as an extension of that closed story.
Found in sanity check from 0237c2c: pull requests #105 and #102 (merged in `0237c2c`), `tests/harness/test_manifest.py` lines 120 to 133 and `tests/test_overlay.py` lines 301 to 308.
- The seal tests build a synthetic seal record with a stand-in checksum. The only test on the real definitions compares them with the table in `docs/confirmation_problems.md`. No test reads `runs/confirmation_seal.json`.
- A pull request that edits a definition the checksum covers and updates the table to match passes continuous integration. `start_run` then refuses the next pool run with "changed since the seal (gate G1)" (`src/remvalgate/harness/manifest.py` lines 424 to 427), and reopening the gate needs a reseal, a group decision on #25.
- At `b6fa293`, `check_seal` passes on the committed files, so the gate is open now.
- The criterion "the confirmation checksums are unchanged and the gate of #25 stays open" in #110, #111 and #112, and the seal comparison in #117, are checked by hand today. This test checks them on every pull request.

## Meta
- Epic: E3
- Phase: M1
- Priority: P0
- Size: S
- Type: harness
