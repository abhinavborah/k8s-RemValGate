# Primary analysis: the fine-tuned planner against the untuned planner

Story E9-S5 (#56). The claim compares the fine-tuned planner's top pick with the untuned planner's
(baseline 3c), per pool problem, as recovered or not. The claim: **3cft recovers more pool problems
than 3c and loses at most one problem that 3c recovers**, by the threshold below.

It is a pre-specified exploratory comparison: its rule was fixed before its result, but the
decision to fine-tune the planner was taken after the failed headroom check (BRD 1.17, R6.6). The
BRD's primary claim (O1, R9.3) still names the gate against baseline 5, which was not trained, and
what replaces it is open (BRD change 1.15); this comparison is reported beside it and never in its
place.

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
counts at n = 16:

- **Supported:** at least 5 wins with no loss, or at least 7 wins with exactly 1 loss.
- **Falsified:** 2 or more losses whatever the wins, or no more wins than losses.
- **Not supported:** anything else (1 to 4 wins with no loss, 2 to 6 wins with 1 loss). This is a
  gain too small for 16 problems to separate from noise, not evidence of no effect.

That threshold was committed for the gate against baseline 5. On #56, two group members confirmed
the threshold and the both-seeds rule for this comparison (2026-11-14, 13:00 and 14:24 UTC). The
three labels were proposed in the 14:24 UTC comment and adopted in #181 at 15:48 UTC by a third
group member, before the batch of #165 step 3 ended (16:02 UTC), so before any result of it
existed. No further group member has confirmed the labels on #56.

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

Not run yet: #165 step 3 started on 2026-11-14 at 15:12 IST. The results are added here when it ends.

## Limits

- n = 16 problems, so only a large gain can pass the test. The minimum gain above says how large.
- The served build (llama-server on a GPU host) differs from LM Studio on the run host, so these
  3c runs are a separate configuration from #165 step 1 and are not pooled with it.
- The training data is small: 6 to 9 working records per fold, and no working MongoDB fix anywhere
  in the pool ([training_data.md](training_data.md), #159).
