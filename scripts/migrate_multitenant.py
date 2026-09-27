import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "gstflow.db"


class MigrationAborted(RuntimeError):
    """
    Raised when a pre-flight safety check finds unsafe legacy data.

    This migration never guesses how to repair unsafe data (e.g. which
    organization a legacy row should belong to). Every check that can
    raise this runs strictly before any schema-changing statement, so
    an abort always leaves the database completely untouched.
    """


def column_exists(conn, table_name, column_name):
    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return any(row[1] == column_name for row in rows)


def table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()

    return row is not None


def businesses_has_old_unique_constraint(conn):
    """
    True if `businesses` still has the pre-Milestone-2 shape (a
    single-column UNIQUE index on `gstin` alone). False if it already
    has the composite UNIQUE(organization_id, gstin) shape, or if the
    table does not exist yet at all (nothing to rebuild).
    """
    if not table_exists(conn, "businesses"):
        return False

    for index in conn.execute("PRAGMA index_list(businesses)").fetchall():
        # index columns: (seq, name, unique, origin, partial)
        name, is_unique = index[1], bool(index[2])

        if not is_unique:
            continue

        columns = [
            row[2]
            for row in conn.execute(f"PRAGMA index_info({name})").fetchall()
        ]

        if columns == ["gstin"]:
            return True
        if columns == ["organization_id", "gstin"]:
            return False

    # No unique index on gstin found at all -- not the old shape.
    return False


def invoices_has_reviewed_by_fk(conn):
    """
    True if `invoices` already has a declared
    FOREIGN KEY (reviewed_by) REFERENCES users(id). False otherwise,
    or if the table does not exist yet at all.
    """
    if not table_exists(conn, "invoices"):
        return False

    for fk in conn.execute("PRAGMA foreign_key_list(invoices)").fetchall():
        # fk columns: (id, seq, table, from, to, on_update, on_delete, match)
        from_column, to_table = fk[3], fk[2]

        if from_column == "reviewed_by" and to_table == "users":
            return True

    return False


def _check_no_null_organization_businesses(conn):
    rows = conn.execute(
        """
        SELECT id, name, gstin, created_at
        FROM businesses
        WHERE organization_id IS NULL
        """
    ).fetchall()

    if rows:
        details = "\n".join(
            f"  id={row[0]} name={row[1]!r} gstin={row[2]!r} "
            f"created_at={row[3]!r}"
            for row in rows
        )
        raise MigrationAborted(
            "Migration aborted before making any change: found "
            f"{len(rows)} row(s) in 'businesses' with organization_id "
            "IS NULL. The composite UNIQUE(organization_id, gstin) "
            "constraint cannot be safely applied while such rows "
            "exist, because SQLite treats every NULL as distinct from "
            "every other NULL, which would silently let these rows "
            "bypass the uniqueness check entirely. This tool will not "
            "guess which organization a legacy row belongs to -- "
            "assign an explicit organization_id to every affected row "
            f"yourself, then re-run this migration. Affected rows:\n"
            f"{details}"
        )


