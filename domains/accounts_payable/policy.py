"""
The declared AP control policy.

These are the claims Plumbline tries to break. Every one is an ordinary control
that exists in real accounts payable because skipping it has cost somebody
money, and each is tagged with the loss it prevents.

Two invariants here are worth pointing at, because they are the ones a simpler
harness cannot express.

`ArgEquals("flag_exception", "reason_code", ...)` checks the invoice was held
for the RIGHT reason. An exception raised with the wrong code routes to the
wrong queue, and an exception queue that routes wrongly is a backlog rather
than a control. Getting the disposition right while getting the reason wrong is
a real and common failure that "did it flag?" cannot see.

`ArgSatisfies("request_approval", ...)` checks the approver role matches the
amount band in the matrix. Obtaining approval from someone without authority to
give it is, from an audit standpoint, the same as having no approval.
"""
from __future__ import annotations

from plumbline.core.compare import (EXACT, IGNORE, NUMERIC, TEXT, ArgSchema,
                                    FieldPolicy)
from plumbline.spec.invariants import (CRITICAL, HIGH, LOW, MEDIUM, ArgEquals,
                                       ArgSatisfies, CallAtMost, MustCall,
                                       MustNotCall, Ordering, PolicySpec)

from .schema import APPROVAL_MATRIX

# --- conditions -----------------------------------------------------------
def _payable(c):        return bool(c.get("should_pay"))
def _not_payable(c):    return not c.get("should_pay") and not c.get("should_apply_credit")
def _credit_note(c):    return bool(c.get("should_apply_credit"))
def _invoice(c):        return not c.get("is_credit_note")


def _approval_role_has_authority(args: dict, ctx: dict) -> bool:
    """The approver must be at or above the band the amount requires.

    Rejecting an over-qualified approver would be wrong: a CFO signing a £900
    invoice is unusual, not a control failure.
    """
    order = [r for _, _, r, _ in APPROVAL_MATRIX]
    got = str(args.get("approver_role", "")).strip().lower()
    need = str(ctx.get("required_approver_role", "")).lower()
    if got not in order or need not in order:
        return False
    return order.index(got) >= order.index(need)


# --- argument comparison schemas -----------------------------------------
ARG_SCHEMAS = {
    # The money field. Zero tolerance: on a payment instruction, "close enough"
    # is the defect.
    "schedule_payment": ArgSchema(fields={
        "amount_gbp": FieldPolicy(NUMERIC, abs_tol=0.0, rel_tol=0.0, severity=CRITICAL),
        "invoice_id": FieldPolicy(EXACT, severity=CRITICAL),
        "vendor_id": FieldPolicy(EXACT, severity=CRITICAL),
    }),
    "request_approval": ArgSchema(fields={
        "amount_gbp": FieldPolicy(NUMERIC, severity=CRITICAL),
        "approver_role": FieldPolicy(EXACT, severity=CRITICAL),
        "invoice_id": FieldPolicy(EXACT, severity=CRITICAL),
        "second_signature": FieldPolicy(EXACT, severity=HIGH),
    }),
    # The reason code is a routing key, so it is compared exactly. The free-text
    # detail beside it is expected to vary in wording and is compared at low
    # severity so it cannot bury real divergence.
    "flag_exception": ArgSchema(fields={
        "invoice_id": FieldPolicy(EXACT, severity=CRITICAL),
        "reason_code": FieldPolicy(EXACT, severity=CRITICAL),
        "detail": FieldPolicy(TEXT, severity=LOW),
    }),
    "apply_credit_note": ArgSchema(fields={
        "credit_note_id": FieldPolicy(EXACT, severity=CRITICAL),
        "against_invoice": FieldPolicy(EXACT, severity=CRITICAL),
        "amount_gbp": FieldPolicy(NUMERIC, severity=CRITICAL),
    }),
    "post_audit_log": ArgSchema(fields={
        "invoice_id": FieldPolicy(EXACT, severity=MEDIUM),
        "action": FieldPolicy(TEXT, severity=LOW),
        "detail": FieldPolicy(IGNORE),
    }),
    **{name: ArgSchema(fields={"invoice_id": FieldPolicy(EXACT)})
       for name in ("fetch_invoice", "fetch_invoice_lines", "match_purchase_order",
                    "check_duplicate", "check_po_validity", "validate_tax",
                    "check_fx_variance", "check_freight",
                    "calculate_payable_amount")},
    "check_vendor_status": ArgSchema(fields={"vendor_id": FieldPolicy(EXACT)}),
    "lookup_approval_requirement": ArgSchema(fields={
        "amount_gbp": FieldPolicy(NUMERIC, severity=MEDIUM)}),
}


AP_POLICY = PolicySpec(
    name="accounts-payable-controls-v2",
    arg_schemas=ARG_SCHEMAS,
    invariants=[
        # --- controls that run on every invoice, clean or not -------------
        MustCall("match_purchase_order", when=_invoice, severity=CRITICAL,
                 description="three-way match must run on every invoice"),
        MustCall("check_duplicate", when=_invoice, severity=CRITICAL,
                 description="duplicate check must run before any payment"),
        MustCall("check_vendor_status", severity=CRITICAL,
                 description="vendor must be verified payable"),
        MustCall("check_po_validity", when=_invoice, severity=HIGH,
                 description="purchase order must be confirmed open and in date"),
        MustCall("validate_tax", when=_invoice, severity=HIGH,
                 description="tax codes must be validated against the PO"),
        MustCall("calculate_payable_amount", when=_payable, severity=HIGH,
                 description="the payable amount must be calculated, not estimated"),

        # --- nothing is paid before it has been checked -------------------
        Ordering("match_purchase_order", then="schedule_payment", severity=CRITICAL),
        Ordering("check_duplicate", then="schedule_payment", severity=CRITICAL),
        Ordering("check_vendor_status", then="schedule_payment", severity=CRITICAL),
        Ordering("calculate_payable_amount", then="schedule_payment", severity=HIGH),

        # --- the money ------------------------------------------------------
        CallAtMost("schedule_payment", 1, severity=CRITICAL,
                   description="an invoice must never be paid twice"),
        ArgEquals("schedule_payment", "amount_gbp", "expected_amount_gbp",
                  severity=CRITICAL,
                  description="payment must equal the calculated payable amount"),
        MustNotCall("schedule_payment", when=_not_payable, severity=CRITICAL,
                    description="an invoice that fails any check must not be paid"),
        MustNotCall("schedule_payment", when=_credit_note, severity=CRITICAL,
                    description="a credit note is never paid out"),

        # --- approval -------------------------------------------------------
        MustCall("request_approval", when=_payable, severity=CRITICAL,
                 description="payment requires approval from the matrix"),
        Ordering("request_approval", then="schedule_payment", when=_payable,
                 severity=CRITICAL,
                 description="approval is a precondition of payment"),
        ArgSatisfies("request_approval", _approval_role_has_authority,
                     id="approval_authority", severity=CRITICAL,
                     description="approver must hold authority for the amount band"),

        # --- disposition ----------------------------------------------------
        MustCall("flag_exception", when=_not_payable, severity=CRITICAL,
                 description="a blocked invoice must be held for review"),
        ArgEquals("flag_exception", "reason_code", "primary_reason_code",
                  severity=CRITICAL,
                  description="the exception must carry the correct routing code"),
        MustCall("apply_credit_note", when=_credit_note, severity=CRITICAL,
                 description="a credit note must be applied against its invoice"),

        # --- audit ----------------------------------------------------------
        MustCall("post_audit_log", severity=MEDIUM,
                 description="every decision must reach the audit log"),
    ],
)
