import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.allowlist import (
    DEFAULT_POLICY_PATH,
    Reason,
    Rejected,
    Verdict,
    check,
    load_policy,
    reason_counts,
    tokenize,
)

POLICY = load_policy(DEFAULT_POLICY_PATH)
NS = "sandbox-a"


def _write_policy(tmp_path, **changes):
    data = json.loads(DEFAULT_POLICY_PATH.read_text())
    for key, value in changes.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    return path


def test_placeholder_policy_loads():
    policy = load_policy(DEFAULT_POLICY_PATH)
    assert policy.binaries == {"kubectl"}
    assert "rollout restart" in policy.verbs
    assert policy.flags["--replicas"] is True


def test_policy_missing_key_raises(tmp_path):
    with pytest.raises(ValueError, match="flags"):
        load_policy(_write_policy(tmp_path, flags=None))


def test_policy_extra_key_raises(tmp_path):
    with pytest.raises(ValueError, match="deny"):
        load_policy(_write_policy(tmp_path, deny=["--all"]))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("binaries", "kubectl"),
        ("resources", ["pods", 1]),
        ("verbs", ["set  image"]),
        ("verbs", ["rollout history deploy"]),
        ("flags", ["-o"]),
        ("flags", {"-o": "false"}),
        ("flags", {"-o": ["wide", 1]}),
    ],
)
def test_policy_wrong_value_raises(tmp_path, key, value):
    with pytest.raises(ValueError, match=key):
        load_policy(_write_policy(tmp_path, **{key: value}))


