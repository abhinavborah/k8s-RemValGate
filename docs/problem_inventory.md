# Inventory of the current mitigation problems

Story E3-S1, requirement R3.1. Descriptive only: read from source, nothing was instantiated,
injected or run, and no evaluation slice is touched.

Source: AIOpsLab at the pinned commit `576c3f080c7ae2459a972afd4a2f8d5f01828430`. Every
`file:line` below is at that commit. Paths are relative to `external/AIOpsLab/aiopslab/`.

- `reg` is `orchestrator/problems/registry.py`.
- `apps` is `external/AIOpsLab/aiopslab-applications/`, a nested repository pinned by AIOpsLab at
  `8038be6` (clean in the checkout). Paths into it are written `apps/...`. Its `astronomy-shop`
  folder is a further nested repository at `68d88623`.
- `prob` is the problem file under `orchestrator/problems/`, giving the base task class and the
  mitigation class.
- `app` is `generators/fault/inject_app.py`, `virt` is `generators/fault/inject_virtual.py`,
  `otel` is `generators/fault/inject_otel.py`.

## Rollback classes

Rollback scope is not an AIOpsLab field. It is derived from what each `inject_*` function changes,
since that is the state a pre-action snapshot must capture. Classes were agreed by a group member
for this inventory and are not yet a group decision.

| Class | Meaning |
|---|---|
| A. K8s object, patch | A field of an existing object is changed in place. A restore patches it back. |
| B. K8s object, recreate | The Deployment is deleted and applied again, so a restore must recreate it, not patch it. |
| C. K8s object, update | A Deployment or ConfigMap is updated in place. A restore updates it back. |
| D. Helm release | State is changed by `Helm.upgrade` on the whole release. A restore would need to go through Helm, not a raw object snapshot (design note, not verified). |
| E. Data in PVC | State is MongoDB users and roles in `/data/db` on a PersistentVolumeClaim. No Kubernetes object snapshot captures it. No automated rollback. |
| F. Namespace-wide | The whole namespace is deleted and redeployed. Whether PersistentVolumes are kept is per the AIOpsLab docstring, not verified. |

## Inventory

| # | Problem id | Type | Injector `fault_type` | Declared target service | Objects actually changed | Namespace | Class | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | `k8s_target_port-misconfig-mitigation-1` | TargetPortMisconfig | `misconfig_k8s` | `user-service` | Service `user-service`: ports with `targetPort` 9090 set to 9999 | `test-social-network` | A | reg:47, prob k8s_target_port/target_port.py:20,164, virt:32-53 |
| 2 | `k8s_target_port-misconfig-mitigation-2` | TargetPortMisconfig | `misconfig_k8s` | `text-service` | Service `text-service`, same change | `test-social-network` | A | reg:59, same classes, virt:32-53 |
| 3 | `k8s_target_port-misconfig-mitigation-3` | TargetPortMisconfig | `misconfig_k8s` | `post-storage-service` | Service `post-storage-service`, same change | `test-social-network` | A | reg:71, same classes, virt:32-53 |
| 4 | `revoke_auth_mongodb-mitigation-1` | RevokeAuth | `revoke_auth` | `mongodb-geo` | MongoDB `admin` user loses `readWrite` on `geo-db` (script run in the mongodb-geo pod); `geo` pods deleted | `test-hotel-reservation` | E | reg:89, prob revoke_auth/revoke_auth.py:20,159, app:32-94 |
| 5 | `revoke_auth_mongodb-mitigation-2` | RevokeAuth | `revoke_auth` | `mongodb-rate` | Same for `rate-db`; `rate` pods deleted | `test-hotel-reservation` | E | reg:101, same classes, app:32-94 |
| 6 | `user_unregistered_mongodb-mitigation-1` | UserUnregistered | `storage_user_unregistered` | `mongodb-geo` | MongoDB `admin` user dropped (script run in the mongodb-geo pod); `geo` pods deleted | `test-hotel-reservation` | E | reg:114, prob storage_user_unregistered/storage_user_unregistered.py:20,165, app:97-147 |
| 7 | `user_unregistered_mongodb-mitigation-2` | UserUnregistered | `storage_user_unregistered` | `mongodb-rate` | Same for the rate database; `rate` pods deleted | `test-hotel-reservation` | E | reg:126, same classes, app:97-147 |
| 8 | `auth_miss_mongodb-mitigation-1` | MongoDBAuthMissing (singleton) | `auth_miss_mongodb` | `url-shorten-mongodb` | Helm release `social-network` upgraded with TLS values `requireTLS` and key paths for `url-shorten-mongodb`, and the command passes only `-f values.yaml` and the TLS `--set` values (no `--reuse-values`, `service/helm.py:161-176`); `url-shorten-service` pods deleted; Deployment `url-shorten-mongodb` restarted | `test-social-network` | D | reg:78, prob auth_miss_mongodb/auth_miss_mongodb.py:20,166, app:176-260 |
| 9 | `misconfig_app_hotel_res-mitigation-1` | MisconfigAppHotelRes (singleton) | `misconfig_app` | `geo` | Deployment `geo`: container `hotel-reserv-geo` image set to `yinfangchen/geo:app3` | `test-hotel-reservation` | C | reg:133, prob misconfig_app/misconfig_app_hotel_res.py:20,159, app:150-173 |
| 10 | `scale_pod_zero_social_net-mitigation-1` | ScalePod (singleton) | `scale_pods_to_zero` | `user-service` | Deployment `user-service` scaled to 0 replicas | `test-social-network` | C | reg:138, prob scale_pod/scale_pod_social_net.py:21,171, virt:56-73 |
| 11 | `assign_to_non_existent_node_social_net-mitigation-1` | AssignNonExistentNode (singleton) | `assign_to_non_existent_node` | `user-service` | Deployment `user-service` deleted and applied again with `nodeSelector kubernetes.io/hostname: extra-node` | `test-social-network` | B | reg:143, prob assign_non_existent_node/assign_non_existent_node_social_net.py:21,174, virt:76-108 |
| 12 | `astronomy_shop_kafka_queue_problems-mitigation-1` | KafkaQueueProblems (singleton) | feature flag `kafkaQueueProblems` | `kafka` | ConfigMap `flagd-config`, key `demo.flagd.json`: flag `kafkaQueueProblems` `defaultVariant` set to `on`; Deployment `flagd` restarted | `astronomy-shop` | C | reg:185, prob kafka_queue_problems/kafka_queue_problems.py:13,101, otel:13-52 |
| 13 | `redeploy_without_PV-mitigation-1` | RedeployWithoutPV (singleton) | none (`inject_redeploy_without_pv`) | none (namespace-wide) | Namespace `test-hotel-reservation` deleted and the application redeployed; PersistentVolumes kept per the AIOpsLab docstring, not verified | `test-hotel-reservation` | F | reg:200, prob redeploy_without_pv/redeploy_without_pv.py:17,158, virt:111-122 |
| 14 | `wrong_bin_usage-mitigation-1` | WrongBinUsage (singleton) | `wrong_bin_usage` | `profile` | Deployment `profile` deleted and applied again with container `command` changed from `profile` to `geo` | `test-hotel-reservation` | B | reg:205, prob wrong_bin_usage/wrong_bin_usage.py:17,157, virt:125-170 |

