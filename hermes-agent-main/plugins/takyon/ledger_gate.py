"""Shared money-ledger privilege gate.

Money ledgers run on the Safebox authority DB login. Runtime planes do not temporarily change role to
write or reserve money. This module only wraps Safebox-owned SECURITY DEFINER functions with a
fail-closed authority assertion and a cleared RLS-bypass GUC.
"""

from __future__ import annotations

import contextlib


@contextlib.contextmanager
def ledger_gate_scope(conn):
    """Run the wrapped money-ledger statement(s) on a Safebox DB session with RLS-bypass cleared.

    ``conn`` is a raw psycopg connection (the leaf modules speak native psycopg). The GUC is saved and
    restored around the block. The restore in ``finally`` covers a caller that holds the connection
    across the block (autocommit=True in tests; the store also resets explicitly)."""
    raw = getattr(conn, "_pg", conn)
    try:
        from .runtime_app import assert_takyon_pg_role
    except ImportError:  # pragma: no cover - alternate load path
        from plugins.takyon.runtime_app import assert_takyon_pg_role

    try:
        assert_takyon_pg_role(raw, "safebox")
    except Exception as exc:
        raise RuntimeError("money ledger gate requires a Safebox authority database login") from exc

    cur = raw.cursor()
    try:
        previous_bypass = ""
        try:
            row = cur.execute("select current_setting('takyon.rls_bypass', true)").fetchone()
            if row is not None:
                value = next(iter(row.values()), None) if hasattr(row, "values") else row[0]
                previous_bypass = str(value or "")
        except Exception:  # noqa: BLE001 - a missing GUC just means "no prior bypass to restore"
            previous_bypass = ""
        cur.execute("select set_config('takyon.rls_bypass', '0', false)")
        try:
            yield
        finally:
            cur.execute("select set_config('takyon.rls_bypass', %s, false)", (previous_bypass,))
    finally:
        cur.close()


def gate_fetchone(conn, sql: str, params):
    """Execute one Safebox-owned definer-function ``select`` and return its single row."""
    with ledger_gate_scope(conn):
        return conn.execute(sql, params).fetchone()
