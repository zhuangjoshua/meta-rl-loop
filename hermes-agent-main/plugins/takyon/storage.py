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
from concurrent.futures import ThreadPoolExecutor
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


def _s3_list_prefix(prefix: str) -> str:
    """Normalize object-store list prefixes so ``biz`` and ``biz/`` address one namespace."""
    raw = str(prefix or "")
    safe = _safe_rel(raw.rstrip("/"), field="prefix") if raw.strip("/") else ""
    return f"{safe}/" if safe else ""


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
    except (safebox.RemoteSafeboxError, safebox.SafeboxAuthorityUnavailable):
        return ""


def _remote_storage_authority_enabled() -> bool:
    return safebox._remote_enabled() and not safebox._local_authority_enabled()


def _supabase_storage_fully_configured() -> bool:
    if _remote_storage_authority_enabled():
        return bool(all(_env_backed_config_value(name) for name in _SUPABASE_STORAGE_CONFIG_KEYS))
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
    # A concurrent ``materialize_workspace_revision(delete_local=True)`` on the SAME local cache
    # dest (e.g. the other worker drain thread re-materializing the business workspace) can delete
    # the parent directory between our ``mkdir`` and ``mkstemp``/``replace`` — a transient ENOENT.
    # Recreate the parent and retry a bounded number of times so a durable workspace write is never
    # lost to a momentary directory-wipe race. (The business mirror flock that used to serialize
    # this was removed because it deadlocked the worker; see core._business_mirror_lock.)
    last_exc: FileNotFoundError | None = None
    for _attempt in range(4):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        except FileNotFoundError as exc:
            last_exc = exc
            continue
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            return
        except FileNotFoundError as exc:
            last_exc = exc
            try:
                os.unlink(tmp)
            except OSError:
                pass
            continue
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    if last_exc is not None:
        raise last_exc


def _read_file_bytes(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_OBJECT_BYTES:
        raise StorageError(f"object too large to sync ({size} bytes > {MAX_OBJECT_BYTES}): {path}")
    return path.read_bytes()


def _bounded_sync_concurrency(raw: str | None) -> int:
    try:
        value = int(str(raw or "").strip() or 12)
    except ValueError:
        value = 12
    return max(1, min(value, 32))


# Bounded fan-out for per-file transfer requests (sync_up PUTs, sync_down GETs, and the
# per-object digest HEADs behind list_digests). A dozen concurrent small-object requests is well
# within what the safebox broker and the S3-compatible stores absorb; the cap exists so a laptop
# rail cannot open an unbounded connection burst through the broker tunnel. Serialized, these
# loops priced every workspace commit at file-count x round-trip latency — measured ~63s per
# commit even on the VPS (readback GETs ~43s + digest HEADs ~9s for a ~140-file workspace).
# TAKYON_STORAGE_SYNC_CONCURRENCY=1 restores the fully serial behavior.
_SYNC_PUT_CONCURRENCY = _bounded_sync_concurrency(os.getenv("TAKYON_STORAGE_SYNC_CONCURRENCY"))


def _map_concurrently(fn, items: list):
    """Run ``fn`` over ``items`` on the bounded sync pool, preserving order; serial when the pool
    is sized 1 or there is nothing to fan out. Exceptions propagate (fail-closed), matching the
    serial loops these calls replaced."""
    workers = min(_SYNC_PUT_CONCURRENCY, len(items))
    if workers <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


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


def workspace_cas_prefix(slug: str) -> str:
    return f"{object_prefix(slug)}{_WORKSPACE_CAS_PREFIX}/"


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
    existing_cas_keys: Iterable[str] | None = None,
    source_digests: dict[str, str] | None = None,
) -> dict[str, object]:
    workspace_root = Path(root).expanduser().resolve()
    # ``source_digests`` lets the commit chokepoint thread ONE digest walk through the whole
    # revision write instead of every helper re-reading + re-hashing the tree; the per-object
    # read-verify at put time below still guards integrity. The keys-only listing fallback is
    # deliberate: CAS keys embed the sha256, so list_digests' per-object digest HEADs add nothing.
    digests = source_digests if source_digests is not None else workspace_source_digests(workspace_root)
    cas_prefix = workspace_cas_prefix(slug)
    existing_cas_key_set = (
        set(existing_cas_keys)
        if existing_cas_keys is not None
        else set(backend.list_object_sizes(cas_prefix).keys())
    )
    seen_digests: set[str] = set()
    to_write: list[tuple[str, str]] = []
    for rel, digest in sorted(digests.items()):
        digest_text = str(digest or "").strip().lower()
        if not digest_text or digest_text in seen_digests:
            continue
        seen_digests.add(digest_text)
        if workspace_cas_key(slug, digest_text) in existing_cas_key_set:
            continue
        to_write.append((rel, digest_text))

    # New CAS objects upload concurrently (bounded pool) — a fresh workspace's first revision
    # writes the whole tree here, and serialized per-object puts priced that at file-count x RTT.
    # Content-addressed keys are naturally race-free (same digest = same bytes), and the
    # read-verify-put per object is unchanged.
    def _write_cas_object(item: tuple[str, str]) -> None:
        rel, digest_text = item
        data = _read_file_bytes(workspace_root / rel)
        actual_digest = digest_bytes(data)
        if actual_digest != digest_text:
            raise StorageError(
                f"workspace source changed while writing revision ({rel}): "
                f"expected {digest_text}, got {actual_digest}"
            )
        backend.put(
            workspace_cas_key(slug, digest_text),
            data,
            digest=digest_text,
        )

    _map_concurrently(_write_cas_object, to_write)
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


