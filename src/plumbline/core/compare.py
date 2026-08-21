"""
Typed argument comparison.

Comparing tool arguments as raw strings produces both false alarms and misses.
`"INV-1029"` vs `"inv-1029 "` is not a divergence. `amount=49.0` vs
`amount=490.0` is the most expensive divergence in the system. A single
equality operator cannot tell those apart, so comparison is typed per field.

Policies, and why each default is what it is:

  EXACT      identifiers, account codes, enum-like values. Any difference is a
             different referent, so normalize whitespace/case and then demand
             equality.
  NUMERIC    money, quantities, thresholds. Compared with an explicit absolute
             and relative tolerance. Default tolerance is ZERO: for financial
             arguments, "close enough" is the bug. Widen it deliberately, per
             field, when the field really is approximate.
  TEXT       free-text reasons, notes, messages. Normalized and compared, but
             flagged at a lower severity, because rewording a note is not the
             same class of defect as changing an amount.
  IGNORE     fields that are legitimately allowed to vary (timestamps, request
             ids, idempotency keys). Declaring these explicitly is what keeps
             the signal clean.
  SET        collections whose order carries no meaning.

Unknown fields fall back to a heuristic: numbers get NUMERIC with zero
tolerance, everything else gets EXACT. The bias is deliberate. An unclassified
field that changes should show up and be triaged, not be silently forgiven.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

EXACT = "exact"
NUMERIC = "numeric"
TEXT = "text"
IGNORE = "ignore"
SET = "set"

_WS = re.compile(r"\s+")


def normalize_scalar(v: Any) -> Any:
    if isinstance(v, str):
        return _WS.sub(" ", v.strip()).lower()
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def canonical(v: Any) -> Any:
    """Hashable canonical form, used for grouping runs by argument identity."""
    if isinstance(v, dict):
        return tuple(sorted((str(k), canonical(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple)):
        return tuple(canonical(x) for x in v)
    return normalize_scalar(v)


@dataclass
class FieldPolicy:
    policy: str = EXACT
    abs_tol: float = 0.0
    rel_tol: float = 0.0
    severity: str = "high"     # high | medium | low
    note: str = ""


@dataclass
class ArgSchema:
    """How to compare the arguments of one tool. `fields` maps argument name to
    policy; `default` applies to anything not listed."""
    fields: dict[str, FieldPolicy] = field(default_factory=dict)
    default: FieldPolicy | None = None

    def policy_for(self, name: str, value: Any) -> FieldPolicy:
        if name in self.fields:
            return self.fields[name]
        if self.default is not None:
            return self.default
        if isinstance(value, bool):
            return FieldPolicy(EXACT)
        if isinstance(value, (int, float)):
            return FieldPolicy(NUMERIC, 0.0, 0.0, "high")
        if isinstance(value, (list, tuple, set)):
            return FieldPolicy(SET, severity="medium")
        return FieldPolicy(EXACT)


@dataclass
class ArgDiff:
    tool: str
    field: str
    expected: Any
    got: Any
    policy: str
    severity: str
    magnitude: float | None = None   # for numeric: ratio got/expected

    def describe(self) -> str:
        if self.magnitude is not None and self.magnitude not in (0.0, 1.0):
            return (f"{self.tool}.{self.field}: expected {self.expected!r}, "
                    f"got {self.got!r} ({self.magnitude:.4g}x)")
        return f"{self.tool}.{self.field}: expected {self.expected!r}, got {self.got!r}"


def _numeric_equal(a: float, b: float, pol: FieldPolicy) -> bool:
    if pol.abs_tol == 0.0 and pol.rel_tol == 0.0:
        return a == b
    return math.isclose(a, b, abs_tol=pol.abs_tol, rel_tol=pol.rel_tol)


def compare_args(tool: str, expected: dict, got: dict,
                 schema: ArgSchema | None = None) -> list[ArgDiff]:
    """Field-by-field comparison of one tool call's arguments against the
    reference. Missing and extra fields both count: an argument the reference
    passed and this run omitted is a divergence even though nothing 'changed'."""
    schema = schema or ArgSchema()
    diffs: list[ArgDiff] = []
    for key in sorted(set(expected) | set(got)):
        ev, gv = expected.get(key, _MISSING), got.get(key, _MISSING)
        pol = schema.policy_for(key, ev if ev is not _MISSING else gv)
        if pol.policy == IGNORE:
            continue
        if ev is _MISSING or gv is _MISSING:
            diffs.append(ArgDiff(tool, key,
                                 None if ev is _MISSING else ev,
                                 None if gv is _MISSING else gv,
                                 pol.policy, pol.severity))
            continue
        if pol.policy == NUMERIC:
            try:
                a, b = float(ev), float(gv)
            except (TypeError, ValueError):
                if canonical(ev) != canonical(gv):
                    diffs.append(ArgDiff(tool, key, ev, gv, pol.policy, pol.severity))
                continue
            if not _numeric_equal(a, b, pol):
                mag = (b / a) if a not in (0.0,) else None
                diffs.append(ArgDiff(tool, key, ev, gv, pol.policy, pol.severity, mag))
        elif pol.policy == SET:
            if _as_set(ev) != _as_set(gv):
                diffs.append(ArgDiff(tool, key, ev, gv, pol.policy, pol.severity))
        else:  # EXACT and TEXT both compare on the normalized value
            if canonical(ev) != canonical(gv):
                diffs.append(ArgDiff(tool, key, ev, gv, pol.policy, pol.severity))
    return diffs


def _as_set(v: Any) -> frozenset:
    if isinstance(v, (list, tuple, set, frozenset)):
        return frozenset(canonical(x) for x in v)
    return frozenset([canonical(v)])


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


_MISSING = _Missing()
