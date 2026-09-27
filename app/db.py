import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "gstflow.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'STAFF',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_login TEXT,
    UNIQUE (organization_id, username),
    FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    gstin TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organization_id, gstin),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS user_business_access (
    user_id INTEGER NOT NULL,
    business_id INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, business_id),
    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,
    FOREIGN KEY (business_id)
        REFERENCES businesses(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER,
    organization_id INTEGER,
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
    invoice_type TEXT,
    uploaded_by INTEGER,
    FOREIGN KEY (business_id) REFERENCES businesses(id),
    FOREIGN KEY (reviewed_by) REFERENCES users(id),
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
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

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT,
    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    user_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id)
        REFERENCES organizations(id)
        ON DELETE CASCADE,
    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_invoices_business
    ON invoices(business_id);

CREATE INDEX IF NOT EXISTS idx_invoices_gstin
    ON invoices(gstin);

CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice
    ON invoice_items(invoice_id);

CREATE INDEX IF NOT EXISTS idx_invoices_org_status
    ON invoices(organization_id, status);

CREATE INDEX IF NOT EXISTS idx_invoices_review_status
    ON invoices(review_status);

CREATE INDEX IF NOT EXISTS idx_invoices_created_at
    ON invoices(created_at);
"""
# DB-01 correction: this SCHEMA now declares all 8 application tables
# (previously only businesses/invoices/invoice_items were here; the
# other 5 existed solely in scripts/migrate_multitenant.py, which is
# why a fresh install that only ran init_db() -- as app/main.py does
# automatically at import time -- crashed on first registration).
#
# The five newly-added tables (organizations, users,
# user_business_access, sessions, activity_logs) are copied verbatim
# from scripts/migrate_multitenant.py's own CREATE TABLE statements --
# same columns, constraints, defaults, and foreign keys, not
# redesigned. That script is still the source of truth for those
# definitions and remains the idempotent upgrade path for any
# pre-existing database that predates this fix; because every
# statement here is CREATE TABLE IF NOT EXISTS, running it against
# such a database simply finds the tables already present (created
# previously by scripts/migrate_multitenant.py) and does nothing.
#
# Statement order matters: every table is declared strictly after the
# tables its own FOREIGN KEY clauses reference (organizations -> users
# -> businesses -> user_business_access -> invoices -> invoice_items
# -> sessions -> activity_logs), so nothing here relies on SQLite's
# forward-reference allowance the way the previous version of this
# file did.
#
# scripts/migrate_multitenant.py's own rebuild/migration logic for
# upgrading a pre-Milestone-2 businesses/invoices shape in place is
# untouched and still lives only in that script, not here.


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Milestone 2 (TEN-02): PRAGMA foreign_keys is a per-connection
    # setting in SQLite, never persisted in the database file itself.
    # Previously this was only ever set on the one-off connection
    # opened inside init_db(), so every other connection opened via
    # this function (all 21+ call sites across the app) ran with FK
    # enforcement OFF. Setting it here means every connection the
    # application ever opens enforces declared foreign keys.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
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

        # ----------------------------------------------------
        # Milestone 2: uploaded_by (maker-checker foundation)
        #
        # Existing databases may already have the invoices table
        # without uploaded_by. Add it only when it does not exist.
        # SQLite allows a REFERENCES clause on a column added via
        # ALTER TABLE ADD COLUMN as long as its default is NULL (which
        # it is here), so this is a safe, additive change even against
        # a table that already has rows -- every existing row simply
        # gets uploaded_by = NULL, which always satisfies the FK.
        # ----------------------------------------------------

        if "uploaded_by" not in invoice_columns:
            conn.execute(
                """
                ALTER TABLE invoices
                ADD COLUMN uploaded_by INTEGER
                REFERENCES users(id)
                """
            )

        conn.commit()
