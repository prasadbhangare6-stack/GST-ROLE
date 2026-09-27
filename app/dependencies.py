from fastapi import Cookie, HTTPException

from .db import connect
from .session import get_session_user


def get_current_user(
    session: str | None = Cookie(default=None),
):
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    with connect() as conn:
        user = get_session_user(conn, session)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session",
        )

    return user
