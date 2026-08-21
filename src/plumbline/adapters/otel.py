"""
Ingest OpenTelemetry traces as trajectories.

Everything else in this package captures trajectories from an agent it drives
itself. That is fine for a study and useless for anybody else's system: nobody
is going to rewrite their agent to run under this harness.

They do not have to. Agent frameworks already emit OpenTelemetry spans, and
those spans already contain what a trajectory needs: which tool ran, with what
arguments, in what order, whether it errored. This module reads them.

TWO CONVENTIONS, BECAUSE THE STANDARD HAS NOT SETTLED

  OpenTelemetry GenAI semantic conventions use `gen_ai.*` attributes. As of the
  v1.42.0 release of 12 June 2026 they live in their own repository, released
  separately from the stability-bound core, and they remain pre-stable: there is
  no 1.0 and attribute names can still change between versions.

  OpenInference, originating with Arize, covers the same ground with `llm.*`,
  `tool.*` and `openinference.span.kind`. The two overlap in intent and differ
  in detail.

Both are supported, detected per span rather than per file, because a real trace
can contain spans from more than one instrumentation library. Where a field
exists under both conventions the GenAI name is preferred, and the OpenInference
name is the fallback.

WHAT THIS DELIBERATELY DOES NOT DO

It does not invent data. If a trace does not record tool arguments, argument
comparison is unavailable for it and the report says so rather than silently
comparing empty dictionaries and reporting perfect agreement. A missing field is
reported as missing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..core.trajectory import DECISION, FINAL, TOOL_CALL, Step, Trajectory

# --- attribute names, in preference order ----------------------------------
TOOL_NAME = ("gen_ai.tool.name", "tool.name", "gen_ai.tool.call.name")
TOOL_ARGS = ("gen_ai.tool.call.arguments", "tool.parameters", "input.value",
             "gen_ai.tool.input")
TOOL_RESULT = ("gen_ai.tool.call.result", "output.value", "tool.result")
OPERATION = ("gen_ai.operation.name", "openinference.span.kind")
MODEL = ("gen_ai.request.model", "gen_ai.response.model", "llm.model_name")
TOKENS_IN = ("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens",
             "llm.token_count.prompt")
TOKENS_OUT = ("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens",
              "llm.token_count.completion")

#: span kinds that represent a tool actually executing
TOOL_KINDS = {"execute_tool", "tool", "TOOL", "execute_tool_call"}
#: span kinds that represent a model call
MODEL_KINDS = {"chat", "text_completion", "generate_content", "LLM", "llm"}


class TraceIngestError(ValueError):
    pass


def _attr_value(v: Any) -> Any:
    """OTLP wraps every value in a type tag. Plain dicts do not."""
    if not isinstance(v, dict):
        return v
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in v:
            return v[key]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "arrayValue" in v:
        return [_attr_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv.get("key"): _attr_value(kv.get("value"))
                for kv in v["kvlistValue"].get("values", [])}
    return v


def _attrs(span: dict) -> dict:
    """Normalise attributes from either the OTLP list form or a plain dict."""
    raw = span.get("attributes", {})
    if isinstance(raw, dict):
        return {k: _attr_value(v) for k, v in raw.items()}
    out = {}
    for item in raw or []:
        if isinstance(item, dict) and "key" in item:
            out[item["key"]] = _attr_value(item.get("value"))
    return out


def _first(attrs: dict, names: Iterable[str], default=None):
    for n in names:
        if n in attrs and attrs[n] not in (None, ""):
            return attrs[n]
    return default


def _as_dict(value: Any) -> dict:
    """Tool arguments arrive as a dict, or as a JSON string, or as neither.

    When they are neither, the value is preserved under a `_raw` key rather
    than discarded, so a report can say the arguments were unparseable instead
    of implying there were none.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"_raw": value} if s else {}
    return {} if value is None else {"_raw": value}


def _failed(span: dict) -> str | None:
    """OTLP status code 2 is ERROR. Some exporters instead record an exception
    event, and OpenInference sets a status message without the numeric code."""
    status = span.get("status") or {}
    code = status.get("code")
    if code in (2, "STATUS_CODE_ERROR", "ERROR"):
        return status.get("message") or "error"
    for ev in span.get("events") or []:
        if ev.get("name") == "exception":
            a = _attrs(ev)
            return (a.get("exception.message") or a.get("exception.type")
                    or "exception")
    return None


def _start(span: dict) -> int:
    for k in ("startTimeUnixNano", "start_time_unix_nano", "startTime"):
        if k in span:
            try:
                return int(span[k])
            except (TypeError, ValueError):
                return 0
    return 0


def _duration_ms(span: dict) -> float | None:
    try:
        s, e = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
        return (e - s) / 1e6
    except (KeyError, TypeError, ValueError):
        return None


