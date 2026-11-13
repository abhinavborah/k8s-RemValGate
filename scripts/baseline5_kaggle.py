"""Fine-tune baseline 5 on one fold and score that fold's held-out candidates (story E6-S4).

Run on a GPU notebook (docs/baseline5.md). Not part of the package or the tests: torch,
transformers, accelerate and peft are installed in the notebook, not from the lockfile, and the
script reads plain JSON only, so the package is not installed.

    pip install "transformers==5.20.2" "accelerate==1.14.0" "peft==0.21.2" \
        "torchvision==0.26.0" "pillow==13.0.0"
    python baseline5_kaggle.py --train training-baseline5-fold<f>-<time>.json \
        --heldout heldout-baseline5-fold<f>-<time>.json --out /kaggle/working

Qwen3.5-4B is LoRA-fine-tuned on the fold's training records with train true and label 1, a
completion-only loss on the candidate's reply. The fine-tuned model then scores every held-out
candidate by the sum of its reply's token log-probabilities given the sampling messages, from its
own forward pass. Training and scoring use one function, completion_logprob, so the tokens a
score covers are the tokens the loss covered. The scores file records the model, revision, LoRA
settings, GPU, hours and date. One fold per call, so a call fits one session.
"""

import argparse
import hashlib
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import accelerate
import peft
import torch
import transformers
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForImageTextToText, AutoTokenizer

MODEL_ID = "Qwen/Qwen3.5-4B"
# the revision of the smoke run (docs/compute.md)
REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
# confirmed by the group member running the story on 2026-11-12; chosen before any run, not tuned
LORA = {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05}
LEARNING_RATE = 1e-4
EPOCHS = 3
SEED = 0
# the language model's attention and MLP projections; the vision tower and the Gated DeltaNet
# projections are left out
TARGET = r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)"
# the end of the assistant turn, so a score covers where the reply stops
END = "<|im_end|>"
SCORES_FORMAT = "remvalgate-baseline5-scores/1"


def encode(tokenizer, messages: list[dict], completion: str) -> tuple[list[int], list[int]]:
    """The prompt's and the completion's token ids, tokenized apart so no token spans the two.

    The replies were sampled with thinking on, but the transcript keeps the reply without its
    thinking, so the prompt is the template with thinking off (an empty thinking block)."""
    prompt = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, enable_thinking=False, tokenize=False
    )
    p = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    c = tokenizer(completion + END, add_special_tokens=False)["input_ids"]
    return p, c


def completion_logprob(model, p: list[int], c: list[int]) -> torch.Tensor:
    """The sum of the completion tokens' log-probabilities given the prompt. Only the logits that
    predict the completion are computed, so a long prompt does not hold a vocabulary-wide row
    per token."""
    ids = torch.tensor([p + c], device=model.device)
    logits = model(input_ids=ids, logits_to_keep=len(c) + 1).logits[0, :-1].float()
    target = torch.tensor(c, device=logits.device)
    return torch.log_softmax(logits, dim=-1).gather(1, target[:, None]).sum()