def workspace_revision_incoming_bytes(
    backend: StorageBackend,
    slug: str,
    root: str | os.PathLike[str],
    *,
    existing_cas_keys: Iterable[str] | None = None,
    source_digests: dict[str, str] | None = None,
) -> int:
    """Net NEW object-store bytes a :func:`write_workspace_revision` of ``root`` would add.

    The canonical commit is content-addressed: each source file is stored once under its sha256 CAS
    key (:func:`workspace_cas_key`), so a file whose digest already exists in this business's CAS
    prefix adds zero bytes. This returns the size sum of only the *unique source digests not already
    present remotely* — the true incremental footprint the operator quota must gate on, matching the
    CAS dedup that the write itself performs (no double-counting an unchanged tree). Used by the live
    commit path to feed :func:`enforce_operator_storage_quota` BEFORE any blob is uploaded."""
    workspace_root = Path(root).expanduser().resolve()
    digests = source_digests if source_digests is not None else workspace_source_digests(workspace_root)
    # Existing CAS digests for THIS business (the only prefix write_workspace_revision touches).
    cas_prefix = workspace_cas_prefix(slug)
    existing_cas_key_set = (
        set(existing_cas_keys)
        if existing_cas_keys is not None
        else set(backend.list_object_sizes(cas_prefix).keys())
    )
    incoming = 0
    counted: set[str] = set()
    for rel, digest in digests.items():
        digest_text = str(digest or "").strip().lower()
        if not digest_text or digest_text in counted:
            continue
        counted.add(digest_text)
        if workspace_cas_key(slug, digest_text) in existing_cas_key_set:
            continue  # already stored — CAS dedup means zero new bytes
        try:
            incoming += (workspace_root / rel).stat().st_size
        except OSError:
            continue
    return incoming


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
        prefix = _s3_list_prefix(prefix)
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                rel = key[len(prefix):] if prefix and key.startswith(prefix) else key
                if rel and _sync_path_excluded(rel):
                    out[key] = _EXCLUDED_DIGEST
                    continue
                keys.append(key)
        # Digest lives in per-object metadata, so listing costs one HEAD per key — fan those out
        # on the bounded pool (serialized they dominated every sync/quota pass at ~key-count x RTT).
        for key, dg in zip(keys, _map_concurrently(self._digest_for_key, keys)):
            if dg is not None:
                out[key] = dg
        return out

    def _digest_for_key(self, key: str) -> str | None:  # pragma: no cover - live only
        """Resolve one object's recorded digest (or hash its bytes when unrecorded); ``None`` means
        the object vanished between the listing and the read — skipped, exactly like the old
        serial loop."""
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _storage_client_missing_object(exc):
                logger.warning("storage list skipped vanished object: %s", key)
                return None
            raise
        dg = (head.get("Metadata") or {}).get(self._META_DIGEST)
        if dg:
            return dg
        # No recorded digest (e.g. written by something else): hash the bytes so the listing is
        # still correct rather than guessing from an ETag.
        try:
            return digest_bytes(self.get(key))
        except ObjectNotFound:
            logger.warning("storage list skipped vanished object during get: %s", key)
            return None

    def list_object_sizes(self, prefix: str) -> dict[str, int]:  # pragma: no cover - live only
        # `Size` rides the list_objects_v2 page itself — no per-object head/get — so quota
        # accounting stays a single cheap listing even for large operators.
        out: dict[str, int] = {}
        prefix = _s3_list_prefix(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                out[obj["Key"]] = int(obj.get("Size") or 0)
        return out


class R2StorageBackend:
    """Cloudflare R2 via its S3-compatible API (lazy ``boto3``) — the PUBLIC product-site mirror.

    This is a SEPARATE bucket from the Supabase workspace store. It holds ONLY finished static
    builds, served read-only at the edge so the VPS leaves the static path. NOTHING private is ever
    written here: only ``<slug>/<build_id>/<rel>`` build files and the ``<slug>/current`` pointer.

    Config resolves through the same env-backed / safebox-backed seam as the Supabase backend
    (``_env_backed_config_value`` for non-secret, ``_sensitive_config_value`` for the keys), so the
    R2 write token is no more exposed than the existing ``SUPABASE_S3_*`` creds — never read from a
    business tool's ``os.environ`` at runtime. If any of ``R2_S3_ENDPOINT`` /
    ``R2_S3_ACCESS_KEY_ID`` / ``R2_S3_SECRET_ACCESS_KEY`` / ``R2_BUCKET`` or ``boto3`` is missing it
    raises :class:`StorageUnconfigured` — never a silent fallback. ``R2_S3_REGION`` defaults to
    ``"auto"`` (R2's convention). sha256 is stored in object metadata at ``put`` so reads/listing
    share the one digest space.

    The mirror is best-effort: callers no-op via :func:`r2_configured` when R2 isn't provisioned, so
    existing Supabase publish behavior is unchanged until the operator sets the ``R2_*`` values.
    """

    name = "r2"
    _META_DIGEST = "sha256"

    def __init__(self) -> None:
        endpoint = _env_backed_config_value("R2_S3_ENDPOINT")
        region = _env_backed_config_value("R2_S3_REGION") or "auto"
        access_key = _sensitive_config_value("R2_S3_ACCESS_KEY_ID")
        secret_key = _sensitive_config_value("R2_S3_SECRET_ACCESS_KEY")
        bucket = _env_backed_config_value("R2_BUCKET")
        missing = [
            name
            for name, val in (
                ("R2_S3_ENDPOINT", endpoint),
                ("R2_S3_ACCESS_KEY_ID", access_key),
                ("R2_S3_SECRET_ACCESS_KEY", secret_key),
                ("R2_BUCKET", bucket),
            )
            if not val
        ]
        if missing:
            raise StorageUnconfigured(
                "r2 storage backend selected but missing: " + ", ".join(missing)
            )
        try:
            import boto3  # noqa: PLC0415 — lazy: the credential-free path never imports this
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise StorageUnconfigured(
                "r2 storage backend selected but boto3 is not installed"
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
        prefix = _s3_list_prefix(prefix)
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                keys.append(obj["Key"])
        # Same bounded fan-out as the Supabase backend: one HEAD per key, concurrent.
        return {
            key: dg
            for key, dg in zip(keys, _map_concurrently(self._digest_for_key, keys))
            if dg is not None
        }

    def _digest_for_key(self, key: str) -> str | None:  # pragma: no cover - live only
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _storage_client_missing_object(exc):
                logger.warning("r2 list skipped vanished object: %s", key)
                return None
            raise
        dg = (head.get("Metadata") or {}).get(self._META_DIGEST)
        if dg:
            return dg
        try:
            return digest_bytes(self.get(key))
        except ObjectNotFound:
            logger.warning("r2 list skipped vanished object during get: %s", key)
            return None

    def list_object_sizes(self, prefix: str) -> dict[str, int]:  # pragma: no cover - live only
        out: dict[str, int] = {}
        prefix = _s3_list_prefix(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                out[obj["Key"]] = int(obj.get("Size") or 0)
        return out


class SafeboxStorageBackend:
    """Remote object-store backend.

    Runtime planes never receive S3/R2 write credentials. They hand bounded object operations to the
    safebox, which resolves the object-store key locally and performs the S3-compatible request.
    """

    def __init__(self, provider: str) -> None:
        name = str(provider or "").strip()
        if name not in {"supabase_s3", "r2"}:
            raise StorageError(f"unknown safebox storage provider: {provider!r}")
        self.name = name

    def put(self, key: str, data: bytes, *, digest: str) -> None:
        safebox.storage_put(self.name, _safe_rel(key, field="object key"), data, digest=digest)

    def get(self, key: str) -> bytes:
        try:
            return safebox.storage_get(self.name, _safe_rel(key, field="object key"))
        except safebox.RemoteSafeboxError as exc:
            if exc.status_code == 404:
                raise ObjectNotFound(key) from exc
            raise

    def delete(self, key: str) -> None:
        safebox.storage_delete(self.name, _safe_rel(key, field="object key"))

    def list_digests(self, prefix: str) -> dict[str, str]:
        safe_prefix = _safe_rel(prefix.rstrip("/"), field="prefix") if prefix.strip("/") else ""
        return safebox.storage_list_digests(self.name, safe_prefix)

    def list_object_sizes(self, prefix: str) -> dict[str, int]:
        safe_prefix = _safe_rel(prefix.rstrip("/"), field="prefix") if prefix.strip("/") else ""
        return safebox.storage_list_object_sizes(self.name, safe_prefix)


def r2_configured() -> bool:
    """True iff the public R2 product-site mirror is fully provisioned.

    Lets every caller of :func:`write_public_site_to_r2` no-op cleanly when ``R2_*`` is unset, so the
    Supabase publish path is unchanged until the operator turns the edge mirror on. ``R2_S3_REGION``
    is intentionally NOT required (defaults to ``"auto"``)."""
    if _remote_storage_authority_enabled():
        return bool(_env_backed_config_value("R2_S3_ENDPOINT") and _env_backed_config_value("R2_BUCKET"))
    return bool(
        _env_backed_config_value("R2_S3_ENDPOINT")
        and _env_backed_config_value("R2_BUCKET")
        and _sensitive_config_value("R2_S3_ACCESS_KEY_ID")
        and _sensitive_config_value("R2_S3_SECRET_ACCESS_KEY")
    )


def public_site_object_key(slug: str, build_id: str, rel: str) -> str:
    """Key a single built-site file in the PUBLIC R2 bucket: ``<slug>/<build_id>/<rel>``.

    Deliberately flat and public-namespaced — no ``__takyon/`` private prefix — because this bucket
    only ever holds servable static output. ``build_id`` reuses the same 16–64 hex-char validation as
    :func:`build_object_prefix` so a pointer can never escape the slug namespace."""
    safe_slug = _safe_slug(slug)
    safe_build_id = str(build_id or "").strip().lower()
    if not safe_build_id or not re.fullmatch(r"[0-9a-f]{16,64}", safe_build_id):
        raise UnsafePath(f"unsafe build id: {build_id!r}")
    return f"{safe_slug}/{safe_build_id}/" + _safe_rel(rel, field="public site path")


def public_site_pointer_key(slug: str) -> str:
    """Key the per-business live-build pointer in the PUBLIC R2 bucket: ``<slug>/current``.

    The edge reader resolves ``<slug>/current`` -> ``build_id`` and then serves
    ``<slug>/<build_id>/<rel>``. Written LAST so a half-uploaded build is never pointed at."""
    return f"{_safe_slug(slug)}/current"


def public_site_object_prefix(slug: str) -> str:
    """R2 prefix for one public product site: ``<slug>/``.

    This covers both the live pointer and every historical build under the public edge bucket.
    """
    return f"{_safe_slug(slug)}/"


def delete_public_site_from_r2(
    slug: str,
    *,
    backend: "StorageBackend | None" = None,
) -> dict[str, object]:
    """Delete one public product site's Cloudflare R2 prefix.

    Business deletion must not strand edge-served static output. If R2 is not provisioned this
    reports a skipped cleanup, matching the publish mirror's optional deployment posture. If R2 is
    provisioned and the backend errors, the exception propagates so the caller can avoid deleting the
    control-plane row while the public site is still live.

    Truthfulness gate: after deleting the prefix this RE-LISTS it. The edge serves
    ``<slug>/current -> build_id -> index.html`` straight from R2, so if any object survives the
    delete (e.g. a slow eventual-consistency window, a partial backend failure, or a scope that could
    not enumerate every key) the public site is still reachable. In that case ``removed`` is reported
    ``False`` with ``residual_count`` and ``still_present=True`` so the caller never records a clean
    delete while customers can still load the "deleted" site — exactly the spec's "say so if a piece
    can't be removed synchronously".
    """
    safe_slug = _safe_slug(slug)
    prefix = public_site_object_prefix(safe_slug)
    if backend is None:
        if not r2_configured():
            return {
                "slug": safe_slug,
                "prefix": prefix,
                "status": "unconfigured",
                "removed": False,
                "deleted": [],
                "deleted_count": 0,
                "skipped": True,
            }
        backend = SafeboxStorageBackend("r2") if _remote_storage_authority_enabled() else R2StorageBackend()
    deleted = delete_prefix(backend, prefix)
    # Confirm the namespace is actually empty — the edge reads R2 directly, so a surviving object
    # (especially the <slug>/current pointer) keeps the public site live. Re-listing is cheap relative
    # to leaving a "deleted" customer site serving on the internet.
    residual = sorted(backend.list_digests(prefix).keys())
    if residual:
        return {
            "slug": safe_slug,
            "prefix": prefix,
            "status": "still_present",
            "removed": False,
            "deleted": deleted,
            "deleted_count": len(deleted),
            "still_present": True,
            "residual": residual,
            "residual_count": len(residual),
        }
    return {
        "slug": safe_slug,
        "prefix": prefix,
        "status": "removed" if deleted else "missing",
        "removed": bool(deleted),
        "deleted": deleted,
        "deleted_count": len(deleted),
    }


def public_r2_backend() -> "StorageBackend":
    """The PUBLIC R2 product-site backend under the current authority mode.

    One selection seam for out-of-module callers (public-asset staging etc.): the brokered
    :class:`SafeboxStorageBackend` when this runtime plane defers storage secrets to the safebox,
    the direct :class:`R2StorageBackend` when local ``R2_*`` credentials are provisioned. Callers
    gate with :func:`r2_configured` first; under remote storage authority that check needs only
    endpoint+bucket, so constructing :class:`R2StorageBackend` directly would demand local keys the
    runtime planes deliberately do not hold (2026-07-03 reddit-launch incident)."""
    return SafeboxStorageBackend("r2") if _remote_storage_authority_enabled() else R2StorageBackend()


def write_public_site_to_r2(
    slug: str,
    build_id: str,
    build_root: str | os.PathLike[str],
    *,
    backend: "StorageBackend | None" = None,
) -> dict[str, object]:
    """Mirror one finished static build into the PUBLIC R2 bucket for edge serving.

    Uploads every file under ``build_root`` to ``<slug>/<build_id>/<rel>`` (digest-tagged), THEN
    writes the ``<slug>/current`` pointer to ``build_id`` so the pointer flips only after the whole
    build is present (no torn read at the edge). Public-only: callers must never hand this a private
    workspace tree — ``build_root`` is the same dist that :func:`write_build_artifact` publishes.

    ``backend`` is injectable for tests; in production it defaults to a fresh :class:`R2StorageBackend`
    (raising :class:`StorageUnconfigured` if ``R2_*`` is absent). Returns the uploaded
    ``{slug, build_id, files, pointer_key}`` for the caller's receipt."""
    safe_slug = _safe_slug(slug)
    if backend is not None:
        r2 = backend
    elif _remote_storage_authority_enabled():
        r2 = SafeboxStorageBackend("r2")
    else:
        r2 = R2StorageBackend()
    root = Path(build_root).expanduser().resolve()
    digests = workspace_file_digests(root)
    pointer_key = public_site_pointer_key(safe_slug)
    normalized_build_id = str(build_id or "").strip().lower()
    # build_id is content-addressed, so a pointer that already equals it means every object of
    # this exact build is already present and live — re-PUTting the whole dist adds nothing.
    # Publishes re-run for non-content reasons (logo republish chains, refresh retries), and each
    # skipped re-mirror saves one broker round trip per file. Only a CONFIRMED pointer match
    # skips; a failed pointer read falls through to the full mirror (fail-open to uploading).
    try:
        current_pointer = r2.get(pointer_key).decode("utf-8", errors="replace").strip().lower()
    except Exception:
        current_pointer = ""
    if normalized_build_id and current_pointer == normalized_build_id:
        return {
            "slug": safe_slug,
            "build_id": normalized_build_id,
            "files": {},
            "pointer_key": pointer_key,
            "skipped": "pointer_already_current",
        }
    uploaded: dict[str, str] = {}

    def _mirror_one(item: tuple[str, str]) -> None:
        rel, digest = item
        r2.put(
            public_site_object_key(safe_slug, build_id, rel),
            _read_file_bytes(root / rel),
            digest=digest,
        )

    items = sorted(digests.items())
    _map_concurrently(_mirror_one, items)
    uploaded.update(items)
    pointer_body = normalized_build_id.encode("utf-8")
    r2.put(pointer_key, pointer_body, digest=digest_bytes(pointer_body))
    return {
        "slug": safe_slug,
        "build_id": normalized_build_id,
        "files": uploaded,
        "pointer_key": pointer_key,
    }


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
        if _remote_storage_authority_enabled():
            return SafeboxStorageBackend("supabase_s3")
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
    seen: set[str] = set()
    unique: list[str] = []
    for slug in slugs:
        safe = _safe_slug(str(slug))
        if safe in seen:
            continue
        seen.add(safe)
        unique.append(safe)
    # One remote listing per owned business, EVERY commit (this quota gate sits on the live commit
    # chokepoint) — serialized this dominated commit latency for operators with many businesses
    # (profiled: ~150 owned slugs x ~0.43s per listing ≈ 65s of every single workspace commit).
    # Fan the per-slug listings out on the bounded sync pool; the sum is order-independent.
    return sum(
        _map_concurrently(lambda safe: prefix_bytes(backend, object_prefix(safe)), unique)
    )


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
    to_upload: list[tuple[str, str]] = []
    for rel, dg in sorted(local.items()):
        if excluded and _sync_rel_matches_prefix(rel, excluded):
            skipped.append(rel)
            continue
        full = prefix + rel
        if remote.get(full) == dg:
            skipped.append(rel)
            continue
        to_upload.append((rel, dg))

    # Upload changed files CONCURRENTLY, not one-by-one. The old serial loop priced a first push
    # of a materialized scaffold workspace (hundreds of small files) at file-count x round-trip
    # latency — measured 115-144s per commit from a Mac console rail (~300 files x ~0.4s RTT,
    # strictly serialized), during which the commit held the business scope and serialized every
    # concurrent turn behind it. PUTs to distinct keys are independent on every backend (boto3
    # clients are thread-safe; the safebox broker calls are stateless per-request HTTP), and the
    # per-file CAS digests are unchanged, so concurrency changes wall-clock only, never semantics.
    # Fail-closed contract is the same as the serial loop: any PUT failure raises out of sync_up
    # with a partial (idempotent, digest-keyed) upload that the next sync heals.
    if to_upload:
        def _put_one(item: tuple[str, str]) -> None:
            rel, dg = item
            backend.put(prefix + rel, _read_file_bytes(src / rel), digest=dg)

        _map_concurrently(_put_one, to_upload)
        uploaded.extend(rel for rel, _ in to_upload)

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
    to_download: list[tuple[str, str, str]] = []
    for full, dg in sorted(remote.items()):
        rel = _safe_rel(full[len(prefix):], field="object key")
        if dg == _EXCLUDED_DIGEST or _sync_path_excluded(rel):
            skipped.append(rel)
            continue
        seen.add(rel)
        if local.get(rel) == dg:
            skipped.append(rel)
            continue
        to_download.append((full, rel, dg))

    # Fetch changed files concurrently (bounded pool) — the mirror image of sync_up's parallel
    # PUTs, and the dominant cost of the per-commit workspace readback when serial (each GET is
    # one round trip through the storage broker). Integrity semantics unchanged: every blob is
    # sha256-verified before it lands, writes are atomic per file to distinct paths, and any
    # failure raises out of sync_down exactly as the serial loop did.
    def _get_one(item: tuple[str, str, str]) -> None:
        full, rel, dg = item
        data = backend.get(full)
        actual = digest_bytes(data)
        if actual != dg:
            raise StorageError(f"integrity check failed for {full}: expected {dg}, got {actual}")
        _atomic_write_bytes(dest / rel, data)

    if to_download:
        _map_concurrently(_get_one, to_download)
        downloaded.extend(rel for _, rel, _ in to_download)

    deleted: list[str] = []
    if delete_local:
        for rel in sorted(local):
            if rel not in seen:
                (dest / rel).unlink(missing_ok=True)
                deleted.append(rel)
    return SyncReport((), tuple(downloaded), tuple(deleted), tuple(skipped))
