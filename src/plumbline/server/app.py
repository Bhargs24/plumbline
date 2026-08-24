"""
The Plumbline server: REST API and web console.

Everything the CLI does is available over HTTP, because a tool a finance team
adopts has to be reachable by their CI, their GRC platform and their auditors,
not only by whoever has the repository checked out.

Three surfaces:

  /api/...        JSON, for CI pipelines and integrations
  /ingest/traces  OpenTelemetry span ingest, so an agent already emitting
                  traces can be measured without touching its code
  /               a console for reading results

Read-only by design apart from ingest. Starting a study spends money against an
API key, and an HTTP endpoint that spends money on an unauthenticated request is
a liability. Studies are launched from the CLI; the server reports on them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from ..store.db import Store
from . import views

app = FastAPI(title="Plumbline", version="0.3.0",
              description="Metamorphic conformance testing for LLM agents.")

#: Resolved at import so a deployment can point at a shared database
#: without editing code.
STORE = Store(Path(os.environ.get("PLUMBLINE_DB", "plumbline.db")))


def _store() -> Store:
    return STORE


# ==========================================================================
# API
# ==========================================================================
@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


@app.get("/api/projects")
def api_projects():
    return {"projects": _store().projects()}


@app.get("/api/runs")
def api_runs(project_id: str | None = None, limit: int = Query(100, le=500)):
    return {"runs": _store().runs(project_id, limit)}


@app.get("/api/runs/{run_id}")
def api_run(run_id: str):
    run = _store().run(run_id)
    if not run:
        raise HTTPException(404, f"no run {run_id}")
    return {"run": run,
            "certificates": _store().certificates(run_id),
            "violations": _store().violation_summary(run_id)}


@app.get("/api/runs/{run_id}/trajectories")
def api_trajectories(run_id: str, arm: str | None = None,
                     perturbation: str | None = None, task_id: str | None = None,
                     only_failing: bool = False, limit: int = Query(500, le=2000)):
    return {"trajectories": _store().trajectories(
        run_id, arm=arm, perturbation=perturbation, task_id=task_id,
        only_failing=only_failing, limit=limit)}


@app.get("/api/runs/{run_id}/trajectories/{trial_id:path}")
def api_trajectory(run_id: str, trial_id: str):
    t = _store().trajectory(run_id, trial_id)
    if t is None:
        raise HTTPException(404, f"no trajectory {trial_id}")
    return t.to_dict()


@app.get("/api/runs/{run_id}/certificate")
def api_certificate(run_id: str, arm: str | None = None, kind: str = "conformance"):
    certs = [c for c in _store().certificates(run_id)
             if c["kind"] == kind and (arm is None or c["arm"] == arm)]
    if not certs:
        raise HTTPException(404, "no certificate for that run, arm and kind")
    return {"certificates": certs}


@app.get("/api/projects/{project_id}/trend")
def api_trend(project_id: str, arm: str | None = None):
    return {"trend": _store().trend(project_id, arm),
            "top_failing_invariants":
                _store().failing_invariants_across_runs(project_id)}


@app.get("/api/runs/{run_id}/gate")
def api_gate(run_id: str, min_bound: float = 0.95, arm: str | None = None):
    """CI gate. Returns pass/fail against a threshold on the certified bound.

    Designed to be called by a pipeline: non-zero `failures` means block the
    deploy. The threshold is the caller's policy decision, not ours, which is
    why it is a parameter rather than a constant.
    """
    certs = [c for c in _store().certificates(run_id)
             if c["kind"] == "conformance" and (arm is None or c["arm"] == arm)]
    if not certs:
        raise HTTPException(404, "no conformance certificate for that run")
    failures = [{"arm": c["arm"], "bound": c["bound"], "grade": c["grade"]}
                for c in certs if (c["bound"] or 0) < min_bound]
    return {"run_id": run_id, "min_bound": min_bound,
            "checked": [{"arm": c["arm"], "bound": c["bound"]} for c in certs],
            "failures": failures, "passed": not failures}


@app.post("/ingest/traces")
async def ingest_traces(request: Request, run_id: str = Query(...),
                        arm: str = Query("observed")):
    """Accept OpenTelemetry spans (OTLP JSON) and store them as trajectories.

    This is how an agent that already emits traces gets measured without any
    change to its code. The response reports what the traces DO NOT support,
    because ingested traces are frequently missing tool arguments and reporting
    that as perfect agreement would be a lie of omission.
    """
    from ..adapters.otel import describe_coverage, spans_to_trajectories
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"body is not valid JSON: {exc}") from exc
    try:
        trajs = spans_to_trajectories(payload, arm=arm)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not _store().run(run_id):
        raise HTTPException(404, f"no run {run_id}; create it first")
    _store().save_trajectories(run_id, trajs)
    return {"ingested": len(trajs), "coverage": describe_coverage(trajs)}


# ==========================================================================
# Console
# ==========================================================================
@app.get("/", response_class=HTMLResponse)
def page_dashboard():
    s = _store()
    return views.dashboard(s.projects(), s.runs(limit=25))


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def page_run(run_id: str, arm: str | None = None,
             perturbation: str | None = None, only_failing: bool = False):
    s = _store()
    run = s.run(run_id)
    if not run:
        raise HTTPException(404, f"no run {run_id}")
    return views.run_detail(
        run, s.certificates(run_id), s.violation_summary(run_id),
        s.trajectories(run_id, arm=arm, perturbation=perturbation,
                       only_failing=only_failing, limit=400),
        selected={"arm": arm, "perturbation": perturbation,
                  "only_failing": only_failing})


@app.get("/runs/{run_id}/trace/{trial_id:path}", response_class=HTMLResponse)
def page_trace(run_id: str, trial_id: str, compare_to: str | None = None):
    s = _store()
    t = s.trajectory(run_id, trial_id)
    if t is None:
        raise HTTPException(404, f"no trajectory {trial_id}")
    other = s.trajectory(run_id, compare_to) if compare_to else None
    peers = [] if other else _comparable(s, run_id, t)
    return views.trace_detail(s.run(run_id), t, other, peers)


def _comparable(store: Store, run_id: str, t) -> list[dict]:
    """Other arms' runs on the same task and the same perturbation variant.

    Exact pairing is what makes a diff attributable to the systems rather than
    to the inputs they happened to receive.
    """
    rows = store.trajectories(run_id, task_id=t.task_id, limit=200)
    seen, out = set(), []
    for r in rows:
        # A different ARM on the same variant. Another trial of the same arm is
        # a repeat, not a comparison, and offering it invites reading sampling
        # noise as a behavioural difference.
        if r["variant_id"] != t.variant_id or r["arm"] == t.arm:
            continue
        if r["arm"] in seen:
            continue
        seen.add(r["arm"])
        out.append(r)
    return out


@app.get("/runs/{run_id}/controls", response_class=HTMLResponse)
def page_controls(run_id: str, arm: str | None = None,
                  operator: str = "llm_agent"):
    """The control-testing workpaper, for the reader who will never open a
    terminal. Same computation as `plumbline attest`."""
    run, att = _attestation(run_id, arm, operator)
    return views.control_attestation(run, att, arm, operator)


@app.get("/api/runs/{run_id}/attestation")
def api_attestation(run_id: str, arm: str | None = None,
                    operator: str = "llm_agent"):
    _, att = _attestation(run_id, arm, operator)
    return att.to_dict()


def _attestation(run_id: str, arm: str | None, operator: str):
    from ..compliance import P2P_FRAMEWORK, attest
    from ..cli import _policy_and_contexts

    s = _store()
    run = s.run(run_id)
    if not run:
        raise HTTPException(404, f"no run {run_id}")
    trajs = s.all_trajectories(run_id)
    if arm:
        trajs = [t for t in trajs if t.arm == arm]
    if not trajs:
        raise HTTPException(404, f"no trajectories for arm {arm!r}")
    spec, contexts = _policy_and_contexts()
    return run, attest(trajs, spec, contexts, P2P_FRAMEWORK,
                       operator=operator, period=run.get("started_at", "")[:7])


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"


@app.exception_handler(HTTPException)
async def _html_errors(request: Request, exc: HTTPException):
    if request.url.path.startswith(("/api/", "/ingest/")):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(views.error_page(exc.status_code, str(exc.detail)),
                        status_code=exc.status_code)
