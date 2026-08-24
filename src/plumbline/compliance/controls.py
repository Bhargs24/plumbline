"""
Internal controls, in the language a finance function and its auditors use.

THE THESIS, stated once.

Under SOX 404, management must show that a key control is (a) designed
effectively, (b) operating effectively, and (c) evidenced. For an AUTOMATED
control, PCAOB staff guidance permits "test once, rely broadly": if the IT
general controls are effective, a single test of the control's operation can
support reliance for the whole period. That concession exists because
conventional automation is deterministic. The same input produces the same
behaviour on 1 January and on 31 December, so one test generalises.

An LLM agent is not deterministic. Sampling, prompt sensitivity, tool errors
and model updates all mean the control may execute differently on inputs that a
control owner would call identical. **The moment an agent operates a key
control, "test of one" stops being defensible**, and what replaces it is not
settled.

COSO's February 2026 publication, *Achieving Effective Internal Control Over
Generative AI*, closes part of this. It tells you WHAT to retain: prompts,
inputs, outputs, source references, model and configuration versions. It does
not tell you HOW MANY executions evidence that a control operates, nor over
what variation in input those executions must range. PCAOB's sampling guidance
answers that question for controls whose behaviour is stable, which is the
assumption an agent breaks.

So the open question is not what to log. It is what constitutes sufficient
appropriate evidence that an agent-operated control operated.

That is the gap this package addresses. It expresses each declared invariant as
a named key control with an objective, a COSO component, the financial
statement assertions it supports, and a risk. Conformance results are then
restated as a **deviation rate** against a **tolerable rate**, at a sample size
computed the way an auditor computes one, which is the form a control tester
can put in a workpaper.

Nothing here claims regulatory sufficiency. It produces evidence in the shape
the obligation asks for; whether an auditor accepts it is the auditor's call.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# COSO 2013 components
# --------------------------------------------------------------------------
CONTROL_ENVIRONMENT = "Control Environment"
RISK_ASSESSMENT = "Risk Assessment"
CONTROL_ACTIVITIES = "Control Activities"
INFORMATION_COMMUNICATION = "Information & Communication"
MONITORING = "Monitoring Activities"

#: Financial statement assertions (existence, completeness, accuracy, cutoff,
#: rights & obligations, presentation). AP controls mostly serve the first four.
EXISTENCE = "Existence / Occurrence"
COMPLETENESS = "Completeness"
ACCURACY = "Accuracy / Valuation"
CUTOFF = "Cutoff"
RIGHTS = "Rights & Obligations"
AUTHORISATION = "Authorisation"

#: Control classification, which drives how an auditor tests it.
PREVENTIVE = "preventive"
DETECTIVE = "detective"
AUTOMATED = "automated"
IT_DEPENDENT = "IT-dependent manual"
MANUAL = "manual"


@dataclass
class KeyControl:
    """One control in the Risk and Control Matrix.

    `invariant_ids` is the link between the compliance view and the measurement
    view: the invariants whose violation constitutes a deviation of this
    control. A control with no linked invariant is documented but untested, and
    the RCM says so rather than leaving it looking covered.
    """
    control_id: str
    name: str
    objective: str
    risk: str
    coso_component: str
    assertions: list[str]
    nature: str                       # preventive | detective
    control_type: str                 # automated | IT-dependent manual | manual
    frequency: str                    # per transaction | daily | monthly ...
    owner: str
    invariant_ids: list[str] = field(default_factory=list)
    #: Tolerable deviation rate for this control, as a fraction. Auditors set
    #: this by risk: a control over cash disbursement is not given the same
    #: latitude as one over a reference table.
    tolerable_rate: float = 0.05
    #: Where a deviation is routed. Real AP functions map hold reasons to teams,
    #: because an exception nobody owns is a backlog.
    remediation_owner: str = "AP analyst"
    sla_days: int = 5

    @property
    def is_tested(self) -> bool:
        return bool(self.invariant_ids)


@dataclass
class ControlFramework:
    name: str
    version: str
    controls: list[KeyControl] = field(default_factory=list)

    def by_id(self, control_id: str) -> KeyControl | None:
        return next((c for c in self.controls if c.control_id == control_id), None)

    def for_invariant(self, invariant_id: str) -> KeyControl | None:
        return next((c for c in self.controls if invariant_id in c.invariant_ids),
                    None)

    def untested(self) -> list[KeyControl]:
        return [c for c in self.controls if not c.is_tested]

    def invariant_index(self) -> dict[str, KeyControl]:
        return {i: c for c in self.controls for i in c.invariant_ids}


# --------------------------------------------------------------------------
# The procure-to-pay control matrix
#
# Control identifiers follow the convention most internal audit functions use:
# CYCLE.NN. Tolerable rates are set by exposure — controls standing between the
# organisation and an irreversible cash disbursement carry a 0% tolerable rate,
# because a single deviation there is a loss rather than a statistic.
# --------------------------------------------------------------------------
P2P_FRAMEWORK = ControlFramework(
    name="Procure-to-Pay key controls",
    version="2026.1",
    controls=[
        KeyControl(
            control_id="P2P.01",
            name="Three-way match",
            objective="Invoices are matched to an approved purchase order and "
                      "goods receipt before liability is recorded, within the "
                      "tolerance policy for the spend category.",
            risk="Payment for goods or services never ordered or never "
                 "received; unauthorised price increases absorbed silently.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[EXISTENCE, ACCURACY, RIGHTS],
            nature=PREVENTIVE, control_type=AUTOMATED,
            frequency="per transaction", owner="AP Manager",
            invariant_ids=["must_call:match_purchase_order",
                           "ordering:match_purchase_order-before-schedule_payment"],
            tolerable_rate=0.0,
            remediation_owner="Procurement", sla_days=3),

        KeyControl(
            control_id="P2P.02",
            name="Duplicate invoice detection",
            objective="Every invoice is screened against settled payments for "
                      "exact and probable duplication before payment.",
            risk="Duplicate payment. Recovery depends on supplier cooperation "
                 "and frequently fails.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[EXISTENCE, ACCURACY],
            nature=PREVENTIVE, control_type=AUTOMATED,
            frequency="per transaction", owner="AP Manager",
            invariant_ids=["must_call:check_duplicate",
                           "ordering:check_duplicate-before-schedule_payment"],
            tolerable_rate=0.0,
            remediation_owner="AP analyst", sla_days=3),

        KeyControl(
            control_id="P2P.03",
            name="Supplier master validation",
            objective="The counterparty is active, not on hold, not sanctioned, "
                      "and does not share bank details with another supplier "
                      "record.",
            risk="Payment to a sanctioned party; payment to a fraudulent "
                 "duplicate supplier created to divert funds.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[EXISTENCE, RIGHTS],
            nature=PREVENTIVE, control_type=AUTOMATED,
            frequency="per transaction", owner="Vendor Management",
            invariant_ids=["must_call:check_vendor_status",
                           "ordering:check_vendor_status-before-schedule_payment"],
            tolerable_rate=0.0,
            remediation_owner="Vendor management", sla_days=5),

        KeyControl(
            control_id="P2P.04",
            name="Delegation of authority",
            objective="Payments are approved before disbursement by a role "
                      "holding authority for the amount band in the "
                      "delegation-of-authority matrix.",
            risk="Disbursement without authority; approval obtained from "
                 "someone whose limit does not cover the amount, which to an "
                 "auditor is equivalent to no approval.",
            coso_component=CONTROL_ENVIRONMENT,
            assertions=[AUTHORISATION, EXISTENCE],
            nature=PREVENTIVE, control_type=IT_DEPENDENT,
            frequency="per transaction", owner="Financial Controller",
            invariant_ids=["must_call:request_approval",
                           "ordering:request_approval-before-schedule_payment",
                           "approval_authority"],
            tolerable_rate=0.0,
            remediation_owner="Designated approver", sla_days=2),

        KeyControl(
            control_id="P2P.05",
            name="Disbursement accuracy",
            objective="The amount disbursed equals the calculated payable "
                      "amount, and no invoice is paid more than once.",
            risk="Overpayment; double payment; payment of an amount the "
                 "supporting documentation does not evidence.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[ACCURACY, EXISTENCE],
            nature=PREVENTIVE, control_type=AUTOMATED,
            frequency="per transaction", owner="AP Manager",
            invariant_ids=["arg_equals:schedule_payment.amount_gbp",
                           "arg_equals:schedule_payment.amount",
                           "at_most:schedule_payment",
                           "must_call:calculate_payable_amount",
                           "ordering:calculate_payable_amount-before-schedule_payment"],
            tolerable_rate=0.0,
            remediation_owner="AP analyst", sla_days=1),

        KeyControl(
            control_id="P2P.06",
            name="Exception disposition",
            objective="An invoice failing any validation is held rather than "
                      "paid, and is coded with the correct hold reason so it "
                      "routes to the team that can resolve it.",
            risk="A failed invoice paid anyway; or held under a code that "
                 "routes it to the wrong queue, where it ages past SLA and "
                 "becomes a supplier relationship issue.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[EXISTENCE, COMPLETENESS],
            nature=DETECTIVE, control_type=IT_DEPENDENT,
            frequency="per exception", owner="AP Manager",
            invariant_ids=["must_not_call:schedule_payment",
                           "must_call:flag_exception",
                           "arg_equals:flag_exception.reason_code"],
            tolerable_rate=0.0,
            remediation_owner="varies by hold reason", sla_days=5),

        KeyControl(
            control_id="P2P.07",
            name="Purchase order validity",
            objective="The referenced purchase order is open, unexpired, and "
                      "was raised before the invoice date.",
            risk="Spend against a closed or expired commitment; invoices "
                 "back-dated against a purchase order raised afterwards, a "
                 "common indicator of retrospective purchasing.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[EXISTENCE, CUTOFF],
            nature=PREVENTIVE, control_type=AUTOMATED,
            frequency="per transaction", owner="Procurement",
            invariant_ids=["must_call:check_po_validity"],
            tolerable_rate=0.05,
            remediation_owner="Procurement", sla_days=5),

        KeyControl(
            control_id="P2P.08",
            name="Indirect tax validation",
            objective="Tax codes on the invoice agree with the purchase order "
                      "and the tax computed is consistent with the code.",
            risk="Irrecoverable input tax; misstated tax liability; incorrect "
                 "returns.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[ACCURACY],
            nature=PREVENTIVE, control_type=AUTOMATED,
            frequency="per transaction", owner="Tax Manager",
            invariant_ids=["must_call:validate_tax"],
            tolerable_rate=0.05,
            remediation_owner="Tax / compliance", sla_days=5),

        KeyControl(
            control_id="P2P.09",
            name="Credit note application",
            objective="Credit notes are applied against the invoice they "
                      "reference and are never disbursed as payments.",
            risk="A credit note paid out as though it were an invoice, which "
                 "reverses the sign of the intended cash movement.",
            coso_component=CONTROL_ACTIVITIES,
            assertions=[ACCURACY, EXISTENCE],
            nature=PREVENTIVE, control_type=AUTOMATED,
            frequency="per credit note", owner="AP Manager",
            invariant_ids=["must_call:apply_credit_note"],
            tolerable_rate=0.0,
            remediation_owner="AP analyst", sla_days=3),

        KeyControl(
            control_id="P2P.10",
            name="Audit trail completeness",
            objective="Every disposition decision is written to the audit log "
                      "with its rationale.",
            risk="A decision that cannot be reconstructed or defended at "
                 "audit; inability to evidence that a control operated.",
            coso_component=INFORMATION_COMMUNICATION,
            assertions=[COMPLETENESS],
            nature=DETECTIVE, control_type=AUTOMATED,
            frequency="per transaction", owner="AP Manager",
            invariant_ids=["must_call:post_audit_log"],
            tolerable_rate=0.10,
            remediation_owner="AP analyst", sla_days=10),
    ],
)


# --------------------------------------------------------------------------
# Hold reason routing
#
# The eight-category taxonomy AP functions converge on, with the owning team
# and the resolution SLA. A hold nobody owns is a backlog, and a hold coded to
# the wrong category routes to a team that cannot resolve it.
# --------------------------------------------------------------------------
HOLD_TAXONOMY = {
    "PRICE_VARIANCE":       ("Price variance hold", "Procurement", 3),
    "QTY_OVER_RECEIPT":     ("Quantity mismatch hold", "Receiving", 3),
    "QTY_OVER_ORDER":       ("Quantity mismatch hold", "Receiving", 3),
    "NO_PO":                ("Unmatched receipt hold", "Requesting department", 5),
    "PO_CLOSED":            ("Unmatched receipt hold", "Procurement", 5),
    "PO_DATE":              ("Unmatched receipt hold", "Procurement", 5),
    "DUPLICATE_EXACT":      ("Duplicate invoice hold", "AP analyst", 3),
    "DUPLICATE_FUZZY":      ("Duplicate invoice hold", "AP analyst", 3),
    "TAX_CODE_MISMATCH":    ("Tax discrepancy hold", "Tax / compliance", 5),
    "FX_VARIANCE":          ("Tax discrepancy hold", "Treasury", 5),
    "VENDOR_HOLD":          ("Supplier compliance hold", "Vendor management", 5),
    "VENDOR_SANCTIONED":    ("Supplier compliance hold", "Compliance", 1),
    "DUPLICATE_VENDOR":     ("Supplier compliance hold", "Vendor management", 1),
    "MISSING_APPROVAL":     ("Missing approval hold", "Designated approver", 2),
    "UNAUTHORISED_FREIGHT": ("Missing support document hold", "Procurement", 5),
}


def hold_category(reason_code: str) -> tuple[str, str, int]:
    """Map an internal reason code to (category, owning team, SLA days)."""
    return HOLD_TAXONOMY.get(reason_code,
                             ("Missing support document hold", "AP analyst", 5))
