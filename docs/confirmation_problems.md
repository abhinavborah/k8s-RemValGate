# Confirmation problems

Story E3-S10 (#71), BRD section 7.1 and R3.3 (creation part). The three problems of the
confirmation slice, authored before any pool rollout so that they exist to be sealed by gate G1
(#25). Nothing may be tuned or prompt-iterated on them. They are opened once, at the end (R9.6,
#59).

Status: authored, validated and sealed. Sealed on 2026-10-30 at 11:51:21 UTC, at commit
`41b6d86`: the record is `runs/confirmation_seal.json`, with one `seal-` touch per problem in
`runs/touch_log.jsonl` (see Seal). Validated on 2026-10-29 (UTC) on a two-node amd64
kind cluster: for each problem the eval failed with the fault injected, passed after the known fix,
and the fault was recovered. The three creation touches are the `conf-` entries in
`runs/touch_log.jsonl`. No system, model or LLM judge ran on them.

## Problems

All three are on Social Network (`test-social-network`) and target `social-graph-service`, which
no pool, development or singleton problem uses. Definitions are in
`src/remvalgate/overlay/dev_problems.py`, their declared data in
`src/remvalgate/overlay/confirmation.py`, and the ids are in `src/remvalgate/harness/slices.json`
under `confirmation`.

| Problem id | Type | Based on | Known fix | Checksum (sha256) |
|---|---|---|---|---|
| `conf-k8s_target_port-misconfig-mitigation-1` | TargetPortMisconfig | `K8STargetPortMisconfigMitigation` | patch `targetPort` back to 9090 | `577e8add11e85922ba724101d3056e94025d6ef68b17b06f4bbb48e699769b22` |
| `conf-scale_pod_zero_social_net-mitigation-1` | ScalePod | `ScalePodSocialNetMitigation` | scale to 1 replica | `d4d90821ceb3dcf86b13be78b9804f408bd3c91c1f1ba9302d69206ed39d6d92` |
| `conf-assign_to_non_existent_node_social_net-mitigation-1` | AssignNonExistentNode | `AssignNonExistentNodeAvailableMitigation` | remove the `nodeSelector` | `0b422bfd8c08960d95973d5006fae2a7f98ec33cef5ec2d993eb3c1211241343` |

The AssignNonExistentNode problem reuses the pool variant's eval, which also needs an available
replica, since the upstream eval passes with the fault in place (`docs/problem_inventory.md`).

## Service choice

TargetPortMisconfig needs a service whose `targetPort` is 9090. Five such services are free of
pool, development and singleton problems (`docs/development_problems.md`), and the pool extension
(#24) needs three of them. Three separate confirmation services would leave #24 two. All three
problems therefore share one service, as the pool's rows on `user-service` do, which leaves
`compose-post-service`, `home-timeline-service` and `user-timeline-service` for #24, with
`url-shorten-service` unused. The cost is that the three outcomes are less independent than on
three services. Infrastructure deployments (MongoDB, Redis, `nginx-thrift`) were not used, since
breaking them makes most pods unhealthy.

## Checksum

Each checksum is the sha256 of a canonical JSON record (sorted keys, no spaces, ASCII) holding the
problem id, the slice, the overlay factory it builds, the fault type, the class it is built from,
the service, the namespace, the known-fix kubectl arguments, the pinned AIOpsLab commit
(`576c3f080c7ae2459a972afd4a2f8d5f01828430`), the source of the factory and of each of its base
classes defined in `src/remvalgate/overlay/dev_problems.py`, and the source of
`src/remvalgate/overlay/checks.py`. Sources are read with universal newlines, so the checksum is the
same on every system. `confirmation.checksum` computes it, and a test checks the table above
against it, so a change to a definition or to the code that defines it fails the tests until the
table is updated.

Code under the AIOpsLab pin is covered by the pin alone. Any edit to `checks.py` changes all three
checksums, even one that does not affect these problems, so such a change is checked by hand
before the table is updated.

## Seal

Gate G1 (#25): no pool run starts until the slice is sealed. `scripts/seal_confirmation.py`, run
once on a clean tree, checks that each problem has its creation touch and no other touch and that
its checksum matches the table above. It then writes `runs/confirmation_seal.json` (the ids and
checksums, the AIOpsLab pin, the commit and the time) and appends one `seal-` touch per problem to
`runs/touch_log.jsonl`. It refuses a second seal. Nothing is built, injected or run.

`start_run` refuses a pool run unless the seal record covers exactly the confirmation ids in
`slices.json`, the touch log holds one `seal-` touch per id at the sealed time, and each checksum,
recomputed under the run's AIOpsLab commit, equals the sealed one. A record written by hand,
without the touches, does not open the gate. An edit to a definition, or a new AIOpsLab pin,
therefore closes the gate again.

Reopening it is a group decision recorded on #25, not a routine step:
`scripts/seal_confirmation.py --reseal` then replaces the record and appends new `seal-` touches
at the new time. The earlier record stays in git history and its touches stay in the log. A reseal
is still refused if any problem has run.

`start_run` refuses every confirmation run, sealed or not, so nothing touches the slice before it
is opened. #59 adds the one path that opens it, once, after the analysis is final.

## Validation

`scripts/validate_overlay_problems.py` takes the problem ids to validate. Run with no ids, it
validates every overlay problem except the confirmation slice, so a routine revalidation never
touches it. A confirmation problem that already has a creation touch is refused, so the command
below cannot run a second time.

The creation touch is written before the fault is injected, so a validation that fails or is
interrupted still counts as a creation, and the touch log keeps that line: it is append only and is
never edited. Such a problem is not retried under the same id. It is replaced by a new problem with
the next number (for example `-2`), which takes its place in `slices.json` and in the table above
and gets its own creation touch, and the replacement and its reason are recorded on the sealing
issue (#25).

    uv run python scripts/validate_overlay_problems.py conf-k8s_target_port-misconfig-mitigation-1 conf-scale_pod_zero_social_net-mitigation-1 conf-assign_to_non_existent_node_social_net-mitigation-1

Each problem is logged as a creation touch in `runs/touch_log.jsonl`, the fault is injected, the
eval must fail, the known fix is applied, the eval must pass, and the fault is recovered. No
system or model runs and the LLM judge is off. A creation touch is not a system run.

The validation ran at commit `e6e599b`, which is not on this branch. Since then the problem
definitions, their registration, their slice entries and their known fixes are unchanged. What
changed: the checksum record gained the overlay factory and the source fields, so the checksums
differ from those computed then, and `log_creation` gained the refusal of a second creation of a
confirmation problem.
