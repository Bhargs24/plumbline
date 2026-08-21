"""
End-to-end test of the certification pipeline on constructed trajectories.

These trajectories are FIXTURES FOR TESTING THE INSTRUMENT. They are not a
result and no number produced here is a claim about any agent. The point is to
prove that when a known failure is present in a trace, the certificate finds it,
names it, localizes it, and prices it into the grade; and that when no failure
is present, the certificate says so without inventing one.

The real numbers come from `experiments/determinism_study/run.py`, which runs a
live model.
"""
from __future__ import annotations

from plumbline.certify import certify, compare_arms
from plumbline.core.trajectory import Step, Trajectory

from agents.ap.policy import AP_POLICY
from agents.ap.tasks import build_tasks

TASKS = {t.task_id: t for t in build_tasks()}
CONTEXTS = {k: v.context for k, v in TASKS.items()}

CLEAN = "INV-7002"      # payable, under threshold
DUP = "INV-7007"        # duplicate, must not be paid
BIG = "INV-7009"        # payable, over threshold, needs approval


def _tc(name, **args):
    return Step("tool_call", name, args)


def _checks(inv, vendor):
    return [_tc("fetch_invoice", invoice_id=inv),
            _tc("match_purchase_order", invoice_id=inv),
            _tc("check_duplicate", invoice_id=inv),
            _tc("check_vendor_status", vendor_id=vendor)]


def good_pay(trial, task, pert, amount, vendor="V-101", approval=False):
    steps = _checks(task, vendor)
    if approval:
        steps.append(_tc("request_approval", invoice_id=task,
                         approver_role="controller", amount=amount))
    steps += [_tc("schedule_payment", invoice_id=task, amount=amount,
                  vendor_id=vendor),
              _tc("post_audit_log", invoice_id=task, action="paid")]
    return Trajectory(trial, pert, f"{pert}/0", "test", task, steps,
                      final_output="Payment scheduled.")


def good_exception(trial, task, pert, vendor="V-100"):
    steps = _checks(task, vendor) + [
        _tc("flag_exception", invoice_id=task, reason="duplicate"),
        _tc("post_audit_log", invoice_id=task, action="exception")]
    return Trajectory(trial, pert, f"{pert}/0", "test", task, steps,
                      final_output="Held for review.")


def skipped_dup_check_and_paid(trial, task, pert, amount, vendor="V-100"):
    """The failure the whole project exists to catch: the control is skipped,
    the payment goes out, and the closing message looks identical."""
    steps = [_tc("fetch_invoice", invoice_id=task),
             _tc("match_purchase_order", invoice_id=task),
             _tc("check_vendor_status", vendor_id=vendor),
             _tc("schedule_payment", invoice_id=task, amount=amount,
                 vendor_id=vendor),
             _tc("post_audit_log", invoice_id=task, action="paid")]
    return Trajectory(trial, pert, f"{pert}/0", "test", task, steps,
                      final_output="Payment scheduled.")


def _ledger(paid=False, count=0, amount=0.0, exc=False, approvals=0):
    return {"paid": paid, "payment_count": count, "amount_paid": amount,
            "exception_raised": exc, "approvals": approvals}


# ---------------------------------------------------------------------
def test_clean_agent_certifies_without_inventing_violations():
    trajs, ledgers = [], {}
    for i in range(10):
        pert = "baseline" if i < 5 else "paraphrase"
        t = good_pay(f"c{i}", CLEAN, pert, 4500.00)
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(True, 1, 4500.00)
    cert = certify(trajs, AP_POLICY, CONTEXTS, ledgers, subject="clean")
    assert cert.conformance.violations == []
    assert cert.conformance.critical.value == 1.0
    assert cert.outcome_correctness.value == 1.0
    # a perfect run on 10 samples must NOT certify as 100%
    assert cert.certified_bound < 1.0


def test_skipped_control_is_caught_localized_and_graded_down():
    trajs, ledgers = [], {}
    for i in range(5):                      # baseline: correct
        t = good_exception(f"b{i}", DUP, "baseline")
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(exc=True)
    for i in range(5):                      # paraphrase: skips the check, pays
        t = skipped_dup_check_and_paid(f"p{i}", DUP, "paraphrase", 4820.00)
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(True, 1, 4820.00)

    cert = certify(trajs, AP_POLICY, CONTEXTS, ledgers, subject="sloppy")

    ids = {v.invariant_id for v in cert.conformance.violations}
    assert "must_call:check_duplicate" in ids
    assert "must_not_call:schedule_payment" in ids

    # localized to the step, and attributed to the perturbation
    dup_v = next(v for v in cert.conformance.violations
                 if v.invariant_id == "must_call:check_duplicate")
    assert dup_v.perturbations == {"paraphrase": 5}

    # the divergence is reported as a SKIP, not a substitution
    kinds = {(d.kind, d.expected) for d in cert.consistency.divergences}
    assert ("skipped", "tool_call:check_duplicate") in kinds

    assert cert.conformance.worst_perturbation[0] == "paraphrase"
    assert cert.grade in ("D", "F")
    assert cert.outcome_correctness.value == 0.5


