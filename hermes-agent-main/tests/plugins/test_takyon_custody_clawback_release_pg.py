from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import custody, safebox  # noqa: E402


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "plugins/takyon/db/migrations/0079_custody_clawback_release.sql"
)


def _account(pg_conn) -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    slug = f"release-{uuid.uuid4().hex[:10]}"
    pg_conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s)",
        (user_id, f"auth0|{uuid.uuid4().hex}"),
    )
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, slug, user_id),
    )
    custody.open_custody_account(pg_conn, user_id)
    return user_id, slug


@contextmanager
def _safebox_session(pg_conn):
    pg_conn.execute("set session authorization takyon_safebox_authority")
    try:
        yield
    finally:
        pg_conn.execute("reset session authorization")


def _release(pg_conn, user_id, slug, clawback_key, release_key):
    return safebox.release_custody_clawback(
        pg_conn,
        user_id,
        slug,
        clawback_key,
        release_key,
        stripe_ref="dp_123",
        metadata={"dispute_outcome": "won"},
    )


def test_immediate_dispute_win_restores_applied_clawback_once(
    pg_conn, monkeypatch
):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    user_id, slug = _account(pg_conn)
    with _safebox_session(pg_conn):
        assert custody.accrue(pg_conn, user_id, slug, 1_000, "pay-1", fee_bps=0) == 1_000
        clawback = custody.clawback(pg_conn, user_id, slug, 400, "clawback-1")
        assert clawback["applied_cents"] == 400
        assert clawback["shortfall_cents"] == 0

        released = _release(pg_conn, user_id, slug, "clawback-1", "release-1")
        replay = _release(pg_conn, user_id, slug, "clawback-1", "release-1")
        alternate_key_replay = _release(
            pg_conn, user_id, slug, "clawback-1", "release-alternate"
        )

    assert released == {
        "credited_cents": 400,
        "owed_balance_cents": 1_000,
        "replayed": False,
    }
    assert replay == {
        "credited_cents": 400,
        "owed_balance_cents": 1_000,
        "replayed": True,
    }
    assert alternate_key_replay == replay
    assert pg_conn.execute(
        "select count(*) from custody_entries where idempotency_key = 'release-1'"
    ).fetchone()[0] == 1
    assert pg_conn.execute(
        "select count(*) from custody_entries "
        "where metadata->>'clawback_idempotency_key' = 'clawback-1' "
        "and metadata->>'custody_clawback_release' = 'true'"
    ).fetchone()[0] == 1
    assert custody.reconcile_custody(pg_conn, user_id)["ok"] is True


def test_win_after_prior_payout_cancels_full_shortfall_without_minting(
    pg_conn, monkeypatch
):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    user_id, slug = _account(pg_conn)
    with _safebox_session(pg_conn):
        custody.accrue(pg_conn, user_id, slug, 500, "pay-before", fee_bps=0)
        assert custody.payout(pg_conn, user_id, 500, "payout-before") == 0
        clawback = custody.clawback(pg_conn, user_id, slug, 300, "clawback-short")
        assert clawback["applied_cents"] == 0
        assert clawback["shortfall_cents"] == 300

        released = _release(
            pg_conn, user_id, slug, "clawback-short", "release-short"
        )
        assert released["credited_cents"] == 0
        assert released["owed_balance_cents"] == 0
        # The canceled shortfall cannot consume later customer money or block its payout.
        assert custody.accrue(pg_conn, user_id, slug, 100, "pay-after", fee_bps=0) == 100
        assert custody.payout(pg_conn, user_id, 100, "payout-after") == 0

    release_metadata = pg_conn.execute(
        "select metadata from custody_entries where idempotency_key = 'release-short'"
    ).fetchone()[0]
    assert int(release_metadata["clawback_cancelled_shortfall_cents"]) == 300
    assert custody.reconcile_custody(pg_conn, user_id)["ok"] is True


def test_partial_later_recovery_credits_only_money_actually_withheld(
    pg_conn, monkeypatch
):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    user_id, slug = _account(pg_conn)
    with _safebox_session(pg_conn):
        custody.clawback(pg_conn, user_id, slug, 1_000, "clawback-partial")
        assert custody.accrue(
            pg_conn, user_id, slug, 400, "recovery-payment", fee_bps=0
        ) == 0
        released = _release(
            pg_conn, user_id, slug, "clawback-partial", "release-partial"
        )
        assert released == {
            "credited_cents": 400,
            "owed_balance_cents": 400,
            "replayed": False,
        }
        assert custody.accrue(
            pg_conn, user_id, slug, 100, "post-release-payment", fee_bps=0
        ) == 500

    recovery_metadata = pg_conn.execute(
        "select metadata from custody_entries where idempotency_key = 'recovery-payment'"
    ).fetchone()[0]
    assert recovery_metadata["clawback_recovery_allocations"] == {
        "clawback-partial": 400
    }
    release_metadata = pg_conn.execute(
        "select metadata from custody_entries where idempotency_key = 'release-partial'"
    ).fetchone()[0]
    assert int(release_metadata["clawback_recovered_cents"]) == 400
    assert int(release_metadata["clawback_cancelled_shortfall_cents"]) == 600
    assert int(release_metadata["clawback_release_credited_cents"]) == 400
    assert custody.reconcile_custody(pg_conn, user_id)["ok"] is True


