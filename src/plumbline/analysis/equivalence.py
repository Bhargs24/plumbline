"""
Behavioral equivalence between an incumbent automation and its replacement.

This answers a different question from the rest of the package, and it is the
question that actually gates a migration.

  Conformance asks: did this agent obey its own declared rules.
  Consistency  asks: does this agent behave the same when the input is reworded.
  Equivalence  asks: does the REPLACEMENT do what the INCUMBENT did.

The third one matters because nobody retires a bot that runs real payroll or
accounts payable until somebody proves the replacement behaves the same way.
Today that proof is a parallel run: both systems execute side by side for thirty
days and a human eyeballs the differences. It is slow, it is manual, and it is
why migrations stall.

There is a structural reason this is a better foundation than robustness
testing. Robustness only reports something when the agent fails; against a
competent model you can measure nothing. Equivalence reports something whenever
two systems differ, and two systems always differ somewhere. The output does not
depend on either side being bad.

The pairing is exact rather than statistical. Both systems are run on the SAME
task under the SAME perturbation variant, so a divergence is attributable to the
systems rather than to the inputs they happened to receive.

And the perturbations are what make this more than a parallel run. An incumbent
RPA bot is deterministic and brittle; a replacement agent is flexible and
non-deterministic. Matching on a fixed test battery proves nothing about what
happens when a vendor rewords their invoice email. Equivalence UNDER
PERTURBATION is the bar.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from ..core.align import EXTRA, SKIPPED, SUBSTITUTE, align
from ..core.compare import ArgSchema, compare_args
from ..core.trajectory import Trajectory
from .consistency import Divergence
from .stats import Proportion, wilson


@dataclass
class EquivalenceReport:
    incumbent: str
    replacement: str
    outcome: Proportion           # same observable end state
    path: Proportion              # same sequence of control steps
    argument: Proportion          # same values, among pairs sharing a path
    incumbent_self_consistency: Proportion
    by_perturbation: dict         # perturbation -> Proportion (outcome)
    by_task: dict                 # task_id -> Proportion (outcome)
    worst_perturbation: tuple
    divergences: list
    n_pairs: int = 0
    unpaired: int = 0             # runs with no counterpart, excluded

    @property
    def retirement_bound(self) -> float:
        """The number a risk owner signs against: the 95% lower bound on outcome
        equivalence under the worst perturbation. Worst case because a migration
        is not safe on average, and a lower bound because a certificate should
        state what can be defended rather than what was observed on a good day."""
        return self.worst_perturbation[1].lo

    @property
    def divergences_observed(self) -> int:
        return sum(d.count for d in self.divergences)

    def runs_needed_for(self, bound: float = 0.95, z: float = 1.959963984540054) -> int:
        """Clean runs required, per perturbation, to reach `bound` if nothing
        diverges. Solving the Wilson lower bound for a perfect record gives
        n >= b*z^2 / (1-b), which is worth surfacing: a 0.95 bound needs about
        73 clean runs and no amount of confidence substitutes for them."""
        if bound >= 1.0:
            return 10 ** 9
        return int(-(-(bound * z * z) // (1.0 - bound)))

    def verdict(self) -> str:
        """Three outcomes, and the middle one is the one that must not be
        confused with the others.

        A perfect record on a small sample is NOT evidence of equivalence, and
        it is also not evidence of divergence. Reporting "materially different"
        when nothing actually differed would send someone to debug a system that
        is fine; reporting "safe to retire" would licence retiring a bot on
        twelve runs. The honest answer is that the study is too small, and the
        report says how much bigger it needs to be.
        """
        b = self.retirement_bound
        worst_n = self.worst_perturbation[1].total
        if b >= 0.95:
            return "Equivalent under every perturbation applied. Safe to retire."
        if self.divergences_observed == 0:
            need = self.runs_needed_for(0.95)
            return (f"No divergence observed in {self.n_pairs} paired runs, but the "
                    f"sample is too small to certify (bound {100 * b:.0f}%). "
                    f"Needs about {need} clean runs per perturbation; the weakest "
                    f"currently has {worst_n}.")
        if b >= 0.85:
            return "Near-equivalent. Review the listed divergences before retiring."
        if b >= 0.60:
            return "Diverges under at least one meaning-preserving change. Not safe to retire."
        return "Materially different system. A parallel run would not have caught this."

    def to_dict(self) -> dict:
        return {
            "incumbent": self.incumbent, "replacement": self.replacement,
            "retirement_bound": round(self.retirement_bound, 4),
            "verdict": self.verdict(),
            "outcome": self.outcome.to_dict(),
            "path": self.path.to_dict(),
            "argument": self.argument.to_dict(),
            "incumbent_self_consistency": self.incumbent_self_consistency.to_dict(),
            "by_perturbation": {k: v.to_dict() for k, v in self.by_perturbation.items()},
            "by_task": {k: v.to_dict() for k, v in self.by_task.items()},
            "worst_perturbation": [self.worst_perturbation[0],
                                   self.worst_perturbation[1].to_dict()],
            "divergences": [d.to_dict() for d in self.divergences],
            "n_pairs": self.n_pairs, "unpaired": self.unpaired,
        }

    def render(self, width: int = 74) -> str:
        L, bar = [], "=" * width
        L.append(bar)
        L.append("  PLUMBLINE PARITY CERTIFICATE")
        L.append(f"  incumbent: {self.incumbent}    replacement: {self.replacement}")
        L.append(bar)
        L.append(f"  Retirement confidence : {100 * self.retirement_bound:5.1f}%")
        L.append(f"  {self.verdict()}")
        L.append("  (95% lower bound on outcome equivalence under the worst")
        L.append(f"   perturbation, which was '{self.worst_perturbation[0]}')")
        L.append("-" * width)
        L.append(f"  Paired runs compared  : {self.n_pairs}"
                 + (f"   ({self.unpaired} unpaired, excluded)" if self.unpaired else ""))
        L.append("-" * width)
        L.append("  EQUIVALENCE  (does the replacement do what the incumbent did)")
        L.append(f"    same end state        {self.outcome.describe()}")
        L.append(f"    same control path     {self.path.describe()}")
        L.append(f"    same tool arguments   {self.argument.describe()}")
        L.append("")
        L.append("  Incumbent's own reproducibility, for reference:")
        L.append(f"    self-consistency      {self.incumbent_self_consistency.describe()}")
        L.append("-" * width)
        L.append("  Outcome equivalence per perturbation, worst first:")
        for name, p in sorted(self.by_perturbation.items(), key=lambda kv: kv[1].value):
            blocks = int(round(p.value * 24))
            flag = "  <- weakest" if name == self.worst_perturbation[0] else ""
            L.append(f"    {name:<14} {'#' * blocks}{'.' * (24 - blocks)} "
                     f"{p.pct:5.1f}%{flag}")
        L.append("-" * width)
        if self.divergences:
            L.append(f"  DIVERGENCES FROM INCUMBENT ({len(self.divergences)} distinct):")
            for d in self.divergences[:8]:
                L.append("    - " + d.describe())
            if len(self.divergences) > 8:
                L.append(f"    ... and {len(self.divergences) - 8} more")
        else:
            L.append("  No divergences. The replacement matched the incumbent")
            L.append("  on every paired run, under every perturbation applied.")
        L.append(bar)
        return "\n".join(L)


def _lbl(sig) -> str:
    return f"{sig[0]}:{sig[1]}" if isinstance(sig, tuple) else str(sig)


def _canonical_run(runs: list[Trajectory]) -> Trajectory | None:
    """The incumbent's representative behavior for one task and variant.

    A deterministic automation produces one path every time, so any run will do.
    When it does not, the mode is used and the disagreement shows up in the
    self-consistency figure, which is itself worth reporting: an incumbent that
    is not reproducible cannot be the reference for anything.
    """
    if not runs:
        return None
    if len(runs) == 1:
        return runs[0]
    modal = Counter(r.effect_path() for r in runs).most_common(1)[0][0]
    for r in runs:
        if r.effect_path() == modal:
            return r
    return runs[0]


def analyze_equivalence(trajectories: list[Trajectory], *,
                        incumbent: str, replacement: str,
                        ledger_states: dict | None = None,
                        arg_schemas: dict | None = None) -> EquivalenceReport:
    """Pair each replacement run with the incumbent run on the same task and
    the same perturbation variant, then compare."""
    ledger_states = ledger_states or {}
    arg_schemas = arg_schemas or {}

    inc_runs = [t for t in trajectories if t.arm == incumbent]
    rep_runs = [t for t in trajectories if t.arm == replacement]
    if not inc_runs or not rep_runs:
        raise ValueError(
            f"need runs from both {incumbent!r} and {replacement!r}; got "
            f"{len(inc_runs)} and {len(rep_runs)}")

    # incumbent reference, keyed on the exact input it saw
    by_key: dict[tuple, list[Trajectory]] = defaultdict(list)
    for t in inc_runs:
        by_key[(t.task_id, t.variant_id)].append(t)
    reference = {k: _canonical_run(v) for k, v in by_key.items()}

    # how reproducible is the incumbent itself
    self_hits = []
    for k, runs in by_key.items():
        ref = reference[k]
        for r in runs:
            self_hits.append(r.effect_path() == ref.effect_path())

    outcome_hits, path_hits, arg_hits = [], [], []
    per_pert: dict[str, list[bool]] = defaultdict(list)
    per_task: dict[str, list[bool]] = defaultdict(list)
    divergences: dict[tuple, Divergence] = {}
    unpaired = 0

    def record(kind, idx, expected, got, severity, traj):
        key = (kind, idx, expected, got)
        d = divergences.get(key)
        if d is None:
            d = Divergence(kind, idx, expected, got, severity)
            divergences[key] = d
        d.perturbations[traj.perturbation] += 1
        d.trial_ids.append(traj.trial_id)
        d.task_ids.add(traj.task_id)

    for rep in rep_runs:
        ref = reference.get((rep.task_id, rep.variant_id))
        if ref is None:
            unpaired += 1
            continue

        # --- outcome: the observable end state, not the closing message ----
        ref_state = ledger_states.get(ref.trial_id)
        rep_state = ledger_states.get(rep.trial_id)
        same_outcome = (ref_state is not None and rep_state == ref_state)
        outcome_hits.append(same_outcome)
        per_pert[rep.perturbation].append(same_outcome)
        per_task[rep.task_id].append(same_outcome)
        if ref_state is not None and not same_outcome:
            record("outcome", -1, str(ref_state), str(rep_state), "critical", rep)

        # --- path -----------------------------------------------------------
        # Effect paths only. Two architectures deliberate differently and that
        # is not a behavioral divergence; see Trajectory.effect_steps.
        ref_path, rep_path = ref.effect_path(), rep.effect_path()
        same_path = ref_path == rep_path
        path_hits.append(same_path)
        if not same_path:
            for op in align(rep_path, ref_path):
                if op.op == SKIPPED:
                    record("skipped", op.ref_index, _lbl(op.ref_item),
                           "(not called)", "high", rep)
                elif op.op == EXTRA:
                    record("extra", op.ref_index if op.ref_index is not None else -1,
                           "(nothing)", _lbl(op.cand_item), "medium", rep)
                elif op.op == SUBSTITUTE:
                    record("substitute", op.ref_index, _lbl(op.ref_item),
                           _lbl(op.cand_item), "high", rep)
        else:
            ref_steps, rep_steps = ref.effect_steps(), rep.effect_steps()
            clean = True
            for i, (a, b) in enumerate(zip(ref_steps, rep_steps, strict=False)):
                schema = arg_schemas.get(b.name, ArgSchema())
                for d in compare_args(b.name, a.args, b.args, schema):
                    if d.severity == "low":
                        continue          # declared as free to vary
                    clean = False
                    record("arg", i, f"{d.tool}.{d.field}={d.expected!r}",
                           f"{d.got!r}", d.severity, rep)
            arg_hits.append(clean)

    by_pert = {k: wilson(sum(v), len(v)) for k, v in per_pert.items()}
    worst = (min(by_pert.items(), key=lambda kv: kv[1].value)
             if by_pert else ("", wilson(0, 0)))

    ordered = sorted(divergences.values(),
                     key=lambda d: ({"outcome": 0, "skipped": 1, "substitute": 2,
                                     "arg": 3, "extra": 4}[d.kind], -d.count))

    return EquivalenceReport(
        incumbent=incumbent, replacement=replacement,
        outcome=wilson(sum(outcome_hits), len(outcome_hits)),
        path=wilson(sum(path_hits), len(path_hits)),
        argument=wilson(sum(arg_hits), len(arg_hits)),
        incumbent_self_consistency=wilson(sum(self_hits), len(self_hits)),
        by_perturbation=by_pert,
        by_task={k: wilson(sum(v), len(v)) for k, v in per_task.items()},
        worst_perturbation=worst,
        divergences=ordered,
        n_pairs=len(outcome_hits), unpaired=unpaired,
    )
