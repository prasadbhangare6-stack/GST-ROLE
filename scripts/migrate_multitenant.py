import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "gstflow.db"


def column_exists(conn, table_name, column_name):
    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return any(row[1] == column_name for row in rows)


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Test database not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

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

        conn.commit()

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

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()