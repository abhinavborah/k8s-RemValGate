import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate import overlay
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.overlay.checks import has_available_replica, poll_until
from remvalgate.overlay.fixes import FIXES


def _factory():
    return object()


def test_shipped_overlay_covers_the_development_slice_and_is_registered():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    dev = {i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT}
    assert dev <= set(overlay.PROBLEMS) <= set(registry.slices)
    assert overlay.register({"upstream-1": _factory}) == list(overlay.PROBLEMS)


def test_every_overlay_problem_has_a_known_fix():
    assert set(FIXES) == set(overlay.PROBLEMS)


def test_register_adds_entries_and_returns_their_ids():
    registry = {"upstream-1": _factory}
    added = overlay.register(registry, {"ours-1": _factory, "ours-2": _factory})
    assert added == ["ours-1", "ours-2"]
    assert set(registry) == {"upstream-1", "ours-1", "ours-2"}


def test_register_refuses_an_upstream_id_and_adds_nothing():
    registry = {"upstream-1": _factory}
    with pytest.raises(ValueError, match="upstream-1"):
        overlay.register(registry, {"ours-1": _factory, "upstream-1": _factory})
    assert list(registry) == ["upstream-1"]


_ids = st.sets(st.text(alphabet="abcdef-_0123456789", min_size=1, max_size=8), max_size=6)


@given(registry_ids=_ids, overlay_ids=_ids)
def test_register_is_the_union_or_a_refusal_that_changes_nothing(registry_ids, overlay_ids):
    registry = dict.fromkeys(registry_ids, _factory)
    before = dict(registry)
    problems = dict.fromkeys(overlay_ids, _factory)
    if registry_ids & overlay_ids:
        with pytest.raises(ValueError):
            overlay.register(registry, problems)
        assert registry == before
    else:
        overlay.register(registry, problems)
        assert set(registry) == registry_ids | overlay_ids


def test_importing_the_overlay_does_not_load_aiopslab():
    # aiopslab reads config.yml on import, which CI does not have
    code = (
        "import sys, remvalgate.overlay\n"
        "assert not any(m.startswith('aiopslab') for m in sys.modules)"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def test_every_overlay_id_builds_its_intended_problem():
    # dev_problems imports aiopslab, so its top-level names are read from the source
    tree = ast.parse(Path(overlay.__file__).with_name("dev_problems.py").read_text())
    defined = {n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    built = {i: f.problem for i, f in overlay.PROBLEMS.items()}
    assert built == {
        "dev-k8s_target_port-misconfig-mitigation-1": "target_port_user_mention",
        "dev-scale_pod_zero_social_net-mitigation-1": "ScalePodMediaMitigation",
        "dev-assign_to_non_existent_node_social_net-mitigation-1": (
            "AssignNonExistentNodeUniqueIdMitigation"
        ),
        "pool-assign_to_non_existent_node_social_net-mitigation-1": (
            "AssignNonExistentNodeAvailableMitigation"
        ),
    }
    assert set(built.values()) <= defined


@pytest.mark.parametrize(
    ("deployment", "expected"),
    [
        (None, False),
        (SimpleNamespace(status=None), False),
        (SimpleNamespace(status=SimpleNamespace(available_replicas=None)), False),
        (SimpleNamespace(status=SimpleNamespace(available_replicas=0)), False),
        (SimpleNamespace(status=SimpleNamespace(available_replicas=1)), True),
    ],
)
def test_has_available_replica(deployment, expected):
    assert has_available_replica(deployment) is expected


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize(
    ("ready_at", "deadline", "expected", "calls"),
    [
        (0, 10.0, True, 1),  # ready at once
        (2, 10.0, True, 3),  # ready on the third check, before the deadline
        (None, 10.0, False, 3),  # checks at 0, 5 and 10, then gives up
        (None, 0.0, False, 1),  # deadline already passed: one check, no wait
    ],
)
def test_poll_until(ready_at, deadline, expected, calls):
    clock = _Clock()
    seen = []

    def check():
        seen.append(clock.now)
        return ready_at is not None and len(seen) > ready_at

    assert poll_until(check, deadline, 5.0, clock, clock.sleep) is expected
    assert len(seen) == calls
