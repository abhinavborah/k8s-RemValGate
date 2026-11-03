import json
import shlex

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.allowlist import (
    DEFAULT_POLICY_PATH,
    Reason,
    ReasonCounts,
    Rejected,
    Verdict,
    check,
    check_chain,
    load_policy,
    parse_args,
    reason_counts,
    split_chain,
    split_targets,
    split_verb,
    tokenize,
)

POLICY = load_policy(DEFAULT_POLICY_PATH)
NS = "sandbox-a"


def _policy_data():
    return json.loads(DEFAULT_POLICY_PATH.read_text())


def _write_policy(tmp_path, data):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    return path


def _with(**changes):
    data = _policy_data()
    for key, value in changes.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    return data


def test_default_policy_loads():
    policy = load_policy(DEFAULT_POLICY_PATH)
    assert policy.binaries == {"kubectl"}
    assert "rollout restart" in policy.verbs
    assert policy.verbs["scale"].flags["--replicas"] == {"1", "2", "3"}
    assert policy.verbs["delete"].name_required is True
    assert "hostNetwork" in policy.patch_denied_keys
    assert policy.exec_shells == {"mongo", "mongosh"}
    assert "createUser" in policy.exec_methods


def test_policy_missing_key_raises(tmp_path):
    with pytest.raises(ValueError, match="patch_denied_keys"):
        load_policy(_write_policy(tmp_path, _with(patch_denied_keys=None)))


def test_policy_extra_key_raises(tmp_path):
    with pytest.raises(ValueError, match="deny"):
        load_policy(_write_policy(tmp_path, _with(deny=["--all"])))


RULE = {"resources": ["pods"], "flags": {}, "name_required": False}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("binaries", "kubectl"),
        ("patch_denied_keys", ["image", 1]),
        ("verbs", ["get"]),
        ("verbs", {"set  image": RULE}),
        ("verbs", {"rollout history deploy": RULE}),
        ("verbs", {"get": {"resources": ["pods"]}}),
        ("verbs", {"get": RULE | {"resources": "pods"}}),
        ("verbs", {"get": RULE | {"name_required": "no"}}),
        ("verbs", {"get": RULE | {"flags": ["-o"]}}),
        ("verbs", {"get": RULE | {"flags": {"-o": "false"}}}),
        ("verbs", {"get": RULE | {"flags": {"-o": ["wide", 1]}}}),
        # one flag must not take a value for one verb and none for another
        (
            "verbs",
            {"get": RULE | {"flags": {"-w": False}}, "describe": RULE | {"flags": {"-w": True}}},
        ),
    ],
)
def test_policy_wrong_value_raises(tmp_path, key, value):
    with pytest.raises(ValueError, match=key):
        load_policy(_write_policy(tmp_path, _with(**{key: value})))


@pytest.mark.parametrize(
    "exec_rule",
    [["mongo"], {"shells": ["mongo"]}, {"shells": "mongo", "methods": []}],
)
def test_policy_wrong_exec_raises(tmp_path, exec_rule):
    with pytest.raises(ValueError, match="exec"):
        load_policy(_write_policy(tmp_path, _with(exec=exec_rule)))


def test_policy_exec_in_verbs_raises(tmp_path):
    data = _policy_data()
    data["verbs"]["exec"] = RULE
    with pytest.raises(ValueError, match="exec"):
        load_policy(_write_policy(tmp_path, data))


def test_policy_not_an_object_raises(tmp_path):
    with pytest.raises(ValueError):
        load_policy(_write_policy(tmp_path, []))


