"""Tests of the synthetic text that checks length_proxy, with no tokenizer and no pool data."""

import json
import random
import re

from remvalgate.harness.length_check import (
    APPS,
    SUFFIX_CHARS,
    compare,
    gate_input,
    listing,
    samples,
)
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry
from remvalgate.harness.training_data import TOKEN_BUDGET, length_proxy
from remvalgate.retrieval.pool_corpus import pool_incidents

# the characters SafeEncodeString maps the decimal digits 0 to 9 onto
HASH_CHARS = set("456789bcdf")
KINDS = ("listing", "pod names", "gate input")


def test_listing_is_laid_out_as_kubectl_lays_it_out():
    names = APPS["Social Network"][1]
    deployments, pods = listing(names, random.Random(0)).removesuffix("\n").split("\n\n")
    headers = ("NAME READY UP-TO-DATE AVAILABLE AGE", "NAME READY STATUS RESTARTS AGE")
    for table, header in zip((deployments, pods), headers, strict=True):
        lines = table.split("\n")
        cells = [line.split() for line in lines]
        assert cells[0] == header.split()
        assert len(lines) == len(names) + 1
        # each column but the last is its widest cell plus 3 wide, and at least 6
        widths = [max(6, max(len(c[i]) for c in cells) + 3) for i in range(len(cells[0]) - 1)]
        starts = [sum(widths[:i]) for i in range(len(cells[0]))]
        for line in lines:
            assert [m.start() for m in re.finditer(r"\S+", line)] == starts
            assert line == line.rstrip()


def test_pods_are_named_as_kubernetes_names_them():
    names = APPS["Hotel Reservation"][1]
    text = listing(names, random.Random(1))
    pods = [line.split()[0] for line in text.splitlines() if line.startswith("pod/")]
    for name, pod in zip(names, pods, strict=True):
        template, suffix = pod.removeprefix(f"pod/{name}-").split("-")
        assert 1 <= len(template) <= 10
        assert set(template) <= HASH_CHARS
        assert len(suffix) == 5
        assert set(suffix) <= set(SUFFIX_CHARS)


def test_gate_input_has_the_fields_of_the_export_in_order():
    rng = random.Random(2)
    text = listing(APPS["Social Network"][1], rng)
    obj = json.loads(gate_input("Social Network", text, rng))
    assert list(obj) == ["task", "first_observation", "diagnosis", "retrieved", "candidate"]
    assert list(obj["diagnosis"]) == ["fault_type", "service", "reason"]
    assert obj["first_observation"] == text
    assert len(obj["retrieved"]) == 3
    assert obj["diagnosis"]["service"] in APPS["Social Network"][1]


def test_samples_hold_no_pool_record():
    texts = [text for seed in range(3) for _, _, text in samples(seed)]
    for entry in pool_incidents(load_registry(DEFAULT_REGISTRY_PATH)):
        for text in texts:
            assert entry.summary not in text
            assert entry.fix_command not in text


def test_samples_depend_on_the_seed_only():
    assert samples(3) == samples(3)
    assert samples(3) != samples(4)
    assert [(app, kind) for app, kind, _ in samples(3)] == [(a, k) for a in APPS for k in KINDS]


def test_compare_gives_each_kind_its_ratios_over_the_seeds():
    rows = compare(length_proxy, seeds=range(2))
    assert [(r["application"], r["kind"]) for r in rows] == [(a, k) for a in APPS for k in KINDS]
    assert all(r["samples"] == 2 and r["ratio_min"] == r["ratio_max"] == 1 for r in rows)
    doubled = compare(lambda text: 2 * length_proxy(text), seeds=range(2))
    assert all(r["ratio_max"] == 2 and r["count_max"] == 2 * r["proxy_max"] for r in doubled)
    # a count over the budget on every sample, a proxy over it on none
    rows = compare(lambda text: TOKEN_BUDGET + 1, range(2))
    over = {(r["application"], r["kind"]): (r["count_over"], r["proxy_over"]) for r in rows}
    assert over["Hotel Reservation", "gate input"] == (2, 0)
