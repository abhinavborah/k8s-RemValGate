from remvalgate.harness.protocol import interleaved


def test_every_arm_runs_on_a_problem_before_the_next_and_the_first_arm_rotates():
    order = interleaved([1000, 1001], ["p1", "p2", "p3"], ["3a", "3b", "3c"])
    assert order[:9] == [
        (1000, "p1", "3a"), (1000, "p1", "3b"), (1000, "p1", "3c"),
        (1000, "p2", "3b"), (1000, "p2", "3c"), (1000, "p2", "3a"),
        (1000, "p3", "3c"), (1000, "p3", "3a"), (1000, "p3", "3b"),
    ]  # fmt: skip
    # the second seed repeats the order after the first seed is done
    assert order[9:] == [(1001, p, a) for _, p, a in order[:9]]
    # each arm goes first equally often over the problems
    firsts = [order[i][2] for i in range(0, 9, 3)]
    assert sorted(firsts) == ["3a", "3b", "3c"]


def test_one_arm_and_one_seed_keep_the_problem_order():
    assert interleaved([1000], ["p1", "p2"], ["1"]) == [(1000, "p1", "1"), (1000, "p2", "1")]
