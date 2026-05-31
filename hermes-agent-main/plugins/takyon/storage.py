"""Externalized per-business filesystem (Phase 7) — the leaf that makes the host stateless.

Today the per-business workspace (the four canonical roots ``product/``, ``distribution/``,
``research/``, ``metrics/``) lives only on the local disk of whichever box ran the CEO. That makes the
host *stateful*: a second runtime on an empty disk cannot resume a business. Phase 7 externalizes that
workspace to an object store so the contract becomes **sync-down → run → sync-up**, with local disk as
pure scratch. A second host then resumes a business from Postgres (its identity/jobs/ledger/schedule)
+ the object store (its files) — the "no-fleet proof".

This is a pure leaf, shaped like ``jobs``/``wakes`` and seamed like the AI gateway's
``get_provider_caller``:

  * :class:`StorageBackend` is the seam — a tiny put/get/delete/list contract. :func:`get_storage_backend`
    selects ONE implementation by config, exactly like a provider selector (NOT a second code path):
      - :class:`LocalStorageBackend` — a real local-directory object store. The credential-free default;
        the literal "local disk = scratch" tier and the CI tier. No new dependency, no new credential.
      - :class:`SupabaseS3StorageBackend` — Supabase Storage over its S3-compatible API (lazy ``boto3``).
        Selected only by an explicit ``TAKYON_STORAGE_BACKEND=supabase_s3`` switch + the ``SUPABASE_S3_*``
        creds. If selected while unconfigured (creds or ``boto3`` missing) it raises
        :class:`StorageUnconfigured` — a `blocked`-with-reason, NEVER a silent fall back to local and
        NEVER a fake "synced" (invariant #8). This backend is wired but **unverified against live
        Supabase** (no live creds in this environment); the operator must provision the keys recorded in
        ``mediationplan.md`` Gate 2 before live cutover.

  * :func:`sync_up` / :func:`sync_down` are content-**digest incremental** (unchanged files are skipped)
    and **integrity-checked** (a downloaded blob whose sha256 ≠ the recorded digest raises rather than
    landing corrupt). They reuse the same path-containment discipline as ``core``'s ``_safe_relpath``
    (no absolute paths, no ``.``/``..`` segments, bounded depth) so an object key can never escape the
    business prefix.

  * :func:`with_business_workspace` is the worker-facing integration seam: sync-down on enter, sync-up
    on **clean** exit (and, by default, mirror deletions); on an exception it deliberately does NOT
    sync up, so a crashed run never overwrites the last good remote state — the requeued job re-syncs
    the last good tree. Mounting this around the worker's per-job run is the operator-gated cutover
    step (Phase 6 left the worker loop unmounted the same way); this leaf only provides the proven seam.

No DB migration and no new table: the object store is the source of truth for file bytes + their
listing; Postgres stays the source of truth for business/jobs/ledger/schedule state. (A Postgres
``business_files`` manifest was considered and rejected — it duplicates the store's own listing and
adds a bucket↔table two-write drift hazard.)
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

# Safety rails (durable hardcodes, like core's MAX_WRITE_CHARS / path containment).
MAX_OBJECT_BYTES = 256 * 1024 * 1024  # 256 MiB — bound a single object so a sync can't OOM the host.
_MAX_KEY_DEPTH = 48
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DEFAULT_LOCAL_DIRNAME = "storage"


class StorageError(Exception):
    """Base for storage-leaf failures."""


class ObjectNotFound(StorageError):
    """A requested object key does not exist in the backend."""


class UnsafePath(StorageError):
    """A slug or object key would escape the business prefix (absolute, ``..``, empty, too deep)."""


class StorageUnconfigured(StorageError):
    """The selected live backend is missing its credentials or client library.

    This is the invariant-#8 block: a live sync STOPS with this reason instead of silently falling
    back to the local store or fabricating a "synced" result."""


# ── digest + path safety ─────────────────────────────────────────────────────────────────────────


def digest_bytes(data: bytes) -> str:
    """The one digest space shared by every backend: lowercase hex sha256 of the object bytes."""
    return hashlib.sha256(data).hexdigest()


def _safe_slug(slug: str) -> str:
    raw = str(slug or "").strip().lower()
    if not raw or not _SLUG_RE.match(raw) or len(raw) > 96:
        raise UnsafePath(f"unsafe business slug: {slug!r}")
    return raw


def _safe_rel(value: str, *, field: str = "object path") -> str:
    """Mirror of ``core._safe_relpath``: reject absolute paths, empty/``.``/``..`` segments, and
    pathologically deep keys. Returns a POSIX relative path with no leading slash."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise UnsafePath(f"{field} is required")
    p = Path(raw)
    if p.is_absolute():
        raise UnsafePath(f"{field} must be relative, not absolute: {raw!r}")
    parts = p.parts
    if not parts:  # e.g. "." / "./" — normalizes to nothing, which would resolve to the root
        raise UnsafePath(f"{field} is not a valid object key: {raw!r}")
    if any(part in {"", ".", ".."} for part in parts):
        raise UnsafePath(f"{field} contains an unsafe segment: {raw!r}")
    if len(parts) > _MAX_KEY_DEPTH:
        raise UnsafePath(f"{field} is too deep: {raw!r}")
    return "/".join(parts)


