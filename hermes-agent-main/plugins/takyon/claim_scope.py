"""ClaimScope — durable worker-pool ownership of jobs (modularization plan Stage 2, UC1).

"When I SSH in, my workers must not be given to anybody else." A shell session's local
worker pool is *owned*: the pool registers a heartbeated row in ``worker_pools``, the
session's enqueues carry a :class:`ClaimScope` that reserves each job for that pool, and
``jobs.claim_one`` enforces the reservation with one indexed predicate. This replaces the
payload-hint affinity triangle (env prefix + sidecar file + ``payload->>`` LIKE matching)
with ownership the database can see and other tooling can inspect.

Reservation semantics (``FallbackPolicy``):

- ``any``         — no reservation; exactly the pre-Stage-2 behavior for unhinted jobs.
- ``after_lease`` — the reserved pool has first claim until ``reservation_expires_at``;
                    after that the job spills to any eligible worker (this reproduces the
                    old grace-window behavior as a config value, so nothing regresses at
                    cutover). Requeues renew the window (commit f899da41's contract).
- ``strict``      — claimable ONLY by the reserved pool while that pool's registry lease is
                    alive; when the owning pool dies (lease lapses / decommission), the job
                    spills rather than strands.

``exclusive=True`` on a pool cuts the other way as well: that pool claims ONLY jobs
reserved for it (UC1's "my workers do nothing else"). Both directions are enforced in
``jobs.claim_one``.

Money safety (plan R1): reservation and budget are ORTHOGONAL rails. A reclaimed job's
billing hold is reconciled by ``jobs.run_one`` step 3 (release stale hold before the new
reserve) exactly as before; nothing here touches reserve→settle→release.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

FallbackPolicy = Literal["strict", "after_lease", "any"]

_VALID_POLICIES = ("strict", "after_lease", "any")

# One env var replaces the old prefix/window/sidecar triangle: the operator-prod script
# starts the session's worker pool and exports the pool id for the shell to bind enqueues.
POOL_ID_ENV = "TAKYON_WORKER_POOL_ID"
POOL_EXCLUSIVE_ENV = "TAKYON_WORKER_POOL_EXCLUSIVE"
RUNTIME_RELEASE_SHA_ENV = "TAKYON_RUNTIME_RELEASE_SHA"
UNPINNED_RELEASE_SHA = "0" * 40

# Default first-claim window for after_lease session reservations — matches the old
# TAKYON_PREFERRED_WORKER_CLAIM_SECONDS default so cutover is behavior-identical.
DEFAULT_LEASE_SECONDS = 3600.0

# A pool row is considered live while its heartbeat lease is in the future and it has not
# been decommissioned. 'draining' still owns its reservations (it is finishing in-flight
# work); only decommissioned/lost/lapsed pools release strict reservations.
POOL_LIVE_STATUSES = ("joining", "active", "draining")


def _valid_release_sha(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 40 or any(ch not in "0123456789abcdef" for ch in normalized):
        return ""
    if normalized == UNPINNED_RELEASE_SHA:
        return ""
    return normalized


def runtime_release_sha(*, runtime_root: str | os.PathLike[str] | None = None) -> str:
    """Return the exact code release this process is executing, or fail closed.

    Deployed trees carry the immutable deploy artifact manifest. Local operator workers run from a
    Git worktree and use its exact HEAD. An explicit env override exists for packaged runtimes, but
    it is validated identically. The all-zero migration sentinel is never accepted as executable
    code, so pre-fence workers cannot impersonate a current release.
    """
    configured_raw = str(os.getenv(RUNTIME_RELEASE_SHA_ENV) or "").strip()
    configured = _valid_release_sha(configured_raw)
    if configured_raw and not configured:
        raise RuntimeError(f"{RUNTIME_RELEASE_SHA_ENV} must be a nonzero 40-character Git SHA")

    root = (
        Path(runtime_root).expanduser().resolve()
        if runtime_root is not None
        else Path(__file__).resolve().parents[2]
    )
    manifest_path = root / ".takyon-deploy-artifact.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f"invalid runtime deploy manifest {manifest_path}: {exc}") from exc
        deployed = _valid_release_sha(
            manifest.get("source_revision") if isinstance(manifest, dict) else ""
        )
        if not deployed:
            raise RuntimeError(f"runtime deploy manifest {manifest_path} has no valid source_revision")
        if configured and configured != deployed:
            raise RuntimeError(
                f"{RUNTIME_RELEASE_SHA_ENV}={configured} does not match deployed runtime {deployed}"
            )
        return deployed

    try:
        resolved = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot resolve runtime release SHA: {exc}") from exc
    git_sha = _valid_release_sha(resolved.stdout if resolved.returncode == 0 else "")
    if not git_sha:
        detail = str(resolved.stderr or resolved.stdout or "not a Git worktree").strip()
        raise RuntimeError(f"cannot resolve runtime release SHA from {root}: {detail}")
    try:
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "--", "."],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot verify runtime worktree cleanliness: {exc}") from exc
    if dirty.returncode != 0:
        detail = str(dirty.stderr or "git status failed").strip()
        raise RuntimeError(f"cannot verify runtime worktree cleanliness at {root}: {detail}")
    if str(dirty.stdout or "").strip():
        raise RuntimeError(
            "refusing to advertise a Git release SHA from a modified runtime worktree; "
            f"commit or remove changes under {root}, or use a revision-sealed deploy artifact"
        )
    if configured and configured != git_sha:
        raise RuntimeError(
            f"{RUNTIME_RELEASE_SHA_ENV}={configured} does not match clean runtime HEAD {git_sha}"
        )
    return git_sha


def require_local_release_sha(supplied: object, *, field: str) -> str:
    """Return the process's sealed release, rejecting caller attempts to override it."""
    local_release = runtime_release_sha()
    supplied_raw = str(supplied or "").strip()
    if not supplied_raw:
        return local_release
    requested = _valid_release_sha(supplied_raw)
    if not requested:
        raise ValueError(f"{field} must be a nonzero 40-character Git SHA")
    if requested != local_release:
        raise RuntimeError(
            f"{field}={requested} does not match this process release {local_release}"
        )
    return local_release


