"""Audit sampling and control attestation.

These test arithmetic an auditor would recompute by hand, against figures that
appear in every attribute-sampling table. If these drift, the workpaper is
wrong in a way a reviewer would catch and we would not.
"""
from __future__ import annotations

import pytest

from plumbline.compliance import (
    P2P_FRAMEWORK,
    assess,
    assess_test_of_one,
    attest,
    hold_category,
    render_text,
    required_sample_size,
)
from plumbline.compliance.sampling import clopper_pearson_upper
from plumbline.core.trajectory import Step, Trajectory
from plumbline.spec.invariants import MustCall, Ordering, PolicySpec


# ---------------------------------------------------- sampling arithmetic
def test_sample_sizes_match_the_published_tables():
    """95%/5% is 59 and 90%/5% is 45 in every attribute-sampling table."""
    assert required_sample_size(0.05, 0.95) == 59
    assert required_sample_size(0.05, 0.90) == 45


def test_a_clean_sample_of_the_required_size_lands_on_tolerance():
    """The sample-size formula and the bound must come from the same
    distribution, or a sample of exactly the prescribed size appears to fail
    the test it was sized for."""
    n = required_sample_size(0.05, 0.95)
    assert clopper_pearson_upper(0, n, 0.95) == pytest.approx(0.05, abs=0.002)


def test_zero_tolerance_controls_cannot_be_satisfied_by_sampling():
    """No finite sample proves zero deviations in a population, which is why
    these get tested full-population."""
    assert required_sample_size(0.0) == 0


def test_small_clean_sample_is_inconclusive_not_effective():
    a = assess("P2P.01", tested=20, deviations=0, tolerable_rate=0.05,
               population=5000)
    assert not a.sufficient and not a.passes
    assert "INCONCLUSIVE" in a.conclusion()
    assert "59 required" in a.conclusion()


def test_adequate_clean_sample_concludes_effective():
    a = assess("P2P.01", tested=59, deviations=0, tolerable_rate=0.05,
               population=5000)
    assert a.sufficient and a.passes and "EFFECTIVE" in a.conclusion()


def test_full_population_needs_no_inference():
    a = assess("P2P.02", tested=384, deviations=0, tolerable_rate=0.0,
               population=384)
    assert a.full_population and a.passes
    assert a.upper_deviation_rate == 0.0, "every item examined; nothing to infer"
    assert "full population" in a.conclusion()


def test_one_deviation_on_a_zero_tolerance_control_is_a_deficiency():
    a = assess("P2P.05", tested=384, deviations=1, tolerable_rate=0.0,
               population=384)
    assert not a.passes and "loss event" in a.conclusion()


def test_auditor_concludes_against_the_upper_bound_not_the_observed_rate():
    a = assess("P2P.07", tested=100, deviations=4, tolerable_rate=0.05,
               population=5000)
    assert a.deviation_rate == 0.04, "the observed rate is inside tolerance"
    assert a.upper_deviation_rate > 0.05
    assert not a.passes, "but the bound is not, so it is a deficiency"


# --------------------------------------------- the compliance argument
def test_test_of_one_is_not_defensible_for_an_agent_operated_control():
    ok, why = assess_test_of_one("automated", "llm_agent", True)
    assert ok is False
    assert "deterministic" in why and "one execution" in why


def test_test_of_one_holds_for_conventional_automation():
    ok, _ = assess_test_of_one("automated", "deterministic", True)
    assert ok is True


def test_failed_itgc_removes_automated_reliance():
    ok, why = assess_test_of_one("automated", "deterministic", False)
    assert ok is False and "IT general controls" in why


# ------------------------------------------------------------ framework
def test_every_framework_control_has_an_objective_and_a_risk():
    for c in P2P_FRAMEWORK.controls:
        assert c.objective and c.risk and c.assertions
        assert 0.0 <= c.tolerable_rate <= 1.0


def test_cash_disbursement_controls_carry_zero_tolerance():
    for cid in ("P2P.01", "P2P.02", "P2P.03", "P2P.04", "P2P.05", "P2P.06"):
        assert P2P_FRAMEWORK.by_id(cid).tolerable_rate == 0.0


def test_hold_reasons_route_to_an_owner_with_an_sla():
    category, owner, sla = hold_category("DUPLICATE_EXACT")
    assert category == "Duplicate invoice hold" and owner and sla > 0
    unknown = hold_category("SOMETHING_NEW")
    assert unknown[1], "an unmapped code still routes somewhere"


# ---------------------------------------------------------- attestation
def _traj(trial, task, pert, names, error=None):
    return Trajectory(trial_id=trial, task_id=task, perturbation=pert,
                      variant_id=f"{pert}/0", arm="react", model="m",
                      error=error,
                      steps=[Step("tool_call", n, {}) for n in names])


