"""
Shared pytest fixtures for the GST-ROLE test suite.

Milestone 1 scope: provide an isolated FastAPI TestClient wired to a
disposable, per-test SQLite database, so tests can exercise the real
application without ever touching the real application database
(data/gstflow.db).

Milestone 2 scope: additionally expose a lower-level `db_conn` fixture
for schema/migration-focused tests that need direct SQLite access
rather than an HTTP client, built on the exact same isolated-database
setup as the `client` fixture (factored out into `_build_isolated_db`
below so both fixtures share one code path instead of duplicating it).

Design notes
------------
The database schema is built by calling the application's own,
*existing, unmodified* schema-creation code -- not a hand-copied
re-implementation of it:

    1. app.db.init_db()                    -> base schema
       (businesses / invoices / invoice_items)
    2. scripts.migrate_multitenant.main()  -> multi-tenant schema
       (organizations / users / user_business_access / sessions /
       activity_logs, plus organization_id columns, plus the
       Milestone 2 composite-GSTIN-uniqueness and reviewed_by-FK
       rebuilds when applicable)

This mirrors exactly what an operator must currently do, by hand, to
get a fully working instance of the app (see the Database Audit /
Bug & Risk Assessment finding DB-01: `init_db()` alone does not create
every table the application needs). Running both steps here is test
setup only -- it does not change any schema-creation code, and it does
not change the schema `init_db()` itself produces in production.

No application source file is modified to achieve isolation: only the
module-level `DB_PATH` constants in `app.db` and
`scripts.migrate_multitenant` are monkeypatched, for the duration of
each test, to point at a fresh temporary file instead of
`data/gstflow.db`.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make sure the repository root (parent of this `tests/` directory) is
# importable as `app` / `scripts`, regardless of how pytest was invoked.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.db as db_module  # noqa: E402  (safe: only ensures data/ dir exists)


@pytest.fixture()
def test_db_path(tmp_path):
    """A fresh, unique SQLite file path, private to a single test."""
    return tmp_path / "gstflow_test.db"


def _build_isolated_db(test_db_path, monkeypatch):
    """
    Redirect app.db's and scripts.migrate_multitenant's database paths
    to `test_db_path`, then build the full schema against it via the
    real, unmodified schema-creation code.

    Returns the (already-patched) `scripts.migrate_multitenant` module
    object, in case a caller needs it (the `client` fixture does not;
    the future `db_conn` fixture does not either, but callers of this
    helper elsewhere might).

    Shared by both the `client` fixture (Milestone 1) and the
    `db_conn` fixture (Milestone 2) so the isolation setup exists in
    exactly one place.
    """
    # Redirect app.db's database path *before* app.main is ever
    # imported anywhere, since importing app.main runs init_db() once
    # as a module-level side effect.
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)

    import scripts.migrate_multitenant as migrate_module
    monkeypatch.setattr(migrate_module, "DB_PATH", test_db_path)

    # Build the schema for *this* test's database explicitly. Both
    # calls are idempotent (CREATE TABLE IF NOT EXISTS / guarded
    # ALTER TABLE / guarded rebuilds), so it is safe to call them even
    # if app.main's own module-level init_db() call already ran
    # against this same path.
    db_module.init_db()
    migrate_module.main()

    return migrate_module


@pytest.fixture()
def client(test_db_path, monkeypatch):
    """
    A FastAPI TestClient backed by a fresh, fully-migrated, isolated
    test database.

    The real application database is never opened, created, or
    modified by this fixture or by anything that uses it.
    """
    _build_isolated_db(test_db_path, monkeypatch)

    import app.main as main_module  # noqa: F401

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture()
def db_conn(test_db_path, monkeypatch):
    """
    A raw sqlite3 connection to a fresh, fully-migrated, isolated test
    database -- for schema/migration-focused tests (Milestone 2) that
    need direct SQL access rather than an HTTP client.

    Uses the exact same isolation setup as the `client` fixture (via
    `_build_isolated_db`), and opens the connection through
    `db_module.connect()` so it picks up the same
    `PRAGMA foreign_keys = ON` behavior every application connection
    gets in production.

    The real application database is never opened, created, or
    modified by this fixture or by anything that uses it.
    """
    _build_isolated_db(test_db_path, monkeypatch)

    conn = db_module.connect()
    try:
        yield conn
    finally:
        conn.close()
