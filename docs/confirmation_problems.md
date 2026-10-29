# Confirmation problems

Story E3-S10 (#71), BRD section 7.1 and R3.3 (creation part). The three problems of the
confirmation slice, authored before any pool rollout so that they exist to be sealed by gate G1
(#25). Nothing may be tuned or prompt-iterated on them. They are opened once, at the end (R9.6,
#59).

Status: authored and validated, not sealed. Validated on 2026-10-29 (UTC) on a two-node amd64
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
| `conf-k8s_target_port-misconfig-mitigation-1` | TargetPortMisconfig | `K8STargetPortMisconfigMitigation` | patch `targetPort` back to 9090 | `f4f1376ab32df943e42fcd64111a5d0506f85cc6ac330c2164d5ae216325749a` |
| `conf-scale_pod_zero_social_net-mitigation-1` | ScalePod | `ScalePodSocialNetMitigation` | scale to 1 replica | `c5926ffe91f740b3b9da9de6b59ecbc0ccc0aaa5ca0f727a4635fec359a47f4e` |
| `conf-assign_to_non_existent_node_social_net-mitigation-1` | AssignNonExistentNode | `AssignNonExistentNodeAvailableMitigation` | remove the `nodeSelector` | `29136b2b815a1d492045f91d7b124fc1e24d5068b21917447df18ff9b35bd68e` |

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
the service, the namespace, the known-fix kubectl arguments and the pinned AIOpsLab commit
(`576c3f080c7ae2459a972afd4a2f8d5f01828430`). `confirmation.checksum` computes it, and a test
checks the table above against it, so a change to a definition fails the tests until the table is
updated.

It does not cover the source of an inherited eval in this repository, such as
`AssignNonExistentNodeAvailableMitigation.eval`. A change there leaves the checksum unchanged.

## Validation

`scripts/validate_overlay_problems.py` takes the problem ids to validate. Run with no ids, it
validates every overlay problem except the confirmation slice, so a routine revalidation never
touches it. A confirmation problem that already has a creation touch is refused, so the command
below cannot run a second time.

    uv run python scripts/validate_overlay_problems.py conf-k8s_target_port-misconfig-mitigation-1 conf-scale_pod_zero_social_net-mitigation-1 conf-assign_to_non_existent_node_social_net-mitigation-1

Each problem is logged as a creation touch in `runs/touch_log.jsonl`, the fault is injected, the
eval must fail, the known fix is applied, the eval must pass, and the fault is recovered. No
system or model runs and the LLM judge is off. A creation touch is not a system run.
