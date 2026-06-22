"""Single-use nonce store for safebox capability tokens (Phase 2). ADDITIVE — not yet wired.

A verified token is single-use: the safebox claims its nonce exactly once. `claim()` is the atomic
check-and-mark — True on the first presentation, False on every replay. The AUTHORITATIVE store is
Postgres on the safebox host (`INSERT ... ON CONFLICT DO NOTHING`); the in-memory store is for tests /
a single-process safebox. Expiry pruning keeps the set bounded; an expired nonce is harmless to forget
because the token itself is already expired (verify_capability rejects it).
"""
from __future__ import annotations

import threading


class InMemoryNonceStore:
    def __init__(self) -> None:
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def claim(self, nonce: str, expires_at: int, *, now: int) -> bool:
        nonce = str(nonce or "")
        if not nonce:
            return False
        with self._lock:
            # prune expired so the set stays bounded
            for dead in [n for n, exp in self._seen.items() if exp <= now]:
                del self._seen[dead]
            if nonce in self._seen:
                return False
            self._seen[nonce] = int(expires_at)
            return True


def pg_claim_nonce(conn, nonce: str, expires_at: int) -> bool:
    """Authoritative single-use claim against the safebox-owned table. True iff this call inserted the
    nonce (first use); False on replay. Schema (created by the safebox migration):

        create table if not exists safebox_used_nonces (
            nonce      text primary key,
            expires_at bigint not null
        );

    A periodic sweep deletes rows where expires_at <= now(). This table is writable ONLY by the safebox.
    """
    row = conn.execute(
        "insert into safebox_used_nonces (nonce, expires_at) values (%s, %s) "
        "on conflict (nonce) do nothing returning nonce",
        (str(nonce or ""), int(expires_at)),
    ).fetchone()
    return row is not None
