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

Rollback scope is not an AIOpsLab field. The class records where the fault state lives. Proposal
version 2 snapshots the objects a candidate command touches and restores that pre-action state
after a failed check, so the class tells whether that location can be restored from a snapshot at
all. The classes were agreed by a group member for this inventory and are not yet a group
decision.

| Class | Meaning |
|---|---|
| A. Service spec field | The fault is a field of a Service spec. Restorable from an object snapshot. |
| B. Deployment spec field | The fault is a field of a Deployment spec: replicas, image, `nodeSelector` or `command`. Restorable from an object snapshot. |
| C. ConfigMap data | The fault is a key in a ConfigMap. Restorable from an object snapshot. |
| D. Helm-managed values | The fault is a value of a Helm release. Which objects it renders into was not traced. |
| E. Data in PVC | The fault is MongoDB users and roles in `/data/db` on a PersistentVolumeClaim. No Kubernetes object records it. No automated rollback. |
| F. Whole namespace | The fault is the deletion and redeploy of the whole namespace. Whether PersistentVolumes are kept is per the AIOpsLab docstring, not verified. |

## Inventory

| # | Problem id | Type | Injector `fault_type` | Declared target service | Objects actually changed | Namespace | Class | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | `k8s_target_port-misconfig-mitigation-1` | TargetPortMisconfig | `misconfig_k8s` | `user-service` | Service `user-service`: ports with `targetPort` 9090 set to 9999 | `test-social-network` | A | reg:47, prob k8s_target_port_misconfig/target_port.py:20,164, virt:32-53 |
| 2 | `k8s_target_port-misconfig-mitigation-2` | TargetPortMisconfig | `misconfig_k8s` | `text-service` | Service `text-service`, same change | `test-social-network` | A | reg:59, same classes, virt:32-53 |
| 3 | `k8s_target_port-misconfig-mitigation-3` | TargetPortMisconfig | `misconfig_k8s` | `post-storage-service` | Service `post-storage-service`, same change | `test-social-network` | A | reg:71, same classes, virt:32-53 |
| 4 | `revoke_auth_mongodb-mitigation-1` | RevokeAuth | `revoke_auth` | `mongodb-geo` | MongoDB `admin` user loses `readWrite` on `geo-db` (script run in the mongodb-geo pod); `geo` pods deleted | `test-hotel-reservation` | E | reg:89, prob revoke_auth/revoke_auth.py:20,159, app:32-94 |
| 5 | `revoke_auth_mongodb-mitigation-2` | RevokeAuth | `revoke_auth` | `mongodb-rate` | Same for `rate-db`; `rate` pods deleted | `test-hotel-reservation` | E | reg:101, same classes, app:32-94 |
| 6 | `user_unregistered_mongodb-mitigation-1` | UserUnregistered | `storage_user_unregistered` | `mongodb-geo` | MongoDB `admin` user dropped (script run in the mongodb-geo pod); `geo` pods deleted | `test-hotel-reservation` | E | reg:114, prob storage_user_unregistered/storage_user_unregistered.py:20,165, app:97-147 |
| 7 | `user_unregistered_mongodb-mitigation-2` | UserUnregistered | `storage_user_unregistered` | `mongodb-rate` | Same for the rate database; `rate` pods deleted | `test-hotel-reservation` | E | reg:126, same classes, app:97-147 |
| 8 | `auth_miss_mongodb-mitigation-1` | MongoDBAuthMissing (singleton) | `auth_miss_mongodb` | `url-shorten-mongodb` | Helm release `social-network` upgraded with TLS values `requireTLS` and key paths for `url-shorten-mongodb`, and the command passes only `-f values.yaml` and the TLS `--set` values (no `--reuse-values`, `service/helm.py:161-176`); `url-shorten-service` pods deleted; Deployment `url-shorten-mongodb` restarted. The chart reads `tls` values in `apps/socialNetwork/helm-chart/socialnetwork/charts/url-shorten-mongodb/values.yaml`, `apps/socialNetwork/helm-chart/socialnetwork/templates/_baseDeployment.tpl` and `apps/socialNetwork/helm-chart/socialnetwork/templates/configs/mongo/mongod.tpl` | `test-social-network` | D | reg:78, prob auth_miss_mongodb/auth_miss_mongodb.py:20,166, app:176-260 |
| 9 | `misconfig_app_hotel_res-mitigation-1` | MisconfigAppHotelRes (singleton) | `misconfig_app` | `geo` | Deployment `geo`: container `hotel-reserv-geo` image set to `yinfangchen/geo:app3` | `test-hotel-reservation` | B | reg:133, prob misconfig_app/misconfig_app_hotel_res.py:20,159, app:150-173 |
| 10 | `scale_pod_zero_social_net-mitigation-1` | ScalePod | `scale_pods_to_zero` | `user-service` | Deployment `user-service` scaled to 0 replicas | `test-social-network` | B | reg:138, prob scale_pod/scale_pod_social_net.py:21,171, virt:56-73 |
| 11 | `assign_to_non_existent_node_social_net-mitigation-1` | AssignNonExistentNode | `assign_to_non_existent_node` | `user-service` | Deployment `user-service` deleted and applied again with `nodeSelector kubernetes.io/hostname: extra-node` | `test-social-network` | B | reg:143, prob assign_non_existent_node/assign_non_existent_node_social_net.py:21,174, virt:76-108 |
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

