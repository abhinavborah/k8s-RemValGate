"""Baseline 2: the recorded fix of the most similar past incident, with no reasoning loop.

The query is the observed symptoms in plain words. The top hit's fix names the service of the
past incident, so that name is replaced by the observed service; without that, the command would
target the wrong service and the baseline could not recover anything.
"""

import re
import shlex

from remvalgate.baselines import BaselineAction, escalate
from remvalgate.baselines.observe import Observation, symptom_query
from remvalgate.harness.allowlist import split_chain, validate_namespace
from remvalgate.retrieval import Retriever


def replace_service(command: str, recorded: str, observed: str) -> str:
    """Replace the recorded service name where it stands as a whole name."""
    whole = rf"(?<![A-Za-z0-9-]){re.escape(recorded)}(?![A-Za-z0-9-])"
    return re.sub(whole, observed, command)


def _in_namespace(command: str, namespace: str) -> str:
    """Set the command's namespace to the run's: a recorded -n or --namespace is dropped, since a
    pool record carries the namespace of its own incident, which may be the other app's.

    Only kubectl's own arguments are read; everything after an exec's -- is kept as it is.
    """
    tokens = shlex.split(command)
    cut = tokens.index("--") if "--" in tokens else len(tokens)
    kept, skip = [], False
    for token in tokens[1:cut]:
        if skip:
            skip = False
        elif token in ("-n", "--namespace"):
            skip = True
        elif not (token.startswith("--namespace=") or token.startswith("-n")):
            kept.append(token)
    return shlex.join([tokens[0], "-n", namespace, *kept, *tokens[cut:]])


def retrieval_only(
    retriever: Retriever, observations: list[Observation], problem_id: str, namespace: str
) -> BaselineAction:
    """Return the top hit's fix for the first observation, or escalate.

    The query and the service both come from the first observation, as in the rule baseline, so
    the fix of one fault never lands on the service of another. Escalates when the retriever
    returns no hit. A retriever refusal (RetrieverRefused) is not caught, and the command is not
    filtered: the harness applies the allow-list to every system's command.
    """
    validate_namespace(namespace)
    if not observations:
        return escalate("nothing was observed to query with")
    first = observations[0]
    hits = retriever.retrieve(symptom_query([first]), problem_id, k=1)
    if not hits:
        return escalate("the retriever returned no hit")
    hit = hits[0]
    service = first.service
    # a fix of several commands is joined with " && "; each part is rewritten on its own, and
    # the harness checks each part against the allow-list (check_chain)
    command = " && ".join(
        _in_namespace(replace_service(part, hit.entry.target_service, service), namespace)
        for part in split_chain(hit.entry.fix_command)
    )
    return BaselineAction(command, f"top hit {hit.entry.entry_id} on {service}")
