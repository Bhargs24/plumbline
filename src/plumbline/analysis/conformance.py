"""
Conformance analysis: did the declared invariants hold, and where did they break.

The unit of measurement is one run against one task's invariants. A run
conforms when it violates nothing. Because severity is declared, conformance is
also reported restricted to CRITICAL invariants, which is the number that
actually decides whether an agent can be trusted with a payment file.

Everything is reported per perturbation as well as overall, because the average
is the least useful view. An agent at 95% overall that sits at 60% under
paraphrase does not have a 5% problem; it has a wording problem that the average
is hiding. The certificate leads with the worst perturbation for that reason.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..core.trajectory import Trajectory
from ..spec.invariants import CRITICAL, SEVERITY_ORDER, PolicySpec, Violation
from .stats import Proportion, wilson


@dataclass
class ViolationGroup:
    """The same violation seen across several runs, kept together so the report
    shows a pattern rather than a list of near-identical lines."""
    invariant_id: str
    severity: str
    step_index: int | None
    step_name: str | None
    detail_example: str
    perturbations: dict = field(default_factory=lambda: defaultdict(int))
    trial_ids: list = field(default_factory=list)
    task_ids: set = field(default_factory=set)

    @property
    def count(self) -> int:
        return len(self.trial_ids)

    def describe(self, max_trials: int = 4) -> str:
        if self.step_index is not None and self.step_index >= 0:
            where = f"at step {self.step_index} ({self.step_name})"
        elif self.step_index == -1:
            where = f"- {self.step_name} never ran"
        else:
            where = "- run level"
        perts = ", ".join(f"{k} x{v}" for k, v in
                          sorted(self.perturbations.items(), key=lambda kv: -kv[1]))
        shown = ", ".join(self.trial_ids[:max_trials])
        more = f" +{self.count - max_trials} more" if self.count > max_trials else ""
        return (f"[{self.severity.upper()}] {self.invariant_id} {where}\n"
                f"      {self.count} run(s) under {perts}\n"
                f"      e.g. {self.detail_example}\n"
                f"      trials: {shown}{more}")

    def to_dict(self) -> dict:
        return {"invariant_id": self.invariant_id, "severity": self.severity,
                "step_index": self.step_index, "step_name": self.step_name,
                "count": self.count, "detail_example": self.detail_example,
                "perturbations": dict(self.perturbations),
                "task_ids": sorted(self.task_ids),
                "trial_ids": self.trial_ids}


@dataclass
class ConformanceReport:
    overall: Proportion                 # runs with zero violations
    critical: Proportion                # runs with zero CRITICAL violations
    by_perturbation: dict               # perturbation -> Proportion (critical)
    by_invariant: dict                  # invariant_id -> Proportion (upheld)
    by_task: dict                       # task_id -> Proportion (critical)
    worst_perturbation: tuple           # (name, Proportion)
    violations: list                    # list[ViolationGroup], worst first
    n_runs: int = 0
    n_errors: int = 0                   # runs that crashed or ran out of turns

    def to_dict(self) -> dict:
        return {
            "overall": self.overall.to_dict(),
            "critical": self.critical.to_dict(),
            "by_perturbation": {k: v.to_dict() for k, v in self.by_perturbation.items()},
            "by_invariant": {k: v.to_dict() for k, v in self.by_invariant.items()},
            "by_task": {k: v.to_dict() for k, v in self.by_task.items()},
            "worst_perturbation": [self.worst_perturbation[0],
                                   self.worst_perturbation[1].to_dict()],
            "violations": [v.to_dict() for v in self.violations],
            "n_runs": self.n_runs, "n_errors": self.n_errors,
        }


def _group_key(v: Violation) -> tuple:
    return (v.invariant_id, v.severity, v.step_index, v.step_name)


def analyze_conformance(trajectories: list[Trajectory], spec: PolicySpec,
                        contexts: dict) -> ConformanceReport:
    """`contexts` maps task_id to that task's ground-truth context."""
    if not trajectories:
        raise ValueError("no trajectories to analyze")

    clean_all, clean_crit = [], []
    per_pert: dict[str, list[bool]] = defaultdict(list)
    per_task: dict[str, list[bool]] = defaultdict(list)
    per_inv_upheld: dict[str, list[bool]] = defaultdict(list)
    groups: dict[tuple, ViolationGroup] = {}
    n_errors = 0

    for traj in trajectories:
        ctx = contexts.get(traj.task_id, {})
        violations = spec.check(traj, ctx)
        if traj.error:
            n_errors += 1

        crit = [v for v in violations if v.severity == CRITICAL]
        clean_all.append(not violations)
        clean_crit.append(not crit)
        per_pert[traj.perturbation].append(not crit)
        per_task[traj.task_id].append(not crit)

        broken = {v.invariant_id for v in violations}
        for inv in spec.applicable(ctx):
            per_inv_upheld[inv.id].append(inv.id not in broken)

        for v in violations:
            key = _group_key(v)
            g = groups.get(key)
            if g is None:
                g = ViolationGroup(v.invariant_id, v.severity, v.step_index,
                                   v.step_name, v.detail)
                groups[key] = g
            g.perturbations[traj.perturbation] += 1
            g.trial_ids.append(traj.trial_id)
            g.task_ids.add(traj.task_id)

    by_pert = {k: wilson(sum(v), len(v)) for k, v in per_pert.items()}
    worst = min(by_pert.items(), key=lambda kv: kv[1].value) if by_pert else ("", wilson(0, 0))

    # Worst first: severity outranks frequency. A single critical violation
    # matters more than twenty low-severity ones, and a report sorted by count
    # would bury it.
    violations = sorted(
        groups.values(),
        key=lambda g: (SEVERITY_ORDER.get(g.severity, 9), -g.count))

    return ConformanceReport(
        overall=wilson(sum(clean_all), len(clean_all)),
        critical=wilson(sum(clean_crit), len(clean_crit)),
        by_perturbation=by_pert,
        by_invariant={k: wilson(sum(v), len(v)) for k, v in per_inv_upheld.items()},
        by_task={k: wilson(sum(v), len(v)) for k, v in per_task.items()},
        worst_perturbation=worst,
        violations=violations,
        n_runs=len(trajectories),
        n_errors=n_errors,
    )


def outcome_correctness(trajectories: list[Trajectory], contexts: dict,
                        ledger_states: dict, matches=None) -> Proportion:
    """Did the run leave the world in the state a correct run would leave it.

    Judged on the system of record, not on the closing message. An agent that
    says it held the invoice for review and then scheduled the payment anyway is
    a failure, and only the ledger reveals it.

    `matches(context, ledger) -> bool` is supplied by the DOMAIN. This module
    must not know what an invoice is: it previously imported one specific
    domain's grader directly, which meant the generic analysis layer crashed on
    any other domain. A missing grader yields an empty proportion rather than a
    guess, so a report says "not measured" instead of inventing a number.
    """
    if matches is None:
        return wilson(0, 0)
    ok = [bool(matches(contexts.get(t.task_id, {}), ledger_states.get(t.trial_id)))
          for t in trajectories]
    return wilson(sum(ok), len(ok))
