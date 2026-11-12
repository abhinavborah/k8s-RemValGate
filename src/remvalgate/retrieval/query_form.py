"""Forms of the retrieval query, the planner's first observation (`kubectl get deployments,pods`).

The observation carries columns that change from run to run of one problem: AGE (how long a
Deployment or pod has existed) and RESTARTS (how often a container restarted, with how long ago).
#164 compares the raw text with forms that drop them, so retrieval depends on the fault and less on
how long the cluster has been up. Pod names also end in random ids (the ReplicaSet hash and the pod
id), new for every pod, so one form drops those too. strip_columns removes named columns from each
kubectl table, using the header's column positions, which kubectl pads to the widest value.
"""

import re

FORMS = {
    "raw": (),
    "no_age": ("AGE",),
    "no_age_restarts": ("AGE", "RESTARTS"),
    # also the random suffixes of pod names (ReplicaSet hash and pod id), new for every pod
    "no_age_restarts_pod_ids": ("AGE", "RESTARTS"),
}
_COLUMN = re.compile(r"\S+")
# the two ids end the pod name: anchored to the whitespace after the name, so a name segment of 6
# to 10 characters followed by one of 5 (home-timeline-redis) is never taken for them
_POD_IDS = re.compile(r"^(pod/\S+)-[a-z0-9]{6,10}-[a-z0-9]{5}(?=\s|$)", re.MULTILINE)


def strip_columns(text: str, columns: tuple[str, ...]) -> str:
    """text with the named columns removed from every table that has them; other lines unchanged.

    A table starts at a header line beginning with NAME and runs until a line that is blank or the
    next header. Trailing spaces left by a removed last column are trimmed.
    """
    if not columns:
        return text
    out, spans = [], []
    for line in text.splitlines():
        if line.startswith("NAME "):
            heads = list(_COLUMN.finditer(line))
            spans = [
                (h.start(), heads[i + 1].start() if i + 1 < len(heads) else None)
                for i, h in enumerate(heads)
                if h.group() in columns
            ]
        elif not line.strip():
            spans = []
        for start, end in reversed(spans):
            line = line[:start] + (line[end:] if end is not None else "")
        out.append(line.rstrip() if spans else line)
    return "\n".join(out)


def query_in_form(text: str, form: str) -> str:
    stripped = strip_columns(text, FORMS[form])
    return _POD_IDS.sub(r"\1", stripped) if form == "no_age_restarts_pod_ids" else stripped


# the rule of #164, fixed before the test ran: Hit@3 at most MARGIN queries below the raw form's
MARGIN = 1


def choose(quality: dict[str, int], stability: dict[str, float]) -> str:
    """The form #164's rule picks: among the forms whose Hit@3 is at most MARGIN below raw's, the
    most stable; on a tie, the one that changes the query least, in FORMS order."""
    kept = [f for f in FORMS if quality[f] >= quality["raw"] - MARGIN]
    best = max(stability[f] for f in kept)
    return next(f for f in kept if stability[f] == best)


# the form #164 chose (runs/query_forms/query_forms-20261112t145249z.json): Hit@3 6 of 16 against
# raw's 7, and the same top 3 as the recorded query in 63 of 66 past runs against raw's 32
QUERY_FORM = "no_age_restarts_pod_ids"


def retrieval_query(observation: str, form: str = QUERY_FORM) -> str:
    """The query retrieval runs on: the first observation in the chosen form."""
    return query_in_form(observation, form)
