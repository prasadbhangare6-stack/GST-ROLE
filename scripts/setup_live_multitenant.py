import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import hash_password

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "gstflow.db"


ORGANIZATIONS = [
    ("Owner 1 Organization",),
    ("Owner 2 Organization",),
]

USERS = [
    ("owner1", "Owner1Test!2026", "OWNER", "Owner 1 Organization"),
    ("user_a", "UserATest!2026", "STAFF", "Owner 1 Organization"),
    ("user_b", "UserBTest!2026", "STAFF", "Owner 1 Organization"),
    ("owner2", "Owner2Test!2026", "OWNER", "Owner 2 Organization"),
    ("user_c", "UserCTest!2026", "STAFF", "Owner 2 Organization"),
]

BUSINESS_ORGANIZATIONS = {
    "07AAAAA1111A1Z1": "Owner 1 Organization",
    "07BBBBB2222B1Z2": "Owner 1 Organization",
    "07CCCCC3333C1Z3": "Owner 2 Organization",
    "07DDDDD4444D1Z4": "Owner 2 Organization",
}

STAFF_ACCESS = {
    "user_a": "07AAAAA1111A1Z1",
    "user_b": "07BBBBB2222B1Z2",
    "user_c": "07CCCCC3333C1Z3",
}


def get_or_create_organization(conn, name):
    row = conn.execute(
        """
        SELECT id
        FROM organizations
        WHERE name = ?
        """,
        (name,),
    ).fetchone()

    if row:
        return row["id"]

    cursor = conn.execute(
        """
        INSERT INTO organizations (name)
        VALUES (?)
        """,
        (name,),
    )
    return cursor.lastrowid


def get_or_create_user(conn, username, password, role, organization_id):
    row = conn.execute(
        """
        SELECT id
        FROM users
        WHERE username = ?
          AND organization_id = ?
        """,
        (username, organization_id),
    ).fetchone()

    if row:
        return row["id"]

    password_hash = hash_password(password)

    cursor = conn.execute(
        """
        INSERT INTO users (
            organization_id,
            username,
            password_hash,
            role,
            active
        )
        VALUES (?, ?, ?, ?, 1)
        """,
        (
            organization_id,
            username,
            password_hash,
            role,
        ),
    )

    return cursor.lastrowid


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Live database not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        print()
        print("========== CREATING ORGANIZATIONS ==========")

        organization_ids = {}

        for (name,) in ORGANIZATIONS:
            organization_ids[name] = get_or_create_organization(
                conn,
                name,
            )
            print(
                f"{name} -> ID={organization_ids[name]}"
            )

        print()
        print("========== ASSIGNING BUSINESSES ==========")

        for gstin, organization_name in BUSINESS_ORGANIZATIONS.items():
            organization_id = organization_ids[organization_name]

            business = conn.execute(
                """
                SELECT id, name, gstin, organization_id
                FROM businesses
                WHERE gstin = ?
                """,
                (gstin,),
            ).fetchone()

            if not business:
                raise RuntimeError(
                    f"Business with GSTIN {gstin} was not found."
                )

            if (
                business["organization_id"] is not None
                and business["organization_id"] != organization_id
            ):
                raise RuntimeError(
                    f"Business {business['name']} is already assigned "
                    f"to another organization."
                )

            conn.execute(
                """
                UPDATE businesses
                SET organization_id = ?
                WHERE id = ?
                """,
                (
                    organization_id,
                    business["id"],
                ),
            )

            print(
                f"{business['name']} "
                f"({business['gstin']}) "
                f"-> {organization_name}"
            )

        print()
        print("========== CREATING USERS ==========")

        user_ids = {}

        for username, password, role, organization_name in USERS:
            organization_id = organization_ids[organization_name]

            user_ids[username] = get_or_create_user(
                conn,
                username,
                password,
                role,
                organization_id,
            )

            print(
                f"{username} | {role} | {organization_name}"
            )

        print()
        print("========== STAFF BUSINESS ACCESS ==========")

        for username, gstin in STAFF_ACCESS.items():
            user_id = user_ids[username]

            business = conn.execute(
                """
                SELECT id, name
                FROM businesses
                WHERE gstin = ?
                """,
                (gstin,),
            ).fetchone()

            if not business:
                raise RuntimeError(
                    f"Business with GSTIN {gstin} was not found."
                )

            conn.execute(
                """
                INSERT OR IGNORE INTO user_business_access (
                    user_id,
                    business_id
                )
                VALUES (?, ?)
                """,
                (
                    user_id,
                    business["id"],
                ),
            )

            print(
                f"{username} -> {business['name']}"
            )

        conn.commit()

        print()
        print("========== VERIFICATION ==========")

        rows = conn.execute(
            """
            SELECT
                o.name AS organization,
                u.username,
                u.role,
                u.active
            FROM users u
            JOIN organizations o
                ON o.id = u.organization_id
            ORDER BY o.id, u.id
            """
        ).fetchall()

        for row in rows:
            print(
                f"{row['organization']} | "
                f"{row['username']} | "
                f"{row['role']} | "
                f"active={row['active']}"
            )

        print()
        print("Business assignments:")

        businesses = conn.execute(
            """
            SELECT
                b.name,
                b.gstin,
                o.name AS organization
            FROM businesses b
            LEFT JOIN organizations o
                ON o.id = b.organization_id
            ORDER BY b.id
            """
        ).fetchall()

        for business in businesses:
            print(
                f"{business['name']} | "
                f"{business['gstin']} | "
                f"{business['organization']}"
            )

        print()
        print("==========================================")
        print("LIVE MULTI-TENANT TEST DATA READY")
        print("==========================================")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()

