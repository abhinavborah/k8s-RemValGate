from remvalgate.overlay.fixes import action, exec_shell, tool_call


def test_arguments_go_by_keyword_whatever_the_key_order():
    # AIOpsLab's parser maps keywords by name, so both orders reach get_logs(namespace, service)
    forward = tool_call("get_logs", {"namespace": "ns", "service": "svc"})
    reverse = tool_call("get_logs", {"service": "svc", "namespace": "ns"})
    assert forward == action('get_logs(namespace="ns", service="svc")')
    assert reverse == action('get_logs(service="svc", namespace="ns")')


def test_an_optional_integer_keeps_its_type():
    assert tool_call("get_traces", {"duration": 5, "namespace": "ns"}) == action(
        'get_traces(duration=5, namespace="ns")'
    )


def test_exec_shell_keeps_the_command_positional_and_an_optional_timeout_by_keyword():
    command = "kubectl -n ns get pods"
    assert tool_call("exec_shell", {"command": command}) == exec_shell(command)
    assert tool_call("exec_shell", {"command": command, "timeout": 60}) == action(
        f'exec_shell("{command}", timeout=60)'
    )