@dataclass(frozen=True)
class ClaimScope:
    """A job-targeting reservation: which pool owns the work and how hard the ownership is."""

    pool_id: str | None = None
    owner_user_id: str | None = None
    fallback: FallbackPolicy = "any"
    lease_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.fallback not in _VALID_POLICIES:
            raise ValueError(f"invalid fallback policy: {self.fallback!r}")
        if self.fallback != "any" and not str(self.pool_id or "").strip():
            raise ValueError(f"fallback={self.fallback!r} requires a pool_id")


def session_claim_scope(
    *,
    owner_user_id: str | None = None,
    lease_seconds: float | None = None,
) -> ClaimScope | None:
    """The current session's scope, from the one pool-id env var the operator-prod script
    exports. Returns None when no session pool is declared (dashboard/VPS lanes) — enqueues
    stay unreserved, exactly the pre-Stage-2 behavior for those lanes."""
    pool_id = str(os.getenv(POOL_ID_ENV) or "").strip()
    if not pool_id:
        return None
    exclusive = str(os.getenv(POOL_EXCLUSIVE_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    window = DEFAULT_LEASE_SECONDS if lease_seconds is None else max(0.0, float(lease_seconds))
    return ClaimScope(
        pool_id=pool_id,
        owner_user_id=owner_user_id,
        fallback="strict" if exclusive else "after_lease",
        lease_seconds=window,
    )


# ── pool registry (worker_pools) ────────────────────────────────────────────────────────


def register_pool(
    conn,
    *,
    pool_id: str,
    owner_user_id: str | None = None,
    session_key: str | None = None,
    hostname: str | None = None,
    exclusive: bool = False,
    concurrency: int = 1,
    capabilities: dict[str, Any] | None = None,
    lease_seconds: float = 900.0,
    release_sha: str | None = None,
) -> None:
    """Idempotent upsert: (re-)register a live pool with a fresh lease. Called on
    ``WorkerPool.run()`` start; a restart with the same pool_id simply revives the row."""
    exact_release = require_local_release_sha(release_sha, field="release_sha")

    conn.execute(
        "insert into worker_pools (pool_id, owner_user_id, session_key, hostname, exclusive,"
        " concurrency, status, capabilities, release_sha, lease_expires_at, registered_at, updated_at)"
        " values (%s, %s, %s, %s, %s, %s, 'active', %s::jsonb, %s,"
        " now() + (%s::double precision * interval '1 second'), now(), now())"
        " on conflict (pool_id) do update set"
        " owner_user_id = excluded.owner_user_id,"
        " session_key = excluded.session_key,"
        " hostname = excluded.hostname,"
        " exclusive = excluded.exclusive,"
        " concurrency = excluded.concurrency,"
        " status = 'active',"
        " capabilities = excluded.capabilities,"
        " release_sha = excluded.release_sha,"
        " lease_expires_at = excluded.lease_expires_at,"
        " updated_at = now()",
        (
            pool_id,
            str(owner_user_id or "").strip() or None,
            str(session_key or "").strip() or None,
            hostname or socket.gethostname(),
            bool(exclusive),
            max(1, int(concurrency)),
            json.dumps(capabilities or {}),
            exact_release,
            max(60.0, float(lease_seconds)),
        ),
    )


def heartbeat_pool(conn, pool_id: str, *, lease_seconds: float = 900.0) -> bool:
    """Renew a live pool's lease. Returns False if the row is missing or decommissioned
    (caller should re-register)."""
    row = conn.execute(
        "update worker_pools set lease_expires_at = now() + (%s::double precision * interval '1 second'),"
        " status = case when status in ('joining','lost') then 'active' else status end,"
        " updated_at = now()"
        " where pool_id = %s and status <> 'decommissioned'"
        " returning pool_id",
        (max(60.0, float(lease_seconds)), pool_id),
    ).fetchone()
    return row is not None


def begin_drain(conn, pool_id: str) -> None:
    """Mark a pool draining: it finishes in-flight work and keeps its reservations, but the
    operator can see it is on the way out."""
    conn.execute(
        "update worker_pools set status = 'draining', updated_at = now()"
        " where pool_id = %s and status in ('joining', 'active')",
        (pool_id,),
    )


def decommission_pool(conn, pool_id: str) -> None:
    """Terminal: the pool is gone. Its strict reservations become spillable immediately
    (the claim predicate checks pool liveness, not this call)."""
    conn.execute(
        "update worker_pools set status = 'decommissioned', updated_at = now() where pool_id = %s",
        (pool_id,),
    )


def reap_lost_pools(conn, *, older_than_seconds: float = 0.0) -> int:
    """Flip lapsed leases to 'lost' (observability only — the claim predicate already treats a
    lapsed lease as dead). Returns rows flipped."""
    return conn.execute(
        "update worker_pools set status = 'lost', updated_at = now()"
        " where status in ('joining', 'active', 'draining')"
        " and lease_expires_at < now() - (%s::double precision * interval '1 second')",
        (max(0.0, float(older_than_seconds)),),
    ).rowcount


def get_pool(conn, pool_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "select pool_id, owner_user_id, session_key, hostname, exclusive, concurrency, status,"
        " capabilities, release_sha, lease_expires_at, registered_at, updated_at"
        " from worker_pools where pool_id = %s",
        (pool_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "pool_id": row[0],
        "owner_user_id": row[1],
        "session_key": row[2],
        "hostname": row[3],
        "exclusive": bool(row[4]),
        "concurrency": int(row[5]),
        "status": row[6],
        "capabilities": row[7],
        "release_sha": row[8],
        "lease_expires_at": row[9],
        "registered_at": row[10],
        "updated_at": row[11],
    }
