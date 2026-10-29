# Compute record

GPU environment and model hosting for the planner and gate (story E1-S4, BRD R1.4, proposal
section XII), the harness timing of one end to end run (story E1-S3), and the five-rollout timing pilot (story E9-S1, gate G2, last section).
Every number comes from the smoke run unless a source is given.

## Environment

Kaggle notebooks, chosen by the story's assignee for the smoke test. The group has not yet agreed
the compute plan for training (BRD section 9, open questions).

| Item | Value |
|---|---|
| GPU | NVIDIA Tesla T4, 2 visible per session; the model runs on one |
| Memory limit | 14.56 GiB total per T4, as reported by torch |
| Cost | none (free tier) |
| Hours | 30 GPU-hours per week on the assignee's account, 1 hour used (Kaggle account settings, read 2026-10-27); the documented weekly quota is "30 hours or sometimes higher depending on demand and resources" ([Kaggle docs](https://www.kaggle.com/docs/efficient-gpu-usage), read 2026-10-26) |
| Session length | 12 hours of execution per CPU or GPU session, "at time of writing" ([Kaggle docs](https://www.kaggle.com/docs/notebooks), Technical Specifications, read 2026-10-27) |
| Software | torch 2.11.0+cu128, CUDA 12.8, transformers 5.20.2, accelerate 1.14.0, torchvision 0.26.0+cu128, pillow 13.0.0, as printed by the run and pinned in the script's install line; Python 3.13 (Kaggle image, seen 2026-10-26, not printed by the script) |

## Smoke run, 2026-10-27

`scripts/smoke_qwen.py` loads `Qwen/Qwen3.5-4B` at revision
`851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` on GPU 0 and generates one completion with thinking
turned off. Torch reported bfloat16 support, so the run used bfloat16. The T4 has compute
capability 7.5 and no native bfloat16: torch counts emulated support by default, so this was
emulated bfloat16. Training dtype and speed on a T4 stay open (see Limits).

| Measure | Value |
|---|---|
| Date (UTC) | 2026-10-27T11:34:04 |
| Load time | 60.2 s (weights downloaded in the session, 53 s of it) |
| Generation | 44 new tokens in 4.3 s, greedy, so no seed, from a one-sentence prompt |
| Peak allocated | 8.53 GiB (torch allocator only) |
| Peak reserved | 8.54 GiB (torch allocator only) |
| Device used after generation | 8.67 GiB, under the 14.56 GiB total; device wide, so it includes the CUDA context, but it is a reading after generation, not a peak |
| Result | PASS: the script checks only that the text has at least five words, no thinking tags and a letter; relevance was checked by eye |

An earlier run of the previous script version on 2026-10-26 gave the same memory peaks.

Raw output:

```
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
date_utc: 2026-10-27T11:34:04+00:00
model: Qwen/Qwen3.5-4B@851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a
gpu: Tesla T4, visible GPUs: 2
compute_capability: 7.5, native_bf16: False
gpu_total_gib: 14.56
torch: 2.11.0+cu128, cuda: 12.8
transformers: 5.20.2, dtype: torch.bfloat16
accelerate: 1.14.0, torchvision: 0.26.0+cu128, pillow: 13.0.0
Fetching 2 files: 100%|██████████| 2/2 [00:53<00:00, 26.83s/it]
Loading weights: 100%|██████████| 723/723 [00:02<00:00, 297.35it/s]
[transformers] `causal_conv1d_fn` is falling back to its reference PyTorch implementation because `causal_conv1d` is not installed. This is correct but much slower; install `causal_conv1d` for the optimized kernel.
[transformers] `chunk_gated_delta_rule` is falling back to its reference PyTorch implementation because `flash-linear-attention` is not installed. This is correct but much slower; install `flash-linear-attention` for the optimized kernel.
[transformers] `causal_conv1d_update` is falling back to its reference PyTorch implementation because `causal_conv1d` is not installed. This is correct but much slower; install `causal_conv1d` for the optimized kernel.
[transformers] `fused_recurrent_gated_delta_rule` is falling back to its reference PyTorch implementation because `flash-linear-attention` is not installed. This is correct but much slower; install `flash-linear-attention` for the optimized kernel.
load_s: 60.2, generate_s: 4.3, new_tokens: 44
seed: n/a (do_sample=False, greedy)
peak_allocated_gib: 8.53, peak_reserved_gib: 8.54
device_used_after_generate_gib: 8.67
completion: A Kubernetes readiness probe determines whether a pod is ready to accept new traffic by repeatedly executing a health check (such as an HTTP request or TCP connection) until the application responds successfully or the probe times out.
result: PASS
```

Transformers warned that the Gated DeltaNet layers fell back to reference PyTorch code because
`flash-linear-attention` and `causal_conv1d` were not installed. Output is correct but slower.
Whether those kernels install and run on a T4 is unchecked.

## Budget against the plan

- The submitted proposal (section XII) and BRD section 9 both estimate 150 to 240 GPU-hours of
  LoRA fine-tuning.
- The BRD window runs 27 days from 2026-10-19, to 2026-11-15. From this run that leaves 19 days,
  about three quota weeks, so at most about 90 GPU-hours per Kaggle account at the base quota.
- These are T4 hours. The estimate assumes a 16 to 24 GB card of unstated speed, and the T4 runs
  bfloat16 through emulation, so this comparison is not like for like until a T4 training step
  timing exists. LoRA runs must also fit the 12-hour session limit or resume from a checkpoint.
- One account does not cover the estimate. Covering it needs two or more members' accounts,
  another GPU source, or the cuts listed in BRD section 9. This is open for the group.

## Limits

- The peak memory is for a short prompt and 44 new tokens. Planner and gate prompts are much
  longer and will use more.
- This run shows inference fits. It does not show that LoRA training of the 4B model fits in
  14.56 GiB, or which dtype and speed training gets on a T4; that needs its own check.
- The Kaggle image runs Python 3.13, but the project requires 3.11 or 3.12, so
  `pip install -e .` fails there. Code that imports the package on Kaggle needs a 3.12
  environment or a change to the supported range.
- Weights are downloaded per session (about a minute here); they are not stored in git.

## Harness timing, one development problem, 2026-10-28 (UTC)

Story E1-S3 (#13), BRD R1.3. `scripts/run_dev_end_to_end.py` ran
`dev-k8s_target_port-misconfig-mitigation-1` on `user-mention-service` once, on a fresh two node
kind cluster (kind v0.33.0, Kubernetes server v1.32.1, Social Network with all 27 pods Ready) at
AIOpsLab `576c3f080c7ae2459a972afd4a2f8d5f01828430`, on repository commit `1ebee77`. After the
redeploy, the node image digest and the 8 container image digests all matched the list in the
README. The manifest says `dirty: true` because two untracked handoff files were in the work
tree. The agent was a script, so no model ran and no tokens were spent.

The script changed after `1ebee77`: its checks now raise an error and its helpers moved into
the overlay package. The commands it sends are the same, and the recorded run stays the
reference.

Seconds, wall clock (`time.perf_counter`), one run of each cycle:

| Phase | Run with the known fix | Reset only |
|---|---|---|
| Inject the fault | 6.1 | 6.1 |
| Fix and submit through `exec_shell` and `submit` | 0.1 | none |
| Evaluate (settle wait included) | 0.1 | none |
| Reset (`recover_fault`, then all pods Ready) | 2.9 | 2.9 |
| Sum of the phases | 9.1 | 8.9 |
| Wall, from building the problem to the last check | 9.4 | 9.2 |

The phase times are rounded and the sums are taken from the unrounded values.

The evaluator reported success, and in both cycles the specs and labels of the Services and
Deployments after the reset equalled the snapshot taken before the injection, with all pods Ready.
The first cycle resets a cluster the fix had already repaired, so the second cycle is the one that
tests the reset.

The run manifest is `e2e-dev-k8s_target_port-misconfig-mitigation-1-20261028t203314z.json`, with
SHA-256 `decf844a80f89c3b162e79b6e74285978e7010cbda37b8e2f4d8a69b6fb1fcf7`, and its timings file
of the same name ending `.timings.json` has SHA-256
`90942bbda5cca3e6c2e5b168c979265b025baaa966a527ae985477ca8c026708`. Both are in `runs/manifests/`,
which git ignores, and were copied on 2026-10-28 to the shared Google Drive folder
`k8s-RemValGate`, under `manifests/`, with the session trace. A read-first file at its root lists them.

The times are a floor for the G2 timing pilot, not an estimate of a rollout. They exclude model
latency, the one-off OpenEBS and Prometheus install, and the Social Network deploy.
The evaluator's settle wait is included, and it returned on its first poll because the Service
was already fixed. The ScalePod and AssignNonExistentNode fixes wait for a rollout, and AssignNonExistentNode recovers by
deleting and reapplying its deployment, so their times will be larger. In the validation of the
overlay problems, the gaps between creation touches were about 37 s and 34 s for them. One run of
each cycle gives no spread. Earlier runs on this cluster have no manifest, so none of their times is reported.

## Five-rollout timing pilot, gate G2, 2026-10-29 (UTC)

Run by `scripts/run_pilot.py` at commit `257e746` on the two node kind cluster of the README
(27 workloads, all Ready, the 8 container image digests and the node image digest matched the
README after the cluster was stopped and started again). The agent was a script that sends the
known fix as `exec_shell` actions through the Orchestrator's parser, so no model ran and no
tokens were spent. Development problems only: the manifests record the `development` slice and
no touch was appended. Each rollout is one injection, the known fix, one 120 s harm window of
`detect_harm` and AIOpsLab's `recover_fault`, with the cluster checked against its clean
snapshot afterwards. The five rollouts are the three development problems, then the first two
again. All five were not harmful and left no fault, and every reset matched the clean snapshot.

Seconds, wall clock (`time.perf_counter`), mean and standard deviation over the 5 rollouts:

| Phase | Mean | SD |
|---|---|---|
| Inject the fault | 23.1 | 15.7 |
| Pod status before the action (waits for the fault to show) | 0.2 | 0.1 |
| Snapshot the objects the fix touches | 0.0 | 0.0 |
| Execute the fix through `exec_shell` | 0.6 | 0.5 |
| Harm window (`detect_harm`, 120 s settle) | 120.3 | 0.1 |
| Reset (`recover_fault`, then all pods Ready) | 2.9 | 0.1 |
| Sum of the phases | 147.1 | 16.3 |
| Wall, from building the problem to the last check | 147.3 | 16.3 |

The spread comes from the injection, which depends on the problem: about 6 s for the target
port, 31 s for AssignNonExistentNode and 36 s for ScalePod. The settle window is 82 percent of
the mean. The five rollouts have 2 of each of two problems and 1 of the third, so the mean
depends on that mix. The summary, the five manifests and the log are in the shared Google Drive
folder `k8s-RemValGate`, under `manifests/`, with the sizes of the local files (the per-rollout
timings files are not copied: the summary holds the same values). SHA-256 of the summary, the five
manifests and the log:

| File | SHA-256 |
|---|---|
| `pilot-20261029t195144z.summary.json` | `49f57e1f4133a56adb58871e7867350c9cdbdf80094267df6787be29d54ed5a5` |
| `pilot-20261029t195144z-1-k8s_target_port.json` | `2fb3b89faa1f6b133f288a40e1b6a03395b5a254c11bb861e8035a030e0ba85a` |
| `pilot-20261029t195144z-2-scale_pod_zero_social_net.json` | `4ae9f941a214de4818b69cdd4c88f34477be17874c1726554b91285e99be63f7` |
| `pilot-20261029t195144z-3-assign_to_non_existent_node_social_net.json` | `812f458a8b45e06ab7b8b2a48f96d196560bfc33f012ab56b420ee90df2a16e5` |
| `pilot-20261029t195144z-4-k8s_target_port.json` | `812fd5a54deec942670a161da220a4bd38eebfe47929495bf781a86703a22ed5` |
| `pilot-20261029t195144z-5-scale_pod_zero_social_net.json` | `1d48d0f9930d48f7c3c3de86553d796721cf77a7e549cbb7ee9cca1ace78a197` |
| `pilot_run.log` | `53c1e3ecc8b46b7c74c7e4e08ff89a7b72e53d3f8ef030760e8c23589f838ae7` |

Against the plan, as arithmetic and not a decision: the BRD plan of about 832 rollouts in 69
hours serial is about 5.0 minutes each. At the measured 147.3 s the same count is about 34 hours
serial, so the harness uses about half of the time per rollout that the plan assumed, and the
rest is left for model latency. The 128 oracle labelling rollouts of R3.5 would be about 5.2
hours instead of about 11. Four-way parallelism needs four clusters, which was not measured.

Limits of the pilot:

- It is a floor. It has no model latency, so K and R cannot be set from it alone: the planner's
  sampling time per rollout has to be added, and no model run has been timed here.
- The restore of the snapshot was not run. A rollout with a failed check adds a restore and a
  second harm decision, so a mixed set costs more than this mean.
- The mean depends on the mix of the three problems, and five rollouts give a wide spread.
- The R3.5 shape (one injection, then K candidates with a reset between them) pays the injection
  once, so its cost per candidate is below this mean.
- After the cluster was stopped and started again, the `media-frontend` pod was Ready 15
  minutes after the nodes started (19:31:50Z to 19:46:54Z), because its init container clones a
  repository from GitHub on every start and the first attempts failed with a network error. A
  restart therefore needs access to GitHub, although the images are already on the nodes. The
  clone is not pinned to a commit, so the Lua scripts it fetches are not covered by the image
  digests.
- `dirty` is true in the manifests because two untracked handoff files were in the work tree.

K and R are a group decision, not set here. One member recommends keeping the planned values, K = 8
and R = 1 (BRD terms: K is 8 planned, R is 1 unless baseline outcomes vary, at most 3). At the
measured cost the harness uses about half of the planned time per rollout, so no cut of K is
needed for harness time. The risk is model latency, which this pilot does not measure. If the
planner's sampling time per rollout exceeds the 2.5 minutes the plan leaves, the rule is to cut K
before raising R. This recommendation is not group-confirmed.