def test_multiple_clawbacks_allocate_recovery_fifo_even_when_released_reverse_order(
    pg_conn, monkeypatch
):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    user_id, slug = _account(pg_conn)
    with _safebox_session(pg_conn):
        custody.clawback(pg_conn, user_id, slug, 500, "clawback-first")
        custody.clawback(pg_conn, user_id, slug, 700, "clawback-second")
        custody.accrue(pg_conn, user_id, slug, 800, "fifo-recovery", fee_bps=0)

        second = _release(
            pg_conn, user_id, slug, "clawback-second", "release-second"
        )
        first = _release(
            pg_conn, user_id, slug, "clawback-first", "release-first"
        )

    assert second["credited_cents"] == 300
    assert first["credited_cents"] == 500
    assert first["owed_balance_cents"] == 800
    allocations = pg_conn.execute(
        "select metadata->'clawback_recovery_allocations' "
        "from custody_entries where idempotency_key = 'fifo-recovery'"
    ).fetchone()[0]
    assert allocations == {"clawback-first": 500, "clawback-second": 300}
    assert custody.reconcile_custody(pg_conn, user_id)["ok"] is True


def test_legacy_aggregate_recovery_is_attributed_fifo_without_history_rewrite(
    pg_conn, monkeypatch
):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    user_id, slug = _account(pg_conn)
    with _safebox_session(pg_conn):
        custody.clawback(pg_conn, user_id, slug, 500, "legacy-first")
        custody.clawback(pg_conn, user_id, slug, 700, "legacy-second")

    # Exact 0076 shape: aggregate recovery metadata, no per-clawback allocation map.
    pg_conn.execute(
        "insert into custody_entries "
        "(user_id, business_slug, kind, gross_cents, fee_cents, net_cents, "
        " idempotency_key, metadata) values (%s, %s, 'accrual', 800, 0, 0, %s, %s::jsonb)",
        (
            user_id,
            slug,
            "legacy-recovery",
            json.dumps({"clawback_recovery_cents": 800}),
        ),
    )

    with _safebox_session(pg_conn):
        second = _release(pg_conn, user_id, slug, "legacy-second", "legacy-release-2")
        first = _release(pg_conn, user_id, slug, "legacy-first", "legacy-release-1")

    assert second["credited_cents"] == 300
    assert first["credited_cents"] == 500
    assert custody.get_custody_balances(pg_conn, user_id).owed_balance_cents == 800
    assert custody.reconcile_custody(pg_conn, user_id)["ok"] is True


def test_release_migration_replay_preserves_existing_ledger_state(pg_conn, monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    user_id, slug = _account(pg_conn)
    with _safebox_session(pg_conn):
        custody.accrue(pg_conn, user_id, slug, 250, "replay-pay", fee_bps=0)
        custody.clawback(pg_conn, user_id, slug, 100, "replay-clawback")
        released = _release(
            pg_conn, user_id, slug, "replay-clawback", "replay-release"
        )
    before = custody.get_custody_balances(pg_conn, user_id)

    pg_conn.execute(MIGRATION.read_text())
    pg_conn.execute(MIGRATION.read_text())

    after = custody.get_custody_balances(pg_conn, user_id)
    assert after == before
    with _safebox_session(pg_conn):
        replay = _release(
            pg_conn, user_id, slug, "replay-clawback", "replay-release"
        )
    assert released["credited_cents"] == replay["credited_cents"] == 100
    assert replay["replayed"] is True
    assert custody.reconcile_custody(pg_conn, user_id)["ok"] is True


def test_release_function_is_executable_only_by_safebox_authority(pg_conn):
    user_id, slug = _account(pg_conn)
    signature = (
        "safebox_custody_release_clawback(uuid,text,text,text,text,jsonb)"
    )
    assert pg_conn.execute(
        "select has_function_privilege('takyon_safebox_authority', %s, 'execute')",
        (signature,),
    ).fetchone()[0] is True
    assert pg_conn.execute(
        "select has_function_privilege('takyon_runtime', %s, 'execute')",
        (signature,),
    ).fetchone()[0] is False

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="safebox_session_required"):
        pg_conn.execute(
            "select * from safebox_custody_release_clawback"
            "(%s, %s, 'missing-clawback', 'unauthorized-release', null, '{}'::jsonb)",
            (user_id, slug),
        ).fetchone()
