"""Load Qwen3.5-4B on one GPU, generate one completion and report peak memory.

Run on a GPU notebook (story E1-S4). Not part of the package or the tests: torch and
transformers are installed in the notebook, not from the lockfile.

    pip install "transformers==5.20.2" "accelerate==1.14.0" "torchvision==0.26.0" "pillow==13.0.0"
    python smoke_qwen.py
"""

import sys
import time
from datetime import UTC, datetime

import accelerate
import PIL
import torch
import torchvision
import transformers
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL_ID = "Qwen/Qwen3.5-4B"
REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
PROMPT = "In one sentence, what does a Kubernetes readiness probe do?"
MAX_NEW_TOKENS = 64


def main() -> int:
    if not torch.cuda.is_available():
        print("no CUDA device: turn on a GPU accelerator")
        return 1
    props = torch.cuda.get_device_properties(0)
    # torch counts emulated bf16 as supported, so a T4 (7.5) gets bf16 too; it is kept there
    # because a cast of bf16 weights to fp16 can overflow
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    native_bf16 = torch.cuda.is_bf16_supported(including_emulation=False)
    print(f"date_utc: {datetime.now(UTC).isoformat(timespec='seconds')}")
    print(f"model: {MODEL_ID}@{REVISION}")
    print(f"gpu: {props.name}, visible GPUs: {torch.cuda.device_count()}")
    print(f"compute_capability: {props.major}.{props.minor}, native_bf16: {native_bf16}")
    print(f"gpu_total_gib: {props.total_memory / 2**30:.2f}")
    print(f"torch: {torch.__version__}, cuda: {torch.version.cuda}")
    print(f"transformers: {transformers.__version__}, dtype: {dtype}")
    print(
        f"accelerate: {accelerate.__version__}, torchvision: {torchvision.__version__}, "
        f"pillow: {PIL.__version__}"
    )

    torch.cuda.reset_peak_memory_stats(0)
    start = time.perf_counter()
    processor = AutoProcessor.from_pretrained(MODEL_ID, revision=REVISION)
    # one GPU only: the compute plan assumes a single 16 to 24 GB card
    model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID, revision=REVISION, dtype=dtype, device_map={"": 0}
    )
    load_s = time.perf_counter() - start

    messages = [{"role": "user", "content": [{"type": "text", "text": PROMPT}]}]
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        enable_thinking=False,
    ).to(model.device)
    start = time.perf_counter()
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
    gen_s = time.perf_counter() - start
    new_tokens = out[0, inputs["input_ids"].shape[1] :]
    text = processor.decode(new_tokens, skip_special_tokens=True).strip()

    allocated = torch.cuda.max_memory_allocated(0) / 2**30
    reserved = torch.cuda.max_memory_reserved(0) / 2**30
    # device wide, so it includes the CUDA context the torch peaks leave out; current, not a peak
    free, total = torch.cuda.mem_get_info(0)
    print(f"load_s: {load_s:.1f}, generate_s: {gen_s:.1f}, new_tokens: {len(new_tokens)}")
    print("seed: n/a (do_sample=False, greedy)")
    print(f"peak_allocated_gib: {allocated:.2f}, peak_reserved_gib: {reserved:.2f}")
    print(f"device_used_after_generate_gib: {(total - free) / 2**30:.2f}")
    print(f"completion: {text}")

    # no memory condition: an out-of-memory error raises before this point, so the only memory
    # limit checked is the card capacity. The text check is a sanity check, not a relevance check:
    # <think> and </think> are not special tokens here, so leaked thinking survives decoding
    ok = len(text.split()) >= 5 and "think>" not in text and any(c.isalpha() for c in text)
    print("result: PASS" if ok else "result: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
