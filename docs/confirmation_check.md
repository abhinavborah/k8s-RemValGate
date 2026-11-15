# Confirmation check

Story E9-S8 (#59), R9.6. The confirmation slice opened once, with the system of BRD 1.20 (3c). A
qualitative check with no test and no inferential weight: 3 problems, 6 runs.

## Setup

- Opened 2026-11-14 at 23:26:15 UTC (one open touch per problem), then 6 runs from 23:26 to about
  23:54 UTC, from commit `cba8a1d`, clean tree, AIOpsLab `576c3f0`, on the local kind cluster
  (2 nodes). Every manifest names that commit and a clean tree.
- Model: Qwen3.5-4B (`qwen/qwen3.5-4b`), GGUF Q8_0, context 32768, thinking on, served by LM
  Studio on the run host; the 3c settings of #165 (temperature 0.7, top-p 0.8, top-k 20, presence
  penalty 1.5, turn cap 16).
- Seeds 1000 and 1001 within the one opening (BRD 1.20).
- Retrieval: configuration (iii) over every pool incident and the runbook, with no record of the
  confirmation problems (BRD 1.20).
- The outcomes were first read on 2026-11-15, after the group agreed that this opening counts.
- The slice was closed on 2026-11-15 at 07:36:50 UTC (one close touch per problem), after the
  outcomes were read, so no further confirmation run is admitted. Runs that open the slice now
  close it when they end.

## Results

| Problem | Seed 1000 | Seed 1001 |
|---|---|---|
| `conf-k8s_target_port-misconfig-mitigation-1` | applied, ineffective | recovered |
| `conf-scale_pod_zero_social_net-mitigation-1` | recovered | recovered |
| `conf-assign_to_non_existent_node_social_net-mitigation-1` | recovered | recovered |

Under the both-seeds rule of BRD 1.20 (#56), a problem counts only when it recovers at both seeds:
2 of 3 problems, since `k8s_target_port` recovered at seed 1001 only. This is not the clean 3 of 3
that #59 frames (p = 0.25). Per run, descriptively: 5 of 6 runs recovered (seed 1000: 2 of 3, seed
1001: 3 of 3). Every run sent one command, the allow-list admitted each, and none was harmful (no
newly unhealthy workload).

- `scale_pod_zero`: `kubectl scale deployment social-graph-service --replicas=1` at both seeds.
- `assign_to_non_existent_node`: a JSON patch that removes the Deployment's `nodeSelector`, at both
  seeds.
- `k8s_target_port`, seed 1000: a merge patch on the Service with `ports: [{targetPort: 9090}]`.
  A merge patch replaces the whole ports list, so the port number was lost and the API server
  rejected it (`spec.ports[0].port: Invalid value: 0`); the fault remained. At seed 1001 a JSON
  patch replaced `/spec/ports/0/targetPort` with 9090 and recovered.

## Limits

- 6 runs on 3 problems, each problem of a type seen in the pool: no rate is meaningful, and the
  result says only that 3c handled these three problems at least once each.
- No baseline ran on the slice, so it gives no comparison.
- The run files are on the shared drive under `run-logs/confirmation59_20261115/`, as
  `confirmation59_20261115.tar.gz` (SHA-256 `63ba67a3...`) with `SHA256SUMS`.
