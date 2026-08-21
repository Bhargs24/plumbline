"""
Invariant specification.

A consistency score is a description. "78.5" tells an engineer nothing they can
act on and tells a compliance reviewer nothing they can sign. An invariant is a
claim about behavior that is either upheld or broken, and when it breaks it
breaks at a specific step for a specific reason.

So Plumbline asks you to declare what must always be true of a run:

    MustCall("detect_duplicate")
    Ordering("match_po", then="schedule_payment")
    CallAtMost("schedule_payment", 1)          # never pay an invoice twice
    ArgEquals("schedule_payment", "amount", from_context="expected_amount")

Then it perturbs the input in meaning-preserving ways and reports which of those
claims survive. This is the difference between "your agent scored 78" and "under
paraphrase, your duplicate-payment control fails 3 runs in 40, first divergence
at step 2". The second one gets fixed.

Severity is declared, not inferred. A skipped audit log and a skipped duplicate
check are not the same event, and any scoring that averages them is lying.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.trajectory import Trajectory

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}
SEVERITY_WEIGHT = {CRITICAL: 1.0, HIGH: 0.6, MEDIUM: 0.3, LOW: 0.1}


@dataclass
class Violation:
    invariant_id: str
    description: str
    severity: str
    detail: str
    step_index: int | None = None    # index into control steps, when localizable
    step_name: str | None = None

    def describe(self) -> str:
        if self.step_index is not None and self.step_index >= 0:
            where = f" at step {self.step_index}"
            if self.step_name:
                where += f" ({self.step_name})"
        elif self.step_index == -1:
            where = " (never called)"
        else:
            where = ""
        return f"[{self.severity.upper()}] {self.invariant_id}{where}: {self.detail}"

    def to_dict(self) -> dict:
        return {
            "invariant_id": self.invariant_id,
            "description": self.description,
            "severity": self.severity,
            "detail": self.detail,
            "step_index": self.step_index,
            "step_name": self.step_name,
        }


class Invariant:
    """Base class. `check` returns None when upheld, a Violation when broken.

    `context` carries per-task ground truth, for example the invoice's true
    amount or whether this invoice is a known duplicate. Invariants that depend
    on the task, not only on the trajectory shape, are the ones worth writing.
    """
    id: str = "invariant"
    description: str = ""
    severity: str = HIGH

    def check(self, traj: Trajectory, context: dict) -> Violation | None:
        raise NotImplementedError

    def applies(self, context: dict) -> bool:
        return True

    def _v(self, detail: str, idx: int | None = None, name: str | None = None) -> Violation:
        return Violation(self.id, self.description, self.severity, detail, idx, name)


@dataclass
class MustCall(Invariant):
    tool: str
    severity: str = CRITICAL
    when: Callable[[dict], bool] | None = None
    description: str = ""

    def __post_init__(self):
        self.id = f"must_call:{self.tool}"
        self.description = self.description or f"{self.tool} must be called on every run"

    def applies(self, context: dict) -> bool:
        return self.when(context) if self.when else True

    def check(self, traj, context):
        if not traj.called(self.tool):
            return self._v(f"{self.tool} was never called", -1, self.tool)
        return None


@dataclass
class MustNotCall(Invariant):
    tool: str
    severity: str = CRITICAL
    when: Callable[[dict], bool] | None = None
    description: str = ""

    def __post_init__(self):
        self.id = f"must_not_call:{self.tool}"
        self.description = self.description or f"{self.tool} must not be called"

    def applies(self, context: dict) -> bool:
        return self.when(context) if self.when else True

    def check(self, traj, context):
        idx = traj.first_index_of(self.tool)
        if idx >= 0:
            return self._v(f"{self.tool} was called but is forbidden here", idx, self.tool)
        return None


@dataclass
class Ordering(Invariant):
    """`first` must appear before every occurrence of `then`. If `then` never
    runs the invariant is vacuously upheld: the point is that you cannot pay
    before you have matched, not that you must pay."""
    first: str
    then: str
    severity: str = CRITICAL
    when: Callable[[dict], bool] | None = None
    description: str = ""

    def __post_init__(self):
        self.id = f"ordering:{self.first}-before-{self.then}"
        self.description = self.description or f"{self.first} must precede {self.then}"

    def applies(self, context: dict) -> bool:
        return self.when(context) if self.when else True

    def check(self, traj, context):
        first_at = None
        for i, s in enumerate(traj.control_steps()):
            if s.name == self.first and first_at is None:
                first_at = i
            if s.name == self.then and first_at is None:
                return self._v(
                    f"{self.then} ran before {self.first} ever ran", i, self.then)
        return None


@dataclass
class CallAtMost(Invariant):
    """Idempotence. The canonical failure this catches is paying an invoice
    twice after a transient tool error, which is the most expensive single
    agent bug in accounts payable."""
    tool: str
    limit: int = 1
    severity: str = CRITICAL
    description: str = ""

    def __post_init__(self):
        self.id = f"at_most:{self.tool}"
        self.description = self.description or f"{self.tool} at most {self.limit} time(s)"

    def check(self, traj, context):
        ctrl = traj.control_steps()
        calls = [i for i, s in enumerate(ctrl) if s.name == self.tool]
        if len(calls) > self.limit:
            return self._v(
                f"{self.tool} called {len(calls)} times, limit is {self.limit}",
                calls[self.limit], self.tool)
        return None


@dataclass
class ArgEquals(Invariant):
    """The argument-level invariant. The reference value comes from task ground
    truth, not from other runs, so this still fires when every run shares the
    same drift. Path-level analysis cannot see this failure at all."""
    tool: str
    arg: str
    from_context: str
    tolerance: float = 0.0
    severity: str = CRITICAL
    description: str = ""

    def __post_init__(self):
        self.id = f"arg_equals:{self.tool}.{self.arg}"
        self.description = (self.description or
                            f"{self.tool}.{self.arg} must equal {self.from_context}")

    def applies(self, context: dict) -> bool:
        return self.from_context in context

    def check(self, traj, context):
        expected = context.get(self.from_context)
        for i, s in enumerate(traj.control_steps()):
            if s.name != self.tool:
                continue
            if self.arg not in s.args:
                return self._v(f"{self.tool} called without {self.arg}", i, self.tool)
            got = s.args[self.arg]
            if _num_ne(got, expected, self.tolerance):
                return self._v(
                    f"{self.tool}.{self.arg} = {got!r}, expected {expected!r}",
                    i, self.tool)
        return None


@dataclass
class ArgSatisfies(Invariant):
    """Escape hatch for rules that are genuinely predicates, for example an
    approval route that must be 'cfo' whenever the amount exceeds a threshold."""
    tool: str
    predicate: Callable[[dict, dict], bool]
    id: str = "arg_satisfies"
    severity: str = HIGH
    description: str = ""

    def __post_init__(self):
        self.description = self.description or f"{self.tool} args must satisfy {self.id}"

    def check(self, traj, context):
        for i, s in enumerate(traj.control_steps()):
            if s.name == self.tool and not self.predicate(s.args, context):
                return self._v(f"{self.tool} args {s.args} violate {self.id}", i, self.tool)
        return None


def _num_ne(got: Any, expected: Any, tol: float) -> bool:
    try:
        g, e = float(got), float(expected)
    except (TypeError, ValueError):
        return str(got).strip().lower() != str(expected).strip().lower()
    return abs(g - e) > tol


@dataclass
class PolicySpec:
    """The declared policy for an agent, plus the argument comparison schemas
    used when diffing runs against each other."""
    name: str
    invariants: list = field(default_factory=list)
    arg_schemas: dict = field(default_factory=dict)   # tool -> ArgSchema

    def check(self, traj: Trajectory, context: dict) -> list[Violation]:
        out = []
        for inv in self.invariants:
            if not inv.applies(context):
                continue
            v = inv.check(traj, context)
            if v is not None:
                out.append(v)
        out.sort(key=lambda v: SEVERITY_ORDER.get(v.severity, 9))
        return out

    def applicable(self, context: dict) -> list:
        return [i for i in self.invariants if i.applies(context)]

    def ids(self) -> list[str]:
        return [i.id for i in self.invariants]
