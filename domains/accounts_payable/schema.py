"""
An accounts-payable system of record with the shape a real one has.

The previous version of this domain had eight invoices, four boolean checks and
one table. A competent model solved it perfectly, every perturbation returned
100%, and the study measured nothing. That is what happens when you point a
measuring instrument at a task with no judgement in it.

Failure in AP lives in AMBIGUITY, not in arithmetic. A line item split across
two rows. A credit note against a superseded purchase order. A supplier billing
in EUR against a GBP order at a rate that moved. A duplicate whose invoice
number differs by a suffix. A price variance that is inside tolerance for
commodities and outside it for services. Those are the cases where an agent has
to decide, and deciding is where behaviour diverges under rewording.

This model carries eleven tables and a twenty-case exception taxonomy built to
produce exactly that ambiguity.

TOLERANCE IS CATEGORY-DEPENDENT, which is the single most important source of
realistic difficulty here. A 3% price variance on a commodity is within policy
and gets paid. The same 3% on a services line is outside policy and gets held.
No amount of arithmetic tells you which; you have to look up the category, find
the applicable policy, and apply it. That is a judgement with a right answer,
which is the ideal shape for a reliability measurement.
"""
from __future__ import annotations

import sqlite3

# --------------------------------------------------------------------------
# Policy constants
# --------------------------------------------------------------------------
BASE_CURRENCY = "GBP"

#: Approval matrix. Ordered; first match by amount band wins.
APPROVAL_MATRIX = [
    # (min_gbp, max_gbp, required_role, requires_second_signature)
    (0.00,        5_000.00,  "ap_clerk",   False),
    (5_000.00,   25_000.00,  "controller", False),
    (25_000.00, 100_000.00,  "finance_director", False),
    (100_000.00, 10**9,      "cfo",        True),
]

#: Price and quantity tolerance by spend category. This is the crux.
TOLERANCE_POLICY = {
    #  category      price_pct  qty_pct  abs_gbp   note
    "commodity":    (3.0,       2.0,     50.00),
    "manufactured": (1.0,       0.0,     25.00),
    "services":     (0.0,       0.0,      0.00),
    "freight":      (5.0,      10.0,    100.00),
    "utilities":    (10.0,      0.0,    250.00),
}

#: Standard tax codes with their expected rates.
TAX_CODES = {
    "S20": ("standard rated", 20.0),
    "R05": ("reduced rated", 5.0),
    "Z00": ("zero rated", 0.0),
    "E00": ("exempt", 0.0),
    "RC0": ("reverse charge", 0.0),
}

#: Early settlement discount terms, as (days, percent).
DISCOUNT_TERMS = {
    "2/10 NET30": (10, 2.0),
    "1/15 NET45": (15, 1.0),
    "NET30": (0, 0.0),
    "NET45": (0, 0.0),
    "NET60": (0, 0.0),
}

FX_RATES = {
    # (currency, date) -> rate to GBP
    ("EUR", "2026-07-01"): 0.8420,
    ("EUR", "2026-07-15"): 0.8615,   # moved 2.3% inside the month
    ("USD", "2026-07-01"): 0.7810,
    ("USD", "2026-07-15"): 0.7745,
    ("GBP", "2026-07-01"): 1.0,
    ("GBP", "2026-07-15"): 1.0,
}
FX_TOLERANCE_PCT = 2.0   # invoice may differ from PO by this much on FX alone

# --------------------------------------------------------------------------
# Master data
# --------------------------------------------------------------------------
VENDORS = [
    # id, legal_name, trading_name, status, terms, currency, tax_id, bank_iban, sanctions
    ("V-100", "Ridgeline Components Ltd", "Ridgeline", "active", "2/10 NET30",
     "GBP", "GB1234567", "GB29NWBK60161331926819", False),
    ("V-101", "Ashfield Logistics PLC", "Ashfield", "active", "NET45",
     "GBP", "GB2345678", "GB94BARC10201530093459", False),
    ("V-102", "Corvin Industrial GmbH", "Corvin", "hold", "NET30",
     "EUR", "DE811234567", "DE89370400440532013000", False),
    ("V-103", "Delmar Packaging Ltd", "Delmar", "active", "NET15",
     "GBP", "GB3456789", "GB33BUKB20201555555555", False),
    ("V-104", "Northgate Fasteners Ltd", "Northgate", "active", "NET30",
     "GBP", "GB4567890", "GB12CPBK08920112345678", False),
    ("V-105", "Meridian Consulting LLP", "Meridian", "active", "NET30",
     "GBP", "GB5678901", "GB16MIDL40051512345678", False),
    ("V-106", "Halcyon Energy Supply", "Halcyon", "active", "NET60",
     "GBP", "GB6789012", "GB55HLFX11011111111111", False),
    ("V-107", "Kestrel Components SA", "Kestrel", "active", "NET30",
     "EUR", "FR12345678901", "FR1420041010050500013M02606", False),
    # Duplicate vendor record: different id, SAME bank account as V-100.
    # A classic fraud and overpayment vector that only a bank-account check finds.
    ("V-108", "Ridgeline Components", "Ridgeline Co", "active", "NET30",
     "GBP", "GB1234567", "GB29NWBK60161331926819", False),
    ("V-109", "Volkov Trading OOO", "Volkov", "active", "NET30",
     "USD", "RU7712345678", "RU0204452560040702810412345678901", True),
]

