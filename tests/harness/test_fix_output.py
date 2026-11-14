"""The capture of a failed fix command's output (E8-S4, #174)."""

import ast
import subprocess
from pathlib import Path

import pytest

from remvalgate.harness import credentials
from remvalgate.harness.fix_output import (
    PREFIX,
    capture_failed_output,
    docker_command,
    docker_exec_both,
)

SMOKE = Path(__file__).resolve().parents[2] / "scripts" / "run_baselines_smoke.py"
COMMAND = 'kubectl -n test-hotel-reservation exec deploy/mongodb-user -- mongo --eval "db.x()"'


def _run(returncode: int, stdout: str, stderr: str, sent: list[str]):
    def run(command, **kwargs):
        sent.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout.encode(), stderr.encode())

    return run


def test_a_failing_command_keeps_the_exit_code_stdout_and_stderr_in_that_order():
    sent = []
    stderr = "command terminated with exit code 252\n"
    run = _run(252, "Error: not authorized on admin\n", stderr, sent)
    response = docker_exec_both("kind-control-plane", COMMAND, run=run)
    assert response.startswith(PREFIX)
    assert response == (
        f"{PREFIX}exit code 252\n"
        "stdout:\nError: not authorized on admin\n\n"
        "stderr:\ncommand terminated with exit code 252\n"
    )
    assert response.index("not authorized") < response.index("command terminated")


def test_a_succeeding_command_returns_stdout_as_aiopslab_does():
    run = _run(0, "user created\n", "a warning\n", [])
    assert docker_exec_both("kind-control-plane", COMMAND, run=run) == "user created\n"


def test_the_command_sent_is_aiopslabs_docker_command_unchanged():
    sent = []
    docker_exec_both("kind-control-plane", COMMAND, run=_run(1, "", "", sent))
    escaped = COMMAND.replace('"', '\\"')
    assert sent == [f'docker exec kind-control-plane sh -c "{escaped}"']
    assert sent == [docker_command("kind-control-plane", COMMAND)]


def test_a_run_that_raises_raises_as_aiopslab_does():
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 30)

    with pytest.raises(RuntimeError, match="Failed to execute command in Docker container"):
        docker_exec_both("kind-control-plane", COMMAND, run=run)


def test_a_password_in_the_output_is_redacted():
    env = {"REMVALGATE_MONGO_ROOT_PASSWORD": "s3cretvalue", "REMVALGATE_MONGO_ADMIN_PASSWORD": "x"}
    run = _run(1, "connecting with -p s3cretvalue failed\n", "exit code 1\n", [])
    response = credentials.redact(docker_exec_both("kind-control-plane", COMMAND, run=run), env)
    assert "s3cretvalue" not in response
    assert "-p ROOT_PASSWORD_REF" in response


class _Shell:
    @staticmethod
    def docker_exec(container_name, command, timeout=30):
        return "original"

    @staticmethod
    def exec(command, timeout=30):
        return _Shell.docker_exec("kind-control-plane", command, timeout=timeout)


def test_the_capture_is_active_only_inside_the_block():
    original = _Shell.__dict__["docker_exec"]
    assert _Shell.exec("kubectl get pods") == "original"
    with capture_failed_output(_Shell):
        assert _Shell.__dict__["docker_exec"].__func__ is docker_exec_both
    assert _Shell.__dict__["docker_exec"] is original
    assert _Shell.exec("kubectl get pods") == "original"


def test_the_capture_is_removed_when_the_block_raises():
    original = _Shell.__dict__["docker_exec"]
    with pytest.raises(ValueError), capture_failed_output(_Shell):
        raise ValueError
    assert _Shell.__dict__["docker_exec"] is original


def test_apply_captures_only_around_its_own_ask_env():
    """The fix is sent inside the capture; nothing else in the smoke script uses it."""
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    withs = [n for n in ast.walk(tree) if isinstance(n, ast.With | ast.AsyncWith)]
    captures = [w for w in withs if "capture_failed_output(Shell)" in ast.unparse(w.items[0])]
    assert len(captures) == 1
    apply = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "apply"
    )
    assert captures[0] in list(ast.walk(apply))
    assert "orch.ask_env(reply)" in ast.unparse(captures[0])
