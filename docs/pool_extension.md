# Pool extension

Story E3-S2 (#24), requirement R3.2. The pool grows from 9 to 16 problems with 7 new ones, made in
three sub-issues: #110 (TargetPortMisconfig), #111 (ScalePod) and #112 (AssignNonExistentNode).
Each problem is created by the overlay of this repository, the AIOpsLab submodule is unchanged, and
nothing is tuned or run on the pool here. A creation is not a system run.

Ids take a `pool-` prefix and continue the numbering of their type, so no id clashes with an
upstream id and the type stem in the id stays readable.

## TargetPortMisconfig (#110)

Three problems on Social Network (`test-social-network`), each built from
`K8STargetPortMisconfigMitigation` with the service as argument. The fault rewrites `targetPort`
9090 to 9999, and the known fix patches it back.

| Problem id | Target service | Known fix |
|---|---|---|
| `pool-k8s_target_port-misconfig-mitigation-4` | `compose-post-service` | patch `targetPort` back to 9090 |
| `pool-k8s_target_port-misconfig-mitigation-5` | `home-timeline-service` | patch `targetPort` back to 9090 |
| `pool-k8s_target_port-misconfig-mitigation-6` | `user-timeline-service` | patch `targetPort` back to 9090 |

Four Social Network services with `targetPort` 9090 are free of pool, development and confirmation
problems (`docs/development_problems.md`, `docs/confirmation_problems.md`). These three are used.
`url-shorten-service`, the fourth, stays unused, since the MongoDBAuthMissing singleton deletes its
pods.

## ScalePod (#111)

Two problems on Social Network (`test-social-network`), each a subclass of
`ScalePodSocialNetMitigation` that sets `faulty_service` after the base constructor, since the base
hard-codes `user-service`. The fault scales the deployment to 0 replicas, and the known fix scales it
back to 1 and waits for the rollout.

| Problem id | Class | Target service | Known fix |
|---|---|---|---|
| `pool-scale_pod_zero_social_net-mitigation-2` | `ScalePodTextMitigation` | `text-service` | scale back to 1 replica |
| `pool-scale_pod_zero_social_net-mitigation-3` | `ScalePodPostStorageMitigation` | `post-storage-service` | scale back to 1 replica |

The other ScalePod problems target `user-service` (pool), `media-service` (development) and
`social-graph-service` (confirmation), so no two ScalePod problems share a service. `text-service`
and `post-storage-service` are also the targets of two upstream TargetPortMisconfig problems in the
pool. A service can be shared across fault types, as `user-service` already is in the pool.
`url-shorten-service` stays unused, since the MongoDBAuthMissing singleton deletes its pods.

## AssignNonExistentNode (#112)

Two problems on Social Network (`test-social-network`), each a subclass of
`AssignNonExistentNodeAvailableMitigation` that sets `faulty_service` after the base constructor.
They keep its stricter eval, which also needs an available replica, since the upstream eval passes
with the fault in place (`docs/problem_inventory.md`). The fault sets a `nodeSelector` for a node
that does not exist, and the known fix removes it and waits for the rollout.

| Problem id | Class | Target service | Known fix |
|---|---|---|---|
| `pool-assign_to_non_existent_node_social_net-mitigation-2` | `AssignNonExistentNodeHomeTimelineMitigation` | `home-timeline-service` | remove the `nodeSelector` |
| `pool-assign_to_non_existent_node_social_net-mitigation-3` | `AssignNonExistentNodeUserTimelineMitigation` | `user-timeline-service` | remove the `nodeSelector` |

The other AssignNonExistentNode problems target `user-service` (pool), `unique-id-service`
(development) and `social-graph-service` (confirmation). #112 asks for a service not used by another
problem of this type, so no two share one. Leaving out the development services and
`url-shorten-service`, five services were free of this type:
`compose-post-service`, `home-timeline-service`, `post-storage-service`, `text-service` and
`user-timeline-service`. Each already has a problem of another type in the pool: one each for
`compose-post-service` and the two timeline services, and two each for `text-service` and
`post-storage-service`. The pair above comes from the three with one and was chosen on a literature
review and the application code:

- Both are on the compose-post write path that the workload drives. `ComposePostHandler.h` in the
  Social Network source under the pin writes the user timeline, then the home timeline, and waits
  on both, so the fault shows as failed compose requests with errors in `compose-post-service`, not
  only as Pending pods.
- The two fail at different points. With `user-timeline-service` down, the request stops before the
  home timeline write. With `home-timeline-service` down, the post and the user timeline are written
  first.
- `compose-post-service` is the entry point, so its symptoms sit in the injected service itself, the
  easier case to localize.
- AIOpsLab picks targets so that their dependencies differ and reuses services across fault types
  (arXiv 2501.06706, section 3.3). No paper found rules on these specific services.

The symptoms are read from the code and are checked by the validation in #117. Fold assignment (#26)
goes by fault type, so a service shared across types does not change the folds.

The definitions are in `src/remvalgate/overlay/dev_problems.py`, the known fixes in
`src/remvalgate/overlay/fixes.py`, and the ids in `src/remvalgate/harness/slices.json` under
`pool`. The confirmation checksums cover `checks.py` and the confirmation factories and their bases,
none of which changed, so gate G1 stays open.

## Validation

Pending. `scripts/validate_overlay_problems.py` needs a live cluster with Social Network deployed.
Run it with explicit ids, since with none it revalidates every overlay problem outside the
confirmation slice, which appends further creation touches. TargetPortMisconfig (#110):

    uv run python scripts/validate_overlay_problems.py pool-k8s_target_port-misconfig-mitigation-4 pool-k8s_target_port-misconfig-mitigation-5 pool-k8s_target_port-misconfig-mitigation-6

ScalePod (#111):

    uv run python scripts/validate_overlay_problems.py pool-scale_pod_zero_social_net-mitigation-2 pool-scale_pod_zero_social_net-mitigation-3

AssignNonExistentNode (#112):

    uv run python scripts/validate_overlay_problems.py pool-assign_to_non_existent_node_social_net-mitigation-2 pool-assign_to_non_existent_node_social_net-mitigation-3

Each problem must fail the eval with the fault injected, pass it after the known fix, and recover
cleanly. The result and the creation touches, three for #110, two for #111 and two for #112, are
recorded here once each run is done.
