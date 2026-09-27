import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "gstflow.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    gstin TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER,
    source_filename TEXT NOT NULL,
    supplier_name TEXT,
    gstin TEXT,
    invoice_number TEXT,
    invoice_date TEXT,
    taxable_value REAL,
    cgst REAL,
    sgst REAL,
    igst REAL,
    total_amount REAL,
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'needs_review',
    review_reasons TEXT,
    verification_checks TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by INTEGER,
    reviewed_at TEXT,
    review_notes TEXT,
    raw_text TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (business_id) REFERENCES businesses(id)
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    serial_no INTEGER,
    description TEXT,
    hsn_sac TEXT,
    quantity REAL,
    rate REAL,
    taxable_value REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_invoices_business
    ON invoices(business_id);

CREATE INDEX IF NOT EXISTS idx_invoices_gstin
    ON invoices(gstin);

CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice
    ON invoice_items(invoice_id);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)

        # ----------------------------------------------------
        # Invoice classification migration
        #
        # Existing databases may already have the invoices
        # table without invoice_type.
        # Add it only when it does not exist.
        # ----------------------------------------------------

        invoice_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(invoices)"
            ).fetchall()
        }

        if "invoice_type" not in invoice_columns:
            conn.execute(
                """
                ALTER TABLE invoices
                ADD COLUMN invoice_type TEXT
                """
            )

        conn.commit()