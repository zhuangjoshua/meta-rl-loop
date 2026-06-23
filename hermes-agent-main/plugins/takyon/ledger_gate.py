"""Shared money-ledger privilege gate — run one SECURITY DEFINER ledger call under the restricted
runtime role so migration 0038's REVOKE actually binds the runtime.

GOAL_RULES §1/§3 (gap G3): the runtime connects to Postgres as the database OWNER (BYPASSRLS) and
therefore holds raw INSERT/UPDATE/DELETE on the money ledgers. Migration 0038 demotes a SEPARATE
``takyon_runtime`` role off those tables and routes every money write through SECURITY DEFINER
functions the role may only EXECUTE. But the still-owner-connected runtime is BYPASSRLS and holds
implicit DML, so 0038's ``revoke ... from takyon_runtime`` cannot constrain the owner directly. This
gate drops to ``takyon_runtime`` (NOBYPASSRLS, no direct ledger DML) and clears the bypass GUC for the
duration of the single definer call, so the write reaches the ledger ONLY via the granted-EXECUTE
gate function — a forged direct write under the runtime principal is then DENIED by the DB. The
definer function still runs its row ops with the OWNER's privileges (security definer), so the money
math is unchanged; only the direct-table-write privilege is constrained by the role drop.

This is the exact same boundary ``app_usage._ledger_gate_scope`` introduced for the usage ledger in
0037, generalized over the role + reused by billing.py and business_credits.py.

``SET ROLE`` is session-scoped (works under autocommit and inside a transaction); the GUC + role are
restored in ``finally`` so surrounding control-plane reads keep their prior authority.

Fails CLOSED on runtime planes: if the role drop itself errors (e.g. an old DB predating migration
0038/0030), the error propagates rather than silently running the gate with bypass authority. On the
dedicated Safebox authority host, the service is the trusted ledger owner and may not be grantable to
the runtime role; in that context the same SECURITY DEFINER gate call runs without ``SET ROLE``.
"""

from __future__ import annotations

import contextlib

# The restricted, NON-bypassing runtime role migration 0038 creates and binds: after 0038 it has NO
# direct INSERT/UPDATE/DELETE on the money ledgers (only EXECUTE on the safebox_billing_* /
# safebox_credits_* SECURITY DEFINER funcs). See plugins/takyon/db/migrations/0038.
LEDGER_RUNTIME_ROLE = "takyon_runtime"


def _needs_runtime_role_demotion() -> bool:
    """Whether this process should demote ledger calls to the runtime role.

    Runtime planes connect with broad owner authority until the DSN cutover lands, so they must drop to
    ``takyon_runtime`` for money-ledger gates. The Safebox service itself is the authority boundary and
    owns those writes; requiring it to assume a runtime-only role breaks live budget reservations when
    the authority DB login deliberately cannot ``SET ROLE takyon_runtime``.
    """
    try:
        from . import safebox

        return not safebox._local_authority_enabled()
    except Exception:
        return True


@contextlib.contextmanager
def ledger_gate_scope(conn, *, role: str = LEDGER_RUNTIME_ROLE):
    """Run the wrapped ledger statement(s) under the restricted ``role`` with RLS-bypass cleared.

    ``conn`` is a raw psycopg connection (the leaf modules speak native psycopg). The role + GUC are
    saved and restored around the block. The restore in ``finally`` covers a caller that holds the
    connection across the block (autocommit=True in tests; the store also resets explicitly)."""
    raw = getattr(conn, "_pg", conn)
    cur = raw.cursor()
    demote = _needs_runtime_role_demotion()
    try:
        previous_bypass = ""
        try:
            row = cur.execute("select current_setting('takyon.rls_bypass', true)").fetchone()
            if row is not None:
                value = next(iter(row.values()), None) if hasattr(row, "values") else row[0]
                previous_bypass = str(value or "")
        except Exception:  # noqa: BLE001 - a missing GUC just means "no prior bypass to restore"
            previous_bypass = ""
        if demote:
            cur.execute(f"set role {role}")
        cur.execute("select set_config('takyon.rls_bypass', '0', false)")
        try:
            yield
        finally:
            if demote:
                cur.execute("reset role")
            cur.execute("select set_config('takyon.rls_bypass', %s, false)", (previous_bypass,))
    finally:
        cur.close()


def gate_fetchone(conn, sql: str, params, *, role: str = LEDGER_RUNTIME_ROLE):
    """Execute one definer-function ``select`` under the restricted ``role`` and return its single
    row. The function is SECURITY DEFINER, so its row ops still run with the owner's privileges; only
    the DIRECT table-write privilege is constrained by the role drop — the boundary 0038 introduces."""
    with ledger_gate_scope(conn, role=role):
        return conn.execute(sql, params).fetchone()
