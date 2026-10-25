# k8s-RemValGate

A small fine-tuned validity gate for agentic Kubernetes incident remediation. An LLM planner samples
several candidate fixes for an incident, the gate scores whether each one will restore the service,
and the system applies the best one only if it clears a calibrated threshold, otherwise it escalates
to a human. The system is evaluated on AIOpsLab mitigation problems against seven baselines.

This is a group project for the course Language Models and Agents (CL3410).

## Status

Planning. The requirements, epics and acceptance criteria are in [docs/brd.md](docs/brd.md), and the
work is tracked as issues on this repository's GitHub Project. The repository layout and environment
are in place; the system itself is not written yet.

## Layout

```
src/remvalgate/   the package
tests/            unit tests (pytest)
docs/             requirements and project notes
pyproject.toml    project metadata, dependencies, pytest and ruff settings
uv.lock           the dependency lockfile
requirements.txt  plain pip export of the lockfile, for Colab
```

## Setup

Python 3.11 or 3.12 is required, the range AIOpsLab supports. `.python-version` selects 3.12.
Dependencies are managed with [uv](https://docs.astral.sh/uv/) and locked in `uv.lock`.

```
uv sync --locked                 # install from the lockfile; fails if the lock is stale
uv run pytest                    # unit tests
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
```

After changing dependencies, run `uv lock` and regenerate the pip export:

```
uv export --format requirements-txt --no-hashes --no-emit-project --no-header -o requirements.txt
```

The export includes the dev group, so the tests run on Colab too. It leaves out the project itself,
which is installed separately:

```
pip install -r requirements.txt
pip install -e .
pytest
```

## AIOpsLab

Decided in #65: AIOpsLab will be added as an unmodified git submodule pinned at a commit, and new
problem definitions will live in an overlay in this repository. No tracked file inside the
submodule will be changed. A fork was rejected because our code would then live in a second
repository, against R1.6.

AIOpsLab commit: not pinned yet. It is recorded here when the install story (#69) pins it.

## Large files

Model weights, rollout logs and datasets are not committed. They live on shared drive storage and
are referenced by checksum.
