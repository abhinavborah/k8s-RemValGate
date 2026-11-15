"""Write the record file of one llama-server on the GPU host (story E5-S7, #159).

Run on the GPU host beside the server it describes; scripts/serve_on_gpu_host.sh calls it. The
SHA-256 values are computed here from the files the server loads, never copied from elsewhere:
the GGUF file, and for a fine-tuned fold the adapter's weights (adapter_model.safetensors) the GGUF
was merged from. The untuned control has no fold and no adapter. The run host checks the record
against the running server with model_build.read_served_build, and against the fold in use.

    python write_served_record.py --gguf <file> --llama-cpp <checkout> --host <name> \
        --out <name>.served.json [--fold F --adapter <adapter folder>] -- <server arguments>

Standard library only, so it runs on the GPU host without the package.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gguf", required=True, type=Path)
    parser.add_argument("--llama-cpp", required=True, type=Path)
    parser.add_argument("--host", required=True, help="where the server runs, e.g. a pod id")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fold", type=int, choices=range(4))
    parser.add_argument("--adapter", type=Path, help="the fold's adapter folder")
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("server_args", nargs="*")
    args = parser.parse_args()
    if (args.fold is None) != (args.adapter is None):
        parser.error("a fold needs its adapter, and the control has neither")
    commit = subprocess.check_output(
        ["git", "-C", str(args.llama_cpp), "rev-parse", "HEAD"], text=True
    ).strip()
    gpu = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], text=True
    ).strip()
    record = {
        "llama_cpp_commit": commit,
        "host": args.host,
        "gpu": gpu,
        "model_file_name": args.gguf.name,
        "model_sha256": sha256(args.gguf),
        "quantization": "Q8_0",
        "context_length": args.context,
        "server_args": args.server_args,
        "fold": args.fold,
        "adapter_sha256": None
        if args.adapter is None
        else sha256(args.adapter / "adapter_model.safetensors"),
    }
    args.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
