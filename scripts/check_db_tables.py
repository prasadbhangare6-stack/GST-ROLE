import sqlite3
from pathlib import Path


for db_name in [
    "gstflow.db",
    "gstflow_auth_test.db",
]:
    db_path = Path("data") / db_name

    print()
    print("=" * 50)
    print(db_name)
    print("=" * 50)

    conn = sqlite3.connect(db_path)

    tables = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' "
        "ORDER BY name"
    ).fetchall()

    for table in tables:
        print(table[0])

    conn.close()