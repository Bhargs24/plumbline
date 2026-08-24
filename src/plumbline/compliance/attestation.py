"""
Control testing attestation: the workpaper.

Everything else in this package describes controls and does audit arithmetic.
This turns a set of trajectories into the document a control tester files: one
row per key control, the population and sample, the deviations found, the
conclusion, and — for every deviation — the specific transaction and step where
the control did not operate.

Two properties are what make it evidence rather than a report.

COMPLETENESS AND ACCURACY OF THE POPULATION (the CAVR requirement). Where a
control test relies on a system-generated report, the tester must establish
that the report is complete and accurate, or the conclusion drawn from it is
worthless. Here the population is the stored trajectories, the attestation
carries a hash over them, and the whole document regenerates from those
trajectories with no model calls. A reviewer can recompute it.

EXCEPTIONS ARE LISTED, NOT SUMMARISED. A deviation rate with no attached
transactions cannot be investigated. Every deviation names the trial, the task,
the perturbation that produced it, and the step index, so a tester can pull the
individual trace.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.trajectory import Trajectory
from ..spec.invariants import PolicySpec
from .controls import ControlFramework, KeyControl, hold_category
from .sampling import SampleAssessment, assess, assess_test_of_one


@dataclass
class Deviation:
    control_id: str
    invariant_id: str
    trial_id: str
    task_id: str
    perturbation: str
    step_index: int | None
    step_name: str | None
    detail: str

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("control_id", "invariant_id", "trial_id", "task_id",
                 "perturbation", "step_index", "step_name", "detail")}


@dataclass
class ControlResult:
    control: KeyControl
    assessment: SampleAssessment
    deviations: list[Deviation] = field(default_factory=list)
    #: Deviation counts split by perturbation. This is the column an auditor
    #: has never seen before and the reason the tool exists: it shows the
    #: conditions under which the control stops operating.
    by_perturbation: dict = field(default_factory=dict)
    #: Deviation counts split by transaction. A rate concentrated in one
    #: scenario is a defect in how that scenario is handled, which is a
    #: different finding from a uniform rate, and remediated differently.
    by_scenario: dict = field(default_factory=dict)

    @property
    def effective(self) -> bool:
        return self.assessment.passes

    def to_dict(self) -> dict:
        return {
            "control_id": self.control.control_id,
            "name": self.control.name,
            "objective": self.control.objective,
            "risk": self.control.risk,
            "coso_component": self.control.coso_component,
            "assertions": self.control.assertions,
            "nature": self.control.nature,
            "control_type": self.control.control_type,
            "frequency": self.control.frequency,
            "owner": self.control.owner,
            "invariants_tested": self.control.invariant_ids,
            "assessment": self.assessment.to_dict(),
            "deviations_by_perturbation": self.by_perturbation,
            "deviations_by_scenario": self.by_scenario,
            "deviations": [d.to_dict() for d in self.deviations[:50]],
            "deviations_truncated": max(0, len(self.deviations) - 50),
            "effective": self.effective,
        }


@dataclass
class Attestation:
    framework: ControlFramework
    results: list[ControlResult]
    population: int
    incomplete: int
    operator: str
    model: str
    period: str
    evidence_hash: str
    generated_utc: str
    test_of_one: tuple
    #: (control, reason) for every control this run could not conclude on
    not_tested: list[tuple] = field(default_factory=list)
    #: implementation -> transactions. A control operated by two different
    #: harnesses is two controls, and one conclusion cannot span both.
    arms: dict = field(default_factory=dict)
    #: distinct transaction scenarios behind the executions. Reported
    #: separately because N executions over K scenarios is not a
    #: population of N, and an auditor reads population as distinct items.
    scenarios: int = 0
    #: perturbation family -> transactions tested under it. This is the
    #: extent of the conclusions: a control is evidenced against the
    #: variation it was actually exposed to and nothing wider.
    conditions: dict = field(default_factory=dict)

    @property
    def deficiencies(self) -> list[ControlResult]:
        return [r for r in self.results if not r.effective]

    @property
    def inconclusive(self) -> list[ControlResult]:
        return [r for r in self.results if not r.assessment.sufficient]

    def to_dict(self) -> dict:
        return {
            "schema": "plumbline/attestation/v1",
            "framework": {"name": self.framework.name,
                          "version": self.framework.version},
            "period": self.period,
            "operator": self.operator,
            "model": self.model,
            "executions": self.population,
            "distinct_scenarios": self.scenarios,
            "incomplete_excluded": self.incomplete,
            "test_of_one_defensible": self.test_of_one[0],
            "test_of_one_reasoning": self.test_of_one[1],
            "controls": [r.to_dict() for r in self.results],
            "not_tested": [{"control_id": c.control_id,
                            "name": c.name, "reason": why}
                           for c, why in self.not_tested],
            "summary": {
                "tested": len(self.results),
                "effective": sum(1 for r in self.results if r.effective),
                "deficient": len(self.deficiencies),
                "inconclusive": len(self.inconclusive),
            },
            "implementations": self.arms,
            "mixed_implementations": len(self.arms) > 1,
            "conditions_tested": self.conditions,
            "evidence_hash": self.evidence_hash,
            "generated_utc": self.generated_utc,
        }


def _evidence_hash(trajectories: list[Trajectory]) -> str:
    """A hash over the population the conclusions rest on.

    Not security. It is the CAVR answer: it pins exactly which trajectories the
    attestation was computed from, so a reviewer can confirm they are looking
    at the same population and detect if it changed underneath them.
    """
    payload = "|".join(sorted(f"{t.trial_id}:{t.path_hash()}"
                              for t in trajectories))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def attest(trajectories: list[Trajectory], spec: PolicySpec, contexts: dict,
           framework: ControlFramework, *, operator: str = "llm_agent",
           period: str = "", itgc_effective: bool = True,
           confidence: float = 0.95) -> Attestation:
    """Test every control in the framework against the stored trajectories."""
    complete = [t for t in trajectories if not t.error]
    incomplete = len(trajectories) - len(complete)
    index = framework.invariant_index()

    # invariant -> deviations, computed once over the population
    found: dict[str, list[Deviation]] = defaultdict(list)
    applicable: dict[str, int] = defaultdict(int)

    for t in complete:
        ctx = contexts.get(t.task_id, {})
        for inv in spec.applicable(ctx):
            applicable[inv.id] += 1
        for v in spec.check(t, ctx):
            control = index.get(v.invariant_id)
            if control is None:
                continue
            found[v.invariant_id].append(Deviation(
                control_id=control.control_id, invariant_id=v.invariant_id,
                trial_id=t.trial_id, task_id=t.task_id,
                perturbation=t.perturbation, step_index=v.step_index,
                step_name=v.step_name, detail=v.detail))

    # A control the run could not test is a scope limitation, and a workpaper
    # that silently omits it reads as though the framework were fully covered.
    # Every control dropped below is recorded with the reason it was dropped.
    spec_invariants = {inv.id for inv in spec.invariants}

    results, not_tested = [], []
    for control in framework.controls:
        if not control.is_tested:
            not_tested.append((control, "No invariant declared for this "
                                        "control. Documented in the matrix but "
                                        "unevidenced by this harness."))
            continue
        missing = [i for i in control.invariant_ids if i not in spec_invariants]
        # The population for a control is the number of transactions on which
        # it was APPLICABLE, not the total. A control that only applies to
        # credit notes is not deviating on the invoices it never governed.
        pop = max((applicable.get(i, 0) for i in control.invariant_ids),
                  default=0)
        if pop == 0:
            if missing:
                not_tested.append((control, "Declared invariant(s) "
                                   + ", ".join(missing)
                                   + " are absent from the policy under test. "
                                     "Outside the scope of this run."))
            else:
                not_tested.append((control, "Applicable to no transaction in "
                                            "this population. No conclusion "
                                            "is drawn either way."))
            continue
        devs: list[Deviation] = []
        for i in control.invariant_ids:
            devs.extend(found.get(i, []))
        # One transaction breaching two invariants of the same control is one
        # deviation of that control, not two.
        distinct = len({d.trial_id for d in devs})
        per_pert: dict = defaultdict(set)
        per_task: dict = defaultdict(set)
        for d in devs:
            per_pert[d.perturbation].add(d.trial_id)
            per_task[d.task_id].add(d.trial_id)

        results.append(ControlResult(
            control=control,
            assessment=assess(control.control_id, tested=pop,
                              deviations=distinct,
                              tolerable_rate=control.tolerable_rate,
                              population=pop, confidence=confidence),
            deviations=sorted(devs, key=lambda d: (d.perturbation, d.trial_id)),
            by_perturbation={k: len(v) for k, v in sorted(per_pert.items())},
            by_scenario={k: len(v) for k, v in sorted(
                per_task.items(), key=lambda kv: -len(kv[1]))}))

    return Attestation(
        framework=framework, results=results, not_tested=not_tested,
        population=len(complete), incomplete=incomplete,
        operator=operator,
        model=next((t.model for t in complete if t.model), "unknown"),
        period=period or datetime.now(timezone.utc).strftime("%Y-%m"),
        arms=dict(sorted(Counter(t.arm for t in complete).items())),
        scenarios=len({t.task_id for t in complete}),
        conditions=dict(sorted(Counter(t.perturbation
                                       for t in complete).items())),
        evidence_hash=_evidence_hash(complete),
        generated_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        test_of_one=assess_test_of_one("automated", operator, itgc_effective))


# --------------------------------------------------------------------------
def render_text(a: Attestation, width: int = 100) -> str:
    """The workpaper as plain text, for a terminal or an email."""
    L, bar = [], "=" * width
    s = a.to_dict()["summary"]
    L.append(bar)
    L.append("  CONTROL OPERATING EFFECTIVENESS — TEST OF CONTROLS")
    L.append(f"  {a.framework.name} v{a.framework.version}   period {a.period}")
    L.append(bar)
    L.append(f"  Control operator     : {a.operator}  ({a.model})")
    L.append(f"  Executions           : {a.population}"
             + (f"   ({a.incomplete} incomplete, excluded)" if a.incomplete else ""))
    L.append(f"  Distinct scenarios   : {a.scenarios}"
             + (f"   ~{a.population / a.scenarios:.0f} executions each"
                if a.scenarios else ""))
    L.append(f"  Controls tested      : {s['tested']} of "
             f"{len(a.framework.controls)}")
    L.append(f"  Effective            : {s['effective']}")
    L.append(f"  Deficient            : {s['deficient']}")
    L.append(f"  Inconclusive         : {s['inconclusive']}")
    L.append("-" * width)
    L.append("  BASIS OF PREPARATION")
    for line in _wrap(
            f"The evidence is {a.population} executions over {a.scenarios} "
            f"distinct transaction scenarios, not a population of "
            f"{a.population} distinct transactions. Deviation rates below are "
            f"per execution. A rate concentrated in one scenario is a defect "
            f"in that scenario's handling, not a uniform failure rate across "
            f"the ledger, and the per-control detail says which.", width - 4):
        L.append(f"  {line}")
    L.append("")
    for line in _wrap(
            "Conclusions extend to the population described below and no "
            "further. Each transaction was executed under one of the "
            "following conditions; a control evidenced here is evidenced "
            "against that variation only. Input variation outside these "
            "families, model versions other than the one named, and controls "
            "listed under scope limitation are not covered.", width - 4):
        L.append(f"  {line}")
    L.append("")
    if len(a.arms) > 1:
        for line in _wrap(
                "QUALIFIED. This population spans " + str(len(a.arms))
                + " different implementations of the control ("
                + ", ".join(f"{k}: {v}" for k, v in a.arms.items())
                + "). They are not the same control, and a single conclusion "
                  "is not attributable to any one of them. Test each "
                  "implementation separately before relying on this.",
                width - 4):
            L.append(f"  {line}")
        L.append("")
    elif a.arms:
        name = next(iter(a.arms))
        L.append(f"    implementation             {name}")
        L.append("")
    for name, n in a.conditions.items():
        L.append(f"    {name:<28}{n:>6} transactions")
    if not a.conditions:
        L.append("    (no perturbation recorded)")
    if a.incomplete:
        L.append("")
        for line in _wrap(
                f"{a.incomplete} run(s) did not complete and are excluded from "
                f"the population. They are neither conforming nor deviating; "
                f"an incomplete execution is an absence of evidence, and "
                f"scoring it either way would misstate the rate.", width - 4):
            L.append(f"  {line}")
    L.append("-" * width)
    L.append("  RELIANCE APPROACH")
    L.append(f"  Test of one defensible: {'YES' if a.test_of_one[0] else 'NO'}")
    for line in _wrap(a.test_of_one[1], width - 4):
        L.append(f"  {line}")
    L.append("-" * width)
    L.append(f"  {'CONTROL':<9}{'NAME':<28}{'POP':>6}{'DEV':>5}{'UDR':>8}"
             f"{'TOL':>6}  CONCLUSION")
    L.append("-" * width)
    for r in a.results:
        c, m = r.control, r.assessment
        verdict = ("EFFECTIVE" if r.effective
                   else ("INCONCLUSIVE" if not m.sufficient else "DEFICIENT"))
        L.append(f"  {c.control_id:<9}{c.name[:26]:<28}{m.tested:>6}"
                 f"{m.deviations:>5}{m.upper_deviation_rate:>7.1%}"
                 f"{c.tolerable_rate:>6.0%}  {verdict}")
    L.append("-" * width)

    deficient = a.deficiencies
    if deficient:
        L.append("  EXCEPTIONS")
        for r in deficient:
            L.append("")
            L.append(f"  {r.control.control_id}  {r.control.name}")
            for line in _wrap(f"Risk: {r.control.risk}", width - 6):
                L.append(f"    {line}")
            for line in _wrap(r.assessment.conclusion(), width - 6):
                L.append(f"    {line}")
            if r.by_perturbation:
                conds = ", ".join(f"{k} ({v})"
                                  for k, v in r.by_perturbation.items())
                for line in _wrap(f"Conditions under which the control did not "
                                  f"operate: {conds}", width - 6):
                    L.append(f"    {line}")
            if r.by_scenario:
                top, n = next(iter(r.by_scenario.items()))
                total = sum(r.by_scenario.values())
                share = n / total if total else 0
                scen = ", ".join(f"{k} ({v})" for k, v in
                                 list(r.by_scenario.items())[:4])
                for line in _wrap(f"Transactions affected: {scen}", width - 6):
                    L.append(f"    {line}")
                # A rate driven by one transaction is a different finding from
                # a uniform one, and saying so before a reviewer works it out
                # is the difference between candour and being caught.
                if share >= 0.6 and total > 2:
                    for line in _wrap(
                            f"CONCENTRATED: {n} of {total} deviations fall on "
                            f"{top} alone. This is a defect in how that "
                            f"transaction is handled rather than a uniform "
                            f"failure rate across the population. Remediate "
                            f"the scenario, then retest.", width - 6):
                        L.append(f"    {line}")
            L.append(f"    Route to: {r.control.remediation_owner}  "
                     f"(SLA {r.control.sla_days} business days)")
            for d in r.deviations[:4]:
                where = (f"step {d.step_index}" if d.step_index is not None
                         and d.step_index >= 0 else "run level")
                # The trial id is what a reviewer pulls to re-read the run.
                # It is printed whole: truncating it makes several distinct
                # executions of the same transaction render as identical rows,
                # which is exactly the distinction the reviewer needs. It
                # already contains the task and the perturbation, so those are
                # not repeated.
                L.append(f"      - {d.trial_id}  ({where})")
                L.append(f"          {d.detail[:86]}")
            if len(r.deviations) > 4:
                L.append(f"      - and {len(r.deviations) - 4} further "
                         f"instance(s)")
    else:
        L.append("  EXCEPTIONS: none. Every tested control operated without "
                 "deviation.")

    if a.not_tested:
        L.append("-" * width)
        L.append("  SCOPE LIMITATION — CONTROLS NOT TESTED BY THIS RUN")
        L.append("  No conclusion is drawn on the following. They are neither "
                 "effective nor deficient")
        L.append("  on this evidence, and remain open for the period.")
        L.append("")
        for c, why in a.not_tested:
            L.append(f"    {c.control_id}  {c.name}")
            for line in _wrap(why, width - 10):
                L.append(f"          {line}")
    L.append("-" * width)
    L.append(f"  Evidence hash {a.evidence_hash} over {a.population} stored "
             f"trajectories")
    L.append("  This attestation regenerates from those trajectories with no "
             "model calls.")
    L.append(f"  Generated {a.generated_utc}")
    L.append(bar)
    return "\n".join(L)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def exception_routing(attestation: Attestation) -> list[dict]:
    """Deviations grouped the way an AP function routes them.

    A deviation rate tells a control owner a control is failing. This tells
    them which team has to do something about it, which is what turns an audit
    finding into work that gets done.
    """
    rows: dict[tuple, dict] = {}
    for r in attestation.results:
        for d in r.deviations:
            code = (d.detail.split()[0] if "reason_code" in d.invariant_id
                    else d.control_id)
            category, owner, sla = hold_category(code)
            key = (r.control.control_id, owner)
            row = rows.setdefault(key, {
                "control_id": r.control.control_id,
                "control": r.control.name,
                "category": category,
                "owner": r.control.remediation_owner or owner,
                "sla_days": r.control.sla_days or sla,
                "trials": set(), "tasks": set()})
            # Counted by execution, not by invariant breach. One execution that
            # breaks two invariants of the same control is one item of work,
            # and this figure has to reconcile with the deviation count in the
            # workpaper or the two documents contradict each other.
            row["trials"].add(d.trial_id)
            row["tasks"].add(d.task_id)
    out = []
    for row in rows.values():
        row["count"] = len(row.pop("trials"))
        row["transactions"] = len(row["tasks"])
        row["tasks"] = sorted(row["tasks"])
        out.append(row)
    return sorted(out, key=lambda r: (-r["count"], r["control_id"]))
