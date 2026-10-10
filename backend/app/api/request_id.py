"""Request id generation.

FastAPI generates the id used for its own tracing. n8n generates its own
internally; the two are correlated through the publish log and logs.
"""
from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _encode(value: int, length: int) -> str:
    out = ""
    for _ in range(length):
        out = _ALPHABET[value % 32] + out
        value //= 32
    return out


def new_request_id(prefix: str = "req") -> str:
    """A lexicographically sortable, collision-resistant id.

    Layout: prefix_<48-bit millisecond timestamp><80-bit random> in base32.
    Sortable by creation time, which makes log searching straightforward.
    """
    ts = _encode(int(time.time() * 1000), 10)
    rand = ""
    while len(rand) < 16:
        rand += _ALPHABET[os.urandom(1)[0] & 0x1F]
    return f"{prefix}_{ts}{rand}"