def test_policy_read_only():
    policy = load_policy(DEFAULT_POLICY_PATH)
    with pytest.raises(TypeError):
        policy.verbs["exec"] = policy.verbs["get"]
    with pytest.raises(TypeError):
        policy.verbs["get"].flags["--kubeconfig"] = True


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
    # single quotes are not inert when the command is wrapped in sh -c "..." by a host shell
    ("kubectl get pods -n sandbox-a -l '$(echo HOST_SUBST_RAN)'", Reason.SHELL_METACHAR),
    ("kubectl get pods -n sandbox-a -l '`echo HOST_SUBST_RAN`'", Reason.SHELL_METACHAR),
    (r"""kubectl get pods -n sandbox-a -l 'x\"; echo HOST_CMD_RAN #'""", Reason.SHELL_METACHAR),
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
    ("kubectl get pods -n ''", Reason.OUT_OF_SANDBOX),
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
    # exec runs only an additive mongo shell call; anything else after -- is not a verb we allow
    ("kubectl exec web-0 -n sandbox-a -- sh", Reason.VERB_NOT_ALLOWED),
    ("kubectl exec mongo-0 -n sandbox-a -- /bin/bash /scripts/x.sh", Reason.VERB_NOT_ALLOWED),
    ("kubectl exec -it mongo-0 -n sandbox-a -- mongo --eval 'db.getUsers()'", Reason.FLAG_DENIED),
    ("kubectl exec mongo-0 -n sandbox-a -- mongo db1", Reason.FLAG_DENIED),
    (
        "kubectl exec mongo-0 -n sandbox-a -- mongo db1 --quiet --eval 'db.getUsers()'",
        Reason.FLAG_DENIED,
    ),
    (
        "kubectl exec mongo-0 -n sandbox-a -- mongo 'db1;x' --eval 'db.getUsers()'",
        Reason.FLAG_DENIED,
    ),
    (
        """kubectl exec mongo-0 -n sandbox-a -- mongo db1 --eval 'db.dropUser("app")'""",
        Reason.FLAG_DENIED,
    ),
    (
        "kubectl exec mongo-0 -n sandbox-a -- mongo db1"
        """ --eval 'db.revokeRolesFromUser("app",["readWrite"])'""",
        Reason.FLAG_DENIED,
    ),
    (
        """kubectl exec mongo-0 -n sandbox-a -- mongo db1 --eval 'db.updateUser("app",{})'""",
        Reason.FLAG_DENIED,
    ),
    (
        "kubectl exec mongo-0 -n sandbox-a -- mongo db1"
        """ --eval 'db.changeUserPassword("app","x")'""",
        Reason.FLAG_DENIED,
    ),
    # a call must name an allowed method directly, so indirect calls are rejected
    (
        """kubectl exec mongo-0 -n sandbox-a -- mongo db1 --eval 'db["drop"+"User"]("app")'""",
        Reason.FLAG_DENIED,
    ),
    (
        """kubectl exec mongo-0 -n sandbox-a -- mongo db1 --eval 'db.createUser.call(db,{})'""",
        Reason.FLAG_DENIED,
    ),
    ("kubectl exec mongo-0 -n sandbox-a -- mongo db1 --eval '(x)'", Reason.FLAG_DENIED),
    ("kubectl get pods -n sandbox-a -- x", Reason.FLAG_DENIED),
    # exec takes no flags besides the namespace, even ones another verb allows
    (
        "kubectl exec mongo-0 -o wide -n sandbox-a -- mongo --eval 'db.getUsers()'",
        Reason.FLAG_DENIED,
    ),
    (
        "kubectl exec mongo-0 -l app=x -n sandbox-a -- mongo --eval 'db.getUsers()'",
        Reason.FLAG_DENIED,
    ),
    (
        "kubectl exec mongo-0 web-0 -n sandbox-a -- mongo --eval 'db.getUsers()'",
        Reason.RESOURCE_NOT_ALLOWED,
    ),
    ("kubectl exec -n sandbox-a -- mongo --eval 'db.getUsers()'", Reason.RESOURCE_NOT_ALLOWED),
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
    # a match-all selector does what --all does
    ("kubectl delete pods -l '!x' -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl delete pods,deployments -l '!x' -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl scale deploy -l '!x' --replicas=1 -n sandbox-a", Reason.FLAG_DENIED),
    # flags are checked per verb
    ("kubectl get pods --replicas=3 -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl get pods -p x -n sandbox-a", Reason.FLAG_DENIED),
    # replicas are bounded
    ("kubectl scale deploy web --replicas=0 -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl scale deploy web --replicas=-1 -n sandbox-a", Reason.FLAG_DENIED),
    ("kubectl scale deploy web --replicas=100000 -n sandbox-a", Reason.FLAG_DENIED),
    # patch bodies must be JSON without host, privilege, volume, image or exposure settings
    (
        "kubectl patch deploy web -n sandbox-a"
        """ -p '{"spec":{"template":{"spec":{"hostNetwork":true}}}}'""",
        Reason.FLAG_DENIED,
    ),
    (
        "kubectl patch deploy web -n sandbox-a"
        """ -p '{"spec":{"template":{"spec":{"containers":[{"name":"web","image":"x"}]}}}}'""",
        Reason.FLAG_DENIED,
    ),
    (
        """kubectl patch svc api -n sandbox-a -p '{"spec":{"type":"NodePort"}}'""",
        Reason.FLAG_DENIED,
    ),
    (
        """kubectl patch svc api -n sandbox-a -p '{"spec":{"externalIPs":["192.0.2.1"]}}'""",
        Reason.FLAG_DENIED,
    ),
    (
        "kubectl patch deploy web -n sandbox-a --type json"
        """ -p '[{"op":"add","path":"/spec/template/spec/hostNetwork","value":true}]'""",
        Reason.FLAG_DENIED,
    ),
    ("kubectl patch deploy web -n sandbox-a -p 'spec: {}'", Reason.FLAG_DENIED),
    ("kubectl patch deploy web -n sandbox-a --type other -p '{}'", Reason.FLAG_DENIED),
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
    # resources are checked per verb, and some verbs need a name
    ("kubectl scale pods web --replicas=1 -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl delete deploy web -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl delete pods -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl delete pod/ -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl rollout restart deploy -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl get pods 'web x' -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
    ("kubectl get pods Web -n sandbox-a", Reason.RESOURCE_NOT_ALLOWED),
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
    "kubectl patch deploy web -n sandbox-a --type json"
    ' -p \'[{"op":"replace","path":"/spec/replicas","value":2}]\'',
    "kubectl rollout restart deploy/web -n sandbox-a",
    "kubectl delete pod web-0 -n sandbox-a",
    "kubectl exec mongo-0 -n sandbox-a -- mongo db1"
    """ --eval 'db.createUser({user:"app",pwd:"pw",roles:["readWrite"]})'""",
    "kubectl exec mongo-0 -n sandbox-a -- mongosh"
    """ --eval 'db.getSiblingDB("db1").grantRolesToUser("app",["readWrite"])'""",
]