def _check_no_duplicate_business_gstins(conn):
    rows = conn.execute(
        """
        SELECT organization_id, gstin, COUNT(*) AS n
        FROM businesses
        GROUP BY organization_id, gstin
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    if rows:
        details = "\n".join(
            f"  organization_id={row[0]} gstin={row[1]!r} count={row[2]}"
            for row in rows
        )
        raise MigrationAborted(
            "Migration aborted before making any change: found "
            f"{len(rows)} (organization_id, gstin) pair(s) already "
            "duplicated in 'businesses'. Applying "
            "UNIQUE(organization_id, gstin) would fail against this "
            "existing data. Resolve the duplicates manually, then "
            f"re-run this migration. Affected pairs:\n{details}"
        )


def _check_no_orphaned_reviewed_by(conn):
    rows = conn.execute(
        """
        SELECT id, reviewed_by
        FROM invoices
        WHERE reviewed_by IS NOT NULL
          AND reviewed_by NOT IN (SELECT id FROM users)
        """
    ).fetchall()

    if rows:
        details = "\n".join(
            f"  invoice id={row[0]} reviewed_by={row[1]}" for row in rows
        )
        raise MigrationAborted(
            "Migration aborted before making any change: found "
            f"{len(rows)} invoice row(s) whose reviewed_by does not "
            "match any existing users.id. Adding "
            "FOREIGN KEY (reviewed_by) REFERENCES users(id) would "
            "fail against this existing data. Resolve or null out "
            "these values manually, then re-run this migration. "
            f"Affected rows:\n{details}"
        )


def _rebuild_businesses_with_composite_unique_gstin(conn):
    """
    Rebuild `businesses` so GSTIN uniqueness is scoped per
    organization instead of global. SQLite cannot alter a UNIQUE
    constraint in place, so this follows SQLite's documented
    "rebuild the table" procedure (12-step ALTER TABLE recipe).

    Every existing `id` is preserved exactly (copied explicitly, not
    left to AUTOINCREMENT), so invoices.business_id and
    user_business_access.business_id references remain valid without
    touching either of those tables.
    """
    # Hard pre-flight blockers -- run before touching any schema state.
    # If either raises, nothing below this point has executed yet.
    _check_no_null_organization_businesses(conn)
    _check_no_duplicate_business_gstins(conn)

    # PRAGMA foreign_keys can only change outside a transaction.
    conn.execute("PRAGMA foreign_keys = OFF")

    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS businesses_new")

        conn.execute(
            """
            CREATE TABLE businesses_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                gstin TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (organization_id, gstin),
                FOREIGN KEY (organization_id) REFERENCES organizations(id)
            )
            """
        )

        conn.execute(
            """
            INSERT INTO businesses_new
                (id, organization_id, name, gstin, created_at)
            SELECT id, organization_id, name, gstin, created_at
            FROM businesses
            """
        )

        conn.execute("DROP TABLE businesses")
        conn.execute("ALTER TABLE businesses_new RENAME TO businesses")

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_businesses_organization
            ON businesses(organization_id)
            """
        )

        violations = conn.execute(
            "PRAGMA foreign_key_check(businesses)"
        ).fetchall()
        if violations:
            raise MigrationAborted(
                "Migration aborted: foreign_key_check reported "
                f"violations after rebuilding 'businesses': {violations}"
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


# The full target column list for `invoices`, in the order the
# rebuilt table declares them. Used to build an explicit, safe
# INSERT ... SELECT column list rather than relying on `SELECT *`.
_INVOICES_TARGET_COLUMNS = [
    "id",
    "business_id",
    "organization_id",
    "source_filename",
    "supplier_name",
    "gstin",
    "invoice_number",
    "invoice_date",
    "taxable_value",
    "cgst",
    "sgst",
    "igst",
    "total_amount",
    "confidence",
    "status",
    "review_reasons",
    "verification_checks",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "review_notes",
    "raw_text",
    "created_at",
    "invoice_type",
    "uploaded_by",
]


def _rebuild_invoices_with_reviewed_by_fk(conn):
    """
    Rebuild `invoices` to add FOREIGN KEY (reviewed_by) REFERENCES
    users(id) -- SQLite cannot add a constraint to an existing column
    without a full table rebuild.

    Every existing `id` is preserved exactly, so
    invoice_items.invoice_id (ON DELETE CASCADE) references remain
    valid without touching invoice_items at all. Only columns that
    actually exist on the current table are copied, so this also
    tolerates being run against a database that, for whatever reason,
    predates a later additive column (it is simply left at its schema
    default instead of the rebuild failing).
    """
    # Hard pre-flight blocker -- run before touching any schema state.
    _check_no_orphaned_reviewed_by(conn)

    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
    }
    columns_to_copy = [
        c for c in _INVOICES_TARGET_COLUMNS if c in existing_columns
    ]
    column_list_sql = ", ".join(columns_to_copy)

    conn.execute("PRAGMA foreign_keys = OFF")

    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS invoices_new")

        conn.execute(
            """
            CREATE TABLE invoices_new (
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
            )
            """
        )

        conn.execute(
            f"""
            INSERT INTO invoices_new ({column_list_sql})
            SELECT {column_list_sql} FROM invoices
            """
        )

        conn.execute("DROP TABLE invoices")
        conn.execute("ALTER TABLE invoices_new RENAME TO invoices")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_business "
            "ON invoices(business_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_gstin "
            "ON invoices(gstin)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_organization "
            "ON invoices(organization_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_org_status "
            "ON invoices(organization_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_review_status "
            "ON invoices(review_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_created_at "
            "ON invoices(created_at)"
        )

        violations = conn.execute(
            "PRAGMA foreign_key_check(invoices)"
        ).fetchall()
        if violations:
            raise MigrationAborted(
                "Migration aborted: foreign_key_check reported "
                f"violations after rebuilding 'invoices': {violations}"
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Test database not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Full manual control over transactions is required for the
    # rebuild steps below (SQLite only allows PRAGMA foreign_keys to
    # change outside a transaction, and DDL must be explicitly grouped
    # with BEGIN/COMMIT to be atomic under Python's sqlite3 module).
    # This does not change behavior for the pre-existing steps below,
    # which are all either idempotent DDL (CREATE TABLE IF NOT EXISTS
    # / CREATE INDEX IF NOT EXISTS / guarded ALTER TABLE) or read-only
    # SELECTs.
    conn.isolation_level = None

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # ---------------------------------------------------------
        # 1. Organizations
        # ---------------------------------------------------------
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # ---------------------------------------------------------
        # 2. Users
        # ---------------------------------------------------------
        conn.execute(
            """
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
            )
            """
        )

        # ---------------------------------------------------------
        # 3. User → Business access
        # ---------------------------------------------------------
        conn.execute(
            """
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
            )
            """
        )

        # ---------------------------------------------------------
        # 4. Sessions
        # ---------------------------------------------------------
        conn.execute(
            """
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
            )
            """
        )

        # ---------------------------------------------------------
        # 5. Activity logs
        # ---------------------------------------------------------
        conn.execute(
            """
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
            )
            """
        )

        # ---------------------------------------------------------
        # 6. Add organization_id to businesses
        # ---------------------------------------------------------
        if not column_exists(conn, "businesses", "organization_id"):
            conn.execute(
                """
                ALTER TABLE businesses
                ADD COLUMN organization_id INTEGER
                """
            )

        # ---------------------------------------------------------
        # 7. Add organization_id to invoices
        # ---------------------------------------------------------
        if not column_exists(conn, "invoices", "organization_id"):
            conn.execute(
                """
                ALTER TABLE invoices
                ADD COLUMN organization_id INTEGER
                """
            )

        # ---------------------------------------------------------
        # 8. Indexes
        # ---------------------------------------------------------
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_users_organization
            ON users(organization_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_businesses_organization
            ON businesses(organization_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_invoices_organization
            ON invoices(organization_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_sessions_user
            ON sessions(user_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_activity_organization
            ON activity_logs(organization_id)
            """
        )

        # ---------------------------------------------------------
        # 9. Check existing businesses
        # ---------------------------------------------------------
        businesses = conn.execute(
            """
            SELECT id, name, gstin, organization_id
            FROM businesses
            ORDER BY id
            """
        ).fetchall()

        print()
        print("Existing businesses:")
        for business in businesses:
            print(
                f"  ID={business['id']} "
                f"Name={business['name']} "
                f"GSTIN={business['gstin']} "
                f"Organization={business['organization_id']}"
            )

        # ---------------------------------------------------------
        # 10. Businesses: GSTIN uniqueness scoped per organization
        #     (Milestone 2 / TEN-01)
        # ---------------------------------------------------------
        print()
        if businesses_has_old_unique_constraint(conn):
            print(
                "Rebuilding 'businesses' to scope GSTIN uniqueness "
                "per organization (UNIQUE(organization_id, gstin))..."
            )
            _rebuild_businesses_with_composite_unique_gstin(conn)
            print("  done.")
        else:
            print(
                "'businesses' already has the composite "
                "UNIQUE(organization_id, gstin) constraint -- "
                "skipping rebuild."
            )

        # ---------------------------------------------------------
        # 11. Invoices: FOREIGN KEY (reviewed_by) REFERENCES users(id)
        #     (Milestone 2 / DB-03)
        # ---------------------------------------------------------
        print()
        if not invoices_has_reviewed_by_fk(conn):
            print(
                "Rebuilding 'invoices' to add "
                "FOREIGN KEY (reviewed_by) REFERENCES users(id)..."
            )
            _rebuild_invoices_with_reviewed_by_fk(conn)
            print("  done.")
        else:
            print(
                "'invoices' already has FOREIGN KEY (reviewed_by) "
                "REFERENCES users(id) -- skipping rebuild."
            )

        print()
        print("Multi-tenant schema migration completed successfully.")
        print(f"Database: {DB_PATH}")
        print()

        tables = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        ).fetchall()

        print("Tables:")
        for table in tables:
            print(f"  {table['name']}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
