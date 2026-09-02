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

from plumbline.certify import certify as _certify
from plumbline.certify import compare_arms
from plumbline.core.trajectory import Step, Trajectory
from plumbline.domains.ap.policy import AP_POLICY
from plumbline.domains.ap.tasks import build_tasks, expected_outcome


def _outcome_matches(ctx, ledger):
    if not ctx or ledger is None:
        return False
    w = expected_outcome(ctx)
    return (bool(ledger.get("paid")) == w["paid"]
            and int(ledger.get("payment_count", 0)) == w["payment_count"]
            and abs(float(ledger.get("amount_paid", 0)) - w["amount_paid"]) < 0.005
            and bool(ledger.get("exception_raised")) == w["exception_raised"])


def certify(*a, **kw):
    """Outcome scoring is supplied by the domain; the analysis core no longer
    imports one. Production wires this the same way."""
    kw.setdefault("outcome_matches", _outcome_matches)
    return _certify(*a, **kw)

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


def test_free_text_wording_variation_is_not_argument_drift():
    """An LLM rewords a free-text reason every run. If that counted as drift,
    argument consistency would read near zero on every study and real drift
    would be buried. Declared `low` severity is the mechanism that prevents it."""
    trajs, ledgers = [], {}
    reasons = ["duplicate of INV-7001", "This appears to be a duplicate.",
               "DUPLICATE: matches an already-paid invoice", "dup, already paid"]
    for i, reason in enumerate(reasons):
        steps = _checks(DUP, "V-100") + [
            _tc("flag_exception", invoice_id=DUP, reason=reason),
            _tc("post_audit_log", invoice_id=DUP, action="exception")]
        t = Trajectory(f"w{i}", "baseline", "baseline/0", "test", DUP, steps,
                       final_output="Held.")
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(exc=True)
    cert = certify(trajs, AP_POLICY, CONTEXTS, ledgers, subject="wording")
    assert cert.consistency.argument.value == 1.0, "reason wording must not count"
    assert not [d for d in cert.consistency.divergences if d.kind == "arg"]


def test_material_argument_drift_is_still_caught_alongside_free_text():
    """Filtering low-severity noise must not also filter the money field."""
    trajs, ledgers = [], {}
    for i in range(4):
        amount = 4500.00 if i < 3 else 45000.00
        steps = _checks(CLEAN, "V-101") + [
            _tc("schedule_payment", invoice_id=CLEAN, amount=amount, vendor_id="V-101"),
            _tc("post_audit_log", invoice_id=CLEAN, action=f"paid run {i}")]
        t = Trajectory(f"m{i}", "baseline", "baseline/0", "test", CLEAN, steps,
                       final_output="Paid.")
        trajs.append(t)
        ledgers[t.trial_id] = _ledger(True, 1, amount)
    cert = certify(trajs, AP_POLICY, CONTEXTS, ledgers, subject="mixed")
    args = [d for d in cert.consistency.divergences if d.kind == "arg"]
    assert len(args) == 1 and "amount" in args[0].expected


# ------------------------------------------------------- parity / equivalence
def _pair(trial, arm, task, pert, steps, ledgers, ledger, variant="v0"):
    t = Trajectory(trial, pert, variant, arm, task, steps, final_output="done.")
    ledgers[t.trial_id] = ledger
    return t


def test_identical_systems_prove_equivalent():
    """80 clean runs per perturbation clears the 0.95 bound. Fewer does not,
    and that is the point of using a bound rather than a point estimate."""
    from plumbline.certify import prove_parity
    trajs, ledgers = [], {}
    for i in range(160):
        pert = "baseline" if i < 80 else "paraphrase"
        steps = _checks(CLEAN, "V-101") + [
            _tc("schedule_payment", invoice_id=CLEAN, amount=4500.0, vendor_id="V-101")]
        trajs.append(_pair(f"old{i}", "incumbent", CLEAN, pert, steps, ledgers,
                           _ledger(True, 1, 4500.0), f"{pert}/{i}"))
        trajs.append(_pair(f"new{i}", "replacement", CLEAN, pert, list(steps), ledgers,
                           _ledger(True, 1, 4500.0), f"{pert}/{i}"))
    r = prove_parity(trajs, incumbent="incumbent", replacement="replacement",
                     ledger_states=ledgers, spec=AP_POLICY)
    assert r.outcome.value == 1.0 and r.path.value == 1.0
    assert r.divergences == []
    assert "Safe to retire" in r.verdict()


