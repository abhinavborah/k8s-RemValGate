## User Story
As a group member, I want the four-tool planner record in `docs/compute.md` to compare the planner's time with the per-rollout budget correctly, so that the cost of sampling K candidates from the planner's diagnosis (#72) is judged from a correct record.

## Acceptance Criteria
- Eval block: Descriptive only; the correction runs nothing and touches no evaluation set
- Scenario: The comparison matches the recorded numbers
- Given the four-tool table in `docs/compute.md` (a mean of 140 s of model time per run, the slowest 261 s) and the 156 s the plan leaves per rollout (5.0 minutes planned, minus the measured harness mean of 144.8 s)
- When the sentence on lines 391 to 392 is corrected
- Then it no longer says that the 140 s mean is over the 156 s
- And it says that the mean is under the 156 s, while the mean with the about 45 s workload and the slowest run are each over it
- And the conclusion for #72 (about 19 minutes for eight separate runs at K = 8) is kept, and no recorded number changes
- And the change adds no em dash

## Dependencies
Blocked by: none
Gap of: #36 Planner that retrieves and diagnoses

## Notes
Source: BRD R5.1 and R9.1, proposal VII and XII. Sub-issue of #36, as an extension of that closed story.
Found in sanity check from 0237c2c: commit `5fd0ccf` (pull request #106), `docs/compute.md` lines 391 to 392.
- The sentence reads: "Each planner run now takes 140 s of model time on average, which is over the 2.6 minutes (156 s) the plan leaves per rollout".
- The 156 s comes from lines 201 to 204 (about 832 rollouts in 69 hours serial, 5.0 minutes each) and line 177 (harness mean 144.8 s). Lines 231 to 232 call it the 2.6 minutes the plan leaves.
- The two-tool section makes the same comparison correctly for its slowest run (line 304).

## Meta
- Epic: E5
- Phase: M1
- Priority: P0
- Size: XS
- Type: experiment
