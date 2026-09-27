import hashlib
import secrets


SESSION_TOKEN_BYTES = 32


def generate_session_token() -> str:
    """
    Generate a cryptographically secure random session token.
    """
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """
    Hash a session token before storing it in the database.
    """
    if not token:
        raise ValueError("Session token cannot be empty.")

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()