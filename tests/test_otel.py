"""Ingesting real-shaped OpenTelemetry traces.

The fixtures below are the two conventions as they actually appear on the wire:
OTLP JSON with type-tagged attribute values and the GenAI `gen_ai.*` names, and
the flatter OpenInference shape with `openinference.span.kind` and `tool.*`.
"""
from __future__ import annotations

from plumbline.adapters.otel import (TraceIngestError, describe_coverage,
                                     load_trace_file, spans_to_trajectories)


def otlp_span(trace, span_id, name, tool=None, args=None, error=None,
              op="execute_tool", start=0, tokens=None):
    attrs = [{"key": "gen_ai.operation.name", "value": {"stringValue": op}}]
    if tool:
        attrs.append({"key": "gen_ai.tool.name", "value": {"stringValue": tool}})
    if args is not None:
        attrs.append({"key": "gen_ai.tool.call.arguments",
                      "value": {"stringValue": args}})
    if tokens:
        attrs += [{"key": "gen_ai.usage.input_tokens", "value": {"intValue": str(tokens[0])}},
                  {"key": "gen_ai.usage.output_tokens", "value": {"intValue": str(tokens[1])}}]
    span = {"traceId": trace, "spanId": span_id, "name": name, "attributes": attrs,
            "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + 5_000_000)}
    if error:
        span["status"] = {"code": 2, "message": error}
    return span


def otlp(spans):
    return {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}


def test_reads_otlp_genai_spans_into_a_trajectory():
    payload = otlp([
        otlp_span("t1", "s1", "chat", op="chat", start=0, tokens=(1200, 90)),
        otlp_span("t1", "s2", "tool", tool="fetch_invoice",
                  args='{"invoice_id": "INV-7002"}', start=10),
        otlp_span("t1", "s3", "tool", tool="check_duplicate",
                  args='{"invoice_id": "INV-7002"}', start=20),
    ])
    trajs = spans_to_trajectories(payload)
    assert len(trajs) == 1
    t = trajs[0]
    assert [s.name for s in t.effect_steps()] == ["fetch_invoice", "check_duplicate"]
    assert t.effect_steps()[0].args == {"invoice_id": "INV-7002"}
    assert t.tokens_in == 1200 and t.tokens_out == 90


def test_model_calls_do_not_enter_the_effect_path():
    """A chat span is not a control step. Counting it would make every agent
    look like it took twice as many actions as it did."""
    payload = otlp([otlp_span("t1", "s1", "chat", op="chat"),
                    otlp_span("t1", "s2", "tool", tool="pay", args="{}")])
    t = spans_to_trajectories(payload)[0]
    assert len(t.effect_steps()) == 1


def test_error_status_marks_the_step_failed():
    payload = otlp([otlp_span("t1", "s1", "tool", tool="match_po", args="{}",
                              error="503 upstream unavailable")])
    t = spans_to_trajectories(payload)[0]
    assert t.effect_steps()[0].failed
    assert t.called("match_po") is False, "a failed call did not happen"


def test_exception_event_is_recognised_without_a_status_code():
    span = otlp_span("t1", "s1", "tool", tool="check", args="{}")
    span["events"] = [{"name": "exception", "attributes": [
        {"key": "exception.message", "value": {"stringValue": "timeout"}}]}]
    t = spans_to_trajectories(otlp([span]))[0]
    assert t.effect_steps()[0].error == "timeout"


def test_reads_openinference_spans():
    spans = [
        {"traceId": "t9", "spanId": "a", "name": "llm",
         "attributes": {"openinference.span.kind": "LLM",
                        "llm.model_name": "gpt-4o",
                        "llm.token_count.prompt": 500}},
        {"traceId": "t9", "spanId": "b", "name": "tool",
         "attributes": {"openinference.span.kind": "TOOL",
                        "tool.name": "schedule_payment",
                        "tool.parameters": '{"amount": 4500.0}'}},
    ]
    t = spans_to_trajectories(spans)[0]
    assert [s.name for s in t.effect_steps()] == ["schedule_payment"]
    assert t.effect_steps()[0].args == {"amount": 4500.0}
    assert t.model == "gpt-4o"


def test_spans_are_ordered_by_start_time_not_arrival():
    payload = otlp([
        otlp_span("t1", "s3", "tool", tool="third", args="{}", start=300),
        otlp_span("t1", "s1", "tool", tool="first", args="{}", start=100),
        otlp_span("t1", "s2", "tool", tool="second", args="{}", start=200),
    ])
    t = spans_to_trajectories(payload)[0]
    assert [s.name for s in t.effect_steps()] == ["first", "second", "third"]


def test_separate_traces_become_separate_trajectories():
    payload = otlp([otlp_span("ta", "s1", "tool", tool="a", args="{}"),
                    otlp_span("tb", "s2", "tool", tool="b", args="{}")])
    assert len(spans_to_trajectories(payload)) == 2


def test_task_and_perturbation_labelling_hooks():
    payload = otlp([otlp_span("t1", "s1", "tool", tool="a", args="{}")])
    t = spans_to_trajectories(
        payload, arm="prod",
        task_of=lambda spans: "INV-7002",
        perturbation_of=lambda spans: "paraphrase")[0]
    assert t.arm == "prod" and t.task_id == "INV-7002"
    assert t.perturbation == "paraphrase"


# ------------------------------------------------ honesty about what is missing
def test_coverage_reports_missing_arguments_rather_than_faking_agreement():
    """Many instrumentations record which tool ran but not its arguments.
    Reporting that as perfect argument agreement would be a lie of omission."""
    payload = otlp([otlp_span("t1", "s1", "tool", tool="pay")])   # no args
    cov = describe_coverage(spans_to_trajectories(payload))
    assert cov["argument_comparison_available"] is False
    assert any("no structured arguments" in n for n in cov["notes"])


def test_coverage_flags_unlabelled_tasks():
    payload = otlp([otlp_span("t1", "s1", "tool", tool="a", args="{}")])
    cov = describe_coverage(spans_to_trajectories(payload))
    assert cov["task_labelling_available"] is False
    assert any("task labelling" in n for n in cov["notes"])


def test_coverage_reports_clean_traces_as_usable():
    payload = otlp([otlp_span("t1", "s1", "tool", tool="a", args='{"x": 1}')])
    cov = describe_coverage(spans_to_trajectories(payload, task_of=lambda s: "T1"))
    assert cov["argument_comparison_available"] is True
    assert cov["task_labelling_available"] is True


def test_unparseable_arguments_are_preserved_not_dropped():
    payload = otlp([otlp_span("t1", "s1", "tool", tool="a", args="not json at all")])
    t = spans_to_trajectories(payload)[0]
    assert t.effect_steps()[0].args["_raw"] == "not json at all"
    cov = describe_coverage([t])
    assert cov["with_unparseable_arguments"] == 1


def test_bad_payload_raises_clearly():
    import pytest
    with pytest.raises(TraceIngestError):
        spans_to_trajectories("not a trace")


def test_roundtrip_through_a_file(tmp_path):
    import json
    p = tmp_path / "trace.json"
    p.write_text(json.dumps(otlp([
        otlp_span("t1", "s1", "tool", tool="fetch", args='{"id": "X"}')])),
        encoding="utf-8")
    trajs = load_trace_file(p)
    assert trajs[0].effect_steps()[0].args == {"id": "X"}
