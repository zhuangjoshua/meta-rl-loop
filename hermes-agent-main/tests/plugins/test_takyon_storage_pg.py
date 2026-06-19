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

import plugins.takyon.core as core_module  # noqa: E402
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


def test_live_build_pointer_uses_transaction_local_set_config(monkeypatch, tmp_path):
    executed: list[tuple[str, tuple[object, ...] | None]] = []

    class _CursorResult:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, sql, params=None):
            normalized = tuple(params) if params is not None else None
            executed.append((sql, normalized))
            if "SELECT live_build_id" in sql:
                return _CursorResult({"live_build_id": "build123"})
            return _CursorResult({"set_config": normalized[0] if normalized else None})

    class _FakeStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def _connect(self):
            return _FakeConn()

    monkeypatch.setattr(core_module, "TakyonStore", _FakeStore)

    assert (
        core_module.live_build_pointer(
            "Lotest",
            takyon_home=tmp_path,
            database_url="postgresql://example.invalid/postgres",
            timeout_ms=2500,
        )
        == "build123"
    )
    assert executed == [
        ("SELECT set_config('statement_timeout', ?, true)", ("2500ms",)),
        ("SELECT live_build_id FROM app_surface_contracts WHERE business_slug = ?", ("lotest",)),
    ]


# ── per-operator storage quota + deletion (S3 storage limits per person + deletion) ─────────────────


def _owned_business(pg_conn, owner_user_id: str) -> str:
    """Add another business under an EXISTING operator; return its slug."""
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", owner_user_id),
    )
    return slug


def test_list_object_sizes_reports_real_bytes(tmp_path):
    backend = _backend(tmp_path)
    src = tmp_path / "src"
    (src / "research").mkdir(parents=True)
    (src / "research" / "a.md").write_bytes(b"x" * 100)
    (src / "research" / "b.md").write_bytes(b"y" * 250)
    storage.sync_up(backend, "biz-x", src)

    sizes = backend.list_object_sizes(storage.object_prefix("biz-x"))
    assert sizes["biz-x/research/a.md"] == 100
    assert sizes["biz-x/research/b.md"] == 250
    assert storage.prefix_bytes(backend, storage.object_prefix("biz-x")) == 350


def test_operator_storage_bytes_aggregates_across_owned_businesses(tmp_path):
    backend = _backend(tmp_path)
    for slug, n in (("biz-a", 100), ("biz-b", 200)):
        src = tmp_path / slug
        (src).mkdir()
        (src / "f.bin").write_bytes(b"z" * n)
        storage.sync_up(backend, slug, src)
    # The operator owns both -> usage is the SUM, not per-business.
    assert storage.operator_storage_bytes(backend, ["biz-a", "biz-b"]) == 300
    # De-duplicates a repeated slug rather than double-counting.
    assert storage.operator_storage_bytes(backend, ["biz-a", "biz-a"]) == 100


def test_enforce_operator_quota_trips_at_or_above_limit(tmp_path):
    backend = _backend(tmp_path)
    src = tmp_path / "biz-a"
    src.mkdir()
    (src / "f.bin").write_bytes(b"z" * 900)
    storage.sync_up(backend, "biz-a", src)

    # used=900, incoming=50 -> 950 < 1000 : allowed, returns prior usage.
    assert storage.enforce_operator_storage_quota(backend, ["biz-a"], 50, quota_bytes=1000) == 900
    # used=900, incoming=100 -> 1000 >= 1000 : refused AT the limit.
    with pytest.raises(storage.StorageQuotaExceeded):
        storage.enforce_operator_storage_quota(backend, ["biz-a"], 100, quota_bytes=1000)


