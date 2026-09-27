import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "gstflow_multitenant_test.db"


def get_user(conn, username):
    return conn.execute(
        """
        SELECT id, organization_id, username, role
        FROM users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()


def get_accessible_businesses(conn, username):
    user = get_user(conn, username)

    if not user:
        raise RuntimeError(f"User not found: {username}")

    if user["role"] == "OWNER":
        return conn.execute(
            """
            SELECT id, name, gstin
            FROM businesses
            WHERE organization_id = ?
            ORDER BY id
            """,
            (user["organization_id"],),
        ).fetchall()

    return conn.execute(
        """
        SELECT
            b.id,
            b.name,
            b.gstin
        FROM businesses b
        JOIN user_business_access uba
            ON uba.business_id = b.id
        WHERE uba.user_id = ?
          AND b.organization_id = ?
        ORDER BY b.id
        """,
        (
            user["id"],
            user["organization_id"],
        ),
    ).fetchall()


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # ---------------------------------------------------------
        # Owner 1 should see ONLY Company A and Company B
        # ---------------------------------------------------------
        owner1_businesses = get_accessible_businesses(
            conn,
            "owner1",
        )

        print()
        print("========== OWNER 1 ACCESS ==========")

        for business in owner1_businesses:
            print(
                f"{business['name']} | "
                f"{business['gstin']}"
            )

        owner1_names = {
            business["name"]
            for business in owner1_businesses
        }

        expected_owner1 = {
            "Company A",
            "Company B",
        }

        assert owner1_names == expected_owner1, (
            f"Owner 1 isolation failed. "
            f"Expected {expected_owner1}, "
            f"got {owner1_names}"
        )

        # ---------------------------------------------------------
        # Owner 2 should see ONLY Company C and Company D
        # ---------------------------------------------------------
        owner2_businesses = get_accessible_businesses(
            conn,
            "owner2",
        )

        print()
        print("========== OWNER 2 ACCESS ==========")

        for business in owner2_businesses:
            print(
                f"{business['name']} | "
                f"{business['gstin']}"
            )

        owner2_names = {
            business["name"]
            for business in owner2_businesses
        }

        expected_owner2 = {
            "Company C",
            "Company D",
        }

        assert owner2_names == expected_owner2, (
            f"Owner 2 isolation failed. "
            f"Expected {expected_owner2}, "
            f"got {owner2_names}"
        )

        # ---------------------------------------------------------
        # User A should see ONLY Company A
        # ---------------------------------------------------------
        user_a_businesses = get_accessible_businesses(
            conn,
            "user_a",
        )

        print()
        print("========== USER A ACCESS ==========")

        for business in user_a_businesses:
            print(
                f"{business['name']} | "
                f"{business['gstin']}"
            )

        user_a_names = {
            business["name"]
            for business in user_a_businesses
        }

        assert user_a_names == {"Company A"}, (
            "User A isolation failed."
        )

        # ---------------------------------------------------------
        # User C should see ONLY Company C
        # ---------------------------------------------------------
        user_c_businesses = get_accessible_businesses(
            conn,
            "user_c",
        )

        print()
        print("========== USER C ACCESS ==========")

        for business in user_c_businesses:
            print(
                f"{business['name']} | "
                f"{business['gstin']}"
            )

        user_c_names = {
            business["name"]
            for business in user_c_businesses
        }

        assert user_c_names == {"Company C"}, (
            "User C isolation failed."
        )

        # ---------------------------------------------------------
        # Explicit cross-tenant attack simulation
        # ---------------------------------------------------------
        owner1 = get_user(conn, "owner1")

        leaked = conn.execute(
            """
            SELECT id, name, gstin
            FROM businesses
            WHERE organization_id != ?
            """,
            (owner1["organization_id"],),
        ).fetchall()

        print()
        print("========== CROSS-TENANT DATA TEST ==========")
        print(
            f"Owner 1 foreign-organization records found: "
            f"{len(leaked)}"
        )

        # This query intentionally finds records belonging
        # to another organization. The application must NEVER
        # use this kind of unrestricted query for Owner 1.
        #
        # The correct access query above restricts by
        # organization_id.
        assert len(leaked) == 2, (
            "Expected exactly two businesses belonging "
            "to Owner 2."
        )

        print()
        print("======================================")
        print("MULTI-TENANT ISOLATION TEST PASSED")
        print("======================================")

    finally:
        conn.close()


if __name__ == "__main__":
    main()