def test_attestation_reports_the_conditions_a_control_stopped_operating():
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    ok = ["fetch_invoice", "check_duplicate"]
    trajs = [_traj(f"g{i}", "INV-1", "baseline", ok) for i in range(10)]
    trajs += [_traj(f"b{i}", "INV-2", "tool_fault", ["fetch_invoice"])
              for i in range(3)]
    a = attest(trajs, spec, {"INV-1": {}, "INV-2": {}}, P2P_FRAMEWORK)
    p2p02 = next(r for r in a.results if r.control.control_id == "P2P.02")
    assert p2p02.assessment.deviations == 3
    assert p2p02.by_perturbation == {"tool_fault": 3}, \
        "the report must name the condition, not only the rate"
    assert not p2p02.effective


def test_attestation_excludes_incomplete_runs_from_the_population():
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    trajs = [_traj("ok", "INV-1", "baseline", ["check_duplicate"]),
             _traj("dead", "INV-1", "baseline", [], error="budget stop")]
    a = attest(trajs, spec, {"INV-1": {}}, P2P_FRAMEWORK)
    assert a.population == 1 and a.incomplete == 1


def test_one_transaction_breaching_two_invariants_is_one_deviation():
    """P2P.01 covers a must-call and an ordering invariant. A single run
    failing both is one deviation of the control, not two."""
    spec = PolicySpec("p", [
        MustCall("match_purchase_order", severity="critical"),
        Ordering("match_purchase_order", then="schedule_payment",
                 severity="critical")])
    t = _traj("x", "INV-1", "baseline", ["schedule_payment"])
    a = attest([t], spec, {"INV-1": {}}, P2P_FRAMEWORK)
    p2p01 = next(r for r in a.results if r.control.control_id == "P2P.01")
    assert p2p01.assessment.deviations == 1
    assert len(p2p01.deviations) == 2, "both invariants are still listed"


def test_workpaper_states_the_reliance_approach_and_the_evidence_hash():
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    t = _traj("x", "INV-1", "baseline", ["check_duplicate"])
    a = attest([t], spec, {"INV-1": {}}, P2P_FRAMEWORK)
    text = render_text(a)
    assert "Test of one defensible: NO" in text
    assert a.evidence_hash and a.evidence_hash in text
    assert "regenerates from those trajectories" in text


def test_untested_controls_are_named_rather_than_left_looking_covered():
    """A control this run could not conclude on is a scope limitation. If it is
    dropped silently, a header reading "1 of 10 tested" never says which nine,
    and a reviewer cannot tell an untested control from a clean one."""
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    t = _traj("x", "INV-1", "baseline", ["check_duplicate"])
    a = attest([t], spec, {"INV-1": {}}, P2P_FRAMEWORK)

    text = render_text(a)
    assert "SCOPE LIMITATION" in text
    assert "neither effective nor deficient" in text

    reasons = {c.control_id: why for c, why in a.not_tested}
    assert len(a.results) + len(a.not_tested) == len(P2P_FRAMEWORK.controls), \
        "every control in the framework is either concluded on or explained"
    assert "P2P.01" in reasons
    assert "absent from the policy under test" in reasons["P2P.01"]
    for cid in reasons:
        assert cid in text, "a dropped control must be named in the workpaper"


def test_evidence_hash_changes_when_the_population_changes():
    """The hash is the CAVR answer: it pins which trajectories the conclusions
    rest on, so a reviewer can detect a population that shifted underneath."""
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    ctx = {"INV-1": {}}
    a = attest([_traj("x", "INV-1", "baseline", ["check_duplicate"])],
               spec, ctx, P2P_FRAMEWORK)
    b = attest([_traj("x", "INV-1", "baseline", ["check_duplicate"]),
                _traj("y", "INV-1", "baseline", ["check_duplicate"])],
               spec, ctx, P2P_FRAMEWORK)
    assert a.evidence_hash != b.evidence_hash


def test_routing_counts_reconcile_with_the_deviation_counts():
    """The routing queue and the workpaper are read side by side. If one says
    16 items of work and the other concluded 8 deviations, a reviewer stops
    trusting both. Counted by execution in both places."""
    from plumbline.compliance import exception_routing
    from plumbline.spec.invariants import Ordering

    spec = PolicySpec("p", [
        MustCall("match_purchase_order", severity="critical"),
        Ordering("match_purchase_order", then="schedule_payment",
                 severity="critical")])
    # each of these breaks BOTH P2P.01 invariants in a single execution
    trajs = [_traj(f"t{i}", "INV-1", "tool_fault", ["schedule_payment"])
             for i in range(8)]
    a = attest(trajs, spec, {"INV-1": {}}, P2P_FRAMEWORK)

    p2p01 = next(r for r in a.results if r.control.control_id == "P2P.01")
    row = next(r for r in exception_routing(a) if r["control_id"] == "P2P.01")
    assert len(p2p01.deviations) == 16, "sixteen invariant breaches"
    assert p2p01.assessment.deviations == 8, "across eight executions"
    assert row["count"] == 8, "so eight items of work, not sixteen"
    assert row["transactions"] == 1 and row["tasks"] == ["INV-1"]