def iter_spans(payload: Any) -> Iterable[dict]:
    """Yield spans from OTLP JSON, a bare list, or JSON Lines."""
    if isinstance(payload, list):
        yield from payload
        return
    if not isinstance(payload, dict):
        raise TraceIngestError(f"cannot read spans from {type(payload).__name__}")
    if "resourceSpans" in payload:
        for rs in payload["resourceSpans"]:
            for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
                yield from ss.get("spans", [])
        return
    if "spans" in payload:
        yield from payload["spans"]
        return
    yield payload


def spans_to_trajectories(payload: Any, *, arm: str = "observed",
                          task_of=None, perturbation_of=None) -> list[Trajectory]:
    """Group spans by trace id and convert each trace into one Trajectory.

    `task_of` and `perturbation_of` are callables taking the list of spans in a
    trace and returning a task id and a perturbation label. They exist because
    only you know how your traces are tagged; without them every trace lands in
    one undifferentiated group, which is honest but not useful.
    """
    by_trace: dict[str, list[dict]] = {}
    for span in iter_spans(payload):
        tid = span.get("traceId") or span.get("trace_id") or "unknown"
        by_trace.setdefault(tid, []).append(span)

    out = []
    for tid, spans in by_trace.items():
        spans.sort(key=_start)
        steps, model, tin, tout, final = [], "", 0, 0, ""
        for span in spans:
            a = _attrs(span)
            op = str(_first(a, OPERATION, "") or "")
            name = _first(a, TOOL_NAME)
            model = model or str(_first(a, MODEL, "") or "")
            tin += int(_first(a, TOKENS_IN, 0) or 0)
            tout += int(_first(a, TOKENS_OUT, 0) or 0)

            is_tool = op in TOOL_KINDS or (name is not None and op not in MODEL_KINDS)
            if not is_tool:
                # A model call is not a control step. It is recorded so token
                # accounting works, but it never enters the effect path.
                continue
            err = _failed(span)
            steps.append(Step(
                kind=TOOL_CALL,
                name=str(name or span.get("name") or "unknown_tool"),
                args=_as_dict(_first(a, TOOL_ARGS)),
                output=_first(a, TOOL_RESULT),
                error=err,
                index=len(steps),
                latency_ms=_duration_ms(span),
            ))
            if not final:
                final = str(_first(a, TOOL_RESULT, "") or "")

        traj = Trajectory(
            trial_id=tid,
            perturbation=(perturbation_of(spans) if perturbation_of else "baseline"),
            variant_id=(perturbation_of(spans) if perturbation_of else "baseline") + "/0",
            arm=arm,
            task_id=(task_of(spans) if task_of else "unknown"),
            steps=steps,
            final_output=final,
            model=model,
            tokens_in=tin,
            tokens_out=tout,
            metadata={"source": "opentelemetry", "span_count": len(spans)},
        )
        out.append(traj)
    return out


def load_trace_file(path: str | Path, **kwargs) -> list[Trajectory]:
    """Read OTLP JSON or JSON Lines from disk."""
    p = Path(path)
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise TraceIngestError(f"{p} is empty")
    if p.suffix == ".jsonl" or text.startswith("{") and "\n{" in text:
        spans = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                spans.append(json.loads(line))
        return spans_to_trajectories(spans, **kwargs)
    return spans_to_trajectories(json.loads(text), **kwargs)


def describe_coverage(trajectories: list[Trajectory]) -> dict:
    """What can and cannot be measured from these traces.

    Ingested traces are frequently incomplete: many instrumentations record
    which tool ran but not what it was called with. Argument comparison is then
    impossible, and reporting it as 100% agreement would be a lie of omission.
    This says so explicitly instead.
    """
    steps = [s for t in trajectories for s in t.effect_steps()]
    with_args = [s for s in steps if s.args and "_raw" not in s.args]
    unparsed = [s for s in steps if "_raw" in (s.args or {})]
    tasks = {t.task_id for t in trajectories}
    return {
        "trajectories": len(trajectories),
        "tool_calls": len(steps),
        "with_structured_arguments": len(with_args),
        "with_unparseable_arguments": len(unparsed),
        "argument_comparison_available": bool(steps) and len(with_args) == len(steps),
        "distinct_tasks": len(tasks),
        "task_labelling_available": tasks != {"unknown"},
        "notes": _coverage_notes(steps, with_args, unparsed, tasks),
    }


def _coverage_notes(steps, with_args, unparsed, tasks) -> list[str]:
    notes = []
    if not steps:
        notes.append("No tool-call spans found. Check that the instrumentation "
                     "emits gen_ai.operation.name=execute_tool or "
                     "openinference.span.kind=TOOL.")
    elif len(with_args) < len(steps):
        notes.append(f"{len(steps) - len(with_args)} of {len(steps)} tool calls "
                     f"carry no structured arguments, so argument-level "
                     f"comparison is unavailable for them.")
    if unparsed:
        notes.append(f"{len(unparsed)} tool calls recorded arguments that were "
                     f"not valid JSON objects; they are preserved under '_raw'.")
    if tasks == {"unknown"}:
        notes.append("No task labelling. Pass task_of= so runs of the same task "
                     "can be compared; without it every trace is treated as the "
                     "same task and the comparison is meaningless.")
    return notes