@pytest.mark.parametrize(("command", "reason"), REJECTED)
def test_rejected_with_reason(command, reason):
    verdict = check(command, POLICY, NS)
    assert verdict == Verdict(command=command, admitted=False, reason=reason)


@pytest.mark.parametrize("command", ADMITTED)
def test_admitted_unchanged(command):
    verdict = check(command, POLICY, NS)
    assert verdict == Verdict(command=command, admitted=True, reason=None)


@pytest.mark.parametrize("char", list(";&|<>(){}$`\\*?[]#~!"))
def test_each_unquoted_scan_character_rejected(char):
    command = f"kubectl get pods -n sandbox-a -l web{char}x"
    assert check(command, POLICY, NS).reason is Reason.SHELL_METACHAR


@pytest.mark.parametrize("char", list("$`\\"))
@pytest.mark.parametrize("quote", ["'", '"'])
def test_expansion_characters_rejected_inside_quotes(char, quote):
    command = f"kubectl get pods -n sandbox-a -l {quote}web{char}x{quote}"
    assert check(command, POLICY, NS).reason is Reason.SHELL_METACHAR


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
    data = _policy_data()
    data["verbs"]["get"]["flags"] |= {"-w": False, "--watch": False}
    policy = load_policy(_write_policy(tmp_path, data))
    assert check(command, policy, NS).admitted is admitted


@pytest.mark.parametrize("namespace", ["", "Sandbox", "sandbox_a", "default", "kube-system"])
def test_invalid_target_namespace_raises(namespace):
    with pytest.raises(ValueError, match="namespace"):
        check("kubectl get pods", POLICY, namespace)


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


def test_parse_args_reads_flags_before_and_after_the_verb():
    parsed = parse_args("-n sandbox-a scale deploy web --replicas 1 -o=wide".split(), POLICY)
    assert parsed.namespaces == ["sandbox-a"]
    assert parsed.all_namespaces is False
    assert parsed.positionals == ["scale", "deploy", "web"]
    assert parsed.flags == [("--replicas", "1"), ("-o", "wide")]
    assert parsed.flag_denied is False
    assert parsed.command is None


def test_parse_args_keeps_the_exec_command():
    parsed = parse_args("exec mongo-0 -A -- mongo --eval x".split(), POLICY)
    assert parsed.all_namespaces is True
    assert parsed.positionals == ["exec", "mongo-0"]
    assert parsed.command == ["mongo", "--eval", "x"]


def test_parse_args_consumes_named_value_flags():
    args = "-c mongo --context=kind exec mongo-0".split()
    assert parse_args(args, POLICY).positionals == ["mongo", "exec", "mongo-0"]
    parsed = parse_args(args, POLICY, value_flags=frozenset({"-c", "--context"}))
    assert parsed.positionals == ["exec", "mongo-0"]
    # the flags stay unknown to the policy, so they are still denied
    assert parsed.flag_denied is True


