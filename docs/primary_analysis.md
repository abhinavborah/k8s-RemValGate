# Primary analysis: the fine-tuned planner against the untuned planner

Story E9-S5 (#56). The claim compares the fine-tuned planner's top pick with the untuned planner's
(baseline 3c), per pool problem, as recovered or not.

## What is compared

- **Systems.** 3cft, the planner with its fold's LoRA adapter merged (#159,
  [finetuned_planner.md](finetuned_planner.md)), and 3c, the same planner without it.
- **Same everything else.** Both are served by the same llama-server build on the same GPU host,
  run interleaved by problem at seeds 1000 and 1001 (#161, #165 step 3), with retrieval
  configuration (iii), the same tools, prompt, sampling and turn cap. A pool problem is served by
  the adapter of its own fold, which held it out.
- **Unit.** A problem counts as recovered only if it recovers at both seeds (the rule of #56,
  `harness.g3.combine_seeds`). Each seed is also reported alone.

## Threshold

The threshold is the minimum gain committed with gate G3 (#53, R6.6,
[baselines_pool.md](baselines_pool.md), `harness.g3.meets_min_gain`), applied to the combined
counts at n = 16: the claim is supported only with 5 wins and 0 losses, or 7 wins and 1 loss
against 3c. Two or more losses falsify it whatever the wins, and anything else falsifies it too.
That threshold was committed for the gate against baseline 5. Applying it to this comparison
(3cft against 3c) is proposed and pending group confirmation (#56), and is disclosed as such.

The two-sided McNemar mid-p (#21) and the Bonett-Price interval for the paired difference (#21)
are reported beside the claim and do not decide it. Each seed's counts are descriptive.

What G3 already bounds: on the untuned planner's own candidates, only 1 problem is recoverable
(#165 step 2), so a ranking over them can gain at most 1. 3cft samples its own candidates, so it is
not bound by that count.

## Reported beside the claim

- Baseline 5, the fine-tuned ranking over 3c's candidates: 7.0 of 16 on 2026-11-14, the same as
  3c's top pick in the labelling run ([baseline5.md](baseline5.md), the result and its run record
  at the end of the 2026-11-14 section).
- The candidates of both planners labelled by execution (#159 against #165 step 2): per problem
  the top pick's outcome, the working candidates of K, mixed outcomes and recoverable problems.

## How it is computed

```
uv run python scripts/summarize_finetune.py \
    --results runs/baselines_pool/<3c and 3cft run id>.result.json ... \
    --labels runs/oracle/<run id>.labels.json ... \
    --ft-labels runs/oracle_3cft/<run id>.labels.json ... \
    --baseline5 <summarize_baseline5 output>.json
```

`src/remvalgate/harness/finetune.py` refuses a run that is missing or repeated, and a 3cft run
whose served model is not its problem's fold (or a control served by a fold's model).

## Results

#165 step 3, run 2026-11-14 from a clean checkout at `f434ca2`, 15:12 to 21:32 IST: the 64 batch
runs (16 problems, 2 arms, seeds 1000 and 1001), served by llama.cpp `61dbf49` on one A100 SXM
80 GB host (the base Qwen3.5-4B Q8_0 for 3c, each fold's merged GGUF for 3cft). Every manifest was
checked: clean commit, the right build per arm by SHA-256, the problem's own fold for 3cft, one
touch per run. The rule and its labels were confirmed on #56 before the result was read.

| | 3c | 3cft | wins (only 3cft) | losses (only 3c) | mid-p | Bonett-Price 95% |
|---|---|---|---|---|---|---|
| both seeds (the claim) | 7 | 5 | 0 | 2 | 0.25 | [-0.32, 0.10] |
| seed 1000 | 7 | 7 | 1 | 1 | 0.75 | [-0.22, 0.22] |
| seed 1001 | 10 | 8 | 1 | 3 | 0.375 | [-0.37, 0.15] |

**The claim is falsified:** 0 wins and 2 losses at both seeds, against a bar of 5 wins and 0
losses or 7 and 1. The losses are `pool-assign_to_non_existent_node_social_net-mitigation-3` and
`pool-scale_pod_zero_social_net-mitigation-2`. The fine-tuned planner is therefore not the final
system: the confirmation slice (#59) and the singleton check (#73) use 3c.

Reported beside it:

| | top pick solves | problems with a working candidate | mixed | recoverable |
|---|---|---|---|---|
| 3c labels (#165 step 2, seed 0) | 7 | 8 | 0 | 1 |
| 3cft labels (#159, seed 0) | 8 | 7 | 1 | 0 |

- Baseline 5 solves 7.0 of 16 ([baseline5.md](baseline5.md)).
- The labelling runs are one draw at seed 0, so their top-pick counts (7 and 8) are descriptive and
  not the claim.

Run notes: one planner retry in the fold 1 labels (`RemoteDisconnected`, recovered; requests carry
a fixed seed). The batch was paused once between two runs (17:17 to 17:41 IST) for a network change
on the run host; no run was cut. Files: `step3.tar.gz` (SHA-256
`12200ed8f809e75dd35f4cb101177e7d6cc32482ca7887a3678051d6bc80292b`), to be uploaded to the shared
drive.

## Limits

- n = 16 problems, so only a large gain can pass the test. The minimum gain above says how large.
- The served build (llama-server on a GPU host) differs from LM Studio on the run host, so these
  3c runs are a separate configuration from #165 step 1 and are not pooled with it.
- The training data is small: 6 to 9 working records per fold, and no working MongoDB fix anywhere
  in the pool ([training_data.md](training_data.md), #159).
