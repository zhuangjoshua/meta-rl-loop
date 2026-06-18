from pathlib import Path

import pytest

from plugins.takyon import storage


def test_sync_down_replaces_unreadable_local_cache_file(tmp_path):
    bucket = tmp_path / "bucket"
    backend = storage.LocalStorageBackend(bucket)

    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "product").mkdir()
    (source / "product" / "surface.md").write_text("fresh\n", encoding="utf-8")
    storage.sync_up(backend, "scopesync", source)

    dest = tmp_path / "dest"
    (dest / "product").mkdir(parents=True)
    unreadable = dest / "product" / "surface.md"
    unreadable.write_text("stale\n", encoding="utf-8")
    unreadable.chmod(0)
    try:
        report = storage.sync_down(backend, "scopesync", dest, delete_local=True)
    finally:
        unreadable.chmod(0o600)

    assert "product/surface.md" in report.downloaded
    assert (dest / "product" / "surface.md").read_text(encoding="utf-8") == "fresh\n"


# ── per-operator storage quota on the canonical (CAS) commit path ──────────────────────────────────
#
# The live durable commit is plugins.takyon.storage.write_workspace_revision (content-addressed),
# NOT sync_up. These tests prove the new workspace_revision_incoming_bytes helper that the live
# commit chokepoint (_commit_business_workspace_revision) feeds into enforce_operator_storage_quota,
# so a write that would exceed the operator's quota fails closed on a real revision write.


def _seed(root: Path) -> None:
    (root / "product").mkdir(parents=True, exist_ok=True)
    (root / "product" / "a.txt").write_bytes(b"x" * 100)
    (root / "product" / "b.txt").write_bytes(b"y" * 200)


def test_workspace_revision_incoming_bytes_counts_only_new_cas_bytes(tmp_path):
    """incoming_bytes for a FRESH business = full source byte size; after that revision is written,
    a re-commit of the SAME tree reports 0 incoming (CAS dedup — nothing new to upload)."""
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    ws = tmp_path / "ws"
    _seed(ws)

    incoming = storage.workspace_revision_incoming_bytes(backend, "acme", ws)
    assert incoming == 300, "first revision must count every source byte as incoming"

    storage.write_workspace_revision(backend, "acme", 1, ws, parent_revision=0)
    # Same tree again: every digest already present → zero new bytes.
    assert storage.workspace_revision_incoming_bytes(backend, "acme", ws) == 0

    # Add a new file: only its bytes are incoming, the unchanged blobs stay deduped.
    (ws / "product" / "c.txt").write_bytes(b"z" * 50)
    assert storage.workspace_revision_incoming_bytes(backend, "acme", ws) == 50


def test_commit_chokepoint_enforcement_fails_closed_when_over_quota(tmp_path):
    """The exact seam the live commit uses: workspace_revision_incoming_bytes feeds
    enforce_operator_storage_quota. With a forced tiny quota the new revision's bytes push the
    operator at/over the limit → StorageQuotaExceeded, and (had core run it) no write would land."""
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    ws = tmp_path / "ws"
    _seed(ws)  # 300 bytes of net-new source

    incoming = storage.workspace_revision_incoming_bytes(backend, "acme", ws)
    assert incoming == 300
    # Tiny 250-byte quota: 0 used + 300 incoming >= 250 → trips closed.
    with pytest.raises(storage.StorageQuotaExceeded):
        storage.enforce_operator_storage_quota(
            backend, ["acme"], incoming, quota_bytes=250
        )


def test_commit_chokepoint_enforcement_allows_when_under_quota(tmp_path):
    """A generous quota lets the same revision through (no exception), and the operator's owned-slug
    aggregate is what's gated — proving the gate blocks only a genuinely over-budget operator."""
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    ws = tmp_path / "ws"
    _seed(ws)
    incoming = storage.workspace_revision_incoming_bytes(backend, "acme", ws)
    # 1 MiB quota, 300 incoming → fine.
    used = storage.enforce_operator_storage_quota(
        backend, ["acme"], incoming, quota_bytes=1024 * 1024
    )
    assert used == 0  # nothing stored yet


def test_operator_quota_env_override_default(monkeypatch):
    """The 5 GiB default holds, and TAKYON_OPERATOR_STORAGE_MAX_BYTES overrides it (canonical env)."""
    monkeypatch.delenv("TAKYON_OPERATOR_STORAGE_MAX_BYTES", raising=False)
    assert storage.operator_storage_max_bytes() == 5 * 1024 * 1024 * 1024
    monkeypatch.setenv("TAKYON_OPERATOR_STORAGE_MAX_BYTES", "12345")
    assert storage.operator_storage_max_bytes() == 12345


# NOTE: the end-to-end commit-path enforcement test (store-level
# _commit_business_workspace_revision fails closed when a revision exceeds the operator quota) lives
# in tests/plugins/test_takyon_storage_pg.py::test_canonical_commit_enforces_operator_quota_fails_closed
# because TakyonStore is Postgres-only (the SQLite control plane is retired), so a real store needs the
# PG rig. The tests above cover the pure leaf + the exact enforce_operator_storage_quota seam the
# commit path feeds, with no DB.