def test_split_targets_reads_both_forms():
    assert split_targets(["deploy,svc", "web", "api"]) == (["deploy", "svc"], ["web", "api"])
    assert split_targets(["deploy/web", "svc/api"]) == (["deploy", "svc"], ["web", "api"])
    assert split_targets(["pods"]) == (["pods"], [])


def test_split_verb_prefers_two_words():
    assert split_verb(["rollout", "restart", "deploy/web"], POLICY) == (
        "rollout restart",
        ["deploy/web"],
    )
    assert split_verb(["get", "pods"], POLICY) == ("get", ["pods"])
    assert split_verb(["drain", "web"], POLICY) is None
    assert split_verb([], POLICY) is None


@given(st.text())
def test_check_never_raises(text):
    # the prefixed form reaches the argv rules, not only the scan
    for command in (text, "kubectl " + text):
        verdict = check(command, POLICY, NS)
        assert verdict.command == command
        assert verdict.admitted == (verdict.reason is None)


NS_FORMS = ["-n sandbox-a", "-nsandbox-a", "--namespace sandbox-a", "--namespace=sandbox-a"]
VERB_RESOURCES = sorted((verb, r) for verb, rule in POLICY.verbs.items() for r in rule.resources)


@given(
    verb_resource=st.sampled_from(VERB_RESOURCES),
    name=st.from_regex(r"[a-z]([a-z0-9-]{0,9}[a-z0-9])?", fullmatch=True),
    ns_form=st.sampled_from(NS_FORMS),
    ns_first=st.booleans(),
)
def test_generated_permitted_admitted(verb_resource, name, ns_form, ns_first):
    parts = [*verb_resource, name]
    parts = [ns_form, *parts] if ns_first else [*parts, ns_form]
    command = " ".join(["kubectl", *parts])
    assert check(command, POLICY, NS) == Verdict(command=command, admitted=True, reason=None)


@given(st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,15}", fullmatch=True))
def test_exec_method_outside_allow_list_rejected(method):
    command = f"""kubectl exec mongo-0 -n sandbox-a -- mongo db1 --eval 'db.{method}("app")'"""
    admitted = check(command, POLICY, NS).admitted
    assert admitted is (method in POLICY.exec_methods)


def _host_wrapper(command):
    # the form a kind setup uses: double quotes escaped, then sh -c "..." under a host shell
    return 'sh -c "' + command.replace('"', '\\"') + '"'


@given(st.sampled_from(ADMITTED))
def test_admitted_commands_survive_the_host_wrapper(command):
    # the host shell must hand sh exactly the admitted text, with nothing to expand
    assert shlex.split(_host_wrapper(command)) == ["sh", "-c", command]
    assert not set("$`\\") & set(command)


@given(
    base=st.sampled_from(ADMITTED),
    op=st.sampled_from([";", "&&", "||", "|", "&", ">", "<", "$(", "`"]),
    tail=st.text(),
)
def test_operator_suffix_rejected(base, op, tail):
    assert check(base + op + tail, POLICY, NS).reason is Reason.SHELL_METACHAR


def test_reason_counts_empty():
    assert reason_counts([]) == ReasonCounts(total=0, by_reason={reason: 0 for reason in Reason})


def test_reason_counts_mixed():
    precedence = [command for command, _ in REJECTED[-5:]]
    verdicts = [check(command, POLICY, NS) for command in [*precedence, *ADMITTED[:2]]]
    counts = reason_counts(verdicts)
    assert counts.total == 7
    assert counts.admitted == 2
    assert counts.by_reason.keys() == set(Reason)
    assert counts.by_reason[Reason.SHELL_METACHAR] == 1
    assert counts.by_reason[Reason.OUT_OF_SANDBOX] == 1
    assert counts.by_reason[Reason.EMPTY] == 0
    assert sum(counts.by_reason.values()) == 5


def test_split_chain_ignores_a_separator_inside_quotes():
    assert split_chain("a 'x && y' && b") == ["a 'x && y'", "b"]
    assert split_chain("a") == ["a"]


def test_check_chain_needs_every_part_admitted():
    good = f"kubectl -n {NS} scale deployment a --replicas=1"
    assert check_chain(f"{good} && {good}", POLICY, NS).admitted
    chain = f"{good} && kubectl -n {NS} delete deployment a"
    verdict = check_chain(chain, POLICY, NS)
    assert not verdict.admitted
    assert verdict.command == chain
    assert verdict.reason == check(f"kubectl -n {NS} delete deployment a", POLICY, NS).reason
