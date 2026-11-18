"""Password hashing and sign-in tokens, using only the Python standard library.

- Passwords are hashed with scrypt (a deliberately slow, memory-hard function) and a
  random 16-byte salt per password. Stored format:
      scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>
  so the cost parameters can be raised later without breaking old hashes.
- Tokens (sign-in sessions, reset links) are 32 random bytes. The database stores only
  their SHA-256 digest: whoever reads the database still cannot sign in with it.
- Comparisons use hmac.compare_digest (constant time).
"""

import hashlib
import hmac
import secrets

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# Used when the email is unknown, so a failed sign-in takes as long either way and the
# response time does not reveal which emails have accounts.
_DUMMY_HASH: str | None = None


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=64 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    """Salted scrypt hash of `password`, in the stored format above."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def _matches(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = _scrypt(password, bytes.fromhex(salt_hex), int(n), int(r), int(p))
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest_hex)


def verify_password(password: str, stored: str | None) -> bool:
    """True if `password` matches the stored hash. With no hash (unknown email, or a user
    without a password) the answer is False, after a check of the same cost."""
    global _DUMMY_HASH
    if not stored:
        _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
        _matches(password, _DUMMY_HASH)  # result ignored: only the time spent matters
        return False
    return _matches(password, stored)


def password_problem(password: str) -> str | None:
    """A message if the password breaks the rules, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    if password.strip() != password or not password.strip():
        return "The password cannot start or end with a space."
    return None


def new_token() -> str:
    """A random, URL-safe token (sent to the user once, never stored as is)."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """SHA-256 hex digest of a token: what the database stores and looks up."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
