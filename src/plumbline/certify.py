"""
Top-level entry point: trajectories in, certificate out.

Kept separate from the runner so a certificate can be produced from stored
evidence without re-running anything. That is the property that makes a
certificate auditable: hand someone the trajectories file and they can rebuild
the number themselves.
"""
from __future__ import annotations

from .analysis.conformance import analyze_conformance, outcome_correctness
from .analysis.equivalence import EquivalenceReport, analyze_equivalence
from .analysis.consistency import analyze_consistency
from .analysis.stats import compare
from .core.trajectory import Trajectory
from .report.certificate import Certificate
from .spec.invariants import CRITICAL, PolicySpec


def certify(trajectories: list[Trajectory], spec: PolicySpec, contexts: dict,
            ledger_states: dict, *, subject: str = "agent",
            provenance: dict | None = None) -> Certificate:
    conformance = analyze_conformance(trajectories, spec, contexts)
    consistency = analyze_consistency(trajectories, spec.arg_schemas, ledger_states)
    outcome = outcome_correctness(trajectories, contexts, ledger_states)
    return Certificate.build(
        subject=subject, policy_name=spec.name,
        conformance=conformance, consistency=consistency, outcome=outcome,
        trajectories=trajectories, provenance=provenance or {})


def critical_conformance_flags(trajectories: list[Trajectory], spec: PolicySpec,
                               contexts: dict) -> list[bool]:
    """Per-run booleans, for comparing two arms with a significance test."""
    out = []
    for t in trajectories:
        violations = spec.check(t, contexts.get(t.task_id, {}))
        out.append(not any(v.severity == CRITICAL for v in violations))
    return out


def compare_arms(trajectories: list[Trajectory], spec: PolicySpec, contexts: dict,
                 arm_a: str, arm_b: str, *, perturbation: str | None = None):
    """Is the difference between two architectures larger than chance."""
    def pick(arm):
        return [t for t in trajectories
                if t.arm == arm and (perturbation is None
                                     or t.perturbation == perturbation)]
    a = critical_conformance_flags(pick(arm_a), spec, contexts)
    b = critical_conformance_flags(pick(arm_b), spec, contexts)
    return compare(arm_a, a, arm_b, b)


def prove_parity(trajectories: list[Trajectory], *, incumbent: str,
                 replacement: str, ledger_states: dict,
                 spec: PolicySpec | None = None) -> EquivalenceReport:
    """Does the replacement behave like the incumbent, including under inputs
    that were changed in ways neither system should care about.

    This is the migration question. Everything else in the package measures one
    system against its own rules; this measures one system against another.
    """
    return analyze_equivalence(
        trajectories, incumbent=incumbent, replacement=replacement,
        ledger_states=ledger_states,
        arg_schemas=(spec.arg_schemas if spec else None))