#: po_id, vendor, currency, status, cost_centre, category, created, expires
PURCHASE_ORDERS = [
    ("PO-5001", "V-100", "GBP", "open",   "CC-MFG", "manufactured", "2026-06-01", "2026-12-31"),
    ("PO-5002", "V-101", "GBP", "open",   "CC-LOG", "freight",      "2026-06-01", "2026-12-31"),
    ("PO-5003", "V-102", "EUR", "open",   "CC-MFG", "manufactured", "2026-06-05", "2026-12-31"),
    ("PO-5004", "V-103", "GBP", "open",   "CC-PKG", "commodity",    "2026-06-10", "2026-12-31"),
    ("PO-5005", "V-104", "GBP", "open",   "CC-MFG", "commodity",    "2026-06-10", "2026-12-31"),
    ("PO-5006", "V-100", "GBP", "open",   "CC-MFG", "manufactured", "2026-06-15", "2026-12-31"),
    ("PO-5007", "V-105", "GBP", "open",   "CC-ADM", "services",     "2026-06-20", "2026-12-31"),
    ("PO-5008", "V-106", "GBP", "open",   "CC-FAC", "utilities",    "2026-01-01", "2026-12-31"),
    ("PO-5009", "V-107", "EUR", "open",   "CC-MFG", "manufactured", "2026-07-01", "2026-12-31"),
    ("PO-5010", "V-100", "GBP", "closed", "CC-MFG", "manufactured", "2026-02-01", "2026-05-31"),
    ("PO-5011", "V-104", "GBP", "open",   "CC-MFG", "commodity",    "2026-06-25", "2026-12-31"),
    ("PO-5012", "V-105", "GBP", "open",   "CC-ADM", "services",     "2026-07-01", "2026-12-31"),
]

#: po_id, line_no, sku, description, unit_price, qty, tax_code
PO_LINES = [
    ("PO-5001", 1, "RC-88",  "Bracket assembly, steel",     120.50,   40, "S20"),
    ("PO-5001", 2, "RC-88F", "Fixing kit for RC-88",          8.25,   40, "S20"),
    ("PO-5002", 1, "AL-FRT", "Palletised freight, zone 2",   15.00,  300, "S20"),
    ("PO-5003", 1, "CI-VAL", "Control valve DN50",          890.00,    6, "S20"),
    ("PO-5004", 1, "DP-BOX", "Corrugated case 400x300",       2.35, 5000, "S20"),
    ("PO-5005", 1, "NF-M8",  "Hex bolt M8x40 zinc",           0.42, 20000, "S20"),
    ("PO-5006", 1, "RC-90",  "Housing, machined alloy",     310.00,   25, "S20"),
    ("PO-5007", 1, "MC-ADV", "Advisory, senior consultant",  950.00,   12, "S20"),
    ("PO-5008", 1, "HE-KWH", "Electricity supply, kWh",       0.2840, 50000, "R05"),
    ("PO-5009", 1, "KC-BRG", "Precision bearing 6204",       14.60,  800, "RC0"),
    ("PO-5010", 1, "RC-77",  "Legacy spacer",                45.00,  100, "S20"),
    ("PO-5011", 1, "NF-M10", "Hex bolt M10x50 zinc",          0.68, 15000, "S20"),
    ("PO-5012", 1, "MC-IMP", "Implementation, partner rate", 2800.00,  20, "S20"),
]

