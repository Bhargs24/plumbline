"""
The trajectory model.

An agent run is a sequence of observable events. Plumbline compares runs at
three resolutions, and the middle one is the reason this project exists:

  1. ACTION TYPE   which tools were called, in what order.
  2. ARGUMENTS     what values those tools were called with.
  3. OUTCOME       what the run finally produced.

The closest prior art ("Consistency as a Testable Property", arXiv 2605.10516)
compares trajectories at resolution 1 only. Its own limitations section names
resolution 2 as open work: "granular trajectory similarity metrics capturing
command content details beyond action type". A refund of $490 instead of $49
takes the identical path and produces a plausible confirmation message, so it is
invisible at resolutions 1 and 3. That is the case this model is built for.

Steps carry their real execution context (errors, latency, token cost, the raw
provider payload) so a trajectory is a replayable record, not just a summary.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Iterator

# Step kinds. Only CONTROL_KINDS participate in path comparison; message and
# thought steps are recorded for debugging but do not define control flow,
# because an agent that thinks differently but acts identically is behaving
# consistently in the only sense a downstream system can observe.
TOOL_CALL = "tool_call"
DECISION = "decision"
THOUGHT = "thought"
MESSAGE = "message"
FINAL = "final"

CONTROL_KINDS = (TOOL_CALL, DECISION)


@dataclass
class Step:
    kind: str
    name: str
    args: dict = field(default_factory=dict)
    output: Any = None
    error: str | None = None
    index: int = -1
    latency_ms: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    raw: dict | None = None

    @property
    def is_control(self) -> bool:
        return self.kind in CONTROL_KINDS

    @property
    def failed(self) -> bool:
        return self.error is not None

    def signature(self) -> tuple[str, str]:
        """Structural identity: what the step did, ignoring the values it used."""
        return (self.kind, self.name)

    def label(self) -> str:
        return f"{self.kind}:{self.name}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Trajectory:
    """One execution of one agent on one task variant.

    `variant_id` groups trials that were run on the SAME perturbed input, which
    is what lets us separate two different questions that get conflated:
      - self-consistency: same input, repeated. Sampling noise.
      - robustness: meaning-preserving change of input. Brittleness.
    Averaging those together hides which one you have.
    """
    trial_id: str
    perturbation: str = "baseline"
    variant_id: str = "baseline/0"
    arm: str = "default"
    task_id: str = ""
    steps: list[Step] = field(default_factory=list)
    final_output: str = ""
    task_input: str = ""
    error: str | None = None
    model: str = ""
    seed: int | None = None
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)

    # ---- views used by the analyzers ---------------------------------
    def control_steps(self) -> list[Step]:
        return [s for s in self.steps if s.is_control]

    def path(self) -> tuple[tuple[str, str], ...]:
        """The control-flow path: ordered signatures of tool calls and decisions.
        Two runs with the same path did the same things in the same order."""
        return tuple(s.signature() for s in self.control_steps())

    def path_str(self) -> str:
        return " -> ".join(f"{k}:{n}" for k, n in self.path()) or "(no control steps)"

    def calls(self, name: str, require_success: bool = True) -> list[Step]:
        """Calls to `name`. By default only ones that actually completed.

        Invariants are claims about effects, so a call that raised did not
        happen. An agent that invoked `check_vendor_status` with a bad argument,
        got an error, and moved on has NOT run the vendor check, and counting it
        as run is how a harness certifies a control that never executed.
        """
        return [s for s in self.control_steps()
                if s.name == name and not (require_success and s.failed)]

    def called(self, name: str, require_success: bool = True) -> bool:
        return bool(self.calls(name, require_success))

    def first_index_of(self, name: str, require_success: bool = True) -> int:
        for i, s in enumerate(self.control_steps()):
            if s.name == name and not (require_success and s.failed):
                return i
        return -1

    def failed_steps(self) -> list[Step]:
        return [s for s in self.steps if s.failed]

    def __iter__(self) -> Iterator[Step]:
        return iter(self.steps)

    def __len__(self) -> int:
        return len(self.steps)

    # ---- identity -----------------------------------------------------
    def path_hash(self) -> str:
        return hashlib.sha256(self.path_str().encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Trajectory":
        d = dict(d)
        steps = [Step.from_dict(s) for s in d.pop("steps", [])]
        known = {f for f in cls.__dataclass_fields__}
        obj = cls(**{k: v for k, v in d.items() if k in known})
        obj.steps = steps
        return obj


class TrajectoryStore:
    """Append-only JSONL store. Trajectories are the raw evidence behind a
    certificate, so they are written to disk before analysis and kept. A
    certificate you cannot re-derive from stored traces is an assertion, not
    evidence."""

    def __init__(self, path):
        from pathlib import Path
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, traj: Trajectory) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(traj.to_dict(), default=str) + "\n")

    def write_all(self, trajs: list[Trajectory]) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            for t in trajs:
                fh.write(json.dumps(t.to_dict(), default=str) + "\n")

    def load(self) -> list[Trajectory]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(Trajectory.from_dict(json.loads(line)))
        return out
