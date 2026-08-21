"""Unit tests for the measurement core.

These test the instrument, not the agent. The instrument has to be right before
any number it produces about an agent means anything.
"""
from __future__ import annotations

import pytest

from plumbline.core.align import EXTRA, MATCH, SKIPPED, SUBSTITUTE, align, first_divergence
from plumbline.core.compare import (EXACT, IGNORE, NUMERIC, TEXT, ArgSchema,
                                     FieldPolicy, compare_args)
from plumbline.core.trajectory import Step, Trajectory
from plumbline.analysis.stats import compare, permutation_test, wilson
from plumbline.spec.invariants import (ArgEquals, CallAtMost, MustCall,
                                        MustNotCall, Ordering, PolicySpec)

REF = (("tool_call", "fetch"), ("tool_call", "match"),
       ("tool_call", "dup"), ("tool_call", "pay"))


# --------------------------------------------------------------- alignment
def test_alignment_reports_skip_as_skip():
    cand = (("tool_call", "fetch"), ("tool_call", "match"), ("tool_call", "pay"))
    op = first_divergence(cand, REF)
    assert op.op == SKIPPED
    assert op.ref_item == ("tool_call", "dup")


def test_alignment_reports_substitution():
    cand = (("tool_call", "fetch"), ("tool_call", "match"),
            ("tool_call", "other"), ("tool_call", "pay"))
    op = first_divergence(cand, REF)
    assert op.op == SUBSTITUTE
    assert op.cand_item == ("tool_call", "other")


def test_alignment_reports_extra_step():
    cand = REF[:2] + (("tool_call", "extra"),) + REF[2:]
    ops = [o for o in align(cand, REF) if o.is_divergence]
    assert len(ops) == 1 and ops[0].op == EXTRA


def test_alignment_identical_has_no_divergence():
    assert first_divergence(REF, REF) is None
    assert all(o.op == MATCH for o in align(REF, REF))


def test_alignment_one_skip_does_not_cascade():
    """A single omission must align as one gap, not as three mismatches."""
    cand = REF[:1] + REF[2:]
    divs = [o for o in align(cand, REF) if o.is_divergence]
    assert len(divs) == 1 and divs[0].op == SKIPPED


def test_alignment_empty_candidate():
    divs = [o for o in align((), REF) if o.is_divergence]
    assert len(divs) == len(REF)
    assert all(o.op == SKIPPED for o in divs)


# --------------------------------------------------- argument comparison
def test_money_drift_is_caught_with_magnitude():
    diffs = compare_args("pay", {"amount": 49.0}, {"amount": 490.0},
                         ArgSchema(fields={"amount": FieldPolicy(NUMERIC)}))
    assert len(diffs) == 1
    assert diffs[0].magnitude == pytest.approx(10.0)


def test_whitespace_and_case_are_not_divergences():
    diffs = compare_args("pay", {"invoice_id": "INV-7001"},
                         {"invoice_id": " inv-7001 "},
                         ArgSchema(fields={"invoice_id": FieldPolicy(EXACT)}))
    assert diffs == []


def test_zero_tolerance_is_the_default_for_numbers():
    diffs = compare_args("pay", {"amount": 49.00}, {"amount": 49.01})
    assert len(diffs) == 1, "a one-cent drift on an unclassified number must show"


def test_ignored_field_is_skipped():
    diffs = compare_args("log", {"ts": 1}, {"ts": 2},
                         ArgSchema(fields={"ts": FieldPolicy(IGNORE)}))
    assert diffs == []


def test_missing_field_counts_as_divergence():
    diffs = compare_args("pay", {"amount": 49.0, "vendor_id": "V-1"},
                         {"amount": 49.0})
    assert len(diffs) == 1 and diffs[0].field == "vendor_id"


def test_extra_field_counts_as_divergence():
    diffs = compare_args("pay", {"amount": 49.0}, {"amount": 49.0, "memo": "x"})
    assert len(diffs) == 1 and diffs[0].field == "memo"


# ------------------------------------------------------------ invariants
def _traj(names_and_args, task="T"):
    return Trajectory("t", task_id=task, steps=[
        Step("tool_call", n, a) for n, a in names_and_args])


def test_must_call_localizes_to_never_called():
    v = MustCall("dup").check(_traj([("fetch", {}), ("pay", {})]), {})
    assert v is not None and v.step_index == -1


def test_ordering_fires_only_when_second_step_runs():
    inv = Ordering("match", then="pay")
    assert inv.check(_traj([("fetch", {}), ("match", {})]), {}) is None
    v = inv.check(_traj([("fetch", {}), ("pay", {})]), {})
    assert v is not None and v.step_index == 1


def test_call_at_most_catches_double_payment():
    v = CallAtMost("pay", 1).check(
        _traj([("pay", {}), ("pay", {})]), {})
    assert v is not None and v.step_index == 1


def test_arg_equals_uses_ground_truth_not_other_runs():
    """This is the case path-level analysis cannot see: every run agrees with
    every other run, and every one of them is wrong."""
    inv = ArgEquals("pay", "amount", "expected_amount")
    t = _traj([("pay", {"amount": 490.0})])
    assert inv.check(t, {"expected_amount": 49.0}) is not None
    assert inv.check(t, {"expected_amount": 490.0}) is None


def test_conditional_invariant_does_not_apply_when_condition_false():
    inv = MustNotCall("pay", when=lambda c: not c.get("should_pay", True))
    assert inv.applies({"should_pay": True}) is False
    assert inv.applies({"should_pay": False}) is True


def test_policy_sorts_violations_by_severity():
    spec = PolicySpec("p", [MustCall("dup", severity="critical"),
                            MustCall("log", severity="medium")])
    out = spec.check(_traj([("fetch", {})]), {})
    assert [v.severity for v in out] == ["critical", "medium"]


# ----------------------------------------------------------------- stats
def test_wilson_interval_contains_estimate_and_stays_in_range():
    p = wilson(35, 40)
    assert p.lo < p.value < p.hi
    assert 0.0 <= p.lo and p.hi <= 1.0


def test_wilson_perfect_score_still_has_uncertainty():
    """40 clean runs is not proof of 100%. The bound must reflect that."""
    p = wilson(40, 40)
    assert p.value == 1.0
    assert p.lo < 0.95


def test_wilson_more_data_tightens_the_bound():
    assert wilson(200, 200).lo > wilson(20, 20).lo


def test_permutation_test_detects_a_real_gap():
    a = [True] * 25 + [False] * 15
    b = [True] * 39 + [False] * 1
    assert permutation_test(a, b, n=4000) < 0.05


def test_permutation_test_does_not_cry_wolf():
    a = [True] * 25 + [False] * 15
    b = [True] * 27 + [False] * 13
    assert permutation_test(a, b, n=4000) > 0.05


def test_permutation_p_value_is_never_zero():
    a = [False] * 30
    b = [True] * 30
    assert permutation_test(a, b, n=2000) > 0.0
