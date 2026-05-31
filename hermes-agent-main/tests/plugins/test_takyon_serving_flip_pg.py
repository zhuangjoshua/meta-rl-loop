"""Phase 8 serving-flip E2E on real Postgres — the acceptance for "kill SQLite" (mediationplan.md
Runtime Cutover step 5/6: flip the runtime to Postgres, run E2E through the real shell, verify
identical behavior; second empty-disk runtime resumes purely from Postgres + Storage).

Two proofs, both against a real migrated throwaway Postgres (never mocks):

  * **Real-shell operator lifecycle on Postgres** — drive the ACTUAL shell parser/router
    (``cli._handle_shell_line``, the same function the interactive ``./takyon`` shell calls per typed
    line) for the model-free operator commands (``/create``, ``/status``, ``/pulse``, ``/test``,
    ``/show``) with ``TAKYON_DB_BACKEND=postgres``. This exercises the slash-command parsing, scoped
    routing, and ``run_takyon_command`` → ``TakyonStore`` path end-to-end on PG — not just the store
    API the other PG tests call directly. The platform owner is seeded exactly as shell startup does
    (``cli._seed_platform_owner_at_startup``), so ``business.upsert`` resolves a real owner.

  * **No-fleet stateless resume via the OPERATOR STORE on Postgres** — the Phase-7 no-fleet proof
    used the raw psycopg leaf; THIS ties it to the full ``TakyonStore``: host A's store creates the
    PG-authoritative business and writes its workspace to local scratch, then syncs up; host B is a
    SECOND store on a genuinely empty disk sharing the same Postgres, which resumes the business from
    PG alone (its disk has no ``businesses/`` tree) and reconstructs the workspace byte-identically
    from Storage. That is the "host is disposable; state lives in Postgres + Storage" acceptance.

Skips unless psycopg is importable and the conftest can reach a test Postgres (TAKYON_TEST_PG_DSN).
"""

from __future__ import annotations

from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import cli  # noqa: E402
from plugins.takyon import core as takyon_core  # noqa: E402
from plugins.takyon import storage  # noqa: E402


def _seed_workspace(root: Path) -> None:
    """Write a realistic four-root workspace (text + a binary blob) under ``root`` (mirrors the
    Phase-7 storage proof's fixture so the byte-identity assertion is apples-to-apples)."""
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "strategy.md").write_text("# Acme\nGoal: win the market\n")
    (root / "product").mkdir(parents=True, exist_ok=True)
    (root / "product" / "runtime.md").write_text("Rails By Owner\n")
    (root / "metrics" / "receipts").mkdir(parents=True, exist_ok=True)
    (root / "metrics" / "receipts" / "r1.json").write_bytes(b"\x00\x01\x02 binary receipt")


