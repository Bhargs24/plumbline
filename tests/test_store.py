"""The store and the server surface."""
from __future__ import annotations

import json

import pytest

from plumbline.core.trajectory import Step, Trajectory
from plumbline.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "t.db")


def _traj(trial, arm, task, pert, steps=3, error=None):
    return Trajectory(trial_id=trial, arm=arm, task_id=task, perturbation=pert,
                      variant_id=f"{pert}/0", error=error,
                      steps=[Step("tool_call", f"s{i}", {"invoice_id": task})
                             for i in range(steps)])


def test_round_trips_a_trajectory(store):
    pid = store.ensure_project("p", "ap")
    rid = store.start_run(pid, "r", "m")
    t = _traj("a/1", "react", "INV-1", "baseline")
    store.save_trajectories(rid, [t])
    back = store.trajectory(rid, "a/1")
    assert back is not None and back.path() == t.path()


def test_project_is_created_once(store):
    a = store.ensure_project("p", "ap")
    b = store.ensure_project("p", "ap")
    assert a == b and len(store.projects()) == 1


def test_filters_narrow_the_trajectory_list(store):
    pid = store.ensure_project("p", "ap")
    rid = store.start_run(pid, "r", "m")
    store.save_trajectories(rid, [
        _traj("1", "react", "INV-1", "baseline"),
        _traj("2", "react", "INV-1", "tool_fault"),
        _traj("3", "plan_execute", "INV-2", "baseline")])
    assert len(store.trajectories(rid)) == 3
    assert len(store.trajectories(rid, arm="react")) == 2
    assert len(store.trajectories(rid, perturbation="tool_fault")) == 1
    assert len(store.trajectories(rid, task_id="INV-2")) == 1


def test_only_failing_surfaces_conformance_outcome_and_crash_failures(store):
    pid = store.ensure_project("p", "ap")
    rid = store.start_run(pid, "r", "m")
    ts = [_traj("ok", "react", "INV-1", "baseline"),
          _traj("nonconf", "react", "INV-2", "baseline"),
          _traj("badoutcome", "react", "INV-3", "baseline"),
          _traj("crashed", "react", "INV-4", "baseline", error="boom")]
    store.save_trajectories(
        rid, ts,
        conformant={"ok": True, "nonconf": False, "badoutcome": True, "crashed": True},
        outcome_ok={"ok": True, "nonconf": True, "badoutcome": False, "crashed": True})
    failing = {r["trial_id"] for r in store.trajectories(rid, only_failing=True)}
    assert failing == {"nonconf", "badoutcome", "crashed"}


def test_violations_group_by_severity_then_frequency(store):
    pid = store.ensure_project("p", "ap")
    rid = store.start_run(pid, "r", "m")
    rows = [{"arm": "react", "invariant_id": "must_call:log", "severity": "medium",
             "perturbation": "baseline", "task_id": "INV-1", "trial_id": f"t{i}",
             "detail": "log missing"} for i in range(5)]
    rows += [{"arm": "react", "invariant_id": "must_call:dup", "severity": "critical",
              "perturbation": "paraphrase", "task_id": "INV-2", "trial_id": f"c{i}",
              "detail": "dup missing"} for i in range(2)]
    store.save_violations(rid, rows)
    summary = store.violation_summary(rid)
    assert summary[0]["severity"] == "critical", "critical outranks frequency"
    assert summary[0]["occurrences"] == 2
    assert summary[1]["occurrences"] == 5


def test_cross_run_query_is_the_reason_the_store_exists(store):
    """A folder of JSONL cannot answer 'which control fails most across runs'."""
    pid = store.ensure_project("p", "ap")
    for n in range(3):
        rid = store.start_run(pid, f"r{n}", "m")
        store.save_violations(rid, [
            {"arm": "react", "invariant_id": "must_call:dup", "severity": "critical",
             "perturbation": "paraphrase", "task_id": "INV-1",
             "trial_id": f"{n}", "detail": "x"}])
    top = store.failing_invariants_across_runs(pid)
    assert top[0]["invariant_id"] == "must_call:dup"
    assert top[0]["occurrences"] == 3 and top[0]["runs_affected"] == 3


def test_certificate_bound_drives_the_ci_gate(store):
    pid = store.ensure_project("p", "ap")
    rid = store.start_run(pid, "r", "m")
    store.save_certificate(rid, "react", "conformance", {"x": 1}, "C", 0.71)
    store.save_certificate(rid, "plan_execute", "conformance", {"x": 1}, "A", 0.97)
    certs = store.certificates(rid)
    below = [c for c in certs if c["bound"] < 0.95]
    assert len(below) == 1 and below[0]["arm"] == "react"


def test_trend_orders_by_time_for_regression_detection(store):
    pid = store.ensure_project("p", "ap")
    for n, bound in enumerate((0.97, 0.93, 0.61)):
        rid = store.start_run(pid, f"r{n}", "m")
        store.save_certificate(rid, "react", "conformance", {}, "x", bound)
    bounds = [t["bound"] for t in store.trend(pid, arm="react")]
    assert bounds == [0.97, 0.93, 0.61], "a regression must be visible in order"


def test_incomplete_runs_are_not_scored_as_violations(tmp_path):
    """A crashed or cut-off run is missing data. Scoring its empty trajectory
    as 'no controls ran' turns an outage into a wall of critical violations and
    silently moves the certified bound."""
    from plumbline.core.trajectory import TrajectoryStore
    from plumbline.spec.invariants import MustCall, PolicySpec
    from plumbline.store.importer import import_run

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    good = _traj("ok", "react", "INV-1", "baseline")
    good.steps = [Step("tool_call", "check_duplicate", {})]
    dead = Trajectory(trial_id="dead", arm="react", task_id="INV-2",
                      perturbation="baseline", variant_id="baseline/0",
                      error="budget stop: spend cap reached")
    TrajectoryStore(run_dir / "trajectories.jsonl").write_all([good, dead])
    (run_dir / "ledger_states.json").write_text("{}", encoding="utf-8")

    store = Store(tmp_path / "s.db")
    policy = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    rid = import_run(store, run_dir, project="p", domain="d",
                     policy=policy, contexts={"INV-1": {}, "INV-2": {}})

    assert store.violation_summary(rid) == [], \
        "the incomplete run must not contribute violations"
    rows = {r["trial_id"]: r for r in store.trajectories(rid)}
    assert rows["ok"]["conformant"] == 1
    assert rows["dead"]["conformant"] is None, "unscored, not scored as failing"
    assert store.run(rid)["n_errors"] == 1, "but it is still counted and visible"
