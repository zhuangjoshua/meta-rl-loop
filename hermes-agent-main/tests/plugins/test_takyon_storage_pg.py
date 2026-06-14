"""Tests for the externalized per-business filesystem (``plugins/takyon/storage.py``, Phase 7) — the
leaf that makes the runtime host stateless so a second runtime resumes a business from Postgres +
the object store, never from local disk.

Proves the Phase 7 contract:
  * **the no-fleet proof** (the acceptance): a business's workspace written + synced up on "host A" is
    resumed BYTE-IDENTICAL on "host B" — a fresh, empty scratch dir — where host B learns the business
    only from Postgres (the ``businesses`` row) and pulls every file from the store. Postgres + Storage,
    no shared disk;
  * sync is content-**digest incremental** (an unchanged file is skipped on re-sync, only changed bytes
    move) and **integrity-checked** (a blob whose sha256 ≠ its recorded digest is refused, not landed);
  * ``delete_remote``/``delete_local`` give faithful mirror semantics (a deletion propagates);
  * **path containment** — an object key can never escape the business prefix;
  * **backend selection is one seam, two impls** — ``local`` is the credential-free default unless
    a full Supabase object-store config is provisioned; ``supabase_s3`` still BLOCKS with a reason
    when selected unprovisioned (invariant #8), never silently downgrading to local;
  * :func:`with_business_workspace` syncs down→up on a clean run but, by the crash discipline, does NOT
    sync up on an exception, so a crashed run never clobbers the last good remote state.

Most tests exercise the pure leaf (no DB); the headline no-fleet test ties it to a real Postgres
business. The module skips entirely unless psycopg is importable.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
import sys
import types

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import storage  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.core import TakyonStore  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────


def _backend(tmp_path) -> storage.LocalStorageBackend:
    """A real local-directory object store standing in for 'the bucket'."""
    return storage.LocalStorageBackend(tmp_path / "bucket")


def _seed_workspace(root: Path) -> None:
    """Write a realistic four-root workspace (text + a binary blob) under ``root``."""
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "strategy.md").write_text("# Acme\nGoal: win the market\n")
    (root / "product").mkdir(parents=True, exist_ok=True)
    (root / "product" / "surface.md").write_text("# Product Surface\n\n- Source path: product/site\n")
    (root / "metrics" / "receipts").mkdir(parents=True, exist_ok=True)
    (root / "metrics" / "receipts" / "r1.json").write_bytes(b"\x00\x01\x02 binary receipt")


def _tree(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root`` as {posix-relpath: bytes} — for byte-identity assertions."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def _business(pg_conn) -> tuple[str, str]:
    """Provision a user + a business they own; return (slug, owner_user_id)."""
    uid, _created, _raw = provision_user_on_first_login(pg_conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    return slug, uid


# ── the no-fleet proof (Postgres + Storage) ───────────────────────────────────────────────────────


def test_no_fleet_resume_from_postgres_and_storage(pg_conn, tmp_path):
    # A real PG-backed business. Host A builds its workspace on local scratch and syncs up.
    slug, uid = _business(pg_conn)
    backend = _backend(tmp_path)
    host_a = tmp_path / "host-a-scratch"
    _seed_workspace(host_a)
    up = storage.sync_up(backend, slug, host_a)
    assert len(up.uploaded) == 3 and up.deleted == ()

    # Host B is a SECOND runtime on an empty disk. It learns the business only from Postgres...
    host_b = tmp_path / "host-b-scratch"
    host_b.mkdir()
    assert _tree(host_b) == {}  # genuinely empty before resume
    (resumed_slug,) = pg_conn.execute(
        "select slug from businesses where owner_user_id = %s", (uid,)
    ).fetchone()

    # ...and reconstructs the entire workspace from the store. That is the acceptance.
    down = storage.sync_down(backend, resumed_slug, host_b)
    assert len(down.downloaded) == 3
    assert _tree(host_b) == _tree(host_a)  # byte-identical resume, including the binary blob


def test_two_businesses_are_isolated_in_the_store(pg_conn, tmp_path):
    # Each business lives under its own prefix; resuming one never leaks the other's files.
    slug_a, _ = _business(pg_conn)
    slug_b, _ = _business(pg_conn)
    backend = _backend(tmp_path)

    a_src = tmp_path / "a"
    (a_src).mkdir()
    (a_src / "research").mkdir()
    (a_src / "research" / "strategy.md").write_text("A secrets\n")
    storage.sync_up(backend, slug_a, a_src)

    b_dest = tmp_path / "b"
    b_dest.mkdir()
    storage.sync_down(backend, slug_b, b_dest)
    assert _tree(b_dest) == {}  # business B sees nothing of business A


# ── incrementality + integrity ─────────────────────────────────────────────────────────────────


def test_sync_is_digest_incremental_only_changed_bytes_move(tmp_path):
    backend = _backend(tmp_path)
    src = tmp_path / "src"
    _seed_workspace(src)
    assert len(storage.sync_up(backend, "biz-x", src).uploaded) == 3

    # Re-up with no changes: everything is skipped, nothing re-uploaded.
    again = storage.sync_up(backend, "biz-x", src)
    assert again.uploaded == () and len(again.skipped) == 3

    # Change exactly one file: only that one moves.
    (src / "research" / "strategy.md").write_text("# Acme\nGoal: REVISED\n")
    delta = storage.sync_up(backend, "biz-x", src)
    assert delta.uploaded == ("research/strategy.md",)
    assert len(delta.skipped) == 2

    # And a fresh host now resumes the revised bytes.
    dest = tmp_path / "dest"
    dest.mkdir()
    storage.sync_down(backend, "biz-x", dest)
    assert (dest / "research" / "strategy.md").read_text() == "# Acme\nGoal: REVISED\n"


def test_sync_down_refuses_a_corrupt_blob(tmp_path):
    # A backend whose listing advertises a digest that its bytes don't match — sync_down must catch the
    # mismatch and raise, never write a corrupt file. (Robustness rail: integrity before it lands.)
    class _LyingBackend:
        name = "lying"

        def list_digests(self, prefix):
            return {f"{prefix}research/strategy.md": "0" * 64}  # claimed sha256 that won't match

        def get(self, key):
            return b"actual bytes that hash to something else"

        def put(self, key, data, *, digest):  # pragma: no cover - unused here
            raise AssertionError

        def delete(self, key):  # pragma: no cover - unused here
            raise AssertionError

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(storage.StorageError, match="integrity check failed"):
        storage.sync_down(_LyingBackend(), "biz-x", dest)
    assert _tree(dest) == {}  # nothing landed


# ── mirror semantics ───────────────────────────────────────────────────────────────────────────


def test_delete_remote_mirrors_local_deletions(tmp_path):
    backend = _backend(tmp_path)
    src = tmp_path / "src"
    _seed_workspace(src)
    storage.sync_up(backend, "biz-x", src)

    # Delete a file locally, then sync up WITH mirror deletion.
    (src / "product" / "surface.md").unlink()
    report = storage.sync_up(backend, "biz-x", src, delete_remote=True)
    assert report.deleted == ("product/surface.md",)

    # A fresh resume no longer has the deleted file.
    dest = tmp_path / "dest"
    dest.mkdir()
    storage.sync_down(backend, "biz-x", dest)
    assert "product/surface.md" not in _tree(dest)
    assert "research/strategy.md" in _tree(dest)


def test_additive_sync_up_default_does_not_delete(tmp_path):
    # Without delete_remote, a missing-locally file is left untouched remotely (safe default).
    backend = _backend(tmp_path)
    src = tmp_path / "src"
    _seed_workspace(src)
    storage.sync_up(backend, "biz-x", src)
    (src / "product" / "surface.md").unlink()
    report = storage.sync_up(backend, "biz-x", src)  # default: additive
    assert report.deleted == ()
    assert backend.get("biz-x/product/surface.md") == b"# Product Surface\n\n- Source path: product/site\n"  # still there


def test_sync_up_excluded_prefixes_preserve_remote_product_site_even_with_delete_remote(tmp_path):
    backend = _backend(tmp_path)
    src = tmp_path / "src"
    _seed_workspace(src)
    (src / "product" / "site").mkdir(parents=True, exist_ok=True)
    (src / "product" / "site" / "index.html").write_text("<h1>Fresh build</h1>\n", encoding="utf-8")
    storage.sync_up(backend, "biz-x", src)

    # Simulate an operator cache that does not own product/site: it still has the rest of the
    # workspace, but its local product/site tree is stale or absent. The exclusion must preserve
    # the canonical product/site bytes even when the sync uses delete_remote mirror semantics.
    shutil.rmtree(src / "product" / "site")
    report = storage.sync_up(
        backend,
        "biz-x",
        src,
        delete_remote=True,
        exclude_prefixes=("product/site",),
    )

    assert "product/site/index.html" not in report.deleted
    assert backend.get("biz-x/product/site/index.html") == b"<h1>Fresh build</h1>\n"


# ── path containment ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", "a/../../b", "", "."])
def test_object_key_cannot_escape_business_prefix(tmp_path, bad):
    backend = _backend(tmp_path)
    with pytest.raises(storage.UnsafePath):
        backend.get(bad)


@pytest.mark.parametrize("bad", ["../x", "Has Space", "", "a/b", "."])
def test_prefix_rejects_unsafe_slug(bad):
    with pytest.raises(storage.UnsafePath):
        storage.object_prefix(bad)


def test_prefix_normalizes_case_rather_than_escaping():
    # Case is collapsed (matches core._slugify) — safe, since casing can't escape a prefix.
    assert storage.object_prefix("UPPER") == "upper/"


# ── backend selection (one seam, two impls) ──────────────────────────────────────────────────────


def test_default_backend_is_local_and_credential_free(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TAKYON_STORAGE_BACKEND", raising=False)
    for key in (
        "SUPABASE_S3_ENDPOINT",
        "SUPABASE_S3_REGION",
        "SUPABASE_S3_ACCESS_KEY_ID",
        "SUPABASE_S3_SECRET_ACCESS_KEY",
        "TAKYON_STORAGE_BUCKET",
    ):
        monkeypatch.delenv(key, raising=False)
    backend = storage.get_storage_backend(root=tmp_path / "b")
    assert backend.name == "local"


def test_full_supabase_config_becomes_default_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TAKYON_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("SUPABASE_S3_ENDPOINT", "https://example.supabase.co/storage/v1/s3")
    monkeypatch.setenv("SUPABASE_S3_REGION", "us-east-2")
    monkeypatch.setenv("SUPABASE_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SUPABASE_S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("TAKYON_STORAGE_BUCKET", "business-workspaces")
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(client=lambda *args, **kwargs: object()),
    )

    backend = storage.get_storage_backend()
    assert backend.name == "supabase_s3"


def test_explicit_local_override_beats_full_supabase_config(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("SUPABASE_S3_ENDPOINT", "https://example.supabase.co/storage/v1/s3")
    monkeypatch.setenv("SUPABASE_S3_REGION", "us-east-2")
    monkeypatch.setenv("SUPABASE_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SUPABASE_S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("TAKYON_STORAGE_BUCKET", "business-workspaces")

    backend = storage.get_storage_backend(root=tmp_path / "b")
    assert backend.name == "local"


def test_live_backend_selected_but_unconfigured_blocks_with_reason(monkeypatch, tmp_path):
    # Invariant #8: explicit opt-in to the live backend with no creds -> blocked-with-reason, NEVER a
    # silent fall back to local and NEVER a fake "synced".
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "supabase_s3")
    for k in (
        "SUPABASE_S3_ENDPOINT",
        "SUPABASE_S3_REGION",
        "SUPABASE_S3_ACCESS_KEY_ID",
        "SUPABASE_S3_SECRET_ACCESS_KEY",
        "TAKYON_STORAGE_BUCKET",
    ):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(storage.StorageUnconfigured) as exc:
        storage.get_storage_backend()
    assert "supabase_s3" in str(exc.value)
    assert "SUPABASE_S3_ENDPOINT" in str(exc.value)  # names the missing creds


def test_unknown_backend_kind_is_rejected(monkeypatch):
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "ftp")
    with pytest.raises(storage.StorageError, match="unknown TAKYON_STORAGE_BACKEND"):
        storage.get_storage_backend()


def test_supabase_backend_uses_cache_business_root(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TAKYON_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("SUPABASE_S3_ENDPOINT", "https://example.supabase.co/storage/v1/s3")
    monkeypatch.setenv("SUPABASE_S3_REGION", "us-east-2")
    monkeypatch.setenv("SUPABASE_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SUPABASE_S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("TAKYON_STORAGE_BUCKET", "business-workspaces")
    store = TakyonStore(root=tmp_path)
    assert store._business_root("Acme", sync=False) == (tmp_path / "cache" / "businesses" / "acme")


# ── worker integration seam + crash discipline ───────────────────────────────────────────────────


def test_with_business_workspace_syncs_down_then_up_on_clean_exit(tmp_path):
    backend = _backend(tmp_path)
    seed = tmp_path / "seed"
    _seed_workspace(seed)
    storage.sync_up(backend, "biz-x", seed)

    scratch = tmp_path / "scratch"
    with storage.with_business_workspace(backend, "biz-x", scratch) as root:
        assert (root / "research" / "strategy.md").exists()  # synced down on enter
        (root / "metrics" / "summary.md").write_text("pulse\n")  # the run produces a new file

    # Clean exit synced up: a fresh resume sees the new file.
    dest = tmp_path / "dest"
    dest.mkdir()
    storage.sync_down(backend, "biz-x", dest)
    assert (dest / "metrics" / "summary.md").read_text() == "pulse\n"


def test_with_business_workspace_does_not_sync_up_on_exception(tmp_path):
    backend = _backend(tmp_path)
    seed = tmp_path / "seed"
    _seed_workspace(seed)
    storage.sync_up(backend, "biz-x", seed)
    before = backend.list_digests(storage.object_prefix("biz-x"))

    scratch = tmp_path / "scratch"
    with pytest.raises(RuntimeError, match="boom"):
        with storage.with_business_workspace(backend, "biz-x", scratch) as root:
            (root / "research" / "strategy.md").write_text("HALF-WRITTEN, crash mid-run\n")
            raise RuntimeError("boom")

    # The crash did NOT sync up: the last good remote state is preserved untouched.
    after = backend.list_digests(storage.object_prefix("biz-x"))
    assert after == before


def test_with_business_workspace_can_sync_partial_progress_on_exception(tmp_path):
    backend = _backend(tmp_path)
    seed = tmp_path / "seed"
    _seed_workspace(seed)
    storage.sync_up(backend, "biz-x", seed)

    scratch = tmp_path / "scratch"
    with pytest.raises(RuntimeError, match="boom"):
        with storage.with_business_workspace(
            backend,
            "biz-x",
            scratch,
            sync_on_exception=True,
        ) as root:
            (root / "metrics").mkdir(parents=True, exist_ok=True)
            (root / "metrics" / "summary.md").write_text("partial progress\n")
            raise RuntimeError("boom")

    resumed = tmp_path / "resumed"
    resumed.mkdir()
    storage.sync_down(backend, "biz-x", resumed)
    assert (resumed / "metrics" / "summary.md").read_text() == "partial progress\n"


def test_mounted_business_workspace_syncs_down_and_cleans_without_syncing_back(tmp_path):
    backend = _backend(tmp_path)
    seed = tmp_path / "seed"
    _seed_workspace(seed)
    storage.sync_up(backend, "biz-x", seed)
    before = backend.list_digests(storage.object_prefix("biz-x"))

    home_ref: Path | None = None
    with storage.mounted_business_workspace(backend, "biz-x", owner_label="tester") as home:
        home_ref = home
        scratch = home / "businesses" / "biz-x"
        assert (scratch / "research" / "strategy.md").exists()
        (scratch / "metrics").mkdir(parents=True, exist_ok=True)
        (scratch / "metrics" / "summary.md").write_text("scratch only\n")

    assert home_ref is not None
    assert not home_ref.exists()
    after = backend.list_digests(storage.object_prefix("biz-x"))
    assert after == before


def test_mounted_business_workspace_defaults_under_takyon_home(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    seed = tmp_path / "seed"
    _seed_workspace(seed)
    storage.sync_up(backend, "biz-x", seed)
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / ".takyon-home"))

    with storage.mounted_business_workspace(backend, "biz-x", owner_label="tester") as home:
        assert home.parent == (tmp_path / ".takyon-home" / "tmp" / "workspaces").resolve()
        assert (home / "businesses" / "biz-x" / "research" / "strategy.md").exists()


def test_sync_excludes_dependency_and_build_caches(tmp_path):
    backend = _backend(tmp_path)
    src = tmp_path / "src"
    _seed_workspace(src)
    (src / "product" / "site" / "node_modules").mkdir(parents=True, exist_ok=True)
    (src / "product" / "site" / "node_modules" / "left-pad.js").write_text("module.exports = 1;\n")
    (src / "product" / "site" / ".next" / "server").mkdir(parents=True, exist_ok=True)
    (src / "product" / "site" / ".next" / "server" / "app.js").write_text("compiled\n")
    (src / "product" / "site" / "src").mkdir(parents=True, exist_ok=True)
    (src / "product" / "site" / "src" / "app.js").write_text("source\n")

    report = storage.sync_up(backend, "biz-x", src, delete_remote=True)
    assert "product/site/node_modules/left-pad.js" not in report.uploaded
    assert "product/site/.next/server/app.js" not in report.uploaded
    assert "product/site/src/app.js" in report.uploaded

    backend.put(
        "biz-x/product/site/node_modules/legacy/index.js",
        b"legacy\n",
        digest=storage.digest_bytes(b"legacy\n"),
    )

    dest = tmp_path / "dest"
    dest.mkdir()
    down = storage.sync_down(backend, "biz-x", dest)
    assert "product/site/node_modules/legacy/index.js" in down.skipped
    assert not (dest / "product" / "site" / "node_modules").exists()
    assert not (dest / "product" / "site" / ".next").exists()
    assert (dest / "product" / "site" / "src" / "app.js").read_text() == "source\n"

    cleanup = storage.sync_up(backend, "biz-x", src, delete_remote=True)
    assert "product/site/node_modules/legacy/index.js" in cleanup.deleted


def test_supabase_listing_skips_head_requests_for_excluded_paths():
    class _Paginator:
        def paginate(self, **_kwargs):
            yield {
                "Contents": [
                    {"Key": "biz-x/product/site/node_modules/pkg/index.js"},
                    {"Key": "biz-x/product/site/index.html"},
                ]
            }

    class _Client:
        def __init__(self):
            self.head_calls: list[str] = []

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return _Paginator()

        def head_object(self, *, Bucket, Key):
            self.head_calls.append(Key)
            assert Bucket == "bucket"
            return {"Metadata": {"sha256": "abc123"}}

        def get_object(self, *, Bucket, Key):  # pragma: no cover - should not be reached here
            raise AssertionError((Bucket, Key))

    backend = storage.SupabaseS3StorageBackend.__new__(storage.SupabaseS3StorageBackend)
    backend.bucket = "bucket"
    backend._client = _Client()

    digests = backend.list_digests("biz-x/")
    assert digests["biz-x/product/site/node_modules/pkg/index.js"] == "<excluded>"
    assert digests["biz-x/product/site/index.html"] == "abc123"
    assert backend._client.head_calls == ["biz-x/product/site/index.html"]


def test_supabase_listing_skips_objects_that_vanish_after_list():
    class _Paginator:
        def paginate(self, **_kwargs):
            yield {
                "Contents": [
                    {"Key": "biz-x/product/site/index.html"},
                    {"Key": "biz-x/product/site/missing.html"},
                ]
            }

    class _ClientError(Exception):
        def __init__(self, code: str):
            self.response = {
                "Error": {"Code": code},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            }
            super().__init__(code)

    class _Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return _Paginator()

        def head_object(self, *, Bucket, Key):
            assert Bucket == "bucket"
            if Key.endswith("missing.html"):
                raise _ClientError("NotFound")
            return {"Metadata": {"sha256": "abc123"}}

        def get_object(self, *, Bucket, Key):  # pragma: no cover - should not be reached here
            raise AssertionError((Bucket, Key))

    backend = storage.SupabaseS3StorageBackend.__new__(storage.SupabaseS3StorageBackend)
    backend.bucket = "bucket"
    backend._client = _Client()

    digests = backend.list_digests("biz-x/")
    assert digests == {"biz-x/product/site/index.html": "abc123"}