def test_sync_up_enforces_operator_quota_before_uploading(tmp_path):
    backend = _backend(tmp_path)
    # Pre-fill another business owned by the same operator so the operator is already near the cap.
    other = tmp_path / "biz-other"
    other.mkdir()
    (other / "big.bin").write_bytes(b"z" * 900)
    storage.sync_up(backend, "biz-other", other)

    src = tmp_path / "biz-new"
    src.mkdir()
    (src / "payload.bin").write_bytes(b"z" * 200)  # would push 900+200 over a 1000 cap
    with pytest.raises(storage.StorageQuotaExceeded):
        storage.sync_up(
            backend,
            "biz-new",
            src,
            operator_owned_slugs=["biz-other", "biz-new"],
            operator_quota_bytes=1000,
        )
    # Fail-closed: the over-quota push uploaded nothing.
    assert backend.list_digests(storage.object_prefix("biz-new")) == {}


def test_business_deletion_purges_only_its_own_prefix(tmp_path):
    backend = _backend(tmp_path)
    for slug in ("biz-a", "biz-b"):
        src = tmp_path / slug
        src.mkdir()
        (src / "f.md").write_text("data\n")
        storage.sync_up(backend, slug, src)

    removed = storage.delete_prefix(backend, storage.object_prefix("biz-a"))
    assert removed == ["biz-a/f.md"]
    assert backend.list_digests(storage.object_prefix("biz-a")) == {}
    # Business B's objects survive untouched.
    assert set(backend.list_digests(storage.object_prefix("biz-b"))) == {"biz-b/f.md"}


def test_purge_operator_storage_removes_all_owned_business_objects(tmp_path):
    backend = _backend(tmp_path)
    for slug in ("biz-a", "biz-b"):
        src = tmp_path / slug
        src.mkdir()
        (src / "f.md").write_text("data\n")
        storage.sync_up(backend, slug, src)
    # A business owned by a DIFFERENT operator must NOT be touched.
    other = tmp_path / "biz-other"
    other.mkdir()
    (other / "f.md").write_text("other\n")
    storage.sync_up(backend, "biz-other", other)

    removed = storage.purge_operator_storage(backend, ["biz-a", "biz-b"])
    assert removed == {"biz-a": ["biz-a/f.md"], "biz-b": ["biz-b/f.md"]}
    assert backend.list_digests(storage.object_prefix("biz-a")) == {}
    assert backend.list_digests(storage.object_prefix("biz-b")) == {}
    assert set(backend.list_digests(storage.object_prefix("biz-other"))) == {"biz-other/f.md"}


def test_store_resolves_operator_slugs_and_storage_bytes(pg_store_dsn, tmp_path, monkeypatch):
    # The TakyonStore glue: owner -> owned slugs -> aggregated object-store bytes,
    # and a full operator purge that strands no objects in the bucket. Exercised through the store's
    # OWN connection (its ``?``->``%s`` adapter), the way the production delete rail calls it. The
    # owner + businesses are seeded through that SAME connection so they live in the store's database.
    monkeypatch.setenv("TAKYON_ALLOW_REMOTE_STORAGE_SYNC_OUTSIDE_VPS", "1")

    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    store = TakyonStore(root=tmp_path, database_url=pg_store_dsn)
    store._workspace_storage_backend_override = backend

    slug_a = f"biz-{uuid.uuid4().hex[:8]}"
    slug_b = f"biz-{uuid.uuid4().hex[:8]}"
    # Seed the owner + businesses through a RAW psycopg connection to the SAME database the store
    # uses (control_plane speaks native ``%s``; the store adapter speaks ``?``).
    with psycopg.connect(pg_store_dsn, autocommit=False) as seed:
        owner, _created, _raw = provision_user_on_first_login(seed, f"auth0|{uuid.uuid4().hex}")
        for slug in (slug_a, slug_b):
            seed.execute(
                "INSERT INTO businesses (slug, name, owner_user_id) VALUES (%s, %s, %s)",
                (slug, "Acme", owner),
            )
        seed.commit()

    for slug, n in ((slug_a, 100), (slug_b, 250)):
        src = tmp_path / slug
        src.mkdir()
        (src / "f.bin").write_bytes(b"z" * n)
        storage.sync_up(backend, slug, src)

    with store._connect() as conn:
        assert sorted(store._owner_business_slugs(conn, owner)) == sorted([slug_a, slug_b])
        assert store._operator_storage_bytes(conn, owner) == 350
        removed = store._purge_operator_storage(conn, owner)
    assert set(removed) == {slug_a, slug_b}
    assert storage.operator_storage_bytes(backend, [slug_a, slug_b]) == 0


