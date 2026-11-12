from remvalgate.retrieval.query_form import FORMS, choose, query_in_form, strip_columns

# a made-up namespace in kubectl's padded layout
OBSERVATION = "\n".join(
    [
        "NAME                         READY   UP-TO-DATE   AVAILABLE   AGE",
        "deployment.apps/demo-db      1/1     1            1           2d19h",
        "deployment.apps/demo-front   0/1     1            0           5m",
        "",
        "NAME                              READY   STATUS             RESTARTS      AGE",
        "pod/demo-db-6f9c7d9b4-abcde       1/1     Running            0             2d19h",
        "pod/demo-front-7c8d5f6c9-fghij    0/1     CrashLoopBackOff   3 (37s ago)   5m",
    ]
)


def test_the_raw_form_is_the_observation_itself():
    assert query_in_form(OBSERVATION, "raw") == OBSERVATION


def test_age_is_removed_from_both_tables_and_nothing_else_changes():
    lines = query_in_form(OBSERVATION, "no_age").splitlines()
    assert lines[0] == "NAME                         READY   UP-TO-DATE   AVAILABLE"
    assert lines[2] == "deployment.apps/demo-front   0/1     1            0"
    assert lines[4].endswith("RESTARTS")
    assert lines[6].endswith("CrashLoopBackOff   3 (37s ago)")
    assert not any("2d19h" in line or line.endswith("5m") for line in lines)


def test_restarts_and_its_ago_note_are_removed_with_age():
    lines = query_in_form(OBSERVATION, "no_age_restarts").splitlines()
    assert lines[4] == "NAME                              READY   STATUS"
    assert lines[6] == "pod/demo-front-7c8d5f6c9-fghij    0/1     CrashLoopBackOff"
    # the status that shows the fault stays
    assert "CrashLoopBackOff" in "\n".join(lines)


def test_text_outside_a_table_is_kept_and_unknown_columns_change_nothing():
    text = "No resources found in demo namespace."
    assert strip_columns(text, ("AGE",)) == text
    assert strip_columns(OBSERVATION, ("NOMINATED",)) == OBSERVATION
    assert set(FORMS) == {"raw", "no_age", "no_age_restarts", "no_age_restarts_pod_ids"}


def test_pod_ids_are_dropped_and_deployment_names_kept():
    lines = query_in_form(OBSERVATION, "no_age_restarts_pod_ids").splitlines()
    assert lines[5].startswith("pod/demo-db ") and "6f9c7d9b4" not in lines[5]
    assert lines[6].startswith("pod/demo-front ") and "CrashLoopBackOff" in lines[6]
    assert lines[1].startswith("deployment.apps/demo-db ")


def test_the_rule_keeps_forms_within_one_query_of_raw_and_picks_the_most_stable():
    quality = {"raw": 9, "no_age": 9, "no_age_restarts": 8, "no_age_restarts_pod_ids": 7}
    stability = {"raw": 0.2, "no_age": 0.5, "no_age_restarts": 0.9, "no_age_restarts_pod_ids": 1.0}
    # pod_ids is 2 below raw, so it is out even though it is the most stable
    assert choose(quality, stability) == "no_age_restarts"
    # a tie keeps the form that changes the query least
    assert choose(quality, dict.fromkeys(FORMS, 0.5)) == "raw"