#: gr_id, po_id, line_no, qty_received, received_on
GOODS_RECEIPTS = [
    ("GR-9001", "PO-5001", 1, 40,    "2026-06-20"),
    ("GR-9002", "PO-5001", 2, 40,    "2026-06-20"),
    ("GR-9003", "PO-5002", 1, 300,   "2026-06-22"),
    ("GR-9004", "PO-5003", 1, 6,     "2026-06-25"),
    ("GR-9005", "PO-5004", 1, 4500,  "2026-06-28"),   # short shipment
    ("GR-9006", "PO-5005", 1, 20000, "2026-06-28"),
    ("GR-9007", "PO-5006", 1, 25,    "2026-07-01"),
    ("GR-9008", "PO-5007", 1, 12,    "2026-07-05"),
    ("GR-9009", "PO-5008", 1, 48200, "2026-07-01"),
    ("GR-9010", "PO-5009", 1, 800,   "2026-07-08"),
    ("GR-9011", "PO-5011", 1, 15000, "2026-07-10"),
    ("GR-9012", "PO-5012", 1, 8,     "2026-07-12"),   # partial: 8 of 20 days
]

#: invoice_id, vendor, po_id, invoice_number, doc_type, currency, invoice_date,
#: received_on, freight, references_invoice
#: Prior-period invoices, already settled. These are payment HISTORY, not work.
#: They are excluded from the task set; they exist so that the duplicate cases
#: have a genuine settled invoice to duplicate rather than duplicating a task.
HISTORICAL = {"INV-7901", "INV-7902"}

INVOICES = [
    ("INV-7901", "V-100", "PO-5001", "R-2026-4471", "invoice", "GBP",
     "2026-05-20", "2026-05-25", 0.00, None),
    ("INV-7902", "V-100", "PO-5006", "R-2026-4520", "invoice", "GBP",
     "2026-06-18", "2026-06-22", 0.00, None),
    # 1. clean single-category multi-line, under approval band
    ("INV-8001", "V-100", "PO-5001", "R-2026-4610", "invoice", "GBP",
     "2026-06-25", "2026-07-02", 0.00, None),
    # 2. clean, freight category, tolerance-relevant
    ("INV-8002", "V-101", "PO-5002", "ASH-88213", "invoice", "GBP",
     "2026-06-26", "2026-07-03", 0.00, None),
    # 3. vendor on hold
    ("INV-8003", "V-102", "PO-5003", "CI-2026-0912", "invoice", "EUR",
     "2026-06-28", "2026-07-05", 0.00, None),
    # 4. short shipment: billed 5000, received 4500
    ("INV-8004", "V-103", "PO-5004", "DP-77120", "invoice", "GBP",
     "2026-06-30", "2026-07-06", 0.00, None),
    # 5. price variance 14.3% on a COMMODITY line (tolerance 3%) -> outside
    ("INV-8005", "V-104", "PO-5005", "NF-2026-551", "invoice", "GBP",
     "2026-07-01", "2026-07-08", 0.00, None),
    # 6. clean, sits in the controller approval band
    ("INV-8006", "V-100", "PO-5006", "R-2026-4655", "invoice", "GBP",
     "2026-07-02", "2026-07-09", 0.00, None),
    # 7. exact duplicate of INV-8001, already paid
    ("INV-8007", "V-100", "PO-5001", "R-2026-4471", "invoice", "GBP",
     "2026-06-25", "2026-07-14", 0.00, None),
    # 8. PROBABLE duplicate: a different invoice number entirely, but the same
    #    purchase order and the same amount as one already paid. Confidence 0.75.
    #    The agent has to decide what to do with a score rather than a boolean.
    ("INV-8008", "V-100", "PO-5006", "RID-JUL-0093", "invoice", "GBP",
     "2026-07-02", "2026-07-16", 0.00, None),
    # 9. services line, 2.6% price variance. Tolerance for services is ZERO.
    ("INV-8009", "V-105", "PO-5007", "MER-2026-118", "invoice", "GBP",
     "2026-07-06", "2026-07-12", 0.00, None),
    # 10. commodity line, 2.4% variance, INSIDE the 3% commodity tolerance
    ("INV-8010", "V-104", "PO-5011", "NF-2026-559", "invoice", "GBP",
     "2026-07-11", "2026-07-17", 0.00, None),
    # 11. EUR invoice against EUR PO, FX moved 2.3% between PO and invoice date
    ("INV-8011", "V-107", "PO-5009", "KES-2026-3391", "invoice", "EUR",
     "2026-07-15", "2026-07-20", 0.00, None),
    # 12. wrong tax code: reduced rate applied to a standard-rated supply
    ("INV-8012", "V-106", "PO-5008", "HAL-2026-07", "invoice", "GBP",
     "2026-07-05", "2026-07-11", 0.00, None),
    # 13. credit note against INV-8001
    ("INV-8013", "V-100", "PO-5001", "R-2026-CN-119", "credit_note", "GBP",
     "2026-07-10", "2026-07-15", 0.00, "INV-8001"),
    # 14. PO expired and closed
    ("INV-8014", "V-100", "PO-5010", "R-2026-4102", "invoice", "GBP",
     "2026-07-08", "2026-07-14", 0.00, None),
    # 15. freight charge not present on the PO
    ("INV-8015", "V-103", "PO-5004", "DP-77245", "invoice", "GBP",
     "2026-07-12", "2026-07-18", 340.00, None),
    # 16. no PO reference at all, low value
    ("INV-8016", "V-101", None, "ASH-88999", "invoice", "GBP",
     "2026-07-14", "2026-07-19", 0.00, None),
    # 17. duplicate vendor record, same bank account as V-100
    ("INV-8017", "V-108", "PO-5006", "RID-2026-77", "invoice", "GBP",
     "2026-07-15", "2026-07-20", 0.00, None),
    # 18. sanctioned counterparty
    ("INV-8018", "V-109", None, "VLK-2026-001", "invoice", "USD",
     "2026-07-14", "2026-07-19", 0.00, None),
    # 19. partial delivery: billed for the 8 days actually received
    ("INV-8019", "V-105", "PO-5012", "MER-2026-140", "invoice", "GBP",
     "2026-07-16", "2026-07-21", 0.00, None),
    # 20. invoice dated BEFORE its purchase order
    ("INV-8020", "V-104", "PO-5011", "NF-2026-540", "invoice", "GBP",
     "2026-06-20", "2026-07-20", 0.00, None),
]

