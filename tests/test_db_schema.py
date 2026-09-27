"""
Milestone 2 tests: database schema completion & integrity hardening.

These tests cover exactly the behaviors called out in the Milestone 2
implementation plan: app.db.init_db() ALONE (a DB-01 regression guard
-- see below) creates the complete 8-table schema, foreign-key
enforcement applies to every connection (not just the one used to
build the schema), GSTIN uniqueness is scoped per organization, the
reviewed_by/uploaded_by additions behave correctly, the in-place
migration for pre-existing ("legacy-shaped") databases is idempotent
and preserves all existing ids/data, and the migration aborts --
rather than guesses -- when it finds legacy data that would be unsafe
to migrate automatically (Risk 1 / Risk 2 decisions).

DB-01 correction: app.db.SCHEMA previously declared only
businesses/invoices/invoice_items; the other 5 application tables
existed solely in scripts/migrate_multitenant.py, so init_db() alone
(which is all app/main.py calls automatically at import time) did not
produce a working schema. SCHEMA now declares all 8 tables --
migrate_multitenant.py remains the idempotent upgrade path for
pre-existing databases and its own rebuild/migration logic for the
businesses/invoices shape is unchanged and untouched.

All tests use only disposable, per-test SQLite files under pytest's
own tmp_path (via the `db_conn`/`test_db_path` fixtures from
tests/conftest.py, or a raw connection to test_db_path built by hand
for tests that specifically need to simulate a *pre-Milestone-2*
("legacy") database shape). The real application database
(data/gstflow.db) is never opened, created, or modified by anything in
this file.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
import scripts.migrate_multitenant as migrate_module
from scripts.migrate_multitenant import (
    MigrationAborted,
    businesses_has_old_unique_constraint,
    invoices_has_reviewed_by_fk,
)


# ============================================================
# Helpers for simulating a pre-Milestone-2 ("legacy") database
# ============================================================

def _seed_legacy_database(path, business_rows=(), invoice_rows=(), user_rows=()):
    """
    Build a standalone SQLite file at `path` matching the exact
    pre-Milestone-2 shape this migration is meant to upgrade:
    already multi-tenant-migrated (organizations/users/etc. exist,
    businesses/invoices already have an organization_id column), but
    still missing the Milestone 2 changes -- `businesses.gstin` is
    still a bare UNIQUE (not scoped per organization), and
    `invoices.reviewed_by` has no declared foreign key.

    `business_rows`: iterable of (id, organization_id, name, gstin).
    `invoice_rows`: iterable of
        (id, business_id, organization_id, source_filename, reviewed_by).
    `user_rows`: iterable of (id, organization_id, username).

    Foreign key enforcement is deliberately left OFF for this raw
    seeding connection, since some tests intentionally seed invalid
    legacy data (e.g. an orphaned reviewed_by) to prove the migration
    detects and aborts on it.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'STAFF',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_login TEXT,
                UNIQUE (organization_id, username)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE user_business_access (
                user_id INTEGER NOT NULL,
                business_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, business_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                user_id INTEGER,
                action TEXT NOT NULL,
                entity_type TEXT,
                entity_id INTEGER,
                details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Pre-Milestone-2 shape: bare UNIQUE(gstin), organization_id
        # already present (added by an earlier migrate run) but NOT
        # part of any composite uniqueness.
        conn.execute(
            """
            CREATE TABLE businesses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                gstin TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                organization_id INTEGER
            )
            """
        )
        # Pre-Milestone-2 shape: reviewed_by exists but has no FK.
        conn.execute(
            """
            CREATE TABLE invoices (
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
                organization_id INTEGER,
                invoice_type TEXT,
                uploaded_by INTEGER,
                FOREIGN KEY (business_id) REFERENCES businesses(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE invoice_items (
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
            )
            """
        )

        conn.commit()

        for user_id, organization_id, username in user_rows:
            conn.execute(
                "INSERT INTO users (id, organization_id, username, password_hash) "
                "VALUES (?, ?, ?, 'x')",
                (user_id, organization_id, username),
            )

        for biz_id, organization_id, name, gstin in business_rows:
            conn.execute(
                "INSERT INTO businesses (id, organization_id, name, gstin) "
                "VALUES (?, ?, ?, ?)",
                (biz_id, organization_id, name, gstin),
            )

        for inv_id, business_id, organization_id, source_filename, reviewed_by in invoice_rows:
            conn.execute(
                "INSERT INTO invoices "
                "(id, business_id, organization_id, source_filename, reviewed_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (inv_id, business_id, organization_id, source_filename, reviewed_by),
            )

        conn.commit()
    finally:
        conn.close()


def _seed_organizations(path, organization_ids):
    conn = sqlite3.connect(path)
    try:
        for org_id in organization_ids:
            conn.execute(
                "INSERT INTO organizations (id, name) VALUES (?, ?)",
                (org_id, f"Org {org_id}"),
            )
        conn.commit()
    finally:
        conn.close()


def _run_migration(test_db_path, monkeypatch):
    monkeypatch.setattr(migrate_module, "DB_PATH", test_db_path)
    migrate_module.main()


# ============================================================
# 1. Fresh database -- DB-01 regression guard
#
# These two tests deliberately do NOT use the db_conn/client
# fixtures (which build the schema via init_db() + migrate_
# multitenant.main() together). They call app.db.init_db() ALONE,
# against a genuinely fresh file, and prove that is now sufficient
# by itself -- matching what app/main.py actually does automatically
# at import time in a real deployment, which is the exact scenario
# DB-01 originally described as crashing. A canary monkeypatch on
# migrate_multitenant.main fails the test outright if anything in
# init_db()'s call path ever tries to invoke it.
# ============================================================

def _forbid_migrate_module_main(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError(
            "migrate_multitenant.main() must not be called by this "
            "test -- init_db() alone must be sufficient."
        )

    monkeypatch.setattr(migrate_module, "main", _fail_if_called)


def test_fresh_db_creates_all_eight_tables(test_db_path, monkeypatch):
    assert not test_db_path.exists()

    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    _forbid_migrate_module_main(monkeypatch)

    db_module.init_db()

    assert test_db_path.exists()

    conn = sqlite3.connect(test_db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    tables = {row[0] for row in rows}

    expected_application_tables = {
        "organizations",
        "users",
        "businesses",
        "user_business_access",
        "invoices",
        "invoice_items",
        "sessions",
        "activity_logs",
    }
    assert len(expected_application_tables) == 8

    # sqlite_sequence is SQLite's own internal bookkeeping table,
    # auto-created because of AUTOINCREMENT usage -- it is not one of
    # the application's 8 tables and must be excluded, not counted,
    # when checking for an exact match.
    application_tables = tables - {"sqlite_sequence"}
    assert application_tables == expected_application_tables


def test_init_db_alone_supports_registration_end_to_end(test_db_path, monkeypatch):
    """
    Proves init_db() alone produces a schema sufficient to complete a
    full registration round trip through the real HTTP app -- the
    exact operation DB-01 originally described as crashing on a fresh
    install/clone. Imports (and reloads, in case an earlier test
    already cached it) app.main, which calls init_db() once at module
    level exactly as it does in real production startup, and never
    calls migrate_multitenant.main() at all.
    """
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    _forbid_migrate_module_main(monkeypatch)

    import importlib
    import app.main as main_module
    # Force app.main's top-level init_db() call to actually re-run
    # against this test's freshly-patched DB_PATH, regardless of
    # whether an earlier test already imported (and cached) it.
    importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        response = test_client.post(
            "/api/register",
            data={
                "organization_name": "DB01 Regression Org",
                "username": "db01_owner",
                "password": "Db01TestPassword1!",
                "confirm_password": "Db01TestPassword1!",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["role"] == "OWNER"


# ============================================================
# 2. Foreign-key enforcement on every connection
# ============================================================

def test_foreign_keys_enforced_on_every_connection(db_conn):
    # Deliberately open a SECOND, independent connection rather than
    # reusing db_conn -- this is what actually exercises the
    # app/db.py connect() fix, since a connection that happened to
    # inherit enforcement from whatever built the schema would still
    # pass under the old, broken code.
    second_conn = db_module.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            second_conn.execute(
                "INSERT INTO invoices (business_id, source_filename) "
                "VALUES (999999, 'test.pdf')"
            )
    finally:
        second_conn.close()


# ============================================================
# 3. Composite GSTIN uniqueness
# ============================================================

def test_composite_gstin_uniqueness_across_organizations(db_conn):
    org1 = db_conn.execute(
        "INSERT INTO organizations (name) VALUES ('Org One')"
    ).lastrowid
    org2 = db_conn.execute(
        "INSERT INTO organizations (name) VALUES ('Org Two')"
    ).lastrowid

    db_conn.execute(
        "INSERT INTO businesses (organization_id, name, gstin) VALUES (?, ?, ?)",
        (org1, "Biz One", "27ABCDE1234F1Z5"),
    )
    db_conn.execute(
        "INSERT INTO businesses (organization_id, name, gstin) VALUES (?, ?, ?)",
        (org2, "Biz Two", "27ABCDE1234F1Z5"),
    )
    db_conn.commit()

    count = db_conn.execute(
        "SELECT COUNT(*) FROM businesses WHERE gstin = ?",
        ("27ABCDE1234F1Z5",),
    ).fetchone()[0]
    assert count == 2


def test_composite_gstin_uniqueness_within_organization(db_conn):
    org1 = db_conn.execute(
        "INSERT INTO organizations (name) VALUES ('Org One')"
    ).lastrowid
    db_conn.execute(
        "INSERT INTO businesses (organization_id, name, gstin) VALUES (?, ?, ?)",
        (org1, "Biz One", "27ABCDE1234F1Z5"),
    )
    db_conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO businesses (organization_id, name, gstin) VALUES (?, ?, ?)",
            (org1, "Biz One Duplicate", "27ABCDE1234F1Z5"),
        )


# ============================================================
# 4. reviewed_by foreign key
# ============================================================

def test_reviewed_by_foreign_key_enforced(db_conn):
    org1 = db_conn.execute(
        "INSERT INTO organizations (name) VALUES ('Org One')"
    ).lastrowid
    db_conn.execute(
        "INSERT INTO invoices (source_filename, organization_id) VALUES (?, ?)",
        ("test.pdf", org1),
    )
    db_conn.commit()

    invoice_id = db_conn.execute(
        "SELECT id FROM invoices WHERE source_filename = 'test.pdf'"
    ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "UPDATE invoices SET reviewed_by = ? WHERE id = ?",
            (999999, invoice_id),
        )


# ============================================================
# 5. uploaded_by
# ============================================================

def test_uploaded_by_column_accepts_valid_user(db_conn):
    org1 = db_conn.execute(
        "INSERT INTO organizations (name) VALUES ('Org One')"
    ).lastrowid
    db_conn.execute(
        "INSERT INTO users (organization_id, username, password_hash, role) "
        "VALUES (?, 'owner1', 'hash', 'OWNER')",
        (org1,),
    )
    user_id = db_conn.execute(
        "SELECT id FROM users WHERE username = 'owner1'"
    ).fetchone()[0]

    db_conn.execute(
        "INSERT INTO invoices (source_filename, organization_id, uploaded_by) "
        "VALUES (?, ?, ?)",
        ("invoice.pdf", org1, user_id),
    )
    db_conn.commit()

    stored = db_conn.execute(
        "SELECT uploaded_by FROM invoices WHERE source_filename = 'invoice.pdf'"
    ).fetchone()[0]
    assert stored == user_id


def test_uploaded_by_foreign_key_enforced(db_conn):
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO invoices (source_filename, uploaded_by) VALUES (?, ?)",
            ("invoice.pdf", 999999),
        )


def test_uploaded_by_defaults_to_null_for_legacy_style_insert(db_conn):
    db_conn.execute(
        "INSERT INTO invoices (source_filename) VALUES (?)",
        ("legacy_invoice.pdf",),
    )
    db_conn.commit()

    stored = db_conn.execute(
        "SELECT uploaded_by FROM invoices WHERE source_filename = 'legacy_invoice.pdf'"
    ).fetchone()[0]
    assert stored is None


# ============================================================
# 6. Index presence
# ============================================================

def test_index_presence(db_conn):
    expected_invoice_indexes = {
        "idx_invoices_business",
        "idx_invoices_gstin",
        "idx_invoices_organization",
        "idx_invoices_org_status",
        "idx_invoices_review_status",
        "idx_invoices_created_at",
    }
    actual_invoice_indexes = {
        row[1] for row in db_conn.execute("PRAGMA index_list(invoices)").fetchall()
    }
    missing = expected_invoice_indexes - actual_invoice_indexes
    assert not missing, f"Missing indexes on invoices: {missing}"

    actual_business_indexes = {
        row[1] for row in db_conn.execute("PRAGMA index_list(businesses)").fetchall()
    }
    assert "idx_businesses_organization" in actual_business_indexes


# ============================================================
# 7. Migration on a pre-existing ("legacy") database:
#    data preservation, idempotency, and the two hard abort
#    conditions from Risk 1 / Risk 2.
# ============================================================

def test_gstin_migration_preserves_existing_ids_and_data(test_db_path, monkeypatch):
    _seed_legacy_database(
        test_db_path,
        business_rows=[
            (10, 1, "Alpha Traders", "27ALPHA0001Z5"),
            (11, 1, "Beta Traders", "27BETA00001Z5"),
        ],
        invoice_rows=[
            (100, 10, 1, "alpha_invoice.pdf", None),
        ],
    )
    _seed_organizations(test_db_path, [1])

    _run_migration(test_db_path, monkeypatch)

    conn = sqlite3.connect(test_db_path)
    try:
        businesses = conn.execute(
            "SELECT id, organization_id, name, gstin FROM businesses ORDER BY id"
        ).fetchall()
        assert businesses == [
            (10, 1, "Alpha Traders", "27ALPHA0001Z5"),
            (11, 1, "Beta Traders", "27BETA00001Z5"),
        ]

        invoice_business_id = conn.execute(
            "SELECT business_id FROM invoices WHERE id = 100"
        ).fetchone()[0]
        assert invoice_business_id == 10
    finally:
        conn.close()

    # Confirm the rebuild actually took effect (not a false-positive
    # "nothing to do" pass-through).
    conn = sqlite3.connect(test_db_path)
    try:
        assert businesses_has_old_unique_constraint(conn) is False
    finally:
        conn.close()


def test_gstin_migration_is_idempotent(test_db_path, monkeypatch):
    _seed_legacy_database(
        test_db_path,
        business_rows=[(10, 1, "Alpha Traders", "27ALPHA0001Z5")],
    )
    _seed_organizations(test_db_path, [1])

    _run_migration(test_db_path, monkeypatch)

    conn = sqlite3.connect(test_db_path)
    try:
        before = conn.execute(
            "SELECT id, organization_id, name, gstin FROM businesses ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    # Running the full migration a second time must not raise and
    # must not change the data or schema any further.
    migrate_module.main()

    conn = sqlite3.connect(test_db_path)
    try:
        after = conn.execute(
            "SELECT id, organization_id, name, gstin FROM businesses ORDER BY id"
        ).fetchall()
        assert after == before
        assert businesses_has_old_unique_constraint(conn) is False
        assert invoices_has_reviewed_by_fk(conn) is True
    finally:
        conn.close()


def test_duplicate_gstin_preflight_check_aborts_on_conflict():
    """
    Unit-tests the `_check_no_duplicate_business_gstins` safety net
    directly, against a hand-built connection that has NO unique
    constraint on `businesses.gstin` at all -- deliberately bypassing
    the ordinary schema, since it is not actually possible to seed two
    rows sharing a `gstin` value under the *real* legacy schema (its
    bare `UNIQUE(gstin)` constraint, present since before Milestone 2,
    already guarantees no duplicate gstin has ever been insertable via
    the application, so this exact state cannot arise through the full
    end-to-end migration pipeline -- this test exists purely to prove
    the safety-net function's own detection logic is correct, as
    defense-in-depth for any database that reaches this state some
    other way, e.g. direct file tampering outside the application).
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE businesses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER,
                name TEXT NOT NULL,
                gstin TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO businesses (organization_id, name, gstin) "
            "VALUES (1, 'Alpha Traders', '27ALPHA0001Z5')"
        )
        conn.execute(
            "INSERT INTO businesses (organization_id, name, gstin) "
            "VALUES (1, 'Alpha Traders Duplicate', '27ALPHA0001Z5')"
        )
        conn.commit()

        with pytest.raises(MigrationAborted, match="duplicated"):
            migrate_module._check_no_duplicate_business_gstins(conn)
    finally:
        conn.close()


def test_migration_aborts_on_null_organization_id(test_db_path, monkeypatch):
    _seed_legacy_database(
        test_db_path,
        business_rows=[
            (10, None, "Legacy Traders (no organization)", "27LEGACY001Z5"),
        ],
    )

    with pytest.raises(MigrationAborted, match="organization_id IS NULL"):
        _run_migration(test_db_path, monkeypatch)

    # Nothing must have changed: the row, and the old schema shape,
    # must both still be exactly as seeded.
    conn = sqlite3.connect(test_db_path)
    try:
        rows = conn.execute(
            "SELECT id, organization_id, name, gstin FROM businesses"
        ).fetchall()
        assert rows == [(10, None, "Legacy Traders (no organization)", "27LEGACY001Z5")]
        assert businesses_has_old_unique_constraint(conn) is True
    finally:
        conn.close()


def test_reviewed_by_migration_detects_orphans(test_db_path, monkeypatch):
    _seed_legacy_database(
        test_db_path,
        business_rows=[(10, 1, "Alpha Traders", "27ALPHA0001Z5")],
        invoice_rows=[
            # reviewed_by=9999 does not correspond to any users.id.
            (100, 10, 1, "alpha_invoice.pdf", 9999),
        ],
    )
    _seed_organizations(test_db_path, [1])

    with pytest.raises(MigrationAborted, match="reviewed_by"):
        _run_migration(test_db_path, monkeypatch)

    conn = sqlite3.connect(test_db_path)
    try:
        # The businesses rebuild (an independent step) is allowed to
        # have already completed -- only the invoices table's
        # reviewed_by data must be untouched.
        reviewed_by = conn.execute(
            "SELECT reviewed_by FROM invoices WHERE id = 100"
        ).fetchone()[0]
        assert reviewed_by == 9999
        assert invoices_has_reviewed_by_fk(conn) is False
    finally:
        conn.close()


# ============================================================
# 8. Dedicated unit tests for the detection helpers themselves
#    (Risk 6), independent of running the full migration.
# ============================================================

def test_businesses_has_old_unique_constraint_true_for_old_shape():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE businesses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                gstin TEXT NOT NULL UNIQUE,
                organization_id INTEGER
            )
            """
        )
        assert businesses_has_old_unique_constraint(conn) is True
    finally:
        conn.close()


def test_businesses_has_old_unique_constraint_false_for_new_shape():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE businesses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                gstin TEXT NOT NULL,
                UNIQUE (organization_id, gstin)
            )
            """
        )
        assert businesses_has_old_unique_constraint(conn) is False
    finally:
        conn.close()


def test_businesses_has_old_unique_constraint_false_when_table_missing():
    conn = sqlite3.connect(":memory:")
    try:
        assert businesses_has_old_unique_constraint(conn) is False
    finally:
        conn.close()


def test_invoices_has_reviewed_by_fk_false_when_missing():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT)
            """
        )
        conn.execute(
            """
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reviewed_by INTEGER
            )
            """
        )
        assert invoices_has_reviewed_by_fk(conn) is False
    finally:
        conn.close()


def test_invoices_has_reviewed_by_fk_true_when_present():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT)
            """
        )
        conn.execute(
            """
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reviewed_by INTEGER,
                FOREIGN KEY (reviewed_by) REFERENCES users(id)
            )
            """
        )
        assert invoices_has_reviewed_by_fk(conn) is True
    finally:
        conn.close()


def test_invoices_has_reviewed_by_fk_false_when_table_missing():
    conn = sqlite3.connect(":memory:")
    try:
        assert invoices_has_reviewed_by_fk(conn) is False
    finally:
        conn.close()
