"""Bounded previews of what get_metrics and get_traces save to disk.

Both tools return a path, and a model that cannot open files would get nothing from them. The
preview is deterministic: per service for traces, per metric for metrics. The tool's own text is
kept first, so the path is still shown.
"""

import csv
import re
from collections import defaultdict
from pathlib import Path

_TRACES = re.compile(r"exported to: (.+?)\s*$")
_METRICS = re.compile(r"exported to directory: (.+?)\s*$", re.MULTILINE)


def _traces_summary(path: Path, top: int, since: int | None) -> list[str]:
    spans: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if since is not None and int(row["start_time"]) < since:
                continue
            entry = spans[row["service_name"]]
            entry[0] += 1
            entry[1] += row["has_error"] == "True"
            entry[2] += float(row["duration"])
    if not spans:
        return ["no spans in the window"]
    ranked = sorted(spans.items(), key=lambda kv: (-kv[1][1], -kv[1][0], kv[0]))
    lines = ["service: spans, error spans, mean duration in microseconds"]
    lines += [
        f"{name}: {n}, {errors}, {total / n:.0f}" for name, (n, errors, total) in ranked[:top]
    ]
    return lines


def _metrics_summary(directory: Path, top: int, since: int | None) -> list[str]:
    lines = ["metric: series, then the highest last values as pod=value"]
    for path in sorted(directory.rglob("*.csv")):
        last: dict[str, tuple[int, float]] = {}
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                stamp, value = int(row["timestamp"]), float(row["value"])
                if row["cmdb_id"] not in last or stamp >= last[row["cmdb_id"]][0]:
                    last[row["cmdb_id"]] = (stamp, value)
        best = sorted(last.items(), key=lambda kv: (-kv[1][1], kv[0]))[:top]
        shown = ", ".join(f"{pod.split('.')[-1]}={value:g}" for pod, (_, value) in best)
        lines.append(f"{path.stem.removeprefix('kpi_')}: {len(last)}, {shown}")
    return lines


def preview(tool: str, output: str, limit: int, top: int = 3, since: int | None = None) -> str:
    """Return the tool's output with a summary of the saved data, cut to limit characters.

    since is a time in microseconds since the epoch: spans that started earlier are left out of the
    trace summary. The tool's export carries spans from before its window, so a caller that knows
    when the fault was injected passes that time. The metrics summary ignores it.

    A tool other than get_traces and get_metrics, or an output with no path, comes back as it is.
    """
    if tool == "get_traces":
        match = _TRACES.search(output)
        summarise = _traces_summary
        top = 12
    elif tool == "get_metrics":
        match = _METRICS.search(output)
        summarise = _metrics_summary
    else:
        return output
    if match is None or not Path(match.group(1)).exists():
        return output
    head = output.split("\n", 1)[0]
    text = "\n".join([head, *summarise(Path(match.group(1)), top, since)])
    if len(text) <= limit:
        return text
    note = f"\n[preview cut, {len(text)} characters]"
    return text[: limit - len(note)] + note
