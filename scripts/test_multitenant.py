import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "gstflow_multitenant_test.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # ---------------------------------------------------------
        # Create two completely separate organizations
        # ---------------------------------------------------------
        conn.execute(
            """
            INSERT INTO organizations (name)
            VALUES (?)
            """,
            ("Owner 1 Organization",),
        )

        owner1_org_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.execute(
            """
            INSERT INTO organizations (name)
            VALUES (?)
            """,
            ("Owner 2 Organization",),
        )

        owner2_org_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        # ---------------------------------------------------------
        # Create test users
        # Passwords are deliberately fake placeholders.
        # Real password hashing comes later.
        # ---------------------------------------------------------
        users = [
            (owner1_org_id, "owner1", "TEST_HASH", "OWNER"),
            (owner1_org_id, "user_a", "TEST_HASH", "STAFF"),
            (owner1_org_id, "user_b", "TEST_HASH", "STAFF"),
            (owner2_org_id, "owner2", "TEST_HASH", "OWNER"),
            (owner2_org_id, "user_c", "TEST_HASH", "STAFF"),
        ]

        conn.executemany(
            """
            INSERT INTO users (
                organization_id,
                username,
                password_hash,
                role
            )
            VALUES (?, ?, ?, ?)
            """,
            users,
        )

        # ---------------------------------------------------------
        # Assign existing businesses to organizations
        # ---------------------------------------------------------
        conn.execute(
            """
            UPDATE businesses
            SET organization_id = ?
            WHERE name IN ('Company A', 'Company B')
            """,
            (owner1_org_id,),
        )

        conn.execute(
            """
            UPDATE businesses
            SET organization_id = ?
            WHERE name IN ('Company C', 'Company D')
            """,
            (owner2_org_id,),
        )

        # ---------------------------------------------------------
        # Get user IDs
        # ---------------------------------------------------------
        user_a_id = conn.execute(
            """
            SELECT id
            FROM users
            WHERE username = 'user_a'
            """
        ).fetchone()["id"]

        user_b_id = conn.execute(
            """
            SELECT id
            FROM users
            WHERE username = 'user_b'
            """
        ).fetchone()["id"]

        user_c_id = conn.execute(
            """
            SELECT id
            FROM users
            WHERE username = 'user_c'
            """
        ).fetchone()["id"]

        # ---------------------------------------------------------
        # Get business IDs
        # ---------------------------------------------------------
        company_a_id = conn.execute(
            """
            SELECT id
            FROM businesses
            WHERE name = 'Company A'
            """
        ).fetchone()["id"]

        company_b_id = conn.execute(
            """
            SELECT id
            FROM businesses
            WHERE name = 'Company B'
            """
        ).fetchone()["id"]

        company_c_id = conn.execute(
            """
            SELECT id
            FROM businesses
            WHERE name = 'Company C'
            """
        ).fetchone()["id"]

        company_d_id = conn.execute(
            """
            SELECT id
            FROM businesses
            WHERE name = 'Company D'
            """
        ).fetchone()["id"]

        # ---------------------------------------------------------
        # Staff assignments
        #
        # Owner 1:
        #   User A → Company A
        #   User B → Company B
        #
        # Owner 2:
        #   User C → Company C
        # ---------------------------------------------------------
        conn.executemany(
            """
            INSERT INTO user_business_access (
                user_id,
                business_id
            )
            VALUES (?, ?)
            """,
            [
                (user_a_id, company_a_id),
                (user_b_id, company_b_id),
                (user_c_id, company_c_id),
            ],
        )

        conn.commit()

        # ---------------------------------------------------------
        # Display organization structure
        # ---------------------------------------------------------
        print()
        print("========== ORGANIZATIONS ==========")

        organizations = conn.execute(
            """
            SELECT id, name
            FROM organizations
            ORDER BY id
            """
        ).fetchall()

        for org in organizations:
            print(
                f"Organization {org['id']}: {org['name']}"
            )

        # ---------------------------------------------------------
        # Display users
        # ---------------------------------------------------------
        print()
        print("========== USERS ==========")

        rows = conn.execute(
            """
            SELECT
                u.id,
                u.username,
                u.role,
                o.name AS organization_name
            FROM users u
            JOIN organizations o
                ON o.id = u.organization_id
            ORDER BY u.id
            """
        ).fetchall()

        for row in rows:
            print(
                f"{row['username']} | "
                f"{row['role']} | "
                f"{row['organization_name']}"
            )

        # ---------------------------------------------------------
        # Display businesses
        # ---------------------------------------------------------
        print()
        print("========== BUSINESSES ==========")

        rows = conn.execute(
            """
            SELECT
                b.id,
                b.name,
                b.gstin,
                o.name AS organization_name
            FROM businesses b
            LEFT JOIN organizations o
                ON o.id = b.organization_id
            ORDER BY b.id
            """
        ).fetchall()

        for row in rows:
            print(
                f"{row['name']} | "
                f"{row['gstin']} | "
                f"{row['organization_name']}"
            )

        # ---------------------------------------------------------
        # Display staff access
        # ---------------------------------------------------------
        print()
        print("========== STAFF ACCESS ==========")

        rows = conn.execute(
            """
            SELECT
                u.username,
                b.name AS business_name,
                o.name AS organization_name
            FROM user_business_access uba
            JOIN users u
                ON u.id = uba.user_id
            JOIN businesses b
                ON b.id = uba.business_id
            JOIN organizations o
                ON o.id = u.organization_id
            ORDER BY u.id, b.id
            """
        ).fetchall()

        for row in rows:
            print(
                f"{row['username']} → "
                f"{row['business_name']} → "
                f"{row['organization_name']}"
            )

        print()
        print("Multi-tenant test data created successfully.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()