def test_canonical_commit_enforces_operator_quota_fails_closed(pg_store_dsn, tmp_path, monkeypatch):
    """The LIVE durable commit chokepoint (``_commit_business_workspace_revision`` →
    ``write_workspace_revision``) now enforces the per-operator storage quota. With a forced tiny
    quota a commit whose net-new CAS bytes exceed the operator's limit fails CLOSED
    (``StorageQuotaExceeded``) and writes NO revision row / NO blobs; a generous quota lets the same
    commit through. This is the gap the fix closes: previously the quota lived only on the test-only
    ``sync_up`` path and was never reached by a real commit."""
    monkeypatch.setenv("TAKYON_ALLOW_REMOTE_STORAGE_SYNC_OUTSIDE_VPS", "1")

    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    store = TakyonStore(root=tmp_path, database_url=pg_store_dsn)
    store._workspace_storage_backend_override = backend

    slug = f"biz-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_store_dsn, autocommit=False) as seed:
        owner, _created, _raw = provision_user_on_first_login(seed, f"auth0|{uuid.uuid4().hex}")
        seed.execute(
            "INSERT INTO businesses (slug, name, owner_user_id) VALUES (%s, %s, %s)",
            (slug, "Acme", owner),
        )
        seed.commit()

    # Seed a local workspace with ~300 net-new source bytes.
    ws = store._business_root(slug, sync=False)
    (ws / "product").mkdir(parents=True, exist_ok=True)
    (ws / "product" / "a.txt").write_bytes(b"x" * 300)

    # Forced tiny quota: the 300 incoming bytes trip the gate at/over 250 → commit fails closed.
    monkeypatch.setenv("TAKYON_OPERATOR_STORAGE_MAX_BYTES", "250")
    with store._connect() as conn:
        with conn:
            with pytest.raises(storage.StorageQuotaExceeded):
                store._commit_business_workspace_revision(
                    conn, slug, actor="operator", reason="quota test"
                )
    # No revision landed and no blobs were uploaded.
    assert storage.operator_storage_bytes(backend, [slug]) == 0
    with store._connect() as conn:
        assert store._business_head_revision_from_conn(conn, slug) == 0

    # Generous quota: the same commit now succeeds and persists a revision + bytes.
    monkeypatch.setenv("TAKYON_OPERATOR_STORAGE_MAX_BYTES", str(10 * 1024 * 1024))
    with store._connect() as conn:
        with conn:
            rev = store._commit_business_workspace_revision(
                conn, slug, actor="operator", reason="quota ok"
            )
    assert rev == 1
    assert storage.operator_storage_bytes(backend, [slug]) >= 300


def _seed_quota_free_business(pg_store_dsn, tmp_path, monkeypatch):
    """A store + local backend + empty business with the storage gates relaxed for commit tests."""
    monkeypatch.setenv("TAKYON_ALLOW_REMOTE_STORAGE_SYNC_OUTSIDE_VPS", "1")
    monkeypatch.setenv("TAKYON_OPERATOR_STORAGE_MAX_BYTES", str(64 * 1024 * 1024))
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    store = TakyonStore(root=tmp_path, database_url=pg_store_dsn)
    store._workspace_storage_backend_override = backend
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_store_dsn, autocommit=False) as seed:
        owner, _created, _raw = provision_user_on_first_login(seed, f"auth0|{uuid.uuid4().hex}")
        seed.execute(
            "INSERT INTO businesses (slug, name, owner_user_id) VALUES (%s, %s, %s)",
            (slug, "Acme", owner),
        )
        seed.commit()
    return store, slug


