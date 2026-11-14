"""Merge one fold's baseline 5 adapter into the base model for serving (story E5-S7, #159).

Run on the GPU host that trained the adapter (docs/baseline5.md), beside baseline5_kaggle.py:
torch, transformers and peft come from that environment, not from the lockfile.

    python merge_baseline5_adapter.py --base <base model folder> \
        --adapter <out>/adapter-baseline5-fold<f>-<time> --out <merged folder>

The merged model keeps the base model's files apart from the weights (tokenizer, chat template,
processor and config), so the GGUF converted from it carries the same chat template as the base.
The conversion is then llama.cpp's convert_hf_to_gguf.py with --outtype q8_0, the quantization
of the untuned runs. Without --adapter, the base model is saved through the same path, which is
the serving check: its GGUF must load in LM Studio like the untuned model.
"""

import argparse
import shutil
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForImageTextToText

# files of the base model folder that are not weights and are copied unchanged
KEEP = ("*.json", "*.jinja", "*.txt", "*.model", "*.tiktoken")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; a merged model is never overwritten")
    model = AutoModelForImageTextToText.from_pretrained(args.base, dtype=torch.bfloat16)
    if args.adapter is not None:
        model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
    model.save_pretrained(args.out, safe_serialization=True)
    for pattern in KEEP:
        for path in args.base.glob(pattern):
            if path.name.startswith("model.safetensors") or path.name == "config.json":
                continue
            shutil.copy2(path, args.out / path.name)
    print(args.out)


if __name__ == "__main__":
    main()