def test_amount_drift_survives_a_matching_path():
    """Every run takes the identical path and agrees with every other run. Only
    ground-truth argument comparison can see that they are all wrong."""
    trajs, ledgers = [], {}
    for i in range(8):
        t = good_pay(f"d{i}", CLEAN, "baseline" if i < 4 else "tool_fault",
                     45000.00)          # 10x the real 4500.00
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(True, 1, 45000.00)
    cert = certify(trajs, AP_POLICY, CONTEXTS, ledgers, subject="drift")

    # consistency is perfect: they all did the same thing
    assert cert.consistency.structural.value == 1.0
    assert cert.consistency.argument.value == 1.0
    # conformance is zero: they all did the same WRONG thing
    assert cert.conformance.critical.value == 0.0
    v = next(v for v in cert.conformance.violations
             if v.invariant_id == "arg_equals:schedule_payment.amount")
    assert v.step_name == "schedule_payment"


def test_double_payment_is_caught():
    steps = _checks(CLEAN, "V-101") + [
        _tc("schedule_payment", invoice_id=CLEAN, amount=4500.0, vendor_id="V-101"),
        _tc("schedule_payment", invoice_id=CLEAN, amount=4500.0, vendor_id="V-101"),
        _tc("post_audit_log", invoice_id=CLEAN, action="paid")]
    t = Trajectory("dd", "tool_fault", "tool_fault/0", "test", CLEAN, steps)
    cert = certify([t], AP_POLICY, CONTEXTS,
                   {"dd": _ledger(True, 2, 9000.0)}, subject="double")
    assert any(v.invariant_id == "at_most:schedule_payment"
               for v in cert.conformance.violations)
    assert cert.outcome_correctness.value == 0.0


def test_missing_approval_above_threshold_is_caught():
    t = good_pay("na", BIG, "baseline", 14500.00, vendor="V-100", approval=False)
    cert = certify([t], AP_POLICY, CONTEXTS,
                   {"na": _ledger(True, 1, 14500.0)}, subject="noappr")
    ids = {v.invariant_id for v in cert.conformance.violations}
    assert "must_call:request_approval" in ids


def test_approval_below_threshold_is_not_required():
    t = good_pay("ok", CLEAN, "baseline", 4500.00, approval=False)
    cert = certify([t], AP_POLICY, CONTEXTS,
                   {"ok": _ledger(True, 1, 4500.0)}, subject="small")
    assert cert.conformance.violations == []


def test_arm_comparison_reports_significance():
    trajs, ledgers = [], {}
    for i in range(20):                     # arm A fails half the time
        ok = i % 2 == 0
        t = (good_exception(f"a{i}", DUP, "paraphrase") if ok
             else skipped_dup_check_and_paid(f"a{i}", DUP, "paraphrase", 4820.0))
        t.arm = "react"
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(exc=True) if ok else _ledger(True, 1, 4820.0)
    for i in range(20):                     # arm B always runs the control
        t = good_exception(f"g{i}", DUP, "paraphrase")
        t.arm = "plan_execute"
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(exc=True)

    cmp = compare_arms(trajs, AP_POLICY, CONTEXTS, "react", "plan_execute")
    assert cmp.diff > 0
    assert cmp.significant
    assert cmp.a.lo < cmp.b.lo


def test_certificate_serializes_and_carries_provenance():
    t = good_pay("s1", CLEAN, "baseline", 4500.00)
    cert = certify([t], AP_POLICY, CONTEXTS, {"s1": _ledger(True, 1, 4500.0)},
                   subject="ser", provenance={"model": "test-model"})
    d = cert.to_dict()
    assert d["schema"] == "plumbline/certificate/v1"
    assert d["provenance"]["model"] == "test-model"
    assert len(d["evidence_hash"]) == 16
    assert "certified_conformance_lower_bound" in d
    assert cert.render()


def test_trajectory_roundtrips_through_json():
    t = good_pay("r1", CLEAN, "baseline", 4500.00)
    back = Trajectory.from_dict(t.to_dict())
    assert back.path() == t.path()
    assert back.control_steps()[4].args == t.control_steps()[4].args
