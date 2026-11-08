"""Summary of oracle labels files (story E3-S5, R3.5), with no cluster.

Reads the <run id>.labels.json files that scripts/run_oracle_labels.py writes, for example the
files of a run that was finished in two parts, and prints the counts, the coverage for k = 1 to K
and the K rule of docs/oracle_labels.md (src/remvalgate/harness/oracle.py). A labels file whose
counts disagree with its labels is refused, and so are a problem labelled twice and files of more
than one slice, such as the pool's with a development dry run's.

    uv run python scripts/summarize_oracle_labels.py runs/oracle/<run id>.labels.json ...
"""

import json
import sys
from pathlib import Path

from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry
from remvalgate.harness.oracle import one_slice, read_labels, summary


def summarize(paths: list[str | Path]) -> dict:
    problems = [read_labels(json.loads(Path(p).read_text(encoding="utf-8"))) for p in paths]
    # dry runs write to the same folder, so a glob over it can pick up development files
    one_slice([p.problem_id for p in problems], load_registry(DEFAULT_REGISTRY_PATH).slices)
    return summary(problems)


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    print(json.dumps(summarize(paths), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