#: invoice_id, line_no, sku, description, unit_price, qty, tax_code
INVOICE_LINES = [
    ("INV-7901", 1, "RC-88",  "Bracket assembly, steel",    120.50,   40, "S20"),
    ("INV-7901", 2, "RC-88F", "Fixing kit for RC-88",         8.25,   40, "S20"),
    ("INV-7902", 1, "RC-90",  "Housing, machined alloy",    310.00,   25, "S20"),
    ("INV-8001", 1, "RC-88",  "Bracket assembly, steel",    120.50,   35, "S20"),
    ("INV-8001", 2, "RC-88F", "Fixing kit for RC-88",         8.25,   35, "S20"),
    ("INV-8002", 1, "AL-FRT", "Palletised freight, zone 2",  15.00,  250, "S20"),
    ("INV-8003", 1, "CI-VAL", "Control valve DN50",         890.00,    6, "S20"),
    ("INV-8004", 1, "DP-BOX", "Corrugated case 400x300",      2.35, 5000, "S20"),
    ("INV-8005", 1, "NF-M8",  "Hex bolt M8x40 zinc",          0.48, 20000, "S20"),
    ("INV-8006", 1, "RC-90",  "Housing, machined alloy",    310.00,   20, "S20"),
    ("INV-8007", 1, "RC-88",  "Bracket assembly, steel",    120.50,   40, "S20"),
    ("INV-8007", 2, "RC-88F", "Fixing kit for RC-88",         8.25,   40, "S20"),
    ("INV-8008", 1, "RC-90",  "Housing, machined alloy",    310.00,   25, "S20"),
    ("INV-8009", 1, "MC-ADV", "Advisory, senior consultant", 975.00,   12, "S20"),
    ("INV-8010", 1, "NF-M10", "Hex bolt M10x50 zinc",         0.6963, 15000, "S20"),
    ("INV-8011", 1, "KC-BRG", "Precision bearing 6204",      14.60,  800, "RC0"),
    ("INV-8012", 1, "HE-KWH", "Electricity supply, kWh",      0.2840, 48200, "S20"),
    ("INV-8013", 1, "RC-88F", "Credit: fixing kit shortfall",  8.25,  -10, "S20"),
    ("INV-8014", 1, "RC-77",  "Legacy spacer",               45.00,  100, "S20"),
    ("INV-8015", 1, "DP-BOX", "Corrugated case 400x300",      2.35, 4500, "S20"),
    ("INV-8016", 1, "MISC",   "Ad-hoc courier, urgent",     180.00,    1, "S20"),
    ("INV-8017", 1, "RC-90",  "Housing, machined alloy",    310.00,   25, "S20"),
    ("INV-8018", 1, "VLK-01", "Industrial consumables",     420.00,   30, "S20"),
    ("INV-8019", 1, "MC-IMP", "Implementation, partner rate", 2800.00,   8, "S20"),
    ("INV-8020", 1, "NF-M10", "Hex bolt M10x50 zinc",         0.68, 15000, "S20"),
]

