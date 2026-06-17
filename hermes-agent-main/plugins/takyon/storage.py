"""Externalized per-business filesystem (Phase 7) — the leaf that makes the durable host state small.

The per-business workspace (the four canonical roots ``product/``, ``distribution/``, ``research/``,
``metrics/``) is designed to live in one durable object-store prefix per business, with local disk as
cache/scratch. The contract is **sync-down → run → sync-up**. A second host then resumes a business
from Postgres (its identity/jobs/ledger/schedule) + the object store (its files) — the "no-fleet
proof".

This is a pure leaf, shaped like ``jobs``/``wakes`` and seamed like the AI gateway's
``get_provider_caller``:

  * :class:`StorageBackend` is the seam — a tiny put/get/delete/list contract. :func:`get_storage_backend`
    selects ONE implementation by config, exactly like a provider selector (NOT a second code path):
      - :class:`LocalStorageBackend` — a real local-directory object store. The credential-free default;
        the literal "local disk = scratch" tier and the CI tier. No new dependency, no new credential.
      - :class:`SupabaseS3StorageBackend` — Supabase Storage over its S3-compatible API (lazy ``boto3``).
        Selected by an explicit ``TAKYON_STORAGE_BACKEND=supabase_s3`` override or, when that override is
        unset, by a fully provisioned ``SUPABASE_S3_*`` + ``TAKYON_STORAGE_BUCKET`` config. If selected
        while unconfigured (creds or ``boto3`` missing) it raises
        :class:`StorageUnconfigured` — a `blocked`-with-reason, NEVER a silent fall back to local and
        NEVER a fake "synced" (invariant #8). This backend is live-verified against Supabase Storage;
        production still depends on the real ``SUPABASE_S3_*`` + ``TAKYON_STORAGE_BUCKET`` values being
        provisioned in the operator env.

  * :func:`sync_up` / :func:`sync_down` are content-**digest incremental** (unchanged files are skipped)
    and **integrity-checked** (a downloaded blob whose sha256 ≠ the recorded digest raises rather than
    landing corrupt). They reuse the same path-containment discipline as ``core``'s ``_safe_relpath``
    (no absolute paths, no ``.``/``..`` segments, bounded depth) so an object key can never escape the
    business prefix.

  * the worker hydrates a business at a pinned base revision and advances canonical only through an
    explicit commit (see ``core`` ``_commit_business_workspace_revision``); there is no whole-tree
    mirror-on-exit seam.

No DB migration and no new table: the object store is the source of truth for file bytes + their
listing; Postgres stays the source of truth for business/jobs/ledger/schedule state. (A Postgres
``business_files`` manifest was considered and rejected — it duplicates the store's own listing and
adds a bucket↔table two-write drift hazard.)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Protocol, runtime_checkable

from takyon_cli.config import load_env

from . import safebox

# Safety rails (durable hardcodes, like core's MAX_WRITE_CHARS / path containment).
MAX_OBJECT_BYTES = 256 * 1024 * 1024  # 256 MiB — bound a single object so a sync can't OOM the host.
# Per-operator (top-level Takyon user) object-store quota — the durable cap that keeps one operator's
# combined business workspaces from filling the shared bucket. Counted across EVERY business the
# operator owns (sum of their `<slug>/` prefixes), not per-business, so it can't be sidestepped by
# spreading bytes over many businesses. Overridable per host via `TAKYON_OPERATOR_STORAGE_MAX_BYTES`.
_DEFAULT_OPERATOR_STORAGE_MAX_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB
_OPERATOR_STORAGE_MAX_BYTES_ENV = "TAKYON_OPERATOR_STORAGE_MAX_BYTES"
_MAX_KEY_DEPTH = 48
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DEFAULT_LOCAL_DIRNAME = "storage"
_EXCLUDED_DIGEST = "<excluded>"
_TAKYON_INTERNAL_PREFIX = "__takyon"
_WORKSPACE_INTERNAL_PREFIX = f"{_TAKYON_INTERNAL_PREFIX}/workspace"
_WORKSPACE_CAS_PREFIX = f"{_WORKSPACE_INTERNAL_PREFIX}/cas"
_WORKSPACE_MANIFESTS_PREFIX = f"{_WORKSPACE_INTERNAL_PREFIX}/manifests"
_BUILD_INTERNAL_PREFIX = f"{_TAKYON_INTERNAL_PREFIX}/builds"
_SYNC_EXCLUDED_SEGMENTS: frozenset[str] = frozenset({
    ".cache",
    ".git",
    ".next",
    ".pytest_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
})
_SYNC_EXCLUDED_SUFFIXES: frozenset[str] = frozenset({
    ".pyc",
    ".pyo",
})
_SOURCE_REVISION_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "product/site/dist",
    "product/site/build",
    "product/site/out",
)
_SUPABASE_STORAGE_CONFIG_KEYS: tuple[str, ...] = (
    "SUPABASE_S3_ENDPOINT",
    "SUPABASE_S3_REGION",
    "TAKYON_STORAGE_BUCKET",
)
_SUPABASE_STORAGE_SECRET_KEYS: tuple[str, ...] = (
    "SUPABASE_S3_ACCESS_KEY_ID",
    "SUPABASE_S3_SECRET_ACCESS_KEY",
)
logger = logging.getLogger(__name__)


def _workspace_scratch_parent(
    scratch_parent: str | os.PathLike[str] | None = None,
) -> Path:
    """Choose one shared scratch root visible to the operator services and the Docker broker.

    `/tmp` is namespaced per systemd unit when `PrivateTmp=true`, so a worker-created bind mount under
    `/tmp/takyon-workspaces/...` is invisible to the broker service that actually owns Docker
    authority. Default to a tracked path under `TAKYON_HOME` instead, with an explicit override for
    tests or unusual hosts.
    """
    if scratch_parent:
        parent = Path(scratch_parent).expanduser()
    else:
        explicit = str(os.getenv("TAKYON_WORKSPACE_SCRATCH_ROOT") or "").strip()
        if explicit:
            parent = Path(explicit).expanduser()
        else:
            takyon_home = str(os.getenv("TAKYON_HOME") or "").strip()
            if takyon_home:
                parent = Path(takyon_home).expanduser() / "tmp" / "workspaces"
            else:
                parent = Path(tempfile.gettempdir()) / "takyon-workspaces"
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    return parent.resolve()


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


class StorageQuotaExceeded(StorageError):
    """An operator's combined object-store usage would exceed its per-operator quota.

    Raised at sync-up time (the write boundary) so a fail-closed gate refuses the upload BEFORE any
    bytes land, rather than reporting an overflow after the fact."""


def _env_backed_config_value(name: str) -> str:
    value = os.getenv(name)
    if value is not None:
        return str(value).strip()
    return str(load_env().get(name) or "").strip()


def _sensitive_config_value(name: str) -> str:
    value = os.getenv(name)
    if value is not None:
        return str(value).strip()
    try:
        return str(safebox.read_env_backed_value(name) or "").strip()
    except safebox.SafeboxAuthorityUnavailable:
        return ""


def _supabase_storage_fully_configured() -> bool:
    return bool(
        all(_env_backed_config_value(name) for name in _SUPABASE_STORAGE_CONFIG_KEYS)
        and all(_sensitive_config_value(name) for name in _SUPABASE_STORAGE_SECRET_KEYS)
    )


def configured_storage_backend_kind() -> str:
    explicit = str(os.getenv("TAKYON_STORAGE_BACKEND") or "").strip().lower()
    if explicit:
        return explicit
    if _supabase_storage_fully_configured():
        return "supabase_s3"
    return "local"


def _storage_client_missing_object(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error") if isinstance(response.get("Error"), dict) else {}
    code = str(error.get("Code") or "").strip().lower()
    status = str((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or "").strip()
    return code in {"404", "nosuchkey", "notfound"} or status == "404"


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


def _safe_owner_label(value: str) -> str:
    raw = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-_")
    return raw[:48] or "anon"


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


def _sync_path_excluded(rel: str) -> bool:
    safe = _safe_rel(rel)
    parts = Path(safe).parts
    if any(part in _SYNC_EXCLUDED_SEGMENTS for part in parts):
        return True
    return Path(safe).suffix.lower() in _SYNC_EXCLUDED_SUFFIXES


def _normalize_sync_prefixes(prefixes: Iterable[str] | None) -> tuple[str, ...]:
    normalized: set[str] = set()
    for prefix in prefixes or ():
        text = str(prefix or "").strip().strip("/")
        if not text:
            continue
        normalized.add(_safe_rel(text, field="sync prefix"))
    return tuple(sorted(normalized))


def _sync_rel_matches_prefix(rel: str, prefixes: tuple[str, ...]) -> bool:
    safe = _safe_rel(rel)
    return any(safe == prefix or safe.startswith(f"{prefix}/") for prefix in prefixes)


def _walk_local_digests(root: Path, *, include_excluded: bool = False) -> dict[str, str]:
    """Map every regular file under ``root`` to its sha256, keyed by POSIX path relative to ``root``.
    Symlinks and atomic-write temp files are skipped; an unsafe relative path raises."""
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        # Don't follow symlinked directories (containment).
        dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
        if not include_excluded:
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _SYNC_EXCLUDED_SEGMENTS
            ]
        for name in filenames:
            abs_path = Path(dirpath, name)
            if abs_path.is_symlink() or _is_scratch_tempfile(name):
                continue
            rel = abs_path.relative_to(root).as_posix()
            _safe_rel(rel)  # raise on anything that couldn't be a safe object key
            if _sync_path_excluded(rel):
                if include_excluded:
                    out[rel] = _EXCLUDED_DIGEST
                continue
            try:
                out[rel] = digest_bytes(_read_file_bytes(abs_path))
            except OSError:
                # Local business mirrors are scratch only; an unreadable file should
                # be treated as stale drift and replaced from the canonical backend,
                # not wedge the whole sync_down/auth/runtime path.
                continue
    return out


def workspace_file_digests(root: str | os.PathLike[str]) -> dict[str, str]:
    """Digest the canonical business workspace view for commit/build operations."""
    return _walk_local_digests(Path(root).expanduser())


def workspace_source_digests(root: str | os.PathLike[str]) -> dict[str, str]:
    """Digest the committed source view of a business workspace.

    Product build outputs are live artifacts, not canonical source. Keep them
    out of source revisions so a build never mutates the committed source tree.
    """
    digests = workspace_file_digests(root)
    return {
        rel: digest
        for rel, digest in digests.items()
        if not _sync_rel_matches_prefix(rel, _SOURCE_REVISION_EXCLUDED_PREFIXES)
    }


def workspace_cas_key(slug: str, digest: str) -> str:
    safe_digest = str(digest or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", safe_digest):
        raise UnsafePath(f"unsafe workspace digest: {digest!r}")
    return f"{object_prefix(slug)}{_WORKSPACE_CAS_PREFIX}/{safe_digest}"


def workspace_manifest_key(slug: str, revision: int) -> str:
    safe_revision = int(revision)
    if safe_revision < 0:
        raise UnsafePath(f"unsafe workspace revision: {revision!r}")
    return f"{object_prefix(slug)}{_WORKSPACE_MANIFESTS_PREFIX}/{safe_revision}.json"


def build_object_prefix(slug: str, build_id: str) -> str:
    safe_build_id = str(build_id or "").strip().lower()
    if not safe_build_id or not re.fullmatch(r"[0-9a-f]{16,64}", safe_build_id):
        raise UnsafePath(f"unsafe build id: {build_id!r}")
    return f"{object_prefix(slug)}{_BUILD_INTERNAL_PREFIX}/{safe_build_id}/"


def build_object_key(slug: str, build_id: str, rel: str) -> str:
    return build_object_prefix(slug, build_id) + _safe_rel(rel, field="build path")


def read_workspace_manifest(
    backend: StorageBackend,
    slug: str,
    revision: int,
) -> dict[str, object]:
    raw = backend.get(workspace_manifest_key(slug, revision))
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - corruption path
        raise StorageError(f"workspace manifest is not valid JSON for {slug}@{revision}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StorageError(f"workspace manifest is not an object for {slug}@{revision}")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise StorageError(f"workspace manifest files are invalid for {slug}@{revision}")
    return payload


def write_workspace_revision(
    backend: StorageBackend,
    slug: str,
    revision: int,
    root: str | os.PathLike[str],
    *,
    parent_revision: int = 0,
    created_at: str = "",
) -> dict[str, object]:
    workspace_root = Path(root).expanduser().resolve()
    digests = workspace_source_digests(workspace_root)
    for rel, digest in sorted(digests.items()):
        backend.put(
            workspace_cas_key(slug, digest),
            _read_file_bytes(workspace_root / rel),
            digest=digest,
        )
    manifest: dict[str, object] = {
        "slug": _safe_slug(slug),
        "revision": int(revision),
        "parent_revision": int(parent_revision),
        "created_at": str(created_at or "").strip(),
        "files": digests,
    }
    body = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    backend.put(
        workspace_manifest_key(slug, revision),
        body,
        digest=digest_bytes(body),
    )
    manifest["manifest_sha256"] = digest_bytes(body)
    return manifest


def materialize_workspace_revision(
    backend: StorageBackend,
    slug: str,
    revision: int,
    dest_dir: str | os.PathLike[str],
    *,
    delete_local: bool = True,
) -> SyncReport:
    manifest = read_workspace_manifest(backend, slug, revision)
    dest = Path(dest_dir).expanduser()
    manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    if not isinstance(manifest_files, dict):
        raise StorageError(f"workspace manifest files are invalid for {slug}@{revision}")
    downloaded: list[str] = []
    skipped: list[str] = []
    local = _walk_local_digests(dest)
    seen: set[str] = set()
    for rel, dg in sorted(manifest_files.items()):
        rel_text = _safe_rel(str(rel), field="workspace path")
        digest_text = str(dg or "").strip().lower()
        seen.add(rel_text)
        if local.get(rel_text) == digest_text:
            skipped.append(rel_text)
            continue
        data = backend.get(workspace_cas_key(slug, digest_text))
        actual = digest_bytes(data)
        if actual != digest_text:
            raise StorageError(
                f"integrity check failed for workspace blob {slug}@{revision}:{rel_text}: expected {digest_text}, got {actual}"
            )
        _atomic_write_bytes(dest / rel_text, data)
        downloaded.append(rel_text)
    deleted: list[str] = []
    if delete_local:
        for rel in sorted(local):
            if rel not in seen:
                (dest / rel).unlink(missing_ok=True)
                deleted.append(rel)
    return SyncReport((), tuple(downloaded), tuple(deleted), tuple(skipped))


def write_build_artifact(
    backend: StorageBackend,
    slug: str,
    build_id: str,
    root: str | os.PathLike[str],
) -> dict[str, str]:
    build_root = Path(root).expanduser().resolve()
    digests = workspace_file_digests(build_root)
    for rel, digest in sorted(digests.items()):
        backend.put(
            build_object_key(slug, build_id, rel),
            _read_file_bytes(build_root / rel),
            digest=digest,
        )
    return digests


def materialize_build_artifact(
    backend: StorageBackend,
    slug: str,
    build_id: str,
    dest_dir: str | os.PathLike[str],
    *,
    delete_local: bool = True,
) -> SyncReport:
    prefix = build_object_prefix(slug, build_id)
    remote = backend.list_digests(prefix)
    dest = Path(dest_dir).expanduser()
    local = _walk_local_digests(dest)
    downloaded: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for full, dg in sorted(remote.items()):
        rel = _safe_rel(full[len(prefix):], field="build path")
        seen.add(rel)
        if local.get(rel) == dg:
            skipped.append(rel)
            continue
        data = backend.get(full)
        actual = digest_bytes(data)
        if actual != dg:
            raise StorageError(
                f"integrity check failed for build blob {slug}:{build_id}:{rel}: expected {dg}, got {actual}"
            )
        _atomic_write_bytes(dest / rel, data)
        downloaded.append(rel)
    deleted: list[str] = []
    if delete_local:
        for rel in sorted(local):
            if rel not in seen:
                (dest / rel).unlink(missing_ok=True)
                deleted.append(rel)
    return SyncReport((), tuple(downloaded), tuple(deleted), tuple(skipped))


# ── backend seam ───────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class StorageBackend(Protocol):
    """The whole object-store contract. Keys are full keys including the ``<slug>/`` prefix."""

    name: str

    def put(self, key: str, data: bytes, *, digest: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def list_digests(self, prefix: str) -> dict[str, str]: ...

    def list_object_sizes(self, prefix: str) -> dict[str, int]: ...


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
            for rel, dg in _walk_local_digests(base, include_excluded=True).items()
        }

    def list_object_sizes(self, prefix: str) -> dict[str, int]:
        safe_prefix = _safe_rel(prefix.rstrip("/"), field="prefix") if prefix.strip("/") else ""
        base = (self.root / safe_prefix) if safe_prefix else self.root
        out: dict[str, int] = {}
        if not base.exists():
            return out
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
            for name in filenames:
                abs_path = Path(dirpath, name)
                if abs_path.is_symlink() or _is_scratch_tempfile(name):
                    continue
                rel = abs_path.relative_to(base).as_posix()
                _safe_rel(rel)
                key = f"{safe_prefix}/{rel}" if safe_prefix else rel
                try:
                    out[key] = abs_path.stat().st_size
                except OSError:
                    continue
        return out


class SupabaseS3StorageBackend:
    """Supabase Storage via its S3-compatible API (lazy ``boto3``).

    Constructed when explicitly selected (``TAKYON_STORAGE_BACKEND=supabase_s3``) or when the
    bucket config is otherwise fully provisioned. If the ``SUPABASE_S3_*`` creds or ``boto3`` are
    missing it raises :class:`StorageUnconfigured` — the
    invariant-#8 block, never a silent fallback. sha256 is stored in object metadata at ``put`` and
    read back in ``list_digests`` so incrementality matches the local backend's digest space.

    NOTE: live-verified against Supabase Storage on 2026-05-31 (put→get→list→delete round-trip with
    sha256 integrity, via the operator-provisioned ``SUPABASE_S3_*`` keys on project
    ``ddftvmjpfghfrdxhavvp``, bucket ``business-workspaces``). The per-method
    ``# pragma: no cover`` markers remain because the offline test suite still doesn't hit the network.
    """

    name = "supabase_s3"
    _META_DIGEST = "sha256"

    def __init__(self) -> None:
        endpoint = _env_backed_config_value("SUPABASE_S3_ENDPOINT")
        region = _env_backed_config_value("SUPABASE_S3_REGION")
        access_key = _sensitive_config_value("SUPABASE_S3_ACCESS_KEY_ID")
        secret_key = _sensitive_config_value("SUPABASE_S3_SECRET_ACCESS_KEY")
        bucket = _env_backed_config_value("TAKYON_STORAGE_BUCKET")
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
                rel = key[len(prefix):] if prefix and key.startswith(prefix) else key
                if rel and _sync_path_excluded(rel):
                    out[key] = _EXCLUDED_DIGEST
                    continue
                try:
                    head = self._client.head_object(Bucket=self.bucket, Key=key)
                except Exception as exc:
                    if _storage_client_missing_object(exc):
                        logger.warning("storage list skipped vanished object: %s", key)
                        continue
                    raise
                dg = (head.get("Metadata") or {}).get(self._META_DIGEST)
                if not dg:
                    # No recorded digest (e.g. written by something else): hash the bytes so the
                    # listing is still correct rather than guessing from an ETag.
                    try:
                        dg = digest_bytes(self.get(key))
                    except ObjectNotFound:
                        logger.warning("storage list skipped vanished object during get: %s", key)
                        continue
                out[key] = dg
        return out

    def list_object_sizes(self, prefix: str) -> dict[str, int]:  # pragma: no cover - live only
        # `Size` rides the list_objects_v2 page itself — no per-object head/get — so quota
        # accounting stays a single cheap listing even for large operators.
        out: dict[str, int] = {}
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                out[obj["Key"]] = int(obj.get("Size") or 0)
        return out


def get_storage_backend(*, root: str | os.PathLike[str] | None = None) -> StorageBackend:
    """Select the configured backend (the provider-selector seam).

    ``TAKYON_STORAGE_BACKEND`` remains the explicit override. When it is unset, a fully provisioned
    Supabase object-store config becomes the durable default; otherwise the credential-free local
    backend is used. Any explicit or inferred ``supabase_s3`` selection still raises
    :class:`StorageUnconfigured` if the live backend is not actually provisioned.
    """
    kind = configured_storage_backend_kind()
    if kind == "local":
        return LocalStorageBackend(root)
    if kind == "supabase_s3":
        return SupabaseS3StorageBackend()
    raise StorageError(f"unknown TAKYON_STORAGE_BACKEND: {kind!r} (expected 'local' or 'supabase_s3')")


# ── per-operator quota + purge ───────────────────────────────────────────────────────────────────


def operator_storage_max_bytes() -> int:
    """The per-operator object-store quota in bytes (env-overridable, like app_media's quotas)."""
    raw = str(os.getenv(_OPERATOR_STORAGE_MAX_BYTES_ENV) or "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            return _DEFAULT_OPERATOR_STORAGE_MAX_BYTES
        if parsed > 0:
            return parsed
    return _DEFAULT_OPERATOR_STORAGE_MAX_BYTES


def prefix_bytes(backend: StorageBackend, prefix: str) -> int:
    """Total bytes stored under one object-store prefix (one business's ``<slug>/`` namespace)."""
    return sum(int(size or 0) for size in backend.list_object_sizes(prefix).values())


def operator_storage_bytes(backend: StorageBackend, slugs: Iterable[str]) -> int:
    """Sum the operator's combined object-store usage across every business they own.

    The operator is the unit of the quota (one top-level Takyon user can own many businesses), so
    usage is aggregated over each owned business's ``<slug>/`` prefix. Slugs are de-duplicated and
    normalized; an unsafe slug raises rather than being silently skipped (containment)."""
    total = 0
    seen: set[str] = set()
    for slug in slugs:
        safe = _safe_slug(str(slug))
        if safe in seen:
            continue
        seen.add(safe)
        total += prefix_bytes(backend, object_prefix(safe))
    return total


def enforce_operator_storage_quota(
    backend: StorageBackend,
    owned_slugs: Iterable[str],
    incoming_bytes: int,
    *,
    quota_bytes: int | None = None,
) -> int:
    """Fail-closed gate: refuse a write that would push the operator at/over its quota.

    Returns the operator's pre-write usage on success. ``incoming_bytes`` is the NET new bytes the
    pending operation would add; the check is ``used + incoming >= quota`` so it trips *at* the limit
    (spec: "raised at or above the per-operator limit"), never silently allowing the boundary write."""
    limit = int(quota_bytes) if quota_bytes is not None else operator_storage_max_bytes()
    used = operator_storage_bytes(backend, owned_slugs)
    if used + max(0, int(incoming_bytes)) >= limit:
        raise StorageQuotaExceeded(
            f"operator storage quota exceeded: {used + max(0, int(incoming_bytes))}/{limit} bytes"
        )
    return used


def delete_prefix(backend: StorageBackend, prefix: str) -> list[str]:
    """Delete every object under one prefix. Returns the keys removed (sorted, deterministic).

    The single canonical "purge a namespace" primitive — business-deletion and operator-deletion
    both route through it so there is one delete path, never a copy per caller (parsimony)."""
    keys = sorted(backend.list_digests(prefix))
    for key in keys:
        backend.delete(key)
    return keys


def purge_operator_storage(backend: StorageBackend, slugs: Iterable[str]) -> dict[str, list[str]]:
    """Remove ALL object-store bytes for an operator across every business they own.

    The operator-account-removal complement to per-business deletion: closing a Takyon user must not
    strand their workspace objects in the bucket. Returns ``{slug: [deleted keys]}``."""
    removed: dict[str, list[str]] = {}
    seen: set[str] = set()
    for slug in slugs:
        safe = _safe_slug(str(slug))
        if safe in seen:
            continue
        seen.add(safe)
        removed[safe] = delete_prefix(backend, object_prefix(safe))
    return removed


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
    backend: StorageBackend,
    slug: str,
    src_dir: str | os.PathLike[str],
    *,
    delete_remote: bool = False,
    exclude_prefixes: Iterable[str] | None = None,
    operator_owned_slugs: Iterable[str] | None = None,
    operator_quota_bytes: int | None = None,
) -> SyncReport:
    """Push the local workspace ``src_dir`` to the backend under the business prefix. Digest-incremental
    (an unchanged file is skipped). ``delete_remote=True`` mirrors local deletions to the store — only
    safe when ``src_dir`` is the complete, post-successful-run tree (the worker's clean-exit contract);
    the raw primitive defaults to additive/idempotent.

    When ``operator_owned_slugs`` is supplied (every business the owning operator holds, including this
    one), the per-operator object-store quota is enforced *before* any bytes are uploaded:
    :class:`StorageQuotaExceeded` is raised if this push would put the operator at or over the limit.
    Caller passes the owner's slug set; this leaf never resolves ownership itself (containment)."""
    prefix = object_prefix(slug)
    src = Path(src_dir).expanduser()
    remote = backend.list_digests(prefix)  # {fullkey: digest}
    local = _walk_local_digests(src)  # {rel: digest}
    excluded = _normalize_sync_prefixes(exclude_prefixes)

    if operator_owned_slugs is not None:
        # Net new bytes this push adds to THIS business's prefix = (post-sync size of the kept local
        # files) − (current remote size of this prefix). Anything outside this business stays counted
        # via the operator aggregate, so the gate sees the operator's true post-write total.
        kept_new_bytes = 0
        for rel in local:
            if excluded and _sync_rel_matches_prefix(rel, excluded):
                continue
            try:
                kept_new_bytes += (src / rel).stat().st_size
            except OSError:
                continue
        this_prefix_remote_bytes = prefix_bytes(backend, prefix)
        incoming = max(0, kept_new_bytes - this_prefix_remote_bytes)
        enforce_operator_storage_quota(
            backend,
            operator_owned_slugs,
            incoming,
            quota_bytes=operator_quota_bytes,
        )

    uploaded: list[str] = []
    skipped: list[str] = []
    for rel, dg in sorted(local.items()):
        if excluded and _sync_rel_matches_prefix(rel, excluded):
            skipped.append(rel)
            continue
        full = prefix + rel
        if remote.get(full) == dg:
            skipped.append(rel)
            continue
        backend.put(full, _read_file_bytes(src / rel), digest=dg)
        uploaded.append(rel)

    deleted: list[str] = []
    if delete_remote:
        local_full = {
            prefix + rel
            for rel in local
            if not (excluded and _sync_rel_matches_prefix(rel, excluded))
        }
        for full in sorted(remote):
            rel = full[len(prefix):]
            if excluded and _sync_rel_matches_prefix(rel, excluded):
                continue
            if full not in local_full:
                backend.delete(full)
                deleted.append(rel)
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
        if dg == _EXCLUDED_DIGEST or _sync_path_excluded(rel):
            skipped.append(rel)
            continue
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