def _load(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


# the labelling run's setup, as training_data.setup compares it within one export
SETUP_FIELDS = ("model_name", "model_version", "k", "seed")
SAMPLING_FIELDS = ("temperature", "top_p", "top_k", "presence_penalty", "max_tokens", "thinking")


def _setup(manifest: dict) -> tuple:
    sampling = manifest.get("sampling", {})
    return tuple(manifest.get(n) for n in SETUP_FIELDS) + tuple(
        sampling.get(n) for n in SAMPLING_FIELDS
    )


def _check(train: dict, heldout: dict) -> None:
    if train.get("format") != "remvalgate-training-data/1" or train.get("kind") != "baseline5":
        raise SystemExit("--train is not a baseline 5 training file")
    if heldout.get("format") != "remvalgate-baseline5-heldout/1":
        raise SystemExit("--heldout is not a baseline 5 held-out file")
    if train["fold"] != heldout["fold"]:
        raise SystemExit(f"fold {train['fold']} training file, fold {heldout['fold']} held out")
    if train["fold_assignment_sha256"] != heldout["fold_assignment_sha256"]:
        raise SystemExit("the two files use different fold assignments")
    files = [*train["labels_files"].values(), *heldout["labels_files"].values()]
    if len({_setup(f["manifest"]) for f in files}) != 1:
        raise SystemExit("the two files come from labelling runs of different setups")
    trained = {r["problem_id"] for r in train["records"]}
    held = {p["problem_id"] for p in heldout["problems"]}
    if trained & held:
        raise SystemExit(f"held-out problems in the training file: {sorted(trained & held)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True, type=Path)
    parser.add_argument("--heldout", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        print("no CUDA device: turn on a GPU accelerator")
        return 1
    train, train_sha = _load(args.train)
    heldout, heldout_sha = _load(args.heldout)
    _check(train, heldout)
    fold = heldout["fold"]
    rows = [r for r in train["records"] if r["train"] and r["label"] == 1]
    start = time.perf_counter()
    started = datetime.now(UTC)
    random.seed(SEED)
    torch.manual_seed(SEED)
    # as in scripts/smoke_qwen.py: a T4 runs bfloat16 through emulation
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION)
    base = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID, revision=REVISION, dtype=dtype, device_map={"": 0}
    )
    base.config.use_cache = False
    config = LoraConfig(**LORA, target_modules=TARGET, bias="none")
    model = get_peft_model(base, config)
    adapted = sum(".lora_A." in name for name, _ in model.named_modules())
    if adapted == 0:
        print("no module matched the LoRA target: print model.named_modules() and fix TARGET")
        return 1
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=LEARNING_RATE
    )
    encoded = [encode(tokenizer, r["messages"], r["completion"]) for r in rows]
    # a long prompt may not fit a T4 in bfloat16 (docs/baseline5.md, Limits)
    longest = max((len(p) for p, _ in encoded), default=0)
    print(f"{len(rows)} training rows, longest training prompt {longest} tokens", flush=True)
    losses = []
    model.train()
    for epoch in range(EPOCHS):
        order = list(range(len(encoded)))
        random.Random(SEED + epoch).shuffle(order)
        for i in order:
            p, c = encoded[i]
            # the mean negative log-likelihood of the completion tokens, batch 1
            loss = -completion_logprob(model, p, c) / len(c)
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            losses.append({"epoch": epoch, "id": rows[i]["id"], "loss": loss.item()})
            print(f"epoch {epoch} {rows[i]['id']} loss {loss.item():.4f}", flush=True)
    model.eval()
    scores, completion_tokens = {}, {}
    with torch.no_grad():
        for problem in heldout["problems"]:
            for candidate in problem["candidates"]:
                p, c = encode(tokenizer, problem["messages"], candidate["completion"])
                scores[candidate["id"]] = completion_logprob(model, p, c).item()
                completion_tokens[candidate["id"]] = len(c)
    hours = (time.perf_counter() - start) / 3600
    props = torch.cuda.get_device_properties(0)
    stamp = started.strftime("%Y%m%dt%H%M%Sz")
    args.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.out / f"adapter-baseline5-fold{fold}-{stamp}")
    body = {
        "format": SCORES_FORMAT,
        "fold": fold,
        "started": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": MODEL_ID,
        "revision": REVISION,
        "dtype": str(dtype),
        "lora": {**LORA, "target_modules": TARGET, "adapted_modules": adapted},
        "learning_rate": LEARNING_RATE,
        "epochs": EPOCHS,
        "batch_size": 1,
        "seed": SEED,
        "chat_template": {"add_generation_prompt": True, "enable_thinking": False, "end": END},
        "gpu": props.name,
        "gpu_hours": round(hours, 4),
        "versions": {
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "transformers": transformers.__version__,
            "accelerate": accelerate.__version__,
            "peft": peft.__version__,
        },
        "train": {"file": args.train.name, "sha256": train_sha},
        "heldout": {"file": args.heldout.name, "sha256": heldout_sha},
        "trained_ids": [r["id"] for r in rows],
        "losses": losses,
        "completion_tokens": completion_tokens,
        "scores": scores,
    }
    path = args.out / f"scores-baseline5-fold{fold}-{stamp}.json"
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(body, indent=1) + "\n")
    print(f"fold {fold}: {len(rows)} training rows, {len(scores)} candidates scored, {hours:.2f} h")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