| Type | Count | Proposal version 2 | Slice (BRD R3.2) |
|---|---|---|---|
| TargetPortMisconfig | 3 | 3 | pool |
| RevokeAuth | 2 | 2 | pool |
| UserUnregistered | 2 | 2 | pool |
| ScalePod | 1 | 1, as a singleton | pool |
| AssignNonExistentNode | 1 | 1, as a singleton | pool |
| Other singletons | 5 | 5 | singleton |
| Total | 14 | 14 | |

The registry holds exactly these 14 ids containing `-mitigation-` at the pin. No mismatch with
the proposal. Proposal version 2 counts rows 8 to 14 as seven singletons. BRD R3.2 moves ScalePod
and AssignNonExistentNode (rows 10 and 11) into the 16-problem pool, so the singleton types are
rows 8, 9 and 12 to 14. The proposal names only ScalePod and
AssignNonExistentNode; the other five type names are taken from AIOpsLab class names.

## Where recover is not the inverse of inject

The restore state recorded here is what the AIOpsLab `recover_*` code does. It is not a snapshot.

- Row 10: recover runs `kubectl scale ... --replicas=1`. It does not restore a recorded count
  (virt:66-73). The Social Network chart sets `global.replicas: 1` (`apps/socialNetwork/helm-chart/socialnetwork/values.yaml:2`);
  a per-service override was not checked.
- Row 9: recover sets the image to `yinfangchen/hotelreservation:latest`, which is the image in
  `apps/hotelReservation/kubernetes/geo/geo-deployment.yaml:31` (app:166-173).
- Rows 11 and 14: AIOpsLab's own inject and recover delete the Deployment and apply it again
  (virt:76-108, 125-170). A pre-action restore of a candidate's change does not need that.
- Row 12: recover sets the flag to `off` (otel:54-87). The chart's rendered example
  `apps/astronomy-shop/charts/opentelemetry-demo/examples/bring-your-own-observability/rendered/flagd-config.yaml:73` ships `defaultVariant: off`; the live ConfigMap was not read.
- Row 13: recover is `app.cleanup()`, which removes the application and restores nothing
  (virt:120-122).
- Rows 6 and 7: recover recreates the `admin` user with `userAdminAnyDatabase` and `readWrite` on
  the target database, which is the role set the init scripts create
  (`generators/fault/script/k8s-geo-mongo.sh:23-28` and `k8s-rate-mongo.sh:23-28`, which differ only
  in `TARGET_DB`).
- Row 8: injection and recovery both go through `Helm.upgrade` on the `social-network` release.
  Install-time `--set` values are not passed again on upgrade. On ARM, `socialnet.py:51-59`
  installs with a `media-frontend` image override, which the upgrade may revert (not checked).
  After the upgrade the injector deletes the `url-shorten-service` pods and restarts the
  `url-shorten-mongodb` Deployment (app:197-215). Whether restoring the objects alone takes effect
  was not checked.
- Row 12: after the ConfigMap update the injector restarts the `flagd` Deployment (otel:48-50).
  Whether restoring the ConfigMap alone takes effect was not checked.

## Pool eval override for row 11

Upstream, the eval of row 11 passes with the fault in place. The fault leaves `user-service` with
one Pending pod, which has no container statuses, and `pods_unhealthy_reasons` skips such pods
(`orchestrator/tasks/mitigation.py:101`). The eval's other check only asks that some pod name
contains the service name, which the Pending pod satisfies. A candidate that does nothing would
therefore count as recovered, which would distort the oracle labels (BRD R3.5), the
recoverable-problem count of gate G3 and the outcome classes. Read from the code, not yet seen on
the cluster.

The pool therefore holds `pool-assign_to_non_existent_node_social_net-mitigation-1` in place of
row 11's id. It is an overlay problem with the same fault and service, and an eval that also
requires at least 1 available replica of `user-service`. The submodule is unchanged.

Status: changed by a group member on 2026-10-28 in response to review, pending group
confirmation. If the group keeps the upstream eval, the pool entry reverts to row 11's id.

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
