"""Keep both output streams of a fix command that fails (E8-S4, #174).

With `k8s_host: kind`, AIOpsLab's `Shell.docker_exec` returns only stderr when a command fails.
`kubectl exec` writes "command terminated with exit code N" to stderr, while the program inside
the pod (the mongo shell, for one) writes its own error to stdout, so the cause is lost.

`capture_failed_output` swaps in `docker_exec_both`, which runs the same docker command and on a
failure returns the exit code, stdout and stderr. The swap is active only while a fix is applied:
the planner's tool calls during diagnosis keep AIOpsLab's output, so their prompts stay
comparable with earlier runs. The pinned AIOpsLab commit is not changed.

Imports nothing from aiopslab, so CI tests it.
"""

import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# the start of AIOpsLab's own failure response, kept so checks on "[ERROR]" still hold
PREFIX = "[ERROR] Docker command execution failed: "


def docker_command(container_name: str, command: str) -> str:
    """The docker command AIOpsLab's Shell.docker_exec builds, unchanged."""
    escaped_command = command.replace('"', '\\"')
    return f'docker exec {container_name} sh -c "{escaped_command}"'


def docker_exec_both(
    container_name: str,
    command: str,
    timeout: float = 30,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    """Shell.docker_exec, except that a failure returns the exit code, stdout and stderr."""
    try:
        out = run(
            docker_command(container_name, command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            timeout=timeout,
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to execute command in Docker container: {container_name}\nError: {str(e)}"
        ) from e
    stdout = out.stdout.decode("utf-8")
    if out.returncode == 0:
        return stdout
    stderr = out.stderr.decode("utf-8")
    return f"{PREFIX}exit code {out.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"


@contextmanager
def capture_failed_output(shell: type) -> Iterator[None]:
    """Within the block, shell.docker_exec keeps both streams of a failed command."""
    original = shell.__dict__["docker_exec"]
    shell.docker_exec = staticmethod(docker_exec_both)
    try:
        yield
    finally:
        shell.docker_exec = original
