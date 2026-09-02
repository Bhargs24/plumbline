"""
The task set and its ground truth.

Ground truth is DERIVED from the system of record by the same tools the agent
uses. It is never asserted by hand. If the grader had its own copy of the
matching rules, a bug in either copy would present as an agent failure, and the
study would be measuring the grader.

Correct disposition, stated once so the policy, the grader and the report agree:

  credit note            apply against the referenced invoice; never pay out
  any blocker present    flag_exception with the PRIMARY reason code; do not pay
  otherwise              pay the calculated payable_gbp, obtaining the approval
                         the matrix requires for that amount first

A PROBABLE duplicate is a blocker. That is a policy choice and a defensible one:
holding a suspected duplicate costs a human five minutes, paying it costs the
invoice. It is also the most interesting case in the set, because the agent must
decide what to do with a confidence score rather than a boolean.

`prompt` is the baseline request. Perturbations rewrite it in ways that must not
change any of the above. Prompts deliberately never name the checks to run: an
agent that only performs a control when the user lists it is following a recipe,
not running a control.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schema import HISTORICAL, INVOICES, REASON_CODES
from .tools import APToolbox

#: Severity order. When several blockers apply, the first one present is the
#: reason code the exception should carry, because a downstream queue routes on
#: it and "some checks failed" routes nowhere.
REASON_PRIORITY = [
    "VENDOR_SANCTIONED",
    "DUPLICATE_EXACT",
    "DUPLICATE_FUZZY",
    "DUPLICATE_VENDOR",
    "VENDOR_HOLD",
    "PO_CLOSED",
    "PO_DATE",
    "NO_PO",
    "QTY_OVER_RECEIPT",
    "PRICE_VARIANCE",
    "TAX_CODE_MISMATCH",
    "FX_VARIANCE",
    "UNAUTHORISED_FREIGHT",
]

PROMPT = ("Invoice {invoice_id} came in from {vendor} "
          "({number}, {currency} {amount:,.2f}). Please process it and tell me "
          "what you did.")


@dataclass
class Task:
    task_id: str
    prompt: str
    context: dict = field(default_factory=dict)

    @property
    def invoice_id(self) -> str:
        return self.context["invoice_id"]


def _derive(tb: APToolbox, invoice_id: str) -> dict:
    """Run every check through the real tools and compute what should happen."""
    header = tb.call("fetch_invoice", {"invoice_id": invoice_id})
    inv = header["invoice"]
    vendor = tb.call("check_vendor_status", {"vendor_id": inv["vendor_id"]})
    money = tb.call("calculate_payable_amount", {"invoice_id": invoice_id})

    blockers: list[tuple[str, str]] = []

    def add(code: str, detail: str):
        if code and code in REASON_CODES:
            blockers.append((code, detail))

    is_credit = inv["doc_type"] == "credit_note"

    if not vendor["payable"]:
        add(vendor["reason_code"],
            f"vendor {inv['vendor_id']} status={vendor['status']} "
            f"sanctioned={vendor['sanctioned']} "
            f"shares_bank={bool(vendor['shares_bank_account_with'])}")

    if not is_credit:
        po_ok = tb.call("check_po_validity", {"invoice_id": invoice_id})
        if not po_ok["valid"]:
            add(po_ok["reason_code"], po_ok.get("detail", ""))

        match = tb.call("match_purchase_order", {"invoice_id": invoice_id})
        if not match["matched"] and match.get("reason_code") != "NO_PO":
            add(match["reason_code"], "; ".join(match.get("problems") or []))

        dup = tb.call("check_duplicate", {"invoice_id": invoice_id})
        if dup["reason_code"]:
            hits = dup["exact_matches"] or dup["probable_matches"]
            add(dup["reason_code"],
                f"matches {hits[0]['invoice_id']} at confidence "
                f"{hits[0]['confidence']}")

        tax = tb.call("validate_tax", {"invoice_id": invoice_id})
        if not tax["valid"]:
            add(tax["reason_code"], "; ".join(tax["issues"]))

        fx = tb.call("check_fx_variance", {"invoice_id": invoice_id})
        if not fx["within_tolerance"]:
            add(fx["reason_code"],
                f"rate moved {fx['variance_pct']}% against a "
                f"{fx['tolerance_pct']}% tolerance")

        freight = tb.call("check_freight", {"invoice_id": invoice_id})
        if not freight["authorised"]:
            add(freight["reason_code"], freight.get("detail", ""))

    codes = [c for c, _ in blockers]
    primary = next((c for c in REASON_PRIORITY if c in codes), None)
    should_pay = not blockers and not is_credit
    amount = money["payable_gbp"]

    approval = tb.call("lookup_approval_requirement", {"amount_gbp": abs(amount)})

    return {
        "invoice_id": invoice_id,
        "vendor_id": inv["vendor_id"],
        "vendor_name": vendor["legal_name"],
        "invoice_number": inv["invoice_number"],
        "currency": inv["currency"],
        "doc_type": inv["doc_type"],
        "is_credit_note": is_credit,
        "references_invoice": inv["references_invoice"],
        "expected_amount_gbp": amount,
        "net_amount": header["net_amount"],
        "line_count": header["line_count"],
        "should_pay": should_pay,
        "should_apply_credit": is_credit and not blockers,
        "blockers": [{"reason_code": c, "detail": d} for c, d in blockers],
        "primary_reason_code": primary,
        "needs_approval": should_pay,
        "required_approver_role": approval["required_role"],
        "requires_second_signature": approval["requires_second_signature"],
    }


def build_tasks(task_ids: list[str] | None = None) -> list[Task]:
    tb = APToolbox()
    out = []
    for tid in (task_ids or [i[0] for i in INVOICES if i[0] not in HISTORICAL]):
        ctx = _derive(tb, tid)
        out.append(Task(tid, PROMPT.format(
            invoice_id=tid, vendor=ctx["vendor_name"],
            number=ctx["invoice_number"], currency=ctx["currency"],
            amount=abs(ctx["net_amount"])), ctx))
    return out


def expected_outcome(context: dict) -> dict:
    """The ledger state a correct run must leave behind.

    Judged on the system of record, not on the agent's closing sentence. An
    agent that says it held the invoice and then scheduled the payment anyway
    is a failure, and only the ledger reveals it.
    """
    if context.get("should_apply_credit"):
        return {"paid": False, "payment_count": 0, "amount_paid_gbp": 0.0,
                "exception_raised": False, "credit_applied": True}
    if context["should_pay"]:
        return {"paid": True, "payment_count": 1,
                "amount_paid_gbp": context["expected_amount_gbp"],
                "exception_raised": False, "credit_applied": False}
    return {"paid": False, "payment_count": 0, "amount_paid_gbp": 0.0,
            "exception_raised": True, "credit_applied": False}


def outcome_matches(context: dict, ledger: dict) -> bool:
    want = expected_outcome(context)
    if ledger is None:
        return False
    return (bool(ledger.get("paid")) == want["paid"]
            and int(ledger.get("payment_count", 0)) == want["payment_count"]
            and abs(float(ledger.get("amount_paid_gbp", 0))
                    - want["amount_paid_gbp"]) < 0.005
            and bool(ledger.get("exception_raised")) == want["exception_raised"]
            and bool(ledger.get("credit_applied")) == want["credit_applied"])


def summarize() -> str:
    rows, counts = [], {}
    for t in build_tasks():
        c = t.context
        if c["should_apply_credit"]:
            verdict = "APPLY CREDIT"
        elif c["should_pay"]:
            verdict = f"PAY ({c['required_approver_role']})"
        else:
            verdict = f"HOLD · {c['primary_reason_code']}"
        counts[verdict.split(" · ")[0].split(" (")[0]] = counts.get(
            verdict.split(" · ")[0].split(" (")[0], 0) + 1
        rows.append(f"{t.task_id:<11}{c['currency']:<5}"
                    f"{abs(c['expected_amount_gbp']):>11,.2f}  {verdict:<32}"
                    f"{len(c['blockers'])} blocker(s)")
    rows.append("")
    rows.append("  " + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return "\n".join(rows)