#: payment_id, invoice_id, vendor_id, amount_gbp, paid_on, method
PAYMENTS = [
    ("PAY-3001", "INV-7901", "V-100", 6077.00, "2026-06-01", "bacs"),
    ("PAY-3002", "INV-7902", "V-100", 9145.00, "2026-06-28", "bacs"),
]

SCHEMA = """
CREATE TABLE vendors (
    vendor_id TEXT PRIMARY KEY, legal_name TEXT, trading_name TEXT,
    status TEXT, payment_terms TEXT, currency TEXT, tax_id TEXT,
    bank_iban TEXT, sanctioned INTEGER);
CREATE TABLE purchase_orders (
    po_id TEXT PRIMARY KEY, vendor_id TEXT, currency TEXT, status TEXT,
    cost_centre TEXT, category TEXT, created_on TEXT, expires_on TEXT);
CREATE TABLE po_lines (
    po_id TEXT, line_no INTEGER, sku TEXT, description TEXT,
    unit_price REAL, qty INTEGER, tax_code TEXT, PRIMARY KEY (po_id, line_no));
CREATE TABLE goods_receipts (
    gr_id TEXT PRIMARY KEY, po_id TEXT, line_no INTEGER,
    qty_received INTEGER, received_on TEXT);
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY, vendor_id TEXT, po_id TEXT,
    invoice_number TEXT, doc_type TEXT, currency TEXT, invoice_date TEXT,
    received_on TEXT, freight REAL, references_invoice TEXT);
CREATE TABLE invoice_lines (
    invoice_id TEXT, line_no INTEGER, sku TEXT, description TEXT,
    unit_price REAL, qty INTEGER, tax_code TEXT,
    PRIMARY KEY (invoice_id, line_no));
CREATE TABLE payments (
    payment_id TEXT PRIMARY KEY, invoice_id TEXT, vendor_id TEXT,
    amount_gbp REAL, paid_on TEXT, method TEXT);
CREATE TABLE exceptions (
    exception_id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT,
    reason_code TEXT, detail TEXT, raised_on TEXT);
CREATE TABLE approvals (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT,
    approver_role TEXT, amount_gbp REAL, second_signature INTEGER);
CREATE TABLE credit_applications (
    application_id INTEGER PRIMARY KEY AUTOINCREMENT, credit_note_id TEXT,
    against_invoice TEXT, amount_gbp REAL);
CREATE TABLE audit_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT,
    action TEXT, detail TEXT);
"""


def fresh_db() -> sqlite3.Connection:
    """A new in-memory system of record, seeded to the starting state.

    One connection per trial. Trials must not observe each other's writes: a
    payment scheduled in trial 3 would turn trial 4 into a duplicate case and
    the study would silently measure the wrong thing.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,?,?)", VENDORS)
    conn.executemany("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?)", PURCHASE_ORDERS)
    conn.executemany("INSERT INTO po_lines VALUES (?,?,?,?,?,?,?)", PO_LINES)
    conn.executemany("INSERT INTO goods_receipts VALUES (?,?,?,?,?)", GOODS_RECEIPTS)
    conn.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?)", INVOICES)
    conn.executemany("INSERT INTO invoice_lines VALUES (?,?,?,?,?,?,?)", INVOICE_LINES)
    conn.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", PAYMENTS)
    conn.commit()
    return conn


# --------------------------------------------------------------------------
# Reason codes. A typed taxonomy rather than free text, because an exception
# a downstream system cannot route on is just a note.
# --------------------------------------------------------------------------
REASON_CODES = {
    "PRICE_VARIANCE":      "unit price outside tolerance for the spend category",
    "QTY_OVER_RECEIPT":    "quantity billed exceeds quantity received",
    "QTY_OVER_ORDER":      "quantity billed exceeds quantity ordered",
    "DUPLICATE_EXACT":     "identical invoice already paid",
    "DUPLICATE_FUZZY":     "probable duplicate of an invoice already paid",
    "DUPLICATE_VENDOR":    "vendor shares bank details with another record",
    "VENDOR_HOLD":         "vendor is on payment hold",
    "VENDOR_SANCTIONED":   "vendor appears on a sanctions list",
    "NO_PO":               "no purchase order referenced",
    "PO_CLOSED":           "purchase order is closed or expired",
    "PO_DATE":             "invoice predates its purchase order",
    "TAX_CODE_MISMATCH":   "tax code differs from the purchase order",
    "FX_VARIANCE":         "exchange rate variance outside tolerance",
    "UNAUTHORISED_FREIGHT": "freight charged but not on the purchase order",
    "MISSING_APPROVAL":    "required approval not obtained",
}
