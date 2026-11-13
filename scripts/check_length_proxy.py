"""Check the tokenizer-free length proxy against a tokenizer (story E7-S5, R7.1), with no pool data.

Counts synthetic text of the gate input's shape (src/remvalgate/harness/length_check.py) with the
tokenizer saved at --tokenizer, loaded from local files only and counted as
scripts/export_training_data.py counts, and prints, per application and kind, the largest proxy
and count, the number of samples over the 2,048 token budget by each, and the range of their ratio
over the samples. Exits 1 when the tokenizer counts more than the proxy on a gate input, the case
in which the proxy's over-budget list can miss a row.
The result for Qwen3.5-4B is in docs/training_data.md (Length check).

    uv run --with transformers==5.22.1 python scripts/check_length_proxy.py --tokenizer DIR
"""

import argparse
import sys
from pathlib import Path

from transformers import AutoTokenizer

from remvalgate.harness.length_check import compare, proxy_undercounts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer", required=True, help="a local tokenizer folder")
    args = parser.parse_args()
    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    rows = compare(lambda text: len(tokenizer(text, add_special_tokens=False)["input_ids"]))
    print(f"tokenizer {Path(args.tokenizer).name}")
    for row in rows:
        print(
            f"{row['application']:<17}  {row['kind']:<10}  samples {row['samples']}  "
            f"proxy max {row['proxy_max']:>4}  count max {row['count_max']:>4}  "
            f"over budget {row['proxy_over']:>2} by proxy, {row['count_over']:>2} by count  "
            f"count/proxy {row['ratio_min']:.3f} to {row['ratio_max']:.3f}"
        )
    under = proxy_undercounts(rows)
    for row in under:
        print(f"the tokenizer counts more than the proxy on a {row['application']} gate input")
    return 1 if under else 0


if __name__ == "__main__":
    sys.exit(main())