def test_small_clean_sample_reports_insufficient_evidence_not_divergence():
    """A perfect record on twelve runs is neither proof of equivalence nor
    evidence of divergence. Saying 'materially different' would send someone to
    debug a system that is fine."""
    from plumbline.certify import prove_parity
    trajs, ledgers = [], {}
    for i in range(6):
        pert = "baseline" if i < 3 else "paraphrase"
        steps = _checks(CLEAN, "V-101") + [
            _tc("schedule_payment", invoice_id=CLEAN, amount=4500.0, vendor_id="V-101")]
        trajs.append(_pair(f"so{i}", "incumbent", CLEAN, pert, steps, ledgers,
                           _ledger(True, 1, 4500.0), f"{pert}/{i}"))
        trajs.append(_pair(f"sn{i}", "replacement", CLEAN, pert, list(steps), ledgers,
                           _ledger(True, 1, 4500.0), f"{pert}/{i}"))
    r = prove_parity(trajs, incumbent="incumbent", replacement="replacement",
                     ledger_states=ledgers, spec=AP_POLICY)
    assert r.divergences_observed == 0
    v = r.verdict()
    assert "too small to certify" in v
    assert "Materially different" not in v and "Safe to retire" not in v
    assert r.runs_needed_for(0.95) > 70


def test_replacement_that_drops_a_control_is_caught_even_when_outcome_matches():
    """The migration failure a 30-day parallel run misses: same result, one
    control quietly not run."""
    from plumbline.certify import prove_parity
    trajs, ledgers = [], {}
    for i in range(6):
        pert = "baseline" if i < 3 else "tool_fault"
        old = _checks(DUP, "V-100") + [_tc("flag_exception", invoice_id=DUP, reason="dup")]
        new = [_tc("fetch_invoice", invoice_id=DUP),
               _tc("check_duplicate", invoice_id=DUP),          # match_po dropped
               _tc("check_vendor_status", vendor_id="V-100"),
               _tc("flag_exception", invoice_id=DUP, reason="dup")]
        trajs.append(_pair(f"o{i}", "incumbent", DUP, pert, old, ledgers,
                           _ledger(exc=True), f"{pert}/{i}"))
        trajs.append(_pair(f"n{i}", "replacement", DUP, pert, new, ledgers,
                           _ledger(exc=True), f"{pert}/{i}"))
    r = prove_parity(trajs, incumbent="incumbent", replacement="replacement",
                     ledger_states=ledgers, spec=AP_POLICY)
    assert r.outcome.value == 1.0, "end states match, which is why this is missed today"
    assert r.path.value == 0.0
    skipped = [d for d in r.divergences if d.kind == "skipped"]
    assert skipped and "match_purchase_order" in skipped[0].expected


def test_divergence_only_under_perturbation_is_localized_to_it():
    from plumbline.certify import prove_parity
    trajs, ledgers = [], {}
    for i in range(4):
        for pert in ("baseline", "paraphrase"):
            old = _checks(CLEAN, "V-101") + [
                _tc("schedule_payment", invoice_id=CLEAN, amount=4500.0, vendor_id="V-101")]
            amount = 45000.0 if pert == "paraphrase" else 4500.0
            new = _checks(CLEAN, "V-101") + [
                _tc("schedule_payment", invoice_id=CLEAN, amount=amount, vendor_id="V-101")]
            trajs.append(_pair(f"o{pert}{i}", "incumbent", CLEAN, pert, old, ledgers,
                               _ledger(True, 1, 4500.0), f"{pert}/{i}"))
            trajs.append(_pair(f"n{pert}{i}", "replacement", CLEAN, pert, new, ledgers,
                               _ledger(True, 1, amount), f"{pert}/{i}"))
    r = prove_parity(trajs, incumbent="incumbent", replacement="replacement",
                     ledger_states=ledgers, spec=AP_POLICY)
    assert r.worst_perturbation[0] == "paraphrase"
    assert r.by_perturbation["baseline"].value == 1.0
    assert r.by_perturbation["paraphrase"].value == 0.0
    assert "Not safe to retire" in r.verdict() or "Materially different" in r.verdict()
    args = [d for d in r.divergences if d.kind == "arg"]
    assert args and "amount" in args[0].expected