def test_stale_base_conflict_on_commentary_surface_auto_resolves_to_head(pg_store_dsn, tmp_path, monkeypatch):
    """A stale committer whose ONLY conflict is the generated commentary render product/surface.md no
    longer hard-fails ("stale workspace base"). The conflict auto-resolves to head's committed render
    (commentary carries no authoritative state — the real publish pointer lives in the
    app_surface_contracts DB row), while the committer's genuine source change is preserved. This is
    the bootstrap thrash fix: business_upsert_app_surface_contract / business_refresh_product_surface /
    the build-worker sync all re-render surface.md against slightly different bases and used to thrash."""
    store, slug = _seed_quota_free_business(pg_store_dsn, tmp_path, monkeypatch)
    ws = store._business_root(slug, sync=False)
    (ws / "product" / "site").mkdir(parents=True, exist_ok=True)

    # r1 — base: a real source file + the generated commentary render.
    (ws / "product" / "site" / "app.tsx").write_text("export const A = 1\n", encoding="utf-8")
    (ws / "product" / "surface.md").write_text("# Product Surface\nrender v1\n", encoding="utf-8")
    with store._connect() as conn:
        with conn:
            r1 = store._commit_business_workspace_revision(conn, slug, actor="agent", reason="r1", expected_base_revision=0)
    assert r1 == 1

    # r2 — upstream advances head by re-rendering ONLY surface.md (app.tsx untouched).
    (ws / "product" / "surface.md").write_text("# Product Surface\nrender v2 (head)\n", encoding="utf-8")
    with store._connect() as conn:
        with conn:
            r2 = store._commit_business_workspace_revision(conn, slug, actor="agent", reason="r2", expected_base_revision=r1)
    assert r2 == 2

    # Stale committer pinned to r1: a real source edit PLUS its own (different) surface.md render.
    (ws / "product" / "site" / "app.tsx").write_text("export const A = 2\n", encoding="utf-8")
    (ws / "product" / "surface.md").write_text("# Product Surface\nrender v3 (stale local)\n", encoding="utf-8")
    with store._connect() as conn:
        with conn:
            r3 = store._commit_business_workspace_revision(conn, slug, actor="agent", reason="r3", expected_base_revision=r1)
    # No raise; a new revision lands.
    assert r3 == 3

    # Head's surface render won (the stale local render was dropped); the real source edit survived.
    assert (ws / "product" / "surface.md").read_text(encoding="utf-8") == "# Product Surface\nrender v2 (head)\n"
    assert (ws / "product" / "site" / "app.tsx").read_text(encoding="utf-8") == "export const A = 2\n"


def test_stale_base_conflict_on_substantive_source_still_raises(pg_store_dsn, tmp_path, monkeypatch):
    """A genuine concurrent edit to a SUBSTANTIVE file (real product source) must still hard-fail — the
    commentary carve-out must not silently merge away real conflicting source changes."""
    store, slug = _seed_quota_free_business(pg_store_dsn, tmp_path, monkeypatch)
    ws = store._business_root(slug, sync=False)
    (ws / "product" / "site").mkdir(parents=True, exist_ok=True)

    (ws / "product" / "site" / "app.tsx").write_text("export const A = 1\n", encoding="utf-8")
    with store._connect() as conn:
        with conn:
            r1 = store._commit_business_workspace_revision(conn, slug, actor="agent", reason="r1", expected_base_revision=0)

    # Upstream advances head by editing the SAME source file.
    (ws / "product" / "site" / "app.tsx").write_text("export const A = 2\n", encoding="utf-8")
    with store._connect() as conn:
        with conn:
            store._commit_business_workspace_revision(conn, slug, actor="agent", reason="r2", expected_base_revision=r1)

    # Stale committer pinned to r1 makes a THIRD, different edit to the same source file → true conflict.
    (ws / "product" / "site" / "app.tsx").write_text("export const A = 3\n", encoding="utf-8")
    with store._connect() as conn:
        with conn:
            with pytest.raises(core_module.TakyonError, match="stale workspace base"):
                store._commit_business_workspace_revision(conn, slug, actor="agent", reason="r3", expected_base_revision=r1)
