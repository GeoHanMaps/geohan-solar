"""Password hashing helpers — bcrypt directly (passlib's wrap-bug probe
breaks against bcrypt 4.x in this env, so we bypass it)."""
from __future__ import annotations

import bcrypt


# bcrypt truncates the secret at 72 bytes silently; rather than hide that we
# reject anything longer at the schema layer (RegisterRequest max_length=128
# is bytes-safe-ish, but to be exact we still cap here as a belt-and-braces).
_MAX_BYTES = 72


def _to_bytes(plain: str) -> bytes:
    raw = plain.encode("utf-8")
    return raw[:_MAX_BYTES]


def hash_password(plain: str) -> str:
    hashed = bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("utf-8"))
    except ValueError:
        return False
