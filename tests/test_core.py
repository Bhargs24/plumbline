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


# --------------------------------------------------------------- budget
def test_dated_model_id_still_prices():
    """A dated snapshot id must not silently price at zero, which would make
    the spend cap fail open."""
    from plumbline.runtime.budget import resolve_price
    assert resolve_price("claude-haiku-4-5-20251001") == resolve_price("claude-haiku-4-5")


def test_unknown_model_raises_rather_than_costing_nothing():
    from plumbline.runtime.budget import UnknownModelPrice, resolve_price
    import pytest as _pytest
    with _pytest.raises(UnknownModelPrice):
        resolve_price("gpt-does-not-exist")


def test_budget_cap_actually_fires():
    from plumbline.runtime.budget import Budget, BudgetExceeded
    import pytest as _pytest
    # ledger_path=None: a test must never journal fake spend into the real
    # ledger, which would inflate the production cap.
    b = Budget(max_usd=0.01, ledger_path=None)
    b.record("claude-haiku-4-5", 1_000_000, 1_000_000)   # $6.00
    assert b.spent_usd > 0.01
    with _pytest.raises(BudgetExceeded):
        b.check()


# ------------------------------------------------- failed calls are not calls
def _traj_with_failure():
    """A run where the vendor check was invoked with a bad argument, raised,
    and was never retried. Observed live in the first smoke test."""
    return Trajectory("f", task_id="T", steps=[
        Step("tool_call", "fetch", {}),
        Step("tool_call", "check_vendor_status", {"vendor_id": "Ridgeline Components"},
             error="vendor Ridgeline Components not found"),
        Step("tool_call", "pay", {"amount": 10.0}),
    ])


def test_failed_control_does_not_satisfy_must_call():
    t = _traj_with_failure()
    assert t.called("check_vendor_status") is False
    assert t.called("check_vendor_status", require_success=False) is True
    assert MustCall("check_vendor_status").check(t, {}) is not None


def test_failed_precondition_does_not_satisfy_ordering():
    t = _traj_with_failure()
    v = Ordering("check_vendor_status", then="pay").check(t, {})
    assert v is not None, "a check that raised cannot license the payment that follows"


def test_failed_payment_attempt_does_not_count_toward_call_limit():
    t = Trajectory("g", task_id="T", steps=[
        Step("tool_call", "pay", {}, error="503 upstream unavailable"),
        Step("tool_call", "pay", {}),
    ])
    assert CallAtMost("pay", 1).check(t, {}) is None, "the retry is the only real payment"


def test_failed_call_with_wrong_amount_is_not_an_arg_violation():
    t = Trajectory("h", task_id="T", steps=[
        Step("tool_call", "pay", {"amount": 999.0}, error="timeout"),
        Step("tool_call", "pay", {"amount": 49.0}),
    ])
    assert ArgEquals("pay", "amount", "expected").check(t, {"expected": 49.0}) is None


def test_sampling_perturbation_rejects_models_that_cannot_sample():
    """The 4.7+ family removed sampling params. Fail at startup, not with a 400
    four hundred calls into a study."""
    from plumbline.adapters.llm import LLMClient
    from plumbline.runtime.cache import ResponseCache
    import pytest as _pytest
    c = LLMClient(model="claude-sonnet-5", cache=ResponseCache(enabled=False), offline=True)
    with _pytest.raises(ValueError, match="sampling"):
        c.complete(system="x", messages=[{"role": "user", "content": "y"}],
                   temperature=1.0, trial_key="t", turn=0)


# ------------------------------------------- budget must survive restarts
def test_spend_cap_is_cumulative_across_processes(tmp_path):
    """A cap held only in memory is not a cap. Running the same study twice
    used to give you twice the ceiling."""
    from plumbline.runtime.budget import Budget, BudgetExceeded
    import pytest as _pytest
    ledger = tmp_path / "spend.json"

    first = Budget(max_usd=1.00, ledger_path=ledger)
    first.record("claude-haiku-4-5", 500_000, 0)      # $0.50
    assert first.total_usd == _pytest.approx(0.50)
    first.check()                                      # still under

    second = Budget(max_usd=1.00, ledger_path=ledger)  # fresh process
    assert second.prior_usd == _pytest.approx(0.50), "must load earlier spend"
    second.record("claude-haiku-4-5", 600_000, 0)      # $0.60 -> $1.10 total
    with _pytest.raises(BudgetExceeded):
        second.check()


def test_ledger_survives_a_killed_run(tmp_path):
    """The first overrun happened because a killed process took its spend
    record with it. Spend is journalled per call, not at the end."""
    from plumbline.runtime.budget import Budget
    ledger = tmp_path / "spend.json"
    b = Budget(max_usd=100.0, ledger_path=ledger)
    b.record("claude-haiku-4-5", 100_000, 0)
    del b                                              # no clean shutdown
    assert Budget(max_usd=100.0, ledger_path=ledger).prior_usd > 0


def test_summary_separates_session_from_total(tmp_path):
    """A rerun against a warm cache pays almost nothing. Quoting that as the
    cost of the study is how the real figure got understated 6x."""
    from plumbline.runtime.budget import Budget
    ledger = tmp_path / "spend.json"
    Budget(max_usd=100.0, ledger_path=ledger).record("claude-haiku-4-5", 500_000, 0)
    rerun = Budget(max_usd=100.0, ledger_path=ledger)
    rerun.record("claude-haiku-4-5", 1_000, 0)
    s = rerun.summary()
    assert s["session_usd"] < 0.01
    assert s["total_usd"] > 0.49
    assert s["prompt_cache_engaged"] is False


def test_tests_never_touch_the_real_ledger():
    """Guard against the mistake above recurring: a Budget with no explicit
    ledger writes to the shared default, so any test using one must opt out."""
    import inspect, re
    import tests.test_core as mod
    src = inspect.getsource(mod)
    for m in re.finditer(r"Budget\((max_usd[^)]*)\)", src):
        assert "ledger_path" in m.group(1), (
            "every constructed budget in the suite must set ledger_path, "
            "or it journals fake spend into the shared production ledger")


def test_equivalence_guard_cache_key_is_stable_across_processes():
    """Python randomises str hashing per process, so hash() as a cache key
    misses every run and silently re-pays for identical work."""
    import subprocess, sys
    code = ("import sys; sys.path.insert(0,'src');"
            "from plumbline.perturb.library import _stable_id;"
            "print(_stable_id('Invoice INV-7002 just came in.'))")
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    b = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert a.stdout.strip() == b.stdout.strip() != ""
