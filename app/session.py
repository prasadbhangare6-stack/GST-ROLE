from datetime import datetime, timedelta, timezone

from app.auth import generate_session_token, hash_session_token


SESSION_DURATION_HOURS = 12


def create_session(conn, user_id: int) -> str:
    """
    Create a new login session.

    The raw token is returned to the application so it can
    be placed in an HttpOnly cookie.

    Only the hashed token is stored in the database.
    """
    token = generate_session_token()
    token_hash = hash_session_token(token)

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(hours=SESSION_DURATION_HOURS)
    ).isoformat()

    conn.execute(
        """
        INSERT INTO sessions (
            user_id,
            token_hash,
            expires_at,
            last_seen_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            token_hash,
            expires_at,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    return token


def get_session_user(conn, token: str):
    """
    Return the authenticated user for a valid session.

    Returns None when the token is missing, invalid,
    or expired.
    """
    if not token:
        return None

    token_hash = hash_session_token(token)

    row = conn.execute(
        """
        SELECT
            s.id AS session_id,
            s.user_id,
            s.expires_at,
            u.username,
            u.organization_id,
            u.role,
            u.active
        FROM sessions s
        JOIN users u
            ON u.id = s.user_id
        WHERE s.token_hash = ?
        """,
        (token_hash,),
    ).fetchone()

    if not row:
        return None

    if not row["active"]:
        return None

    expires_at = datetime.fromisoformat(
        row["expires_at"]
    )

    if expires_at <= datetime.now(timezone.utc):
        conn.execute(
            """
            DELETE FROM sessions
            WHERE id = ?
            """,
            (row["session_id"],),
        )
        conn.commit()
        return None

    conn.execute(
        """
        UPDATE sessions
        SET last_seen_at = ?
        WHERE id = ?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            row["session_id"],
        ),
    )
    conn.commit()

    return row


def revoke_session(conn, token: str):
    """
    Log out by deleting the session associated
    with the supplied token.
    """
    if not token:
        return

    token_hash = hash_session_token(token)

    conn.execute(
        """
        DELETE FROM sessions
        WHERE token_hash = ?
        """,
        (token_hash,),
    )

    conn.commit()