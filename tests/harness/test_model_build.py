import ast
import hashlib
import json
import pathlib

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
URL = "http://localhost:1234/v1"
RUNTIME = (
    "LLM ENGINE    SELECTED    MODEL FORMAT\n"
    "llama.cpp-mac-arm64-apple-metal-advsimd@2.73.0       ✓            GGUF\n"
    "llama.cpp-mac-arm64-apple-metal-advsimd@2.72.0                    GGUF\n"
    "mlx-llm-mac-arm64-apple-metal-advsimd@1.14.0         ✓            MLX\n"
)


def _host(
    tmp_path,
    ps=PS,
    version="\x1b[38mbanner\x1b[0m\nCLI commit: 43d4b26\n",
    runtime=RUNTIME,
    app="0.4.27+2\n",
):
    hub = tmp_path / ".lmstudio" / "hub" / "models" / "qwen" / "qwen3.5-4b"
    hub.mkdir(parents=True)
    (hub / "model.yaml").write_text(
        "model: qwen/qwen3.5-4b\nbase:\n  - key: lmstudio-community/qwen3.5-4b-gguf\n"
        "config:\n  operation:\n    fields:\n"
        "      - key: llm.prediction.temperature\n        value: 1.0\n"
        "      - key: llm.prediction.llama.presencePenalty\n"
        "        value:\n          checked: true\n          value: 1.5\n"
    )
    internal = tmp_path / ".lmstudio" / ".internal"
    internal.mkdir(parents=True)
    (internal / "http-server-config.json").write_text('{"port": 1234}')
    folder = tmp_path / ".lmstudio" / "models" / "lmstudio-community" / "Qwen3.5-4B-GGUF"
    folder.mkdir(parents=True)
    (folder / "Qwen3.5-4B-Q8_0.gguf").write_bytes(b"weights")
    (folder / "mmproj-Qwen3.5-4B-BF16.gguf").write_bytes(b"vision")

    def run(*argv):
        return {
            ("lms", "ps", "--json"): json.dumps(ps),
            ("lms", "runtime", "ls"): runtime,
            ("lms", "version"): version,
        }.get(argv[:3], app)

    return run


def test_the_build_is_read_from_the_host_with_the_gguf_files_hash(tmp_path):
    build = read_build("qwen/qwen3.5-4b", URL, run=_host(tmp_path), home=tmp_path)
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
        read_build("qwen/qwen3.5-4b", URL, run=_host(tmp_path, ps=[]), home=tmp_path)


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


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"app": ""}, "app version"),
        ({"version": "banner only\n"}, "CLI commit"),
        (
            {"runtime": RUNTIME.replace("\u2713            GGUF", "             GGUF")},
            "GGUF engine",
        ),
    ],
)
def test_a_field_the_host_does_not_report_stops_the_run(tmp_path, kwargs, match):
    # two builds that both read as unknown would otherwise pass check_one_build
    with pytest.raises(RuntimeError, match=match):
        read_build("qwen/qwen3.5-4b", URL, run=_host(tmp_path, **kwargs), home=tmp_path)


@pytest.mark.parametrize(
    "url", ["http://10.0.0.5:1234/v1", "http://localhost:8080/v1", "https://example.org/v1"]
)
def test_a_server_other_than_this_hosts_lm_studio_is_refused(tmp_path, url):
    with pytest.raises(RuntimeError, match="not this host's LM Studio server"):
        read_build("qwen/qwen3.5-4b", url, run=_host(tmp_path), home=tmp_path)


def test_the_build_names_its_base_url_and_host_so_two_hosts_stay_apart(tmp_path):
    build = read_build("qwen/qwen3.5-4b", URL, run=_host(tmp_path), home=tmp_path)
    assert build["base_url"] == URL and build["host"]
    other = {"model_name": "m", "model_build": {**build, "host": "another-host"}}
    with pytest.raises(ValueError, match="more than one model build"):
        check_one_build([{"model_name": "m", "model_build": build}, other])


def _body(path: pathlib.Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, name
    return ast.unparse(found[0])


def test_the_build_is_read_before_the_touch_in_both_run_scripts():
    # a model that is not loaded, or a server that is not the recorded one, must stop a run before
    # start_run appends its touch (#161); read inside the functions that call start_run, so a
    # helper defined above them cannot satisfy the check
    scripts = pathlib.Path(__file__).resolve().parents[2] / "scripts"
    for name, function in (("run_baselines_pool.py", "run_one"), ("run_oracle_labels.py", "main")):
        body = _body(scripts / name, function)
        assert 0 < body.index("build_for(") < body.index("start_run("), name


SERVED = {
    "llama_cpp_commit": "61dbf49228c8a96b72e40c89d93cb7c003411f8c",
    "host": "gpu-host-1",
    "gpu": "NVIDIA A100-SXM4-80GB",
    "model_file_name": "fold0-q8_0.gguf",
    "model_sha256": "ab" * 32,
    "quantization": "Q8_0",
    "context_length": 32768,
    "server_args": ["--jinja", "-c", "32768"],
    "fold": 0,
    "adapter_sha256": "cd" * 32,
}
PROPS = {
    "model_path": "/root/work/fold0-q8_0.gguf",
    "default_generation_settings": {"n_ctx": 32768},
    "build_info": "b9999-61dbf49",
}


def _served(tmp_path, record=SERVED, props=PROPS, url="https://a.example/v1", fold=0):
    from remvalgate.harness.model_build import read_served_build

    path = tmp_path / "served.json"
    path.write_text(json.dumps(record))
    return read_served_build("qwen/qwen3.5-4b", url, path, get_props=lambda u, k: props, fold=fold)


def test_a_served_build_records_the_record_and_the_server(tmp_path):
    build = _served(tmp_path)
    assert build["server"] == "llama-server"
    assert build["model_sha256"] == "ab" * 32
    assert build["build_info"] == "b9999-61dbf49"


def test_a_served_build_refuses_a_server_that_differs_from_its_record(tmp_path):
    for change in (
        {"model_path": "/root/work/fold1-q8_0.gguf"},
        {"default_generation_settings": {"n_ctx": 16384}},
        {"build_info": "b1-0000000"},
    ):
        with pytest.raises(RuntimeError):
            _served(tmp_path, props={**PROPS, **change})


def test_a_served_build_refuses_an_incomplete_record(tmp_path):
    with pytest.raises(RuntimeError, match="lacks"):
        _served(tmp_path, record={k: v for k, v in SERVED.items() if k != "model_sha256"})


def test_the_endpoint_is_not_part_of_the_build_key(tmp_path):
    from remvalgate.harness.model_build import build_key

    a = _served(tmp_path, url="https://a.example/v1")
    b = _served(tmp_path, url="https://b.example/v1")
    assert build_key({"model_build": a}) == build_key({"model_build": b})
    c = _served(tmp_path, record={**SERVED, "model_sha256": "ef" * 32})
    assert build_key({"model_build": a}) != build_key({"model_build": c})


def test_a_served_build_refuses_another_folds_model(tmp_path):
    # a swapped --fold-servers entry, or fold 1's record given for fold 0, would serve held-out
    # problems with an adapter trained on them
    with pytest.raises(RuntimeError, match="serves fold 0, the run uses fold 1"):
        _served(tmp_path, fold=1)
    with pytest.raises(RuntimeError, match="serves fold 0, the run uses fold None"):
        _served(tmp_path, fold=None)


def test_the_control_has_no_fold_and_no_adapter(tmp_path):
    control = {**SERVED, "fold": None, "adapter_sha256": None, "model_file_name": "fold0-q8_0.gguf"}
    assert _served(tmp_path, record=control, fold=None)["fold"] is None
    for bad in ({"adapter_sha256": "cd" * 32}, {"fold": None}):
        record = {**(control if "fold" not in bad else SERVED), **bad}
        with pytest.raises(RuntimeError, match="adapter"):
            _served(tmp_path, record=record, fold=None)
    with pytest.raises(RuntimeError, match="lacks"):
        _served(tmp_path, record={k: v for k, v in SERVED.items() if k != "fold"})


def test_a_fold_without_a_record_is_refused():
    from remvalgate.harness.model_build import build_for

    with pytest.raises(RuntimeError, match="needs a record file"):
        build_for("qwen/qwen3.5-4b", "http://localhost:1234/v1", None, fold=2)