Namespaces come from the application metadata files (`service/metadata/`). The mongodb scripts
are in `generators/fault/script/` (`FAULT_SCRIPTS`, `paths.py:27`), mounted into the pods from
ConfigMaps `failure-admin-geo` and `failure-admin-rate` (`service/apps/hotelres.py:14,27-66,110-114`). `/data/db` is mounted from `geo-pvc` and `rate-pvc`
(`apps/hotelReservation/kubernetes/geo/mongodb-geo-deployment.yaml`,
`apps/hotelReservation/kubernetes/rate/mongodb-rate-deployment.yaml`).
The hotel reservation application is deployed from `apps/hotelReservation/kubernetes`, not from its
Helm chart (`service/apps/hotelres.py:67-71`).

## Count by type

| Type | Count | Proposal version 2 |
|---|---|---|
| TargetPortMisconfig | 3 | 3 |
| RevokeAuth | 2 | 2 |
| UserUnregistered | 2 | 2 |
| Singletons | 7 | 7 |
| Total | 14 | 14 |

The registry holds exactly these 14 ids containing `-mitigation-` at the pin. No mismatch with
the proposal. The seven singletons are rows 8 to 14. The proposal names only ScalePod and
AssignNonExistentNode; the other five type names are taken from AIOpsLab class names.

## Where recover is not the inverse of inject

The restore state recorded here is what the AIOpsLab `recover_*` code does. It is not a snapshot.

- Row 10: recover runs `kubectl scale ... --replicas=1`. It does not restore a recorded count
  (virt:66-73). The Social Network chart sets `global.replicas: 1` (`apps/socialNetwork/helm-chart/socialnetwork/values.yaml:2`);
  a per-service override was not checked.
- Row 9: recover sets the image to `yinfangchen/hotelreservation:latest`, which is the image in
  `apps/hotelReservation/kubernetes/geo/geo-deployment.yaml:31` (app:166-173).
- Rows 11 and 14: recover deletes and applies the Deployment again, so a restore recreates the
  object and does not patch it (virt:95-108, 149-170).
- Row 12: recover sets the flag to `off` (otel:54-87). The chart's rendered example
  `apps/astronomy-shop/charts/opentelemetry-demo/examples/bring-your-own-observability/rendered/flagd-config.yaml:73` ships `defaultVariant: off`; the live ConfigMap was not read.
- Row 13: recover is `app.cleanup()`, which removes the application and restores nothing
  (virt:120-122).
- Rows 6 and 7: recover recreates the `admin` user with `userAdminAnyDatabase` and `readWrite` on
  the target database, which is the role set the init script `generators/fault/script/k8s-geo-mongo.sh:23-28` creates.
  The rate script was not read.
- Row 8: injection and recovery both go through `Helm.upgrade` on the `social-network` release.
  Install-time `--set` values are not passed again on upgrade. On ARM, `socialnet.py:51-59`
  installs with a `media-frontend` image override, which the upgrade may revert (not checked).

## Observations

- Proposal version 2 limits rollback to the three fault types whose state is a Kubernetes object
  (TargetPortMisconfig, ScalePod, AssignNonExistentNode) and declares RevokeAuth and
  UserUnregistered out of scope. Rows 4 to 7 are consistent with that, since their state is
  MongoDB data in a PVC.
- By the source, rows 9, 12 and 14 also change only Kubernetes objects. They are singleton types
  set aside from the evaluation pool, so this does not change the proposal's three-type claim.
  Whether to extend snapshot coverage to them is a decision for the group.
- Rows 1, 10 and 11 all target `user-service`.
- The BRD risk section says "11 to 14 problems today". The proposal says 11 in the paper and 14
  in the repository. This inventory confirms 14 at the pin.
