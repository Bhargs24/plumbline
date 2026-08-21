"""
Consistency analysis: did perturbation change behavior away from nominal.

Conformance answers "was the policy upheld". Consistency answers a different and
complementary question: "does this agent do the same thing when asked the same
thing differently". Both are needed. An agent can be consistently wrong, which
conformance catches and consistency does not, and an agent can be erratically
right, which consistency catches and conformance does not. Erratic-but-right is
the state that passes evals and fails in production.

The reference is the agent's own modal BASELINE path for that task, not the
mode across all runs. That distinction matters: taking the mode over everything
lets a perturbation that affects most runs redefine what "normal" means, and the
divergence then disappears into the reference.

Divergences are localized with sequence alignment, so a skipped control is
reported as a skip rather than as a substitution of whatever followed it.
Argument drift is compared field by field with typed policies, which is the
resolution the closest prior art names as future work.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..core.align import EXTRA, SKIPPED, SUBSTITUTE, align
from ..core.compare import ArgSchema, compare_args
from ..core.trajectory import Trajectory
from .stats import Proportion, wilson


@dataclass
class Divergence:
    kind: str                # "skipped" | "extra" | "substitute" | "arg"
    step_index: int
    expected: str
    got: str
    severity: str = "high"
    perturbations: dict = field(default_factory=lambda: defaultdict(int))
    trial_ids: list = field(default_factory=list)
    task_ids: set = field(default_factory=set)

    @property
    def count(self) -> int:
        return len(self.trial_ids)

    def describe(self, max_trials: int = 4) -> str:
        perts = ", ".join(f"{k} x{v}" for k, v in
                          sorted(self.perturbations.items(), key=lambda kv: -kv[1]))
        shown = ", ".join(self.trial_ids[:max_trials])
        more = f" +{self.count - max_trials} more" if self.count > max_trials else ""
        head = {"skipped": "STEP SKIPPED", "extra": "EXTRA STEP",
                "substitute": "DIFFERENT STEP", "arg": "ARGUMENT DRIFT",
                "outcome": "DIFFERENT END STATE"}.get(self.kind, self.kind.upper())
        # An outcome divergence is about the run as a whole, so there is no step
        # to point at. Printing "at step -1" would read as a defect in the tool.
        where = "" if self.step_index is None or self.step_index < 0 \
            else f" at step {self.step_index}"
        return (f"[{head}]{where}: expected {self.expected}, "
                f"got {self.got}\n"
                f"      {self.count} run(s) under {perts}\n"
                f"      trials: {shown}{more}")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "step_index": self.step_index,
                "expected": self.expected, "got": self.got,
                "severity": self.severity, "count": self.count,
                "perturbations": dict(self.perturbations),
                "task_ids": sorted(self.task_ids), "trial_ids": self.trial_ids}


@dataclass
class ConsistencyReport:
    structural: Proportion          # runs whose path matches nominal
    argument: Proportion            # same-path runs whose args match nominal
    outcome: Proportion             # runs whose final ledger state matches nominal
    self_consistency: Proportion    # baseline runs agreeing with each other
    by_perturbation: dict           # perturbation -> Proportion (structural)
    reference_paths: dict           # task_id -> path string
    divergences: list
    n_runs: int = 0

    def to_dict(self) -> dict:
        return {"structural": self.structural.to_dict(),
                "argument": self.argument.to_dict(),
                "outcome": self.outcome.to_dict(),
                "self_consistency": self.self_consistency.to_dict(),
                "by_perturbation": {k: v.to_dict()
                                    for k, v in self.by_perturbation.items()},
                "reference_paths": self.reference_paths,
                "divergences": [d.to_dict() for d in self.divergences],
                "n_runs": self.n_runs}


def _lbl(sig) -> str:
    return f"{sig[0]}:{sig[1]}" if isinstance(sig, tuple) else str(sig)


def _reference_path(trajs: list[Trajectory]) -> tuple:
    """Modal path among baseline runs; falls back to modal overall when a task
    has no baseline runs."""
    base = [t.path() for t in trajs if t.perturbation == "baseline"]
    pool = base or [t.path() for t in trajs]
    return Counter(pool).most_common(1)[0][0] if pool else ()


def _reference_args(trajs: list[Trajectory], ref_path: tuple) -> list[dict]:
    """Modal argument set for each step of the reference path, taken from the
    baseline runs that followed it."""
    on_path = [t for t in trajs
               if t.path() == ref_path and t.perturbation == "baseline"]
    if not on_path:
        on_path = [t for t in trajs if t.path() == ref_path]
    if not on_path:
        return []
    out = []
    for i in range(len(ref_path)):
        counts = Counter()
        seen = {}
        for t in on_path:
            steps = t.control_steps()
            if i < len(steps):
                key = tuple(sorted((k, str(v)) for k, v in steps[i].args.items()))
                counts[key] += 1
                seen[key] = steps[i].args
        out.append(seen[counts.most_common(1)[0][0]] if counts else {})
    return out


def analyze_consistency(trajectories: list[Trajectory],
                        arg_schemas: dict | None = None,
                        ledger_states: dict | None = None) -> ConsistencyReport:
    arg_schemas = arg_schemas or {}
    ledger_states = ledger_states or {}

    by_task: dict[str, list[Trajectory]] = defaultdict(list)
    for t in trajectories:
        by_task[t.task_id].append(t)

    structural_hits, arg_hits, outcome_hits = [], [], []
    per_pert: dict[str, list[bool]] = defaultdict(list)
    self_hits: list[bool] = []
    divergences: dict[tuple, Divergence] = {}
    reference_paths: dict[str, str] = {}

    def record(kind, idx, expected, got, severity, traj):
        key = (kind, idx, expected, got)
        d = divergences.get(key)
        if d is None:
            d = Divergence(kind, idx, expected, got, severity)
            divergences[key] = d
        d.perturbations[traj.perturbation] += 1
        d.trial_ids.append(traj.trial_id)
        d.task_ids.add(traj.task_id)

    for task_id, trajs in by_task.items():
        ref_path = _reference_path(trajs)
        ref_args = _reference_args(trajs, ref_path)
        reference_paths[task_id] = (" -> ".join(_lbl(s) for s in ref_path)
                                    or "(no control steps)")

        # self-consistency: baseline runs agreeing with the baseline mode
        base = [t for t in trajs if t.perturbation == "baseline"]
        for t in base:
            self_hits.append(t.path() == ref_path)

        # reference ledger state, for outcome comparison
        ref_ledger = None
        base_states = [ledger_states.get(t.trial_id) for t in base]
        base_states = [s for s in base_states if s is not None]
        if base_states:
            counts = Counter(tuple(sorted(s.items())) for s in base_states)
            ref_ledger = dict(counts.most_common(1)[0][0])

        for t in trajs:
            path = t.path()
            same_path = path == ref_path
            structural_hits.append(same_path)
            per_pert[t.perturbation].append(same_path)

            if not same_path:
                for op in align(path, ref_path):
                    if op.op == SKIPPED:
                        record("skipped", op.ref_index, _lbl(op.ref_item),
                               "(not called)", "high", t)
                    elif op.op == EXTRA:
                        record("extra", op.ref_index if op.ref_index is not None else -1,
                               "(nothing)", _lbl(op.cand_item), "medium", t)
                    elif op.op == SUBSTITUTE:
                        record("substitute", op.ref_index, _lbl(op.ref_item),
                               _lbl(op.cand_item), "high", t)
            else:
                # same route: check the values it used
                steps = t.control_steps()
                clean = True
                for i, step in enumerate(steps):
                    if i >= len(ref_args):
                        break
                    schema = arg_schemas.get(step.name, ArgSchema())
                    diffs = compare_args(step.name, ref_args[i], step.args, schema)
                    # Fields declared `low` are ones the policy author said are
                    # allowed to vary, typically free-text reasons and notes.
                    # Counting them would drive argument consistency to near
                    # zero on every study, since an LLM rewords prose every run,
                    # and would bury real drift under noise. This is what the
                    # declared severity is for.
                    for d in (x for x in diffs if x.severity != "low"):
                        clean = False
                        record("arg", i,
                               f"{d.tool}.{d.field}={d.expected!r}",
                               f"{d.got!r}", d.severity, t)
                arg_hits.append(clean)

            if ref_ledger is not None:
                got = ledger_states.get(t.trial_id)
                outcome_hits.append(got is not None and got == ref_ledger)

    ordered = sorted(divergences.values(),
                     key=lambda d: ({"skipped": 0, "substitute": 1, "arg": 2,
                                     "extra": 3}[d.kind], -d.count))

    return ConsistencyReport(
        structural=wilson(sum(structural_hits), len(structural_hits)),
        argument=wilson(sum(arg_hits), len(arg_hits)),
        outcome=wilson(sum(outcome_hits), len(outcome_hits)),
        self_consistency=wilson(sum(self_hits), len(self_hits)),
        by_perturbation={k: wilson(sum(v), len(v)) for k, v in per_pert.items()},
        reference_paths=reference_paths,
        divergences=ordered,
        n_runs=len(trajectories),
    )
