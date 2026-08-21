"""
Load a run directory into the store.

Studies write JSONL to a directory because that is the right format for an
append-only record produced by a parallel worker pool. The store is the right
format for querying across runs. This moves one to the other, and is idempotent:
re-importing the same directory replaces rather than duplicates.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.trajectory import TrajectoryStore
from .db import Store


def import_run(store: Store, run_dir: str | Path, *, project: str, domain: str,
               label: str = "", policy=None, contexts: dict | None = None,
               outcome_fn=None) -> str:
    run_dir = Path(run_dir)
    trajs = TrajectoryStore(run_dir / "trajectories.jsonl").load()
    if not trajs:
        raise FileNotFoundError(f"no trajectories in {run_dir}")

    ledger_path = run_dir / "ledger_states.json"
    ledgers = (json.loads(ledger_path.read_text(encoding="utf-8"))
               if ledger_path.exists() else {})

    project_id = store.ensure_project(project, domain,
                                      getattr(policy, "name", ""))
    model = next((t.model for t in trajs if t.model), "")
    run_id = store.start_run(project_id, label or run_dir.name, model,
                             {"source": str(run_dir)})

    conformant, outcome_ok, violation_rows = {}, {}, []
    if policy is not None and contexts is not None:
        from ..spec.invariants import CRITICAL
        for t in trajs:
            ctx = contexts.get(t.task_id, {})
            violations = policy.check(t, ctx)
            conformant[t.trial_id] = not any(v.severity == CRITICAL
                                             for v in violations)
            for v in violations:
                violation_rows.append({
                    "arm": t.arm, "invariant_id": v.invariant_id,
                    "severity": v.severity, "perturbation": t.perturbation,
                    "task_id": t.task_id, "trial_id": t.trial_id,
                    "step_index": v.step_index, "step_name": v.step_name,
                    "detail": v.detail})
            if outcome_fn is not None:
                outcome_ok[t.trial_id] = outcome_fn(ctx, ledgers.get(t.trial_id))

    store.save_trajectories(run_id, trajs, conformant, outcome_ok)
    if violation_rows:
        store.save_violations(run_id, violation_rows)

    # certificates, if the directory carries any
    for f in sorted(run_dir.glob("certificate-*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        store.save_certificate(
            run_id, payload.get("provenance", {}).get("arm", f.stem.split("-", 1)[-1]),
            "conformance", payload, payload.get("grade", ""),
            float(payload.get("certified_conformance_lower_bound") or 0))
    for f in sorted(run_dir.glob("parity-*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        store.save_certificate(run_id, payload.get("replacement", ""), "parity",
                               payload, "", float(payload.get("retirement_bound") or 0),
                               payload.get("incumbent", ""))

    errors = sum(1 for t in trajs if t.error)
    cost = 0.0
    summary = run_dir / "summary.json"
    if summary.exists():
        s = json.loads(summary.read_text(encoding="utf-8"))
        # This run's spend, not the cumulative ledger. Summing the ledger
        # total across runs double-counts every earlier run.
        cost = float(s.get("cost_usd_this_run")
                     if s.get("cost_usd_this_run") is not None
                     else (s.get("cost_usd") or 0))
    store.finish_run(run_id, n_runs=len(trajs), n_errors=errors,
                     cost_usd=cost, wall_seconds=0.0)
    return run_id
