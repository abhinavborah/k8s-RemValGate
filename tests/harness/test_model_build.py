import hashlib
import json

import pytest

from remvalgate.harness.model_build import build_key, check_one_build, read_build

PS = [
    {
        "modelKey": "qwen/qwen3.5-4b",
        "path": "qwen/qwen3.5-4b",
        "selectedVariant": "qwen/qwen3.5-4b@q8_0",
        "quantization": {"name": "Q8_0", "bits": 8},
        "contextLength": 32768,
        "parallel": 4,
    }
]
RUNTIME = (
    "LLM ENGINE    SELECTED    MODEL FORMAT\n"
    "llama.cpp-mac-arm64-apple-metal-advsimd@2.73.0       ✓            GGUF\n"
    "llama.cpp-mac-arm64-apple-metal-advsimd@2.72.0                    GGUF\n"
    "mlx-llm-mac-arm64-apple-metal-advsimd@1.14.0         ✓            MLX\n"
)


def _host(tmp_path, ps=PS):
    hub = tmp_path / ".lmstudio" / "hub" / "models" / "qwen" / "qwen3.5-4b"
    hub.mkdir(parents=True)
    (hub / "model.yaml").write_text(
        "model: qwen/qwen3.5-4b\nbase:\n  - key: lmstudio-community/qwen3.5-4b-gguf\n"
        "config:\n  operation:\n    fields:\n"
        "      - key: llm.prediction.temperature\n        value: 1.0\n"
        "      - key: llm.prediction.llama.presencePenalty\n"
        "        value:\n          checked: true\n          value: 1.5\n"
    )
    folder = tmp_path / ".lmstudio" / "models" / "lmstudio-community" / "Qwen3.5-4B-GGUF"
    folder.mkdir(parents=True)
    (folder / "Qwen3.5-4B-Q8_0.gguf").write_bytes(b"weights")
    (folder / "mmproj-Qwen3.5-4B-BF16.gguf").write_bytes(b"vision")

    def run(*argv):
        return {
            ("lms", "ps", "--json"): json.dumps(ps),
            ("lms", "runtime", "ls"): RUNTIME,
            ("lms", "version"): "\x1b[38mbanner\x1b[0m\nCLI commit: 43d4b26\n",
        }.get(argv[:3], "0.4.27+2\n")

    return run


def test_the_build_is_read_from_the_host_with_the_gguf_files_hash(tmp_path):
    build = read_build("qwen/qwen3.5-4b", run=_host(tmp_path), home=tmp_path)
    assert build["app_version"] == "0.4.27+2"
    assert build["cli_commit"] == "43d4b26"
    # the selected GGUF engine, not the selected MLX one or an older llama.cpp
    assert build["gguf_engine"] == "llama.cpp-mac-arm64-apple-metal-advsimd@2.73.0"
    assert (build["quantization"], build["context_length"], build["parallel"]) == ("Q8_0", 32768, 4)
    # the weights file, never the mmproj vision file
    assert build["model_file"].endswith("Qwen3.5-4B-Q8_0.gguf")
    assert build["model_sha256"] == hashlib.sha256(b"weights").hexdigest()
    # the defaults the server applies to parameters a request does not send; no override here
    assert build["server_defaults"] == {
        "hub": {
            "llm.prediction.temperature": 1.0,
            "llm.prediction.llama.presencePenalty": {"checked": True, "value": 1.5},
        },
        "override": {},
    }


def test_a_model_that_is_not_loaded_stops_the_run(tmp_path):
    with pytest.raises(RuntimeError, match="not loaded"):
        read_build("qwen/qwen3.5-4b", run=_host(tmp_path, ps=[]), home=tmp_path)


def test_runs_with_different_builds_are_refused_and_the_file_path_does_not_count():
    a = {"model_name": "m", "model_build": {"model_sha256": "x", "model_file": "/a"}}
    same = {"model_name": "m", "model_build": {"model_sha256": "x", "model_file": "/b"}}
    other = {"model_name": "m", "model_build": {"model_sha256": "y", "model_file": "/a"}}
    old = {"model_name": "m"}
    check_one_build([a, same])
    assert build_key(a) == build_key(same)
    with pytest.raises(ValueError, match="more than one model build"):
        check_one_build([a, other])
    # a run written before builds were recorded does not pair with one that has a build
    with pytest.raises(ValueError, match="more than one model build"):
        check_one_build([a, old])
    # a run with no language model takes no part
    check_one_build([a, {"model_name": "none"}])