def test_parity_needs_both_sides_and_says_so():
    import pytest as _pytest

    from plumbline.certify import prove_parity
    t = good_pay("only", CLEAN, "baseline", 4500.00)
    t.arm = "incumbent"
    with _pytest.raises(ValueError, match="both"):
        prove_parity([t], incumbent="incumbent", replacement="missing",
                     ledger_states={"only": _ledger(True, 1, 4500.0)})


def test_unpaired_runs_are_excluded_not_counted_as_divergences():
    """Credit exhaustion killed 82 runs in the first study (the API refused every call once the account balance hit zero) and they were scored as
    total behavioral divergence. A run with no counterpart is missing data, not
    evidence."""
    from plumbline.certify import prove_parity
    trajs, ledgers = [], {}
    steps = _checks(CLEAN, "V-101") + [
        _tc("schedule_payment", invoice_id=CLEAN, amount=4500.0, vendor_id="V-101")]
    for i in range(4):
        trajs.append(_pair(f"i{i}", "incumbent", CLEAN, "baseline", steps, ledgers,
                           _ledger(True, 1, 4500.0), f"baseline/{i}"))
    for i in range(2):      # replacement only ran half of them
        trajs.append(_pair(f"r{i}", "replacement", CLEAN, "baseline", list(steps),
                           ledgers, _ledger(True, 1, 4500.0), f"baseline/{i}"))
    trajs.append(_pair("orphan", "replacement", CLEAN, "baseline", list(steps),
                       ledgers, _ledger(True, 1, 4500.0), "baseline/99"))
    r = prove_parity(trajs, incumbent="incumbent", replacement="replacement",
                     ledger_states=ledgers, spec=AP_POLICY)
    assert r.n_pairs == 2 and r.unpaired == 1
    assert r.divergences == []


# ------------------------------------------------------------------ report
def test_report_uses_only_tokens_the_stylesheet_defines():
    """The chart once emitted var(--line) and var(--text-muted) after the
    palette was renamed. Undefined custom properties fail silently: gridlines
    and tick labels simply stop having a color."""
    import re

    from plumbline.report.html import CSS, dot_plot
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", CSS))
    svg = dot_plot([("baseline", [(0, 1.0, .94, 1.0, 64), (1, .98, .91, .99, 56)]),
                    ("tool fault", [(0, .81, .70, .89, 64), (1, .98, .91, .99, 56)])],
                   label_rows={"tool fault"})
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", svg))
    assert used, "the chart should reference tokens"
    assert used <= defined, f"undefined in stylesheet: {sorted(used - defined)}"


def test_fragment_carries_its_own_title_and_fonts():
    """Without these the artifact inherits its filename as a name and the
    display faces silently fall back."""
    from plumbline.report.build import PAGE_TITLE
    from plumbline.report.html import FONTS
    assert "fonts.googleapis.com" in FONTS
    assert PAGE_TITLE and "—" not in PAGE_TITLE


def test_every_color_token_is_defined_in_all_three_theme_scopes():
    """A token defined only inside the dark blocks renders unstyled in the
    default un-stamped state, which is the classic unreadable-artifact bug."""
    import re

    from plumbline.report.html import CSS

    def block_at(marker: str) -> str:
        """Slice a CSS block by counting braces, since these blocks nest and a
        whitespace-sensitive regex silently matches nothing."""
        start = CSS.index(marker)
        depth, i = 0, CSS.index("{", start)
        for j in range(i, len(CSS)):
            if CSS[j] == "{":
                depth += 1
            elif CSS[j] == "}":
                depth -= 1
                if depth == 0:
                    return CSS[start:j + 1]
        raise AssertionError(f"unbalanced braces after {marker!r}")

    def tokens(s):
        return set(re.findall(r"(--[a-z0-9-]+)\s*:", s))
    base = tokens(CSS.split("@media")[0])
    assert base, ":root must define the complete light palette"
    for marker in ("@media (prefers-color-scheme: dark)",
                   ':root[data-theme="dark"]'):
        toks = tokens(block_at(marker))
        assert toks, f"{marker} defines no tokens"
        assert toks <= base, (
            f"{marker} defines tokens absent from :root: {sorted(toks - base)}. "
            f"Those render unstyled in the default un-stamped theme.")
