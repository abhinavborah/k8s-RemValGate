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

The definitions are in `src/remvalgate/overlay/dev_problems.py`, the known fixes in
`src/remvalgate/overlay/fixes.py`, and the ids in `src/remvalgate/harness/slices.json` under
`pool`. The confirmation checksums cover `checks.py` and the confirmation factories and their bases,
none of which changed, so gate G1 stays open.

## Validation

Pending. `scripts/validate_overlay_problems.py` needs a live cluster with Social Network deployed.
Run it with explicit ids, since with none it also revalidates the development problems and the
pool variant of AssignNonExistentNode, which appends further creation touches:

    uv run python scripts/validate_overlay_problems.py pool-k8s_target_port-misconfig-mitigation-4 pool-k8s_target_port-misconfig-mitigation-5 pool-k8s_target_port-misconfig-mitigation-6

Each problem must fail the eval with the fault injected, pass it after the known fix, and recover
cleanly. The result and the three creation touches are recorded here once the run is done.
