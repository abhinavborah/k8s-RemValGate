# Compute record

GPU environment and model hosting for the planner and gate (story E1-S4, BRD R1.4, proposal
section XII). Every number below comes from the smoke run unless a source is given.

## Environment

Kaggle notebooks, chosen by the story's assignee for the smoke test. The group has not yet agreed
the compute plan for training (BRD section 9, open questions).

| Item | Value |
|---|---|
| GPU | NVIDIA Tesla T4, 2 visible per session; the model runs on one |
| Memory limit | 14.56 GiB total per T4, as reported by torch |
| Cost | none (free tier) |
| Hours | 30 GPU-hours per week on the assignee's account, 1 hour used (Kaggle account settings, read 2026-10-27); the documented weekly quota is "30 hours or sometimes higher depending on demand and resources" ([Kaggle docs](https://www.kaggle.com/docs/efficient-gpu-usage), read 2026-10-26) |
| Software | Python 3.13, torch 2.11.0+cu128, CUDA 12.8, transformers 5.20.2; accelerate, torchvision and pillow were installed unpinned and this run did not record their versions (the script now prints them) |

## Smoke run, 2026-10-26

`scripts/smoke_qwen.py` loads `Qwen/Qwen3.5-4B` at revision
`851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` on GPU 0 and generates one completion with thinking
turned off. Torch reported bfloat16 support, so the run used bfloat16. The T4 has compute
capability 7.5 and no native bfloat16: torch counts emulated support by default, so this was
emulated bfloat16. Training dtype and speed on a T4 stay open (see Limits).

| Measure | Value |
|---|---|
| Date (UTC) | 2026-10-26T15:22:10 |
| Load time | 62.0 s (first download, about 9.3 GB) |
| Generation | 44 new tokens in 3.9 s, greedy, from a one-sentence prompt |
| Peak allocated | 8.53 GiB |
| Peak reserved | 8.54 GiB, under the 14.56 GiB total |
| Result | PASS: a relevant one-sentence answer |

Transformers warned that the Gated DeltaNet layers fell back to reference PyTorch code because
`flash-linear-attention` and `causal_conv1d` were not installed. Output is correct but slower.
Whether those kernels install and run on a T4 is unchecked.

## Budget against the plan

- The submitted proposal (section XII) and BRD section 9 both estimate 150 to 240 GPU-hours of
  LoRA fine-tuning.
- The BRD window runs 27 days from 2026-10-19. From this run that leaves about three quota weeks,
  so about 90 GPU-hours per Kaggle account at the base quota.
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