def _tree(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root`` as {posix-relpath: bytes} — for byte-identity assertions."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_shell_operator_lifecycle_on_postgres(pg_store_dsn, tmp_path, monkeypatch):
    # The full serving flip: the operator store backend AND the URL the shell's own TakyonStore()
    # resolves both point at the migrated throwaway DB; TAKYON_HOME isolates the business filesystem.
    monkeypatch.setenv("TAKYON_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|e2e-operator")

    # Shell startup seeds the single platform owner exactly as _interactive_shell does.
    store = takyon_core.TakyonStore()
    cli._seed_platform_owner_at_startup(store)

    def shell(line: str, current: str | None):
        return cli._handle_shell_line(line, current_business=current, store=store, model="", max_turns=1)

    # /create through the REAL shell parser/router. --no-auto keeps it model-free (no CEO turn, no cron).
    _out, current = shell("/create --no-auto --test e2eco ship the thing", None)
    assert current == "e2eco"  # the shell scope followed the create

    # The business landed on PG, owned by the seeded platform owner (NOT a fabricated/NULL owner).
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        row = conn.execute(
            "select b.owner_user_id, b.goal, b.mode, b.status, u.auth0_sub "
            "from businesses b join users u on u.id = b.owner_user_id where b.slug = %s",
            ("e2eco",),
        ).fetchone()
    assert row is not None
    assert row[1] == "ship the thing" and row[2] == "test" and row[3] == "active"
    assert row[4] == "auth0|e2e-operator"  # the same single owner the shell seeded

    # /status and /pulse read back through the shell on PG (the read path, not just the write path).
    status_out, _ = shell("/status e2eco", "e2eco")
    assert "e2eco" in status_out
    pulse_out, _ = shell("/pulse e2eco", "e2eco")
    assert pulse_out  # a non-empty pulse render proves calculate_pulse ran on PG

    # /test status reflects the mode persisted on PG.
    test_out, _ = shell("/test e2eco status", "e2eco")
    assert "test" in test_out.lower()

    # A workspace file under the business root reads back byte-faithfully through /show on PG.
    biz_root = store.root / "businesses" / "e2eco"
    (biz_root / "research").mkdir(parents=True, exist_ok=True)
    (biz_root / "research" / "strategy.md").write_text("# e2eco\nGoal: ship\n")
    show_out, _ = shell("/show e2eco research/strategy.md", "e2eco")
    assert "Goal: ship" in show_out


def test_no_fleet_resume_via_operator_store_on_postgres(pg_store_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|nofleet-operator")

    # Host A: a full operator store on its own disk creates the PG-authoritative business...
    host_a = tmp_path / "host-a"
    store_a = takyon_core.TakyonStore(root=host_a, database_url=pg_store_dsn)
    store_a.seed_platform_owner()
    result = store_a.commit(
        scope="business:nfco",
        operations=[{"action": "business.upsert", "business": "nfco", "name": "NoFleet Co",
                     "goal": "resume anywhere", "mode": "test"}],
        idempotency_key="p86-nfco-create", reason="p8.6", actor="test",
    )
    assert result["success"] is True

    # ...and writes its workspace to local scratch, then syncs up to the shared bucket.
    biz_a = host_a / "businesses" / "nfco"
    _seed_workspace(biz_a)
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    up = storage.sync_up(backend, "nfco", biz_a)
    assert len(up.uploaded) == 3 and up.deleted == ()

    # Host B: a SECOND store on a genuinely empty disk, sharing the same Postgres. Its disk has no
    # businesses/ tree yet — the stateless precondition, asserted BEFORE host B touches anything.
    host_b = tmp_path / "host-b"
    biz_b = host_b / "businesses" / "nfco"
    assert not biz_b.exists()  # genuinely empty before resume — nothing local to lean on

    # It learns the business PURELY from PG (its disk is still blank) — the stateless resume proper.
    store_b = takyon_core.TakyonStore(root=host_b, database_url=pg_store_dsn)
    summary = store_b.read(scope="business:nfco", query="summary")
    assert (summary.get("business") or {}).get("slug") == "nfco"
    assert (summary.get("business") or {}).get("goal") == "resume anywhere"

    # The two state planes are INDEPENDENT, and host B reconstructs both from scratch:
    #   * Postgres plane — reading the summary mirrors the PG-authoritative app surface contract to
    #     local disk. That materialization comes purely from PG, with no Storage round-trip; on a
    #     fresh host it is the ONLY file present before we touch Storage.
    surface = biz_b / "product" / "surface.md"
    assert surface.is_file() and surface.read_text().startswith("# App Surface Contract")
    assert _tree(biz_b) == {"product/surface.md": surface.read_bytes()}  # PG plane only, so far

    #   * Storage plane — sync_down reconstructs the workspace blobs byte-for-byte (default
    #     delete_local=False, so the PG-mirrored surface contract is left untouched alongside them).
    down = storage.sync_down(backend, "nfco", biz_b)
    assert len(down.downloaded) == 3
    workspace_only = {rel: data for rel, data in _tree(biz_b).items() if rel != "product/surface.md"}
    assert workspace_only == _tree(biz_a)  # byte-identical Storage resume, including the binary blob