def test_policy_not_an_object_raises(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text("[]")
    with pytest.raises(ValueError):
        load_policy(path)


def test_policy_flags_read_only():
    with pytest.raises(TypeError):
        load_policy(DEFAULT_POLICY_PATH).flags["--kubeconfig"] = True


def test_reason_set_is_fixed():
    assert [r.value for r in Reason] == [
        "EMPTY",
        "SHELL_METACHAR",
        "PARSE_ERROR",
        "BINARY_NOT_ALLOWED",
        "OUT_OF_SANDBOX",
        "FLAG_DENIED",
        "VERB_NOT_ALLOWED",
        "RESOURCE_NOT_ALLOWED",
    ]


# synthetic fixtures only: namespace sandbox-a, names web, api, web-0
REJECTED = [
    ("", Reason.EMPTY),
    ("   ", Reason.EMPTY),
    ("kubectl get pods -n sandbox-a; kubectl delete ns sandbox-a", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a && rm -rf /", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a || true", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a | sh", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a &", Reason.SHELL_METACHAR),
    ("kubectl get pods -n $(echo sandbox-a)", Reason.SHELL_METACHAR),
    ("kubectl get pods -n `echo sandbox-a`", Reason.SHELL_METACHAR),
    ('kubectl get pods -n "$(id)"', Reason.SHELL_METACHAR),
    ('kubectl get pods -n "`id`"', Reason.SHELL_METACHAR),
    ("kubectl get pods -n ${NS}", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a > out", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a < in", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a 2>&1", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a\nkubectl delete ns sandbox-a", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a {--all-namespaces,}", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a#x --all-namespaces", Reason.SHELL_METACHAR),
    ("kubectl delete pods web* -n sandbox-a", Reason.SHELL_METACHAR),
    (r"kubectl get pods -n sandbox\-a", Reason.SHELL_METACHAR),
    ("kubectl get pods -n 'sandbox-a", Reason.PARSE_ERROR),
    ("KUBECONFIG=/x kubectl get pods -n sandbox-a", Reason.BINARY_NOT_ALLOWED),
    ("env kubectl get pods -n sandbox-a", Reason.BINARY_NOT_ALLOWED),
    ("sudo kubectl get pods -n sandbox-a", Reason.BINARY_NOT_ALLOWED),
    ("bash -c 'kubectl get pods -n sandbox-a'", Reason.BINARY_NOT_ALLOWED),
    ("/usr/bin/kubectl get pods -n sandbox-a", Reason.BINARY_NOT_ALLOWED),
    ("helm rollback web 1 -n sandbox-a", Reason.BINARY_NOT_ALLOWED),
    ("kubectl get pods -A", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods --all-namespaces", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods --all-namespaces=true", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods -n kube-system", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods --namespace=other", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods -n sandbox-a -n other", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods -n sandbox-a -n sandbox-a", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods -n", Reason.OUT_OF_SANDBOX),
    ("kubectl delete pods --all -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a --kubeconfig=/x", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a --context other", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a --cluster other", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a -s https://example.invalid", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a --as admin", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a --as-group admins", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a --token x", Reason.FLAG_DENIED),
    ("kubectl apply -f m.yaml -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl apply -k dir -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl delete pod web-0 -n sandbox-a --grace-period=0", Reason.FLAG_DENIED),
    ("kubectl exec web-0 -n sandbox-a -- sh", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a -o", Reason.FLAG_DENIED),
    # an empty attached value must not swallow the next token as the value
    ("kubectl get pods -n sandbox-a -o= --context=other", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a -l= -A", Reason.OUT_OF_SANDBOX),
    ("kubectl get pods -n= sandbox-a", Reason.OUT_OF_SANDBOX),
    # output formats that read a local file
    ("kubectl get pods -n sandbox-a -o jsonpath-file=x", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a -o=go-template-file=x", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a --output templatefile=x", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a -o custom-columns-file=x", Reason.FLAG_DENIED),
    ("kubectl exec web-0 -n sandbox-a", Reason.VERB_NOT_ALLOWED),
    ("kubectl drain web -n sandbox-a", Reason.VERB_NOT_ALLOWED),
    ("kubectl rollout pause deploy/web -n sandbox-a", Reason.VERB_NOT_ALLOWED),
    ("kubectl -n sandbox-a", Reason.VERB_NOT_ALLOWED),
    ("kubectl delete ns sandbox-a -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl delete namespace sandbox-a -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl get nodes -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl get secrets -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl get deploy,secrets -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl get deploy/web secrets/x -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl get -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    # one command breaking several rules: the earliest check in Reason order wins
    ("kubectl delete ns --all -A; rm x", Reason.SHELL_METACHAR),
    ("sudo kubectl delete ns --all -A", Reason.BINARY_NOT_ALLOWED),
    ("kubectl delete ns --all -A", Reason.OUT_OF_SANDBOX),
    ("kubectl delete ns --all -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl drain ns -n sandbox-a", Reason.VERB_NOT_ALLOWED),
]

ADMITTED = [
    "kubectl get pods -n sandbox-a",
    "kubectl -n sandbox-a get pods",
    "kubectl get pods -nsandbox-a",
    "kubectl get pods --namespace sandbox-a",
    "kubectl get pods --namespace=sandbox-a",
    "kubectl get deploy,svc -n sandbox-a -o wide",
    'kubectl get pods -n sandbox-a -l "app=web"',
    "kubectl describe svc api -n sandbox-a",
    "kubectl scale deployment web --replicas=1 -n sandbox-a",
    "kubectl scale deployment web --replicas 1 -n sandbox-a",
    "kubectl patch svc api -n sandbox-a --type merge"
    ' -p \'{"spec":{"ports":[{"port":9000,"targetPort":9001}]}}\'',
    "kubectl rollout restart deploy/web -n sandbox-a",
    "kubectl delete pod web-0 -n sandbox-a",
]


@pytest.mark.parametrize(("command", "reason"), REJECTED)
def test_rejected_with_reason(command, reason):
    verdict = check(command, POLICY, NS)
    assert verdict == Verdict(command=command, admitted=False, reason=reason)


@pytest.mark.parametrize("command", ADMITTED)
def test_admitted_unchanged(command):
    verdict = check(command, POLICY, NS)
    assert verdict == Verdict(command=command, admitted=True, reason=None)


@pytest.mark.parametrize(
    ("command", "admitted"),
    [
        ("kubectl get pods -n sandbox-a -w", True),
        ("kubectl get pods -n sandbox-a --watch", True),
        # pflag reads -wA as -w -A
        ("kubectl get pods -n sandbox-a -wA", False),
        ("kubectl get pods -n sandbox-a -wn kube-system", False),
        ("kubectl get pods -n sandbox-a --watch=other", False),
    ],
)
def test_boolean_flag_takes_no_value(tmp_path, command, admitted):
    flags = json.loads(DEFAULT_POLICY_PATH.read_text())["flags"] | {"-w": False, "--watch": False}
    policy = load_policy(_write_policy(tmp_path, flags=flags))
    assert check(command, policy, NS).admitted is admitted


def test_tokenize_returns_argv():
    assert tokenize("kubectl get pods -n 'sandbox-a'") == [
        "kubectl",
        "get",
        "pods",
        "-n",
        "sandbox-a",
    ]
    with pytest.raises(Rejected) as excinfo:
        tokenize("a; b")
    assert excinfo.value.reason is Reason.SHELL_METACHAR


@given(st.text())
def test_check_never_raises(command):
    verdict = check(command, POLICY, NS)
    assert verdict.command == command
    assert verdict.admitted == (verdict.reason is None)


NS_FORMS = ["-n sandbox-a", "-nsandbox-a", "--namespace sandbox-a", "--namespace=sandbox-a"]


@given(
    verb=st.sampled_from(sorted(POLICY.verbs)),
    resource=st.sampled_from(sorted(POLICY.resources)),
    name=st.from_regex(r"[a-z][a-z0-9-]{0,10}", fullmatch=True),
    ns_form=st.sampled_from(NS_FORMS),
    ns_first=st.booleans(),
)
def test_generated_permitted_admitted(verb, resource, name, ns_form, ns_first):
    parts = [verb, resource, name]
    parts = [ns_form, *parts] if ns_first else [*parts, ns_form]
    command = " ".join(["kubectl", *parts])
    assert check(command, POLICY, NS) == Verdict(command=command, admitted=True, reason=None)


@given(
    base=st.sampled_from(ADMITTED),
    op=st.sampled_from([";", "&&", "||", "|", "&", ">", "<", "$(", "`"]),
    tail=st.text(),
)
def test_operator_suffix_rejected(base, op, tail):
    assert check(base + op + tail, POLICY, NS).reason is Reason.SHELL_METACHAR


def test_reason_counts_empty():
    assert reason_counts([]) == {reason: 0 for reason in Reason}


def test_reason_counts_mixed():
    precedence = [command for command, _ in REJECTED[-5:]]
    verdicts = [check(command, POLICY, NS) for command in [*precedence, *ADMITTED[:2]]]
    counts = reason_counts(verdicts)
    assert counts.keys() == set(Reason)
    assert counts[Reason.SHELL_METACHAR] == 1
    assert counts[Reason.OUT_OF_SANDBOX] == 1
    assert counts[Reason.EMPTY] == 0
    assert sum(counts.values()) == 5
