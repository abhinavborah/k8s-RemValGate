# Compute record

GPU environment and model hosting for the planner and gate (story E1-S4, BRD R1.4, proposal
section XII), and the harness timing of one end to end run (story E1-S3, last section).
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
which git ignores, and have not been copied to the shared drive yet.

The times are a floor for the G2 timing pilot, not an estimate of a rollout. They exclude model
latency, the one-off OpenEBS and Prometheus install, and the Social Network deploy (7 min 9 s for
the deploy script here, which stopped at its own 300 s wait; the pods became Ready after a
further `kubectl wait`; the share spent on image pulls was not measured). The evaluator's settle
wait is included, and it returned on its first poll because the Service was already fixed. The
ScalePod and AssignNonExistentNode fixes wait for a rollout, and AssignNonExistentNode recovers by
deleting and reapplying its deployment, so their times will be larger. In the validation of the
overlay problems, the gaps between creation touches were about 37 s and 34 s for them. One run of
each cycle gives no spread; it was made 4 times on this cluster, and the sums of the phases were 9.1 s and 8.9 s each time.
The wall time was measured in the last 2 runs only, at 9.3 s to 9.4 s and 9.1 s to 9.2 s.
