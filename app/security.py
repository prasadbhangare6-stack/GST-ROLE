from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """
    Create a secure Argon2 password hash.
    """
    if not password:
        raise ValueError("Password cannot be empty.")

    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against an Argon2 hash.
    """
    if not password or not password_hash:
        return False

    try:
        return _password_hasher.verify(
            password_hash,
            password,
        )
    except VerifyMismatchError:
        return False