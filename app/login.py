from app.security import verify_password
from app.session import create_session


def authenticate_user(conn, username: str, password: str):
    """
    Verify username/password and create a session.

    Returns:
        (user, session_token) on success
        (None, None) on failure
    """

    if not username or not password:
        return None, None

    user = conn.execute(
        """
        SELECT
            id,
            organization_id,
            username,
            password_hash,
            role,
            active
        FROM users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()

    if not user:
        return None, None

    if not user["active"]:
        return None, None

    if not verify_password(
        password,
        user["password_hash"],
    ):
        return None, None

    session_token = create_session(
        conn,
        user["id"],
    )

    conn.commit()

    return user, session_token