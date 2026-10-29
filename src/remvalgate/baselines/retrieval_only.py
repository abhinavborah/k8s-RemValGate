"""Baseline 2: the recorded fix of the most similar past incident, with no reasoning loop.

The query is the observed symptoms in plain words. The top hit's fix names the service of the
past incident, so that name is replaced by the observed service; without that, the command would
target the wrong service and the baseline could not recover anything.
"""

import re
import shlex

from remvalgate.baselines import BaselineAction, escalate
from remvalgate.baselines.observe import Observation, symptom_query
from remvalgate.harness.allowlist import validate_namespace
from remvalgate.retrieval import Retriever


def replace_service(command: str, recorded: str, observed: str) -> str:
    """Replace the recorded service name where it stands as a whole name."""
    whole = rf"(?<![A-Za-z0-9-]){re.escape(recorded)}(?![A-Za-z0-9-])"
    return re.sub(whole, observed, command)


def _in_namespace(command: str, namespace: str) -> str:
    tokens = shlex.split(command)
    if any(t in ("-n", "--namespace") or t.startswith("--namespace=") for t in tokens):
        return command
    return shlex.join([tokens[0], "-n", namespace, *tokens[1:]])


def retrieval_only(
    retriever: Retriever, observations: list[Observation], problem_id: str, namespace: str
) -> BaselineAction:
    """Return the top hit's fix for the first observed service, or escalate.

    A retriever refusal (RetrieverRefused) is not caught, and the command is not filtered:
    the harness applies the allow-list to every system's command.
    """
    validate_namespace(namespace)
    if not observations:
        return escalate("nothing was observed to query with")
    (hit,) = retriever.retrieve(symptom_query(observations), problem_id, k=1)
    service = observations[0].service
    command = replace_service(hit.entry.fix_command, hit.entry.target_service, service)
    return BaselineAction(
        _in_namespace(command, namespace), f"top hit {hit.entry.entry_id} on {service}"
    )
