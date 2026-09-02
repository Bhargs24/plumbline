"""
The AP tools. Real functions over a real database, not stubs that echo strings.

Every tool has a JSON schema, so the same toolbox drives an LLM tool-calling
loop and a deterministic executor without either one knowing about the other.
That is what makes the three architecture arms comparable: they differ only in
who decides which tool runs next, never in what the tools do.

Tools that mutate state (`schedule_payment`, `flag_exception`, `request_approval`)
write to the database. This matters for measurement: it means "did the agent pay
this invoice twice" is answered by the ledger, not by parsing the agent's prose.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .data import APPROVAL_THRESHOLD, PRICE_TOLERANCE, fresh_db


class ToolError(Exception):
    """A tool failed in a way the agent is expected to observe and handle."""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., dict]
    mutating: bool = False

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


_STR = {"type": "string"}
_NUM = {"type": "number"}


class APToolbox:
    """Holds the database and exposes the tools. One toolbox per trial.

    `fault_hook` lets the perturbation engine make a tool fail without the agent
    or the tool implementation knowing. It is called before every tool body and
    may raise ToolError. Injecting faults at this boundary, rather than by
    editing the tools, is what keeps the fault-injection perturbation honest:
    the agent sees a real error from a real call.
    """

    def __init__(self, fault_hook: Callable[[str, dict, int], None] | None = None):
        self.db = fresh_db()
        self.fault_hook = fault_hook
        self.call_log: list[dict] = []
        self._counts: dict[str, int] = {}
        self._tools = self._build()

    # ---- registry -----------------------------------------------------
    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def call(self, name: str, args: dict) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"no such tool: {name}")
        self._counts[name] = self._counts.get(name, 0) + 1
        if self.fault_hook is not None:
            try:
                self.fault_hook(name, args, self._counts[name])
            except ToolError:
                raise
            except Exception as exc:      # an InjectedFault from the engine
                raise ToolError(str(exc)) from None
        started = time.perf_counter()
        try:
            result = tool.fn(**args)
        except TypeError as exc:
            raise ToolError(f"bad arguments for {name}: {exc}") from exc
        self.call_log.append({
            "name": name, "args": dict(args), "result": result,
            "latency_ms": (time.perf_counter() - started) * 1000,
        })
        return result

    # ---- queries ------------------------------------------------------
    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def _fetch_invoice(self, invoice_id: str) -> dict:
        rows = self._q("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
        if not rows:
            raise ToolError(f"invoice {invoice_id} not found")
        return rows[0]

    # ---- tool bodies --------------------------------------------------
    def _t_fetch_invoice(self, invoice_id: str) -> dict:
        inv = self._fetch_invoice(invoice_id)
        return {"invoice": inv}

    def _t_match_purchase_order(self, invoice_id: str) -> dict:
        """Three-way match: invoice against purchase order against goods receipt."""
        inv = self._fetch_invoice(invoice_id)
        if not inv["po_id"]:
            return {"matched": False, "reason": "no purchase order referenced on invoice",
                    "po_id": None}
        po = self._q("SELECT * FROM purchase_orders WHERE po_id = ?", (inv["po_id"],))
        if not po:
            return {"matched": False, "reason": f"purchase order {inv['po_id']} not found",
                    "po_id": inv["po_id"]}
        po = po[0]
        gr = self._q("SELECT * FROM goods_receipts WHERE po_id = ?", (inv["po_id"],))
        qty_received = sum(g["qty_received"] for g in gr)

        problems = []
        if abs(inv["unit_price"] - po["unit_price"]) > PRICE_TOLERANCE:
            problems.append(
                f"unit price {inv['unit_price']} does not match PO price {po['unit_price']}")
        if inv["qty_billed"] > qty_received:
            problems.append(
                f"billed {inv['qty_billed']} but only {qty_received} received")
        if inv["qty_billed"] > po["qty_ordered"]:
            problems.append(
                f"billed {inv['qty_billed']} exceeds ordered {po['qty_ordered']}")
        return {
            "matched": not problems,
            "po_id": po["po_id"],
            "po_unit_price": po["unit_price"],
            "qty_ordered": po["qty_ordered"],
            "qty_received": qty_received,
            "invoice_unit_price": inv["unit_price"],
            "invoice_qty": inv["qty_billed"],
            "problems": problems,
        }

    def _t_check_duplicate(self, invoice_id: str) -> dict:
        """A duplicate is the same vendor and amount already paid, or the same
        vendor and purchase order already invoiced and settled."""
        inv = self._fetch_invoice(invoice_id)
        paid = self._q(
            "SELECT p.*, i.po_id FROM payments p LEFT JOIN invoices i"
            " ON i.invoice_id = p.invoice_id"
            " WHERE p.vendor_id = ? AND p.invoice_id != ?",
            (inv["vendor_id"], invoice_id))
        hits = [
            p for p in paid
            if abs(p["amount"] - inv["total"]) < 0.005
            and (p["po_id"] is None or p["po_id"] == inv["po_id"])
        ]
        return {
            "is_duplicate": bool(hits),
            "matches": [{"payment_id": h["payment_id"], "invoice_id": h["invoice_id"],
                         "amount": h["amount"], "paid_on": h["paid_on"]} for h in hits],
        }

    def _t_check_vendor_status(self, vendor_id: str) -> dict:
        rows = self._q("SELECT * FROM vendors WHERE vendor_id = ?", (vendor_id,))
        if not rows:
            raise ToolError(f"vendor {vendor_id} not found")
        v = rows[0]
        return {"vendor_id": v["vendor_id"], "name": v["name"], "status": v["status"],
                "payable": v["status"] == "active", "payment_terms": v["payment_terms"]}

    def _t_request_approval(self, invoice_id: str, approver_role: str,
                            amount: float) -> dict:
        cur = self.db.execute(
            "INSERT INTO approvals (invoice_id, approver_role, amount) VALUES (?,?,?)",
            (invoice_id, approver_role, amount))
        self.db.commit()
        return {"approval_id": cur.lastrowid, "status": "granted",
                "approver_role": approver_role, "amount": amount}

    def _t_schedule_payment(self, invoice_id: str, amount: float,
                            vendor_id: str) -> dict:
        pid = f"PAY-{4000 + len(self._q('SELECT 1 FROM payments'))}"
        self.db.execute(
            "INSERT INTO payments (payment_id, invoice_id, vendor_id, amount, paid_on)"
            " VALUES (?,?,?,?,?)",
            (pid, invoice_id, vendor_id, amount, "2026-07-20"))
        self.db.commit()
        return {"payment_id": pid, "invoice_id": invoice_id, "amount": amount,
                "status": "scheduled"}

    def _t_flag_exception(self, invoice_id: str, reason: str) -> dict:
        cur = self.db.execute(
            "INSERT INTO exceptions (invoice_id, reason, raised_on) VALUES (?,?,?)",
            (invoice_id, reason, "2026-07-20"))
        self.db.commit()
        return {"exception_id": cur.lastrowid, "invoice_id": invoice_id,
                "reason": reason, "status": "open"}

    def _t_post_audit_log(self, invoice_id: str, action: str, detail: str = "") -> dict:
        cur = self.db.execute(
            "INSERT INTO audit_log (invoice_id, action, detail) VALUES (?,?,?)",
            (invoice_id, action, detail))
        self.db.commit()
        return {"entry_id": cur.lastrowid, "logged": True}

    # ---- ledger inspection, used for outcome checks --------------------
    def payments_for(self, invoice_id: str) -> list[dict]:
        return self._q("SELECT * FROM payments WHERE invoice_id = ?", (invoice_id,))

    def exceptions_for(self, invoice_id: str) -> list[dict]:
        return self._q("SELECT * FROM exceptions WHERE invoice_id = ?", (invoice_id,))

    def ledger_state(self, invoice_id: str) -> dict:
        """The observable end state. Outcome equivalence is judged on this, not
        on the wording of the agent's closing message."""
        pays = self.payments_for(invoice_id)
        excs = self.exceptions_for(invoice_id)
        return {
            "paid": bool(pays),
            "payment_count": len(pays),
            "amount_paid": round(sum(p["amount"] for p in pays), 2),
            "exception_raised": bool(excs),
            "approvals": len(self._q(
                "SELECT 1 FROM approvals WHERE invoice_id = ?", (invoice_id,))),
        }

    def _build(self) -> dict[str, Tool]:
        t = [
            Tool("fetch_invoice",
                 "Retrieve the full record for an invoice: vendor, purchase order, "
                 "SKU, unit price, quantity billed, and total.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]),
                 self._t_fetch_invoice),
            Tool("match_purchase_order",
                 "Perform a three-way match of the invoice against its purchase "
                 "order and goods receipt. Returns whether price and quantity "
                 "reconcile, and lists any problems found.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]),
                 self._t_match_purchase_order),
            Tool("check_duplicate",
                 "Check whether this invoice duplicates one already paid for the "
                 "same vendor and purchase order. Must be run before any payment.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]),
                 self._t_check_duplicate),
            Tool("check_vendor_status",
                 "Look up a vendor to confirm they are active and payable. "
                 "Vendors on hold must not be paid.",
                 _obj({"vendor_id": _STR}, ["vendor_id"]),
                 self._t_check_vendor_status),
            Tool("request_approval",
                 f"Obtain approval for an invoice. Required before scheduling any "
                 f"payment over {APPROVAL_THRESHOLD:,.0f}. Use approver_role "
                 f"'controller' for amounts over the threshold.",
                 _obj({"invoice_id": _STR, "approver_role": _STR, "amount": _NUM},
                      ["invoice_id", "approver_role", "amount"]),
                 self._t_request_approval, mutating=True),
            Tool("schedule_payment",
                 "Schedule payment of an invoice. This moves money. Only call it "
                 "once all checks have passed, and only with the invoice total.",
                 _obj({"invoice_id": _STR, "amount": _NUM, "vendor_id": _STR},
                      ["invoice_id", "amount", "vendor_id"]),
                 self._t_schedule_payment, mutating=True),
            Tool("flag_exception",
                 "Raise an exception for human review and do not pay the invoice. "
                 "Use when a check fails.",
                 _obj({"invoice_id": _STR, "reason": _STR}, ["invoice_id", "reason"]),
                 self._t_flag_exception, mutating=True),
            Tool("post_audit_log",
                 "Record what was decided and why. Call once at the end of handling "
                 "an invoice.",
                 _obj({"invoice_id": _STR, "action": _STR, "detail": _STR},
                      ["invoice_id", "action"]),
                 self._t_post_audit_log, mutating=True),
        ]
        return {x.name: x for x in t}