def test_no_library_function_is_named_so_pytest_collects_it():
    """A public helper named test_* is imported into any user's suite and
    collected as a test case, failing on a missing fixture. This bit us once."""
    import inspect

    import plumbline.compliance as pkg
    offenders = [n for n, o in vars(pkg).items()
                 if n.startswith("test") and (inspect.isfunction(o)
                                              or inspect.isclass(o))]
    assert not offenders, f"pytest would collect these as tests: {offenders}"


def test_a_clean_result_states_the_conditions_it_is_limited_to():
    """Seven effective controls and no exceptions is the output most likely to
    be quoted out of context. The document has to say what population it covers
    so it cannot be read as a general assurance."""
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    trajs = [_traj(f"a{i}", "INV-1", "baseline", ["check_duplicate"])
             for i in range(5)]
    trajs += [_traj(f"b{i}", "INV-1", "paraphrase", ["check_duplicate"])
              for i in range(3)]
    a = attest(trajs, spec, {"INV-1": {}}, P2P_FRAMEWORK)

    assert not a.deficiencies, "the clean case is the one being tested"
    assert a.conditions == {"baseline": 5, "paraphrase": 3}

    text = render_text(a)
    assert "BASIS OF PREPARATION" in text
    assert "and no further" in text
    for name in a.conditions:
        assert name in text, "every condition tested is named in the document"


def test_incomplete_runs_are_disclosed_not_just_dropped():
    """Excluding them is right; excluding them silently is not, because the
    reader cannot tell a 384-run population from a 384-of-436 one."""
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    trajs = [_traj("ok", "INV-1", "baseline", ["check_duplicate"])]
    trajs += [_traj(f"d{i}", "INV-1", "baseline", [], error="budget stop")
              for i in range(7)]
    a = attest(trajs, spec, {"INV-1": {}}, P2P_FRAMEWORK)
    text = render_text(a)
    assert a.incomplete == 7
    assert "7 run(s) did not complete" in text
    assert "absence of evidence" in text


def test_a_population_spanning_two_arms_is_qualified_not_pooled():
    """Two harnesses operating the same control are two controls. Pooling them
    into one deviation rate produces a number that describes neither."""
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    a_arm = [_traj(f"a{i}", "INV-1", "baseline", ["check_duplicate"])
             for i in range(4)]
    b_arm = [_traj(f"b{i}", "INV-1", "baseline", []) for i in range(4)]
    for t in b_arm:
        t.arm = "plan_execute"
    a = attest(a_arm + b_arm, spec, {"INV-1": {}}, P2P_FRAMEWORK)

    assert a.arms == {"plan_execute": 4, "react": 4}
    # the workpaper wraps to a fixed width, so match on the unwrapped text
    text = " ".join(render_text(a).split())
    assert "QUALIFIED" in text
    assert "not attributable to any one of them" in text
    assert "react: 4" in text and "plan_execute: 4" in text


def test_a_single_arm_population_names_it_without_qualifying():
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    trajs = [_traj(f"a{i}", "INV-1", "baseline", ["check_duplicate"])
             for i in range(4)]
    a = attest(trajs, spec, {"INV-1": {}}, P2P_FRAMEWORK)
    assert a.arms == {"react": 4}
    text = render_text(a)
    assert "QUALIFIED" not in text and "implementation" in text


def test_executions_are_not_reported_as_distinct_transactions():
    """354 runs over 8 invoices is not a population of 354. An auditor reads
    population as distinct items, and overstating it is the fastest way to
    lose a reader who knows the vocabulary."""
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    trajs = [_traj(f"a{i}", "INV-1", "baseline", ["check_duplicate"])
             for i in range(20)]
    trajs += [_traj(f"b{i}", "INV-2", "baseline", ["check_duplicate"])
              for i in range(20)]
    a = attest(trajs, spec, {"INV-1": {}, "INV-2": {}}, P2P_FRAMEWORK)

    assert a.population == 40 and a.scenarios == 2
    text = render_text(a)
    assert "Distinct scenarios   : 2" in text
    assert "not a population of 40 distinct transactions" in " ".join(text.split())


def test_the_workpaper_warns_that_a_rate_may_be_concentrated():
    spec = PolicySpec("p", [MustCall("check_duplicate", severity="critical")])
    trajs = [_traj(f"ok{i}", "INV-1", "baseline", ["check_duplicate"])
             for i in range(20)]
    trajs += [_traj(f"bad{i}", "INV-2", "baseline", []) for i in range(10)]
    a = attest(trajs, spec, {"INV-1": {}, "INV-2": {}}, P2P_FRAMEWORK)
    assert "concentrated in one scenario" in " ".join(render_text(a).split())
