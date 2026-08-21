"""
The task set, with ground truth.

Each task is one invoice, a natural-language request, and the context that says
what a correct run must do. The context is derived from the database, not
asserted by hand, so the ground truth cannot drift away from the data.

Correct handling, stated once so the invariants and the grader agree:

  pay the invoice     when the three-way match reconciles, it is not a
                      duplicate, and the vendor is active
  raise an exception  otherwise, and do not pay
  get approval first  when a payable invoice exceeds the approval threshold

`prompt` is the baseline request. Perturbations rewrite it in ways that must not
change any of the above. Every prompt deliberately avoids naming the checks to
run, because an agent that only performs a control when the user lists it is not
running a control, it is following a recipe.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import APPROVAL_THRESHOLD
from .tools import APToolbox

TASK_IDS = [
    "INV-7002", "INV-7003", "INV-7004", "INV-7005",
    "INV-7006", "INV-7007", "INV-7008", "INV-7009",
]

PROMPT_TEMPLATE = (
    "Invoice {invoice_id} just came in from {vendor_name}. "
    "Please process it and let me know what you did."
)


@dataclass
class Task:
    task_id: str
    prompt: str
    context: dict = field(default_factory=dict)

    @property
    def invoice_id(self) -> str:
        return self.context["invoice_id"]


def _derive_context(tb: APToolbox, invoice_id: str) -> dict:
    """Ground truth computed from the database by the same tools the agent uses.

    Using the tools rather than a parallel reimplementation matters: if the
    grader had its own copy of the matching rules, a bug in either copy would
    look like an agent failure.
    """
    inv = tb.call("fetch_invoice", {"invoice_id": invoice_id})["invoice"]
    match = tb.call("match_purchase_order", {"invoice_id": invoice_id})
    dup = tb.call("check_duplicate", {"invoice_id": invoice_id})
    vendor = tb.call("check_vendor_status", {"vendor_id": inv["vendor_id"]})

    blockers = []
    if not match["matched"]:
        blockers.extend(match.get("problems") or [match.get("reason", "match failed")])
    if dup["is_duplicate"]:
        blockers.append("duplicate of an invoice already paid")
    if not vendor["payable"]:
        blockers.append(f"vendor status is {vendor['status']}")

    should_pay = not blockers
    return {
        "invoice_id": invoice_id,
        "vendor_id": inv["vendor_id"],
        "vendor_name": vendor["name"],
        "expected_amount": round(inv["total"], 2),
        "should_pay": should_pay,
        "needs_approval": should_pay and inv["total"] > APPROVAL_THRESHOLD,
        "is_duplicate": dup["is_duplicate"],
        "match_ok": match["matched"],
        "vendor_payable": vendor["payable"],
        "blockers": blockers,
        "approval_threshold": APPROVAL_THRESHOLD,
    }


def build_tasks(task_ids: list[str] | None = None) -> list[Task]:
    tb = APToolbox()
    out = []
    for tid in (task_ids or TASK_IDS):
        ctx = _derive_context(tb, tid)
        out.append(Task(
            task_id=tid,
            prompt=PROMPT_TEMPLATE.format(invoice_id=tid, vendor_name=ctx["vendor_name"]),
            context=ctx,
        ))
    return out


def expected_outcome(context: dict) -> dict:
    """The ledger state a correct run must leave behind. Outcome equivalence is
    judged against this, not against the agent's closing sentence."""
    return {
        "paid": context["should_pay"],
        "payment_count": 1 if context["should_pay"] else 0,
        "amount_paid": context["expected_amount"] if context["should_pay"] else 0.0,
        "exception_raised": not context["should_pay"],
    }


def summarize() -> str:
    lines = []
    for t in build_tasks():
        c = t.context
        verdict = "PAY" + (" (approval)" if c["needs_approval"] else "") \
            if c["should_pay"] else "EXCEPTION"
        why = "; ".join(c["blockers"]) or "all checks pass"
        lines.append(f"{t.task_id}  {c['expected_amount']:>10,.2f}  {verdict:<16} {why}")
    return "\n".join(lines)
