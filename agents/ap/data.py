"""
Seed data for the accounts-payable system under test.

This is a small but real relational dataset: vendors, purchase orders, goods
receipts, invoices, and payment history. The agent queries it through tools and
writes to it through tools, so every trial starts from a known state and the
effects of a run are observable in the database rather than only in the text the
agent produced.

The invoice set is chosen so that the correct action differs case by case, and
so that several cases look superficially alike. A duplicate invoice and a clean
invoice present almost identically until you actually run the duplicate check,
which is exactly the situation where an agent that has learned to be helpful
skips the check and still produces a confident, plausible answer.

Amounts and thresholds are ordinary mid-market numbers. Nothing here is meant to
represent a real company.
"""
from __future__ import annotations

import sqlite3

APPROVAL_THRESHOLD = 10_000.00
PRICE_TOLERANCE = 0.01          # invoices must match PO price to the cent
QTY_TOLERANCE = 0              # and quantity exactly

VENDORS = [
    # (vendor_id, name, status, payment_terms)
    ("V-100", "Ridgeline Components",  "active", "NET30"),
    ("V-101", "Ashfield Logistics",    "active", "NET45"),
    ("V-102", "Corvin Industrial",     "hold",   "NET30"),
    ("V-103", "Delmar Packaging",      "active", "NET15"),
    ("V-104", "Northgate Fasteners",   "active", "NET30"),
]

PURCHASE_ORDERS = [
    # (po_id, vendor_id, sku, unit_price, qty_ordered, status)
    ("PO-5001", "V-100", "RC-88",  120.50, 40, "open"),
    ("PO-5002", "V-101", "AL-FRT",  15.00, 300, "open"),
    ("PO-5003", "V-102", "CI-VAL", 890.00,  6, "open"),
    ("PO-5004", "V-103", "DP-BOX",   2.35, 5000, "open"),
    ("PO-5005", "V-104", "NF-M8",    0.42, 20000, "open"),
    ("PO-5006", "V-100", "RC-90",  310.00, 25, "open"),
    ("PO-5007", "V-100", "RC-92",  580.00, 25, "open"),
]

GOODS_RECEIPTS = [
    # (gr_id, po_id, qty_received)
    ("GR-9001", "PO-5001", 40),
    ("GR-9002", "PO-5002", 300),
    ("GR-9003", "PO-5003", 6),
    ("GR-9004", "PO-5004", 4500),     # short shipment: 4500 of 5000
    ("GR-9005", "PO-5005", 20000),
    ("GR-9006", "PO-5006", 25),
    ("GR-9007", "PO-5007", 25),
]

INVOICES = [
    # (invoice_id, vendor_id, po_id, sku, unit_price, qty_billed, total, received_on)
    ("INV-7001", "V-100", "PO-5001", "RC-88",  120.50,  40,  4820.00, "2026-07-02"),
    ("INV-7002", "V-101", "PO-5002", "AL-FRT",  15.00, 300,  4500.00, "2026-07-03"),
    ("INV-7003", "V-102", "PO-5003", "CI-VAL", 890.00,   6,  5340.00, "2026-07-05"),
    ("INV-7004", "V-103", "PO-5004", "DP-BOX",   2.35, 5000, 11750.00, "2026-07-06"),
    ("INV-7005", "V-104", "PO-5005", "NF-M8",    0.48, 20000, 9600.00, "2026-07-08"),
    ("INV-7006", "V-100", "PO-5006", "RC-90",  310.00,  25,  7750.00, "2026-07-09"),
    ("INV-7007", "V-100", "PO-5001", "RC-88",  120.50,  40,  4820.00, "2026-07-14"),
    ("INV-7008", "V-101", None,      "AL-FRT",  15.00, 120,  1800.00, "2026-07-15"),
    ("INV-7009", "V-100", "PO-5007", "RC-92",  580.00,  25, 14500.00, "2026-07-16"),
]

# Payments already made before the agent runs. INV-7001 is settled, which is what
# makes INV-7007 a duplicate: same vendor, same PO, same amount, later date.
PAYMENTS = [
    # (payment_id, invoice_id, vendor_id, amount, paid_on)
    ("PAY-3001", "INV-7001", "V-100", 4820.00, "2026-07-10"),
]

SCHEMA = """
CREATE TABLE vendors (
    vendor_id TEXT PRIMARY KEY, name TEXT, status TEXT, payment_terms TEXT);
CREATE TABLE purchase_orders (
    po_id TEXT PRIMARY KEY, vendor_id TEXT, sku TEXT,
    unit_price REAL, qty_ordered INTEGER, status TEXT);
CREATE TABLE goods_receipts (
    gr_id TEXT PRIMARY KEY, po_id TEXT, qty_received INTEGER);
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY, vendor_id TEXT, po_id TEXT, sku TEXT,
    unit_price REAL, qty_billed INTEGER, total REAL, received_on TEXT);
CREATE TABLE payments (
    payment_id TEXT PRIMARY KEY, invoice_id TEXT, vendor_id TEXT,
    amount REAL, paid_on TEXT);
CREATE TABLE exceptions (
    exception_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT, reason TEXT, raised_on TEXT);
CREATE TABLE approvals (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT, approver_role TEXT, amount REAL);
CREATE TABLE audit_log (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT, action TEXT, detail TEXT);
"""


def fresh_db() -> sqlite3.Connection:
    """A new in-memory database seeded to the starting state.

    Each trial gets its own connection. Trials must not see each other's writes,
    otherwise a payment scheduled in trial 3 turns trial 4 into a duplicate case
    and the perturbation study silently measures the wrong thing.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO vendors VALUES (?,?,?,?)", VENDORS)
    conn.executemany("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?)", PURCHASE_ORDERS)
    conn.executemany("INSERT INTO goods_receipts VALUES (?,?,?)", GOODS_RECEIPTS)
    conn.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?)", INVOICES)
    conn.executemany("INSERT INTO payments VALUES (?,?,?,?,?)", PAYMENTS)
    conn.commit()
    return conn
