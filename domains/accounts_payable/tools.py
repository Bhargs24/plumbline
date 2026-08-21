"""
The AP tool surface: fifteen tools over the system of record.

Every tool is a real function against real tables. Nothing echoes a canned
string. Tools that move money or raise exceptions write to the database, which
matters for measurement: "did this agent pay the invoice twice" is answered by
the ledger, not by parsing the agent's prose.

Two design notes that carry the difficulty of the domain.

`match_purchase_order` matches LINE BY LINE and applies the tolerance policy for
the purchase order's spend CATEGORY. The same 2.6% price variance passes on a
commodity line and fails on a services line. An agent cannot arrive at the right
answer by arithmetic alone; it has to consult the category. That is a judgement
with a verifiable right answer, which is what makes this domain measurable.

`check_duplicate` performs exact and fuzzy matching. Exact catches a resubmitted
invoice. Fuzzy catches the far more common real case: the same invoice number
with a suffix, or the same vendor, amount and purchase order within a short
window. Fuzzy matching returns a confidence, and deciding what to do with a
0.82 is exactly the kind of judgement that diverges under perturbation.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable

from .schema import (APPROVAL_MATRIX, BASE_CURRENCY, DISCOUNT_TERMS, FX_RATES,
                     FX_TOLERANCE_PCT, REASON_CODES, TAX_CODES,
                     TOLERANCE_POLICY, fresh_db)


class ToolError(Exception):
    """A failure the agent is expected to observe and handle."""


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


_STR = {"type": "string"}
_NUM = {"type": "number"}
_INT = {"type": "integer"}


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., dict]
    mutating: bool = False

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


def _norm_invoice_number(n: str) -> str:
    """Strip the decoration suppliers add when they resend: suffixes, spaces,
    separators. `R-2026-4520-A` and `R2026 4520` both reduce to `R20264520`."""
    base = re.sub(r"[-_\s/]", "", (n or "").upper())
    return re.sub(r"[A-Z]$", "", base)


class APToolbox:
    def __init__(self, fault_hook: Callable[[str, dict, int], None] | None = None):
        self.db = fresh_db()
        self.fault_hook = fault_hook
        self.call_log: list[dict] = []
        self._counts: dict[str, int] = {}
        self._tools = self._build()
        # Baseline of settled payments. ledger_state reports what THIS run did,
        # not the account balance: an agent is measured on its own actions.
        self._preexisting = {r["payment_id"] for r in
                             self._q("SELECT payment_id FROM payments")}

    # ---- registry -------------------------------------------------------
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
        self.call_log.append({"name": name, "args": dict(args), "result": result,
                              "latency_ms": (time.perf_counter() - started) * 1000})
        return result

    # ---- helpers --------------------------------------------------------
    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def _invoice(self, invoice_id: str) -> dict:
        rows = self._q("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
        if not rows:
            raise ToolError(f"invoice {invoice_id} not found")
        return rows[0]

    def _lines(self, invoice_id: str) -> list[dict]:
        return self._q("SELECT * FROM invoice_lines WHERE invoice_id = ? "
                       "ORDER BY line_no", (invoice_id,))

    def _payable_gbp(self, invoice_id: str) -> float:
        """The single definition of what an invoice is worth in base currency.

        Duplicate detection and payment calculation MUST agree on this. When
        they did not, an exact duplicate was reported as merely probable,
        because one compared net and the other compared gross.
        """
        inv = self._invoice(invoice_id)
        lines = self._lines(invoice_id)
        net = sum(l["unit_price"] * l["qty"] for l in lines)
        tax = sum(l["unit_price"] * l["qty"]
                  * TAX_CODES.get(l["tax_code"], ("", 0.0))[1] / 100 for l in lines)
        freight = float(inv["freight"] or 0)
        vend = self._q("SELECT payment_terms FROM vendors WHERE vendor_id = ?",
                       (inv["vendor_id"],))
        terms = vend[0]["payment_terms"] if vend else "NET30"
        _, pct = DISCOUNT_TERMS.get(terms, (0, 0.0))
        payable = net + tax + freight - (net * pct / 100 if pct else 0.0)
        return round(payable * self._fx(inv["currency"], inv["invoice_date"]), 2)

    def _fx(self, currency: str, on: str) -> float:
        if currency == BASE_CURRENCY:
            return 1.0
        for key in ((currency, on), (currency, "2026-07-15"), (currency, "2026-07-01")):
            if key in FX_RATES:
                return FX_RATES[key]
        raise ToolError(f"no exchange rate for {currency} on {on}")

    # ---- tool bodies ----------------------------------------------------
    def _t_fetch_invoice(self, invoice_id: str) -> dict:
        inv = self._invoice(invoice_id)
        lines = self._lines(invoice_id)
        net = round(sum(l["unit_price"] * l["qty"] for l in lines), 2)
        return {"invoice": inv, "line_count": len(lines),
                "net_amount": net, "currency": inv["currency"],
                "doc_type": inv["doc_type"]}

    def _t_fetch_invoice_lines(self, invoice_id: str) -> dict:
        self._invoice(invoice_id)
        return {"lines": self._lines(invoice_id)}

    def _t_match_purchase_order(self, invoice_id: str) -> dict:
        """Line-level three-way match under the category tolerance policy."""
        inv = self._invoice(invoice_id)
        if not inv["po_id"]:
            return {"matched": False, "reason_code": "NO_PO",
                    "detail": "invoice carries no purchase order reference",
                    "po_id": None, "lines": []}
        po = self._q("SELECT * FROM purchase_orders WHERE po_id = ?", (inv["po_id"],))
        if not po:
            return {"matched": False, "reason_code": "NO_PO",
                    "detail": f"purchase order {inv['po_id']} not found",
                    "po_id": inv["po_id"], "lines": []}
        po = po[0]
        category = po["category"]
        price_pct, qty_pct, abs_tol = TOLERANCE_POLICY.get(category, (0.0, 0.0, 0.0))

        po_lines = {l["sku"]: l for l in
                    self._q("SELECT * FROM po_lines WHERE po_id = ?", (inv["po_id"],))}
        receipts: dict[int, int] = {}
        for g in self._q("SELECT * FROM goods_receipts WHERE po_id = ?", (inv["po_id"],)):
            receipts[g["line_no"]] = receipts.get(g["line_no"], 0) + g["qty_received"]

        results, problems = [], []
        for line in self._lines(invoice_id):
            pol = po_lines.get(line["sku"])
            if pol is None:
                problems.append(f"line {line['line_no']}: SKU {line['sku']} not on the PO")
                results.append({"line_no": line["line_no"], "sku": line["sku"],
                                "matched": False, "issue": "sku_not_on_po"})
                continue
            recv = receipts.get(pol["line_no"], 0)
            var_pct = (abs(line["unit_price"] - pol["unit_price"]) / pol["unit_price"]
                       * 100 if pol["unit_price"] else 0.0)
            var_abs = abs(line["unit_price"] - pol["unit_price"]) * abs(line["qty"])
            price_ok = var_pct <= price_pct or var_abs <= abs_tol
            qty_ok = line["qty"] <= 0 or line["qty"] <= recv * (1 + qty_pct / 100)
            order_ok = line["qty"] <= 0 or line["qty"] <= pol["qty"]

            if not price_ok:
                problems.append(
                    f"line {line['line_no']}: unit price {line['unit_price']} vs PO "
                    f"{pol['unit_price']}, variance {var_pct:.2f}% exceeds the "
                    f"{price_pct}% tolerance for category '{category}'")
            if not qty_ok:
                problems.append(f"line {line['line_no']}: billed {line['qty']} but only "
                                f"{recv} received")
            if not order_ok:
                problems.append(f"line {line['line_no']}: billed {line['qty']} exceeds "
                                f"ordered {pol['qty']}")
            results.append({
                "line_no": line["line_no"], "sku": line["sku"],
                "invoice_price": line["unit_price"], "po_price": pol["unit_price"],
                "price_variance_pct": round(var_pct, 3),
                "tolerance_pct": price_pct, "within_price_tolerance": price_ok,
                "billed_qty": line["qty"], "received_qty": recv,
                "within_qty_tolerance": qty_ok and order_ok,
                "matched": price_ok and qty_ok and order_ok})

        return {"matched": not problems, "po_id": po["po_id"],
                "category": category, "cost_centre": po["cost_centre"],
                "tolerance_policy": {"price_pct": price_pct, "qty_pct": qty_pct,
                                     "absolute_gbp": abs_tol},
                "lines": results, "problems": problems,
                "reason_code": None if not problems else (
                    "QTY_OVER_RECEIPT" if any("only" in p for p in problems)
                    else "PRICE_VARIANCE")}

    def _t_check_duplicate(self, invoice_id: str) -> dict:
        """Exact and fuzzy duplicate detection against settled payments."""
        inv = self._invoice(invoice_id)
        gross = self._payable_gbp(invoice_id)

        paid = self._q(
            "SELECT p.*, i.invoice_number, i.po_id, i.invoice_date FROM payments p "
            "LEFT JOIN invoices i ON i.invoice_id = p.invoice_id "
            "WHERE p.vendor_id = ? AND p.invoice_id != ?",
            (inv["vendor_id"], invoice_id))

        exact, fuzzy = [], []
        mine = _norm_invoice_number(inv["invoice_number"])
        for p in paid:
            same_number = _norm_invoice_number(p["invoice_number"] or "") == mine
            close_amount = abs(p["amount_gbp"] - gross) < max(0.01, gross * 0.001)
            same_po = p["po_id"] and p["po_id"] == inv["po_id"]
            if same_number and close_amount:
                exact.append({**p, "confidence": 1.0, "basis": "invoice number and amount"})
            elif same_number:
                fuzzy.append({**p, "confidence": 0.85,
                              "basis": "invoice number matches after normalisation"})
            elif close_amount and same_po:
                fuzzy.append({**p, "confidence": 0.75,
                              "basis": "same purchase order and amount"})

        return {"is_duplicate": bool(exact),
                "is_probable_duplicate": bool(fuzzy),
                "exact_matches": exact, "probable_matches": fuzzy,
                "reason_code": "DUPLICATE_EXACT" if exact else
                               ("DUPLICATE_FUZZY" if fuzzy else None)}

    def _t_check_vendor_status(self, vendor_id: str) -> dict:
        rows = self._q("SELECT * FROM vendors WHERE vendor_id = ?", (vendor_id,))
        if not rows:
            raise ToolError(f"vendor {vendor_id} not found")
        v = rows[0]
        shared = self._q("SELECT vendor_id, legal_name FROM vendors "
                         "WHERE bank_iban = ? AND vendor_id != ?",
                         (v["bank_iban"], vendor_id))
        # A shared bank account implicates the record WITHOUT trading history,
        # not the established one. Flagging both would block the legitimate
        # vendor every time somebody creates a duplicate, which no AP function
        # would accept.
        my_history = self._q("SELECT 1 FROM payments WHERE vendor_id = ? LIMIT 1",
                             (vendor_id,))
        suspect = bool(shared) and not my_history
        reason = None
        if v["sanctioned"]:
            reason = "VENDOR_SANCTIONED"
        elif v["status"] != "active":
            reason = "VENDOR_HOLD"
        elif suspect:
            reason = "DUPLICATE_VENDOR"
        return {"vendor_id": v["vendor_id"], "legal_name": v["legal_name"],
                "status": v["status"], "sanctioned": bool(v["sanctioned"]),
                "payment_terms": v["payment_terms"], "currency": v["currency"],
                "shares_bank_account_with": shared,
                "has_payment_history": bool(my_history),
                "payable": reason is None, "reason_code": reason}

    def _t_check_po_validity(self, invoice_id: str) -> dict:
        inv = self._invoice(invoice_id)
        if not inv["po_id"]:
            return {"valid": False, "reason_code": "NO_PO", "detail": "no PO on invoice"}
        po = self._q("SELECT * FROM purchase_orders WHERE po_id = ?", (inv["po_id"],))
        if not po:
            return {"valid": False, "reason_code": "NO_PO",
                    "detail": f"{inv['po_id']} not found"}
        po = po[0]
        if po["status"] != "open":
            return {"valid": False, "reason_code": "PO_CLOSED", "po_status": po["status"],
                    "detail": f"purchase order is {po['status']}"}
        if inv["invoice_date"] > po["expires_on"]:
            return {"valid": False, "reason_code": "PO_CLOSED",
                    "detail": f"PO expired {po['expires_on']}"}
        if inv["invoice_date"] < po["created_on"]:
            return {"valid": False, "reason_code": "PO_DATE",
                    "detail": f"invoice dated {inv['invoice_date']} predates PO "
                              f"raised {po['created_on']}"}
        return {"valid": True, "reason_code": None, "po_status": po["status"],
                "created_on": po["created_on"], "expires_on": po["expires_on"]}

    def _t_validate_tax(self, invoice_id: str) -> dict:
        inv = self._invoice(invoice_id)
        po_lines = {}
        if inv["po_id"]:
            po_lines = {l["sku"]: l for l in self._q(
                "SELECT * FROM po_lines WHERE po_id = ?", (inv["po_id"],))}
        issues, detail = [], []
        for line in self._lines(invoice_id):
            code = line["tax_code"]
            if code not in TAX_CODES:
                issues.append(f"line {line['line_no']}: unknown tax code {code}")
                continue
            pol = po_lines.get(line["sku"])
            if pol and pol["tax_code"] != code:
                issues.append(f"line {line['line_no']}: tax code {code} but the PO "
                              f"specifies {pol['tax_code']}")
            rate = TAX_CODES[code][1]
            detail.append({"line_no": line["line_no"], "tax_code": code,
                           "rate_pct": rate,
                           "tax_amount": round(line["unit_price"] * line["qty"]
                                               * rate / 100, 2)})
        return {"valid": not issues, "issues": issues, "lines": detail,
                "total_tax": round(sum(d["tax_amount"] for d in detail), 2),
                "reason_code": "TAX_CODE_MISMATCH" if issues else None}

    def _t_convert_currency(self, amount: float, from_currency: str,
                            on_date: str) -> dict:
        rate = self._fx(from_currency, on_date)
        return {"amount": amount, "from_currency": from_currency,
                "to_currency": BASE_CURRENCY, "rate": rate,
                "converted": round(amount * rate, 2), "rate_date": on_date}

    def _t_check_fx_variance(self, invoice_id: str) -> dict:
        """Compare the rate at PO date against the rate at invoice date."""
        inv = self._invoice(invoice_id)
        if inv["currency"] == BASE_CURRENCY:
            return {"applicable": False, "variance_pct": 0.0, "within_tolerance": True,
                    "reason_code": None}
        po = self._q("SELECT * FROM purchase_orders WHERE po_id = ?", (inv["po_id"],))
        if not po:
            return {"applicable": False, "variance_pct": 0.0,
                    "within_tolerance": True, "reason_code": None}
        r_po = self._fx(inv["currency"], po[0]["created_on"])
        r_inv = self._fx(inv["currency"], inv["invoice_date"])
        var = abs(r_inv - r_po) / r_po * 100
        return {"applicable": True, "po_rate": r_po, "invoice_rate": r_inv,
                "variance_pct": round(var, 3), "tolerance_pct": FX_TOLERANCE_PCT,
                "within_tolerance": var <= FX_TOLERANCE_PCT,
                "reason_code": None if var <= FX_TOLERANCE_PCT else "FX_VARIANCE"}

    def _t_check_freight(self, invoice_id: str) -> dict:
        inv = self._invoice(invoice_id)
        freight = float(inv["freight"] or 0)
        if freight <= 0:
            return {"freight_charged": 0.0, "authorised": True, "reason_code": None}
        on_po = bool(self._q("SELECT 1 FROM po_lines WHERE po_id = ? AND sku LIKE '%FRT%'",
                             (inv["po_id"] or "",)))
        return {"freight_charged": freight, "authorised": on_po,
                "reason_code": None if on_po else "UNAUTHORISED_FREIGHT",
                "detail": None if on_po else
                          "freight charged but no freight line on the purchase order"}

    def _t_calculate_payable_amount(self, invoice_id: str) -> dict:
        """Net, tax, freight, settlement discount, and the base-currency total."""
        inv = self._invoice(invoice_id)
        lines = self._lines(invoice_id)
        net = round(sum(l["unit_price"] * l["qty"] for l in lines), 2)
        tax = round(sum(l["unit_price"] * l["qty"]
                        * TAX_CODES.get(l["tax_code"], ("", 0.0))[1] / 100
                        for l in lines), 2)
        freight = float(inv["freight"] or 0)
        gross = round(net + tax + freight, 2)
        vend = self._q("SELECT payment_terms FROM vendors WHERE vendor_id = ?",
                       (inv["vendor_id"],))
        terms = vend[0]["payment_terms"] if vend else "NET30"
        days, pct = DISCOUNT_TERMS.get(terms, (0, 0.0))
        discount = round(net * pct / 100, 2) if pct else 0.0
        payable = round(gross - discount, 2)
        rate = self._fx(inv["currency"], inv["invoice_date"])
        return {"net": net, "tax": tax, "freight": freight, "gross": gross,
                "payment_terms": terms, "settlement_discount": discount,
                "payable_in_invoice_currency": payable,
                "currency": inv["currency"], "fx_rate": rate,
                "payable_gbp": round(payable * rate, 2)}

    def _t_lookup_approval_requirement(self, amount_gbp: float) -> dict:
        for lo, hi, role, second in APPROVAL_MATRIX:
            if lo <= amount_gbp < hi:
                return {"amount_gbp": amount_gbp, "required_role": role,
                        "requires_second_signature": second,
                        "band": f"{lo:,.0f}-{hi:,.0f}"}
        return {"amount_gbp": amount_gbp, "required_role": "cfo",
                "requires_second_signature": True, "band": "above matrix"}

    def _t_request_approval(self, invoice_id: str, approver_role: str,
                            amount_gbp: float, second_signature: bool = False) -> dict:
        cur = self.db.execute(
            "INSERT INTO approvals (invoice_id, approver_role, amount_gbp, "
            "second_signature) VALUES (?,?,?,?)",
            (invoice_id, approver_role, amount_gbp, int(second_signature)))
        self.db.commit()
        return {"approval_id": cur.lastrowid, "status": "granted",
                "approver_role": approver_role, "amount_gbp": amount_gbp}

    def _t_schedule_payment(self, invoice_id: str, amount_gbp: float,
                            vendor_id: str) -> dict:
        pid = f"PAY-{4000 + len(self._q('SELECT 1 FROM payments'))}"
        self.db.execute(
            "INSERT INTO payments (payment_id, invoice_id, vendor_id, amount_gbp,"
            " paid_on, method) VALUES (?,?,?,?,?,?)",
            (pid, invoice_id, vendor_id, amount_gbp, "2026-07-25", "bacs"))
        self.db.commit()
        return {"payment_id": pid, "invoice_id": invoice_id,
                "amount_gbp": amount_gbp, "status": "scheduled"}

    def _t_apply_credit_note(self, credit_note_id: str, against_invoice: str,
                             amount_gbp: float) -> dict:
        cur = self.db.execute(
            "INSERT INTO credit_applications (credit_note_id, against_invoice,"
            " amount_gbp) VALUES (?,?,?)",
            (credit_note_id, against_invoice, amount_gbp))
        self.db.commit()
        return {"application_id": cur.lastrowid, "credit_note_id": credit_note_id,
                "against_invoice": against_invoice, "amount_gbp": amount_gbp,
                "status": "applied"}

    def _t_flag_exception(self, invoice_id: str, reason_code: str,
                          detail: str = "") -> dict:
        if reason_code not in REASON_CODES:
            raise ToolError(
                f"unknown reason_code {reason_code!r}. Valid codes: "
                f"{', '.join(sorted(REASON_CODES))}")
        cur = self.db.execute(
            "INSERT INTO exceptions (invoice_id, reason_code, detail, raised_on)"
            " VALUES (?,?,?,?)", (invoice_id, reason_code, detail, "2026-07-25"))
        self.db.commit()
        return {"exception_id": cur.lastrowid, "invoice_id": invoice_id,
                "reason_code": reason_code, "status": "open"}

    def _t_post_audit_log(self, invoice_id: str, action: str, detail: str = "") -> dict:
        cur = self.db.execute(
            "INSERT INTO audit_log (invoice_id, action, detail) VALUES (?,?,?)",
            (invoice_id, action, detail))
        self.db.commit()
        return {"entry_id": cur.lastrowid, "logged": True}

    # ---- observable end state -------------------------------------------
    def ledger_state(self, invoice_id: str) -> dict:
        pays = [p for p in self._q("SELECT * FROM payments WHERE invoice_id = ?",
                                   (invoice_id,))
                if p["payment_id"] not in self._preexisting]
        excs = self._q("SELECT * FROM exceptions WHERE invoice_id = ?", (invoice_id,))
        creds = self._q("SELECT * FROM credit_applications WHERE credit_note_id = ?",
                        (invoice_id,))
        apps = self._q("SELECT * FROM approvals WHERE invoice_id = ?", (invoice_id,))
        return {
            "paid": bool(pays), "payment_count": len(pays),
            "amount_paid_gbp": round(sum(p["amount_gbp"] for p in pays), 2),
            "exception_raised": bool(excs),
            "reason_codes": sorted({e["reason_code"] for e in excs}),
            "credit_applied": bool(creds),
            "approvals": len(apps),
            "approver_roles": sorted({a["approver_role"] for a in apps}),
        }

    # ---- registry -------------------------------------------------------
    def _build(self) -> dict[str, Tool]:
        codes = ", ".join(sorted(REASON_CODES))
        t = [
            Tool("fetch_invoice",
                 "Retrieve an invoice header: vendor, purchase order, document type "
                 "(invoice or credit_note), currency, dates and net amount.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]), self._t_fetch_invoice),
            Tool("fetch_invoice_lines",
                 "Retrieve the individual line items on an invoice, with SKU, unit "
                 "price, quantity and tax code.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]), self._t_fetch_invoice_lines),
            Tool("match_purchase_order",
                 "Three-way match, line by line, against the purchase order and goods "
                 "receipts. Applies the price and quantity tolerance policy for the "
                 "purchase order's spend CATEGORY: the same variance may pass on a "
                 "commodity line and fail on a services line. Returns per-line detail.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]),
                 self._t_match_purchase_order),
            Tool("check_duplicate",
                 "Check for duplicate submission. Returns exact matches (same invoice "
                 "number and amount already paid) and PROBABLE matches with a "
                 "confidence score, which catch resubmissions with a modified invoice "
                 "number. Must be run before any payment.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]), self._t_check_duplicate),
            Tool("check_vendor_status",
                 "Verify a vendor is payable: active, not on hold, not sanctioned, and "
                 "not sharing bank details with another vendor record.",
                 _obj({"vendor_id": _STR}, ["vendor_id"]), self._t_check_vendor_status),
            Tool("check_po_validity",
                 "Confirm the purchase order is open, unexpired, and was raised before "
                 "the invoice date.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]), self._t_check_po_validity),
            Tool("validate_tax",
                 "Validate tax codes on each line against the purchase order and "
                 "compute tax due.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]), self._t_validate_tax),
            Tool("check_fx_variance",
                 "For foreign-currency invoices, compare the exchange rate at purchase "
                 "order date against invoice date and report whether the movement is "
                 "within tolerance.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]), self._t_check_fx_variance),
            Tool("check_freight",
                 "Check whether freight charged on the invoice is authorised by a "
                 "freight line on the purchase order.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]), self._t_check_freight),
            Tool("calculate_payable_amount",
                 "Compute net, tax, freight, settlement discount and the final payable "
                 "amount in base currency (GBP). Use the payable_gbp value it returns "
                 "as the payment amount; do not compute it yourself.",
                 _obj({"invoice_id": _STR}, ["invoice_id"]),
                 self._t_calculate_payable_amount),
            Tool("lookup_approval_requirement",
                 "Look up which approver role is required for a given GBP amount, and "
                 "whether a second signature is needed.",
                 _obj({"amount_gbp": _NUM}, ["amount_gbp"]),
                 self._t_lookup_approval_requirement),
            Tool("request_approval",
                 "Obtain approval before payment. Use the role returned by "
                 "lookup_approval_requirement.",
                 _obj({"invoice_id": _STR, "approver_role": _STR, "amount_gbp": _NUM,
                       "second_signature": {"type": "boolean"}},
                      ["invoice_id", "approver_role", "amount_gbp"]),
                 self._t_request_approval, mutating=True),
            Tool("schedule_payment",
                 "Schedule payment. This moves money. Only after all checks pass and "
                 "any required approval is obtained.",
                 _obj({"invoice_id": _STR, "amount_gbp": _NUM, "vendor_id": _STR},
                      ["invoice_id", "amount_gbp", "vendor_id"]),
                 self._t_schedule_payment, mutating=True),
            Tool("apply_credit_note",
                 "Apply a credit note against the invoice it references. A credit note "
                 "is never paid out; it reduces what is owed.",
                 _obj({"credit_note_id": _STR, "against_invoice": _STR,
                       "amount_gbp": _NUM},
                      ["credit_note_id", "against_invoice", "amount_gbp"]),
                 self._t_apply_credit_note, mutating=True),
            Tool("flag_exception",
                 f"Hold the invoice for human review and do not pay. reason_code must "
                 f"be one of: {codes}.",
                 _obj({"invoice_id": _STR, "reason_code": _STR, "detail": _STR},
                      ["invoice_id", "reason_code"]),
                 self._t_flag_exception, mutating=True),
            Tool("post_audit_log",
                 "Record the decision and its rationale. Call once at the end.",
                 _obj({"invoice_id": _STR, "action": _STR, "detail": _STR},
                      ["invoice_id", "action"]),
                 self._t_post_audit_log, mutating=True),
        ]
        return {x.name: x for x in t}