def object_prefix(slug: str) -> str:
    """The object-key namespace for a business: ``"<slug>/"`` (trailing slash, so listing is exact)."""
    return f"{_safe_slug(slug)}/"


def _is_scratch_tempfile(name: str) -> bool:
    # Skip the in-flight temp files atomic writes leave behind (``.<name>.<rand>.tmp``).
    return name.startswith(".") and name.endswith(".tmp")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_file_bytes(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_OBJECT_BYTES:
        raise StorageError(f"object too large to sync ({size} bytes > {MAX_OBJECT_BYTES}): {path}")
    return path.read_bytes()


def _walk_local_digests(root: Path) -> dict[str, str]:
    """Map every regular file under ``root`` to its sha256, keyed by POSIX path relative to ``root``.
    Symlinks and atomic-write temp files are skipped; an unsafe relative path raises."""
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        # Don't follow symlinked directories (containment).
        dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
        for name in filenames:
            abs_path = Path(dirpath, name)
            if abs_path.is_symlink() or _is_scratch_tempfile(name):
                continue
            rel = abs_path.relative_to(root).as_posix()
            _safe_rel(rel)  # raise on anything that couldn't be a safe object key
            out[rel] = digest_bytes(_read_file_bytes(abs_path))
    return out


# ── backend seam ───────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class StorageBackend(Protocol):
    """The whole object-store contract. Keys are full keys including the ``<slug>/`` prefix."""

    name: str

    def put(self, key: str, data: bytes, *, digest: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def list_digests(self, prefix: str) -> dict[str, str]: ...


class LocalStorageBackend:
    """A real local-directory object store. Credential-free; the default and the test/CI tier.

    Files live at ``<root>/<key>``. Digests are computed by reading (no sidecar that could drift), so
    the listing is always exactly the bytes on disk. ``root`` defaults to ``$TAKYON_HOME/storage``
    (overridable via ``TAKYON_STORAGE_LOCAL_DIR``) — deliberately separate from any business *scratch*
    dir, so this stands in for "the bucket"."""

    name = "local"

    def __init__(self, root: str | os.PathLike[str] | None = None):
        if root is None:
            root = os.getenv("TAKYON_STORAGE_LOCAL_DIR") or (
                Path(os.getenv("TAKYON_HOME") or Path.home() / ".takyon").expanduser()
                / _DEFAULT_LOCAL_DIRNAME
            )
        self.root = Path(root).expanduser().resolve()

    def _path(self, key: str) -> Path:
        safe = _safe_rel(key, field="object key")
        path = (self.root / safe).resolve()
        if self.root not in (path, *path.parents):
            raise UnsafePath(f"object key escaped storage root: {key!r}")
        return path

    def put(self, key: str, data: bytes, *, digest: str) -> None:
        if len(data) > MAX_OBJECT_BYTES:
            raise StorageError(f"object too large to put ({len(data)} bytes): {key}")
        _atomic_write_bytes(self._path(key), data)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return _read_file_bytes(path)

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def list_digests(self, prefix: str) -> dict[str, str]:
        safe_prefix = _safe_rel(prefix.rstrip("/"), field="prefix") if prefix.strip("/") else ""
        base = (self.root / safe_prefix) if safe_prefix else self.root
        return {
            f"{safe_prefix}/{rel}" if safe_prefix else rel: dg
            for rel, dg in _walk_local_digests(base).items()
        }


class SupabaseS3StorageBackend:
    """Supabase Storage via its S3-compatible API (lazy ``boto3``).

    Constructed ONLY when explicitly selected (``TAKYON_STORAGE_BACKEND=supabase_s3``). If the
    ``SUPABASE_S3_*`` creds or ``boto3`` are missing it raises :class:`StorageUnconfigured` — the
    invariant-#8 block, never a silent fallback. sha256 is stored in object metadata at ``put`` and
    read back in ``list_digests`` so incrementality matches the local backend's digest space.

    NOTE: wired but **unverified against live Supabase** (no live creds in this environment). Treat as
    cutover-ready code, not a tested path, until the operator provisions the keys in mediationplan Gate 2.
    """

    name = "supabase_s3"
    _META_DIGEST = "sha256"

    def __init__(self) -> None:
        endpoint = os.getenv("SUPABASE_S3_ENDPOINT")
        region = os.getenv("SUPABASE_S3_REGION")
        access_key = os.getenv("SUPABASE_S3_ACCESS_KEY_ID")
        secret_key = os.getenv("SUPABASE_S3_SECRET_ACCESS_KEY")
        bucket = os.getenv("TAKYON_STORAGE_BUCKET")
        missing = [
            name
            for name, val in (
                ("SUPABASE_S3_ENDPOINT", endpoint),
                ("SUPABASE_S3_REGION", region),
                ("SUPABASE_S3_ACCESS_KEY_ID", access_key),
                ("SUPABASE_S3_SECRET_ACCESS_KEY", secret_key),
                ("TAKYON_STORAGE_BUCKET", bucket),
            )
            if not val
        ]
        if missing:
            raise StorageUnconfigured(
                "supabase_s3 storage backend selected but missing: " + ", ".join(missing)
            )
        try:
            import boto3  # noqa: PLC0415 — lazy: the credential-free path never imports this
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise StorageUnconfigured(
                "supabase_s3 storage backend selected but boto3 is not installed"
            ) from exc
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put(self, key: str, data: bytes, *, digest: str) -> None:  # pragma: no cover - live only
        if len(data) > MAX_OBJECT_BYTES:
            raise StorageError(f"object too large to put ({len(data)} bytes): {key}")
        self._client.put_object(
            Bucket=self.bucket,
            Key=_safe_rel(key, field="object key"),
            Body=data,
            Metadata={self._META_DIGEST: digest},
        )

    def get(self, key: str) -> bytes:  # pragma: no cover - live only
        try:
            resp = self._client.get_object(
                Bucket=self.bucket, Key=_safe_rel(key, field="object key")
            )
        except self._client.exceptions.NoSuchKey as exc:
            raise ObjectNotFound(key) from exc
        return resp["Body"].read()

    def delete(self, key: str) -> None:  # pragma: no cover - live only
        self._client.delete_object(Bucket=self.bucket, Key=_safe_rel(key, field="object key"))

    def list_digests(self, prefix: str) -> dict[str, str]:  # pragma: no cover - live only
        out: dict[str, str] = {}
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                head = self._client.head_object(Bucket=self.bucket, Key=key)
                dg = (head.get("Metadata") or {}).get(self._META_DIGEST)
                if not dg:
                    # No recorded digest (e.g. written by something else): hash the bytes so the
                    # listing is still correct rather than guessing from an ETag.
                    dg = digest_bytes(self.get(key))
                out[key] = dg
        return out


def get_storage_backend(*, root: str | os.PathLike[str] | None = None) -> StorageBackend:
    """Select the configured backend (the provider-selector seam). ``TAKYON_STORAGE_BACKEND`` chooses:
    ``local`` (default — credential-free) or ``supabase_s3`` (explicit opt-in; raises
    :class:`StorageUnconfigured` if not provisioned, never silently downgrades to local)."""
    kind = (os.getenv("TAKYON_STORAGE_BACKEND") or "local").strip().lower()
    if kind == "local":
        return LocalStorageBackend(root)
    if kind == "supabase_s3":
        return SupabaseS3StorageBackend()
    raise StorageError(f"unknown TAKYON_STORAGE_BACKEND: {kind!r} (expected 'local' or 'supabase_s3')")


# ── sync ───────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyncReport:
    """What a sync changed. Paths are workspace-relative (no ``<slug>/`` prefix) for readability."""

    uploaded: tuple[str, ...] = ()
    downloaded: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def changed(self) -> int:
        return len(self.uploaded) + len(self.downloaded) + len(self.deleted)


def sync_up(
    backend: StorageBackend, slug: str, src_dir: str | os.PathLike[str], *, delete_remote: bool = False
) -> SyncReport:
    """Push the local workspace ``src_dir`` to the backend under the business prefix. Digest-incremental
    (an unchanged file is skipped). ``delete_remote=True`` mirrors local deletions to the store — only
    safe when ``src_dir`` is the complete, post-successful-run tree (the worker's clean-exit contract);
    the raw primitive defaults to additive/idempotent."""
    prefix = object_prefix(slug)
    src = Path(src_dir).expanduser()
    remote = backend.list_digests(prefix)  # {fullkey: digest}
    local = _walk_local_digests(src)  # {rel: digest}

    uploaded: list[str] = []
    skipped: list[str] = []
    for rel, dg in sorted(local.items()):
        full = prefix + rel
        if remote.get(full) == dg:
            skipped.append(rel)
            continue
        backend.put(full, _read_file_bytes(src / rel), digest=dg)
        uploaded.append(rel)

    deleted: list[str] = []
    if delete_remote:
        local_full = {prefix + rel for rel in local}
        for full in sorted(remote):
            if full not in local_full:
                backend.delete(full)
                deleted.append(full[len(prefix):])
    return SyncReport(tuple(uploaded), (), tuple(deleted), tuple(skipped))


def sync_down(
    backend: StorageBackend, slug: str, dest_dir: str | os.PathLike[str], *, delete_local: bool = False
) -> SyncReport:
    """Pull the business's workspace from the backend into ``dest_dir`` (created if absent). The core of
    the no-fleet resume: an empty ``dest_dir`` is reconstructed entirely from the store. Digest-
    incremental, and every downloaded blob is sha256-verified against its recorded digest before it
    lands (corruption/tamper → :class:`StorageError`, not a bad file). ``delete_local=True`` prunes
    local files the store no longer has (safe because local disk is scratch)."""
    prefix = object_prefix(slug)
    dest = Path(dest_dir).expanduser()
    remote = backend.list_digests(prefix)  # {fullkey: digest}
    local = _walk_local_digests(dest)  # {rel: digest}

    downloaded: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for full, dg in sorted(remote.items()):
        rel = _safe_rel(full[len(prefix):], field="object key")
        seen.add(rel)
        if local.get(rel) == dg:
            skipped.append(rel)
            continue
        data = backend.get(full)
        actual = digest_bytes(data)
        if actual != dg:
            raise StorageError(f"integrity check failed for {full}: expected {dg}, got {actual}")
        _atomic_write_bytes(dest / rel, data)
        downloaded.append(rel)

    deleted: list[str] = []
    if delete_local:
        for rel in sorted(local):
            if rel not in seen:
                (dest / rel).unlink(missing_ok=True)
                deleted.append(rel)
    return SyncReport((), tuple(downloaded), tuple(deleted), tuple(skipped))


@contextmanager
def with_business_workspace(
    backend: StorageBackend,
    slug: str,
    root: str | os.PathLike[str],
    *,
    delete_remote: bool = True,
    delete_local: bool = True,
) -> Iterator[Path]:
    """Worker integration seam: sync-down on enter → yield the scratch ``root`` → sync-up on **clean**
    exit. On an exception the body raises through WITHOUT syncing up, so a crashed run never clobbers
    the last good remote state (the requeued job re-syncs the last good tree). Mounting this around the
    worker's per-job run is the operator-gated cutover step."""
    root_path = Path(root).expanduser()
    root_path.mkdir(parents=True, exist_ok=True)
    sync_down(backend, slug, root_path, delete_local=delete_local)
    yield root_path
    sync_up(backend, slug, root_path, delete_remote=delete_remote)
