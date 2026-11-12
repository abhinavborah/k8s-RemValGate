"""Ablation without interactive tools (story E5-S3, BRD R5.3): the log rule of the fixed telemetry
bundle, and the diagnostics of a run read from its transcript.

The arm notools runs the untuned planner with retrieval configuration (iii), as baseline 3c does,
with get_logs, get_metrics and get_traces removed and read-only exec_shell (kubectl get and
describe) kept. In their place the harness collects a fixed bundle once, right after the first
observation: get_metrics and get_traces of the namespace, and get_logs of each Deployment whose
pods are not healthy then, sorted by name, at most LOG_SERVICES. Other kinds are left out, because
AIOpsLab's get_logs finds a pod by a label holding the service name (app in Social Network,
io.kompose.service in Hotel Reservation), which equals the Deployment's name in both applications
at the pinned aiopslab-applications commit (8038be6: Social Network's base Deployment template, and
the geo and rate manifests of Hotel Reservation). The rule reads only the pod status the planner
could also see, and never the problem's fault type or target.

diagnostics describes one run of either arm: how its loop stopped, its turns and refused turns, its
tool calls by name (a call to a removed tool is counted, and was refused), its completion tokens
and model time, its largest prompt in tokens as the server counted it (None in transcripts that
predate the count), and the size of its bundle.
"""

from collections import Counter
from collections.abc import Mapping

from remvalgate.harness.harm import Status

# the most Deployments whose logs go into the bundle
LOG_SERVICES = 3


def unready_services(status: Mapping[str, Status], limit: int = LOG_SERVICES) -> list[str]:
    """The names of the Deployments whose workload is unhealthy, sorted, at most limit.

    status is parse_pods of the namespace, keyed as harm keys a workload.
    """
    return sorted(
        key.removeprefix("Deployment/")
        for key, s in status.items()
        if key.startswith("Deployment/") and not s.healthy
    )[:limit]


def diagnostics(transcript: Mapping) -> dict:
    """The diagnostics of one run, from its transcript: dataclasses.asdict of the planner's Plan,
    with the bundle of a notools run."""
    steps = transcript["steps"]
    bundle = transcript.get("bundle", [])
    # a reply that never parsed has no tool, and a tool that is not a str is no call by name
    calls = Counter(s["tool"] for s in steps if isinstance(s["tool"], str))
    # older transcripts, 3c's among them, record no prompt tokens
    prompts = [s["prompt_tokens"] for s in steps if s.get("prompt_tokens") is not None]
    return {
        "stop": transcript["stop"],
        "turns": len(steps),
        "refused_turns": sum(s["observation"].startswith("Refused") for s in steps),
        "tool_calls": dict(sorted(calls.items())),
        "completion_tokens": sum(s["completion_tokens"] for s in steps),
        "model_seconds": round(sum(s["seconds"] for s in steps), 1),
        "max_prompt_tokens": max(prompts, default=None),
        "bundle_parts": len(bundle),
        "bundle_chars": sum(len(part["output"]) for part in bundle),
    }
