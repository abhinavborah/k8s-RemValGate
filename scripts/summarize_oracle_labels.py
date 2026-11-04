"""Summary of oracle labels files (story E3-S5, R3.5), with no cluster.

Reads the <run id>.labels.json files that scripts/run_oracle_labels.py writes, for example the
files of a run that was finished in two parts, and prints the counts, the coverage for k = 1 to K
and the K rule of docs/oracle_labels.md (src/remvalgate/harness/oracle.py). A labels file whose
counts disagree with its labels is refused, and so is a problem labelled twice.

    uv run python scripts/summarize_oracle_labels.py runs/oracle/<run id>.labels.json ...
"""

import json
import sys
from pathlib import Path

from remvalgate.harness.oracle import read_labels, summary


def summarize(paths: list[str | Path]) -> dict:
    return summary([read_labels(json.loads(Path(p).read_text(encoding="utf-8"))) for p in paths])


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    print(json.dumps(summarize(paths), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
