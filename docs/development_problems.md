# Development problems

Story E3-S8, requirement R3.8. Designation only: nothing was instantiated, injected or run, and
no evaluation slice is touched. The problems are authored and validated in E3-S9 (#70).

Status: proposed by a group member on 2026-10-28, pending group confirmation (BRD review
point 9).

Development problems serve prompt iteration, smoke tests, the end to end run of E1-S3 (#13) and
the G2 timing pilot. They are kept apart from the evaluation pool, the confirmation slice and the
singleton types. Their runs are not evaluation-set touches.

## Designated problems

One mitigation problem for each pool fault type whose state is a Kubernetes object, all on
Social Network (`test-social-network`), each on a service that no pool or singleton problem uses.

| Problem id | Type | Based on | Target service |
|---|---|---|---|
| `dev-k8s_target_port-misconfig-mitigation-1` | TargetPortMisconfig | `K8STargetPortMisconfigMitigation` | `user-mention-service` |
| `dev-scale_pod_zero_social_net-mitigation-1` | ScalePod | `ScalePodSocialNetMitigation` | `media-service` |
| `dev-assign_to_non_existent_node_social_net-mitigation-1` | AssignNonExistentNode | `AssignNonExistentNodeSocialNetMitigation` | `unique-id-service` |

`K8STargetPortMisconfigMitigation` takes the service as an argument. The ScalePod and
AssignNonExistentNode base tasks set `faulty_service = "user-service"` in their constructor, so
#70 adds a small subclass for each that sets the service.

The ids start with `dev-`, which no upstream id does, so the overlay registration never clashes.
The ids are in `src/remvalgate/harness/slices.json` under `development`, beside the 9 current
pool problems and the 5 singleton problems. The split follows BRD R3.2 (`docs/brd.md`), which
puts ScalePod and AssignNonExistentNode in the pool; the inventory (`docs/problem_inventory.md`)
still labels those two as singletons and counts 7. The
registry loader refuses an id in more than one slice, and a test loads the committed registry.

## Reserved services

The pool extension (#24) and the confirmation problems (#71) do not use `user-mention-service`,
`media-service` or `unique-id-service`, for any fault type.

TargetPortMisconfig rewrites `targetPort` 9090 to 9999 (`aiopslab/generators/fault/inject_virtual.py:32-42`
at the pinned commit). At apps commit `8038be6`, 11 Social Network charts set `targetPort: 9090`:
`compose-post-service`, `home-timeline-service`, `media-service`, `post-storage-service`,
`social-graph-service`, `text-service`, `unique-id-service`, `url-shorten-service`,
`user-mention-service`, `user-service` and `user-timeline-service`. The pool already uses 3 of
them and needs 3 more, and the confirmation slice needs 1. With the reservation above, 5 remain
for those 4: `compose-post-service`, `home-timeline-service`, `social-graph-service`,
`url-shorten-service` and `user-timeline-service`. The MongoDBAuthMissing singleton deletes the
`url-shorten-service` pods, so that service is the last choice.

## Not designated

- RevokeAuth and UserUnregistered. Their fault scripts are mounted only through the ConfigMaps
  `failure-admin-geo` and `failure-admin-rate`, and the revoke and recover scripts exist only for
  `geo` and `rate` (`aiopslab/service/apps/hotelres.py:27-66` at the pinned commit). Both
  databases are already used by the pool. A development instance
  would need new scripts in the overlay. Prompt iteration on these two types therefore uses no
  problem of their own.
- Detection, localization and analysis tasks of existing injections. Each shares its fault and
  service with a pool or singleton problem, so tuning on it would tune on evaluation incidents.
  They also cannot be fixed with a known command, which #13 and #70 need.
- Singleton types. They are set aside for the exploratory check (R3.6).

## Open point

The eval block of E3-S8 and E3-S9 says "touch logged: yes". The harness logs only pool and
confirmation uses (`LOGGED` in `src/remvalgate/harness/manifest.py`, and the Runs section of the
README), so development runs write no touch entry. This designation runs nothing and writes no
entry. Which of the two is intended is for the group to decide.
