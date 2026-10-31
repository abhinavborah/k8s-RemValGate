from remvalgate.planner.telemetry import preview


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_traces_are_summarised_per_service_with_error_services_first(tmp_path):
    csv_path = _write(
        tmp_path / "t.csv",
        "trace_id,span_id,parent_span,service_name,operation_name,start_time,duration,has_error,response\n"
        "a,1,0,alpha,op,1,100,False,x\n"
        "a,2,1,alpha,op,2,300,False,x\n"
        "a,3,1,beta,op,3,50,True,x\n",
    )
    out = preview("get_traces", f"Traces data exported to: {csv_path}", 2000)
    lines = out.splitlines()
    assert lines[0].startswith("Traces data exported to")
    assert lines[2] == "beta: 1, 1, 50"
    assert lines[3] == "alpha: 2, 0, 200"


def test_metrics_show_the_highest_last_values_per_metric(tmp_path):
    _write(
        tmp_path / "container" / "kpi_container_threads.csv",
        "timestamp,cmdb_id,kpi_name,value\n"
        "1,node.pod-a,container_threads,5\n"
        "2,node.pod-a,container_threads,9\n"
        "1,node.pod-b,container_threads,7\n",
    )
    out = preview("get_metrics", f"Metrics data exported to directory: {tmp_path}\n\ntree", 2000)
    assert "container_threads: 2, pod-a=9, pod-b=7" in out


def test_an_empty_trace_window_says_so(tmp_path):
    csv_path = _write(
        tmp_path / "t.csv",
        "trace_id,span_id,parent_span,service_name,operation_name,start_time,duration,has_error,response\n",
    )
    assert "no spans" in preview("get_traces", f"Traces data exported to: {csv_path}", 2000)


def test_the_preview_is_cut_to_the_limit(tmp_path):
    csv_path = _write(
        tmp_path / "t.csv",
        "trace_id,span_id,parent_span,service_name,operation_name,start_time,duration,has_error,response\n"
        + "".join(f"a,{i},0,svc{i},op,1,10,False,x\n" for i in range(30)),
    )
    out = preview("get_traces", f"Traces data exported to: {csv_path}", 120)
    assert "[preview cut" in out and len(out) <= 120


def test_spans_that_started_before_since_are_left_out(tmp_path):
    csv_path = _write(
        tmp_path / "t.csv",
        "trace_id,span_id,parent_span,service_name,operation_name,start_time,duration,has_error,response\n"
        "a,1,0,previous,op,5,100,True,x\n"
        "b,2,0,new,op,10,50,False,x\n"
        "b,3,2,new,op,12,70,False,x\n",
    )
    out = preview("get_traces", f"Traces data exported to: {csv_path}", 2000, since=10)
    assert "previous:" not in out and "new: 2, 0, 60" in out
    assert "no spans" in preview(
        "get_traces", f"Traces data exported to: {csv_path}", 2000, since=99
    )


def test_other_tools_and_missing_files_pass_through(tmp_path):
    assert preview("get_logs", "log text", 100) == "log text"
    assert preview("get_traces", "Traces data exported to: /no/such.csv", 100).endswith("such.csv")
    assert preview("get_traces", "Error: something", 100) == "Error: something"
