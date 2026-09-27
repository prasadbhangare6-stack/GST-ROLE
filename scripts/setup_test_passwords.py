import sys
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.security import hash_password

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "gstflow_auth_test.db"


TEST_PASSWORDS = {
    "owner1": "Owner1Test!2026",
    "user_a": "UserATest!2026",
    "user_b": "UserBTest!2026",
    "owner2": "Owner2Test!2026",
    "user_c": "UserCTest!2026",
}


def main():
    conn = sqlite3.connect(DB_PATH)

    try:
        for username, password in TEST_PASSWORDS.items():
            password_hash = hash_password(password)

            conn.execute(
                """
                UPDATE users
                SET password_hash = ?
                WHERE username = ?
                """,
                (password_hash, username),
            )

        conn.commit()

        print()
        print("Test passwords created successfully.")
        print()

        rows = conn.execute(
            """
            SELECT username, role, password_hash
            FROM users
            ORDER BY id
            """
        ).fetchall()

        for username, role, password_hash in rows:
            print(
                f"{username} | "
                f"{role} | "
                f"Argon2 hash: {password_hash[:20]}..."
            )

    finally:
        conn.close()


if __name__ == "__main__":
    main()