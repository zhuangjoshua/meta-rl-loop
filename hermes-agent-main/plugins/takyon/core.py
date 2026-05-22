"""Core storage and guardrails for the Takyon business plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - Takyon normally depends on python-dotenv.
    def load_dotenv(dotenv_path: Path, override: bool = False, encoding: str = "utf-8") -> bool:
        """Tiny fallback so the Takyon plugin fails on missing APIs, not imports."""
        try:
            lines = Path(dotenv_path).read_text(encoding=encoding).splitlines()
        except OSError:
            return False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and (override or key not in os.environ):
                os.environ[key] = value
        return True

from takyon_constants import get_takyon_home
from tools.registry import tool_error, tool_result

from .registry import business_registry_snapshot


TAKYON_TOOLSET = "takyon"
DEFAULT_TAKYON_DIRNAME = "takyon"
MAX_READ_CHARS = 64_000
MAX_WRITE_CHARS = 1_000_000

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_CONTROL_STATES = {"active", "paused", "killed"}

# Guardrail aliases only. Agents can always pass explicit env names through
# requires_env when an API is not listed here.
_API_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "database": ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"),
    "fal": ("FAL_KEY", "FAL_API_KEY"),
    "firecrawl": ("FIRECRAWL_API_KEY",),
    "llm": ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
    "meta": ("META_ACCESS_TOKEN", "FACEBOOK_ACCESS_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "parallel": ("PARALLEL_API_KEY",),
    "stripe": ("STRIPE_SECRET_KEY",),
    "tavily": ("TAVILY_API_KEY",),
    "vercel": ("VERCEL_TOKEN",),
    "x": ("X_API_KEY", "TWITTER_API_KEY", "X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN"),
}

_JOB_API_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "ai_gateway_setup": ("llm",),
    "ceo_wakeup": ("llm",),
    "community_research": ("tavily",),
    "foundation": ("llm",),
    "meta_seedance": ("openai",),
    "product_backend": ("vercel",),
    "product_ui": ("vercel",),
    "stripe_setup": ("stripe",),
    "website_build_deploy": ("vercel",),
    "x_social": ("x",),
}


class TakyonError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _slugify(value: str) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw)
    raw = raw.strip("-_")
    if not raw:
        raise TakyonError("business slug is required")
    if not _SLUG_RE.match(raw):
        raise TakyonError(
            "business slug must start with a lowercase letter/number and contain only a-z, 0-9, '_' or '-'"
        )
    return raw


def _safe_relpath(value: str, *, field: str = "path") -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise TakyonError(f"{field} is required")
    path = Path(raw)
    if path.is_absolute():
        raise TakyonError(f"{field} must be relative, not absolute: {raw!r}")
    parts = path.parts
    if any(part in {"", ".", ".."} for part in parts):
        raise TakyonError(f"{field} contains an unsafe segment: {raw!r}")
    if len(parts) > 48:
        raise TakyonError(f"{field} is too deep")
    return Path(*parts)


def _atomic_write_text(path: Path, content: str) -> None:
    if len(content) > MAX_WRITE_CHARS:
        raise TakyonError(f"content is too large for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_text_limited(path: Path, limit: int = MAX_READ_CHARS) -> str:
    data = path.read_text(encoding="utf-8", errors="replace")
    if len(data) > limit:
        return data[:limit] + "\n\n[truncated]"
    return data


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_env_files() -> list[Path]:
    paths: list[Path] = []
    for key in ("TAKYON_ENV_FILE", "POLSIAV3_ENV_FILE", "POLSIA3_ENV_FILE"):
        value = os.getenv(key)
        if value:
            paths.append(Path(value).expanduser())

    root = _repo_root()
    search_roots = [
        root.parent / "polsia3",
        root.parent / "polsiav3",
        root.parent / "polsia-v3",
        root.parent / "polsia",
    ]
    for base in search_roots:
        paths.extend([base / ".env.local", base / ".env", base / "secrets" / ".env"])
    return paths


_loaded_env_paths: set[Path] = set()


def load_takyon_env() -> list[str]:
    """Load nearby Takyon/Polsia env files without overriding Takyon env."""
    loaded: list[str] = []
    for path in _candidate_env_files():
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in _loaded_env_paths or not resolved.exists() or not resolved.is_file():
            continue
        load_dotenv(dotenv_path=resolved, override=False, encoding="utf-8")
        _loaded_env_paths.add(resolved)
        loaded.append(str(resolved))
    return loaded


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _missing_env_for_requirement(requirement: str) -> list[str]:
    key = str(requirement or "").strip()
    if not key:
        return []
    alias = _API_ENV_ALIASES.get(key.lower())
    if alias:
        return [] if any(os.getenv(name) for name in alias) else ["/".join(alias)]
    return [] if os.getenv(key) else [key]


def _require_api_access(op: dict[str, Any]) -> None:
    load_takyon_env()
    missing: list[str] = []
    required_api = list(_as_list(op.get("requires_api")))
    if str(op.get("action") or "") == "job.enqueue":
        required_api.extend(_JOB_API_REQUIREMENTS.get(str(op.get("kind") or ""), ()))
    if str(op.get("provider") or "").strip():
        required_api.append(str(op.get("provider")))
    for req in required_api:
        missing.extend(_missing_env_for_requirement(str(req)))
    for req in _as_list(op.get("requires_env")):
        missing.extend(_missing_env_for_requirement(str(req)))
    if missing:
        action = op.get("action") or "<unknown>"
        raise TakyonError(
            f"{action} requires missing API/env credential(s): {', '.join(sorted(set(missing)))}"
        )


def _hash_operation(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _budget_amount(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("amount", "cap", "limit", "monthly_cap"):
            if key in value:
                try:
                    return float(value[key])
                except (TypeError, ValueError):
                    return None
    return None


def _scope_parts(scope: str) -> dict[str, str | None]:
    raw = str(scope or "").strip()
    if not raw:
        raise TakyonError("scope is required")
    if raw == "global":
        return {"raw": raw, "business": None, "kind": "global", "resource": None}
    if not raw.startswith("business:"):
        raise TakyonError("scope must be 'global' or start with 'business:<slug>'")
    rest = raw[len("business:") :]
    if "/" not in rest:
        business = _slugify(rest)
        return {"raw": f"business:{business}", "business": business, "kind": "business", "resource": None}
    business_raw, resource = rest.split("/", 1)
    business = _slugify(business_raw)
    if not resource:
        raise TakyonError("scope resource is empty")
    return {"raw": f"business:{business}/{resource}", "business": business, "kind": "resource", "resource": resource}


def _scope_ancestors(scope: str) -> list[str]:
    parsed = _scope_parts(scope)
    raw = str(parsed["raw"])
    ancestors = ["global"]
    business = parsed["business"]
    if business:
        ancestors.append(f"business:{business}")
    if raw not in ancestors:
        bits = raw.split("/")
        current = bits[0]
        for bit in bits[1:]:
            current = f"{current}/{bit}"
            ancestors.append(current)
    return ancestors


class TakyonStore:
    """File + SQLite store for isolated business brains and campaign workspaces."""

    def __init__(self, root: str | os.PathLike[str] | None = None):
        base = Path(root).expanduser() if root else Path(os.getenv("TAKYON_HOME") or get_takyon_home() / DEFAULT_TAKYON_DIRNAME)
        self.root = base.resolve()
        self.db_path = self.root / "state.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        self._init_db(conn)
        return conn

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS businesses (
              slug TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              goal TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'active',
              budget_json TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspaces (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              path TEXT NOT NULL,
              kind TEXT NOT NULL DEFAULT 'workspace',
              status TEXT NOT NULL DEFAULT 'active',
              budget_json TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (business_slug, path),
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL,
              business_slug TEXT,
              kind TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued',
              payload_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL,
              parent_id TEXT,
              status TEXT NOT NULL,
              prompt TEXT,
              result_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_entries (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL,
              business_slug TEXT,
              amount REAL NOT NULL,
              currency TEXT NOT NULL DEFAULT 'USD',
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              payload_json TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS control_states (
              scope TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              reason TEXT NOT NULL DEFAULT '',
              actor TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL,
              business_slug TEXT,
              event_type TEXT NOT NULL,
              payload_json TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS idempotency_keys (
              key TEXT PRIMARY KEY,
              operation_hash TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

    def _business_root(self, slug: str) -> Path:
        return self.root / "businesses" / _slugify(slug)

    def _resolve_business_file(self, slug: str, rel: str) -> Path:
        root = self._business_root(slug)
        path = (root / _safe_relpath(rel)).resolve()
        if root.resolve() not in (path, *path.parents):
            raise TakyonError("path escaped business root")
        return path

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in list(result):
            if key.endswith("_json"):
                result[key[:-5]] = _json_loads(result.pop(key), {})
        return result

    def _business(self, conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM businesses WHERE slug = ?", (_slugify(slug),)).fetchone()
        return self._row_to_dict(row)

    def _ensure_business(self, conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
        business = self._business(conn, slug)
        if not business:
            raise TakyonError(f"business not found: {slug}")
        return business

    def _control_blocker(self, conn: sqlite3.Connection, scope: str, *, allow_paused: bool = False) -> dict[str, Any] | None:
        ancestors = _scope_ancestors(scope)
        placeholders = ",".join("?" for _ in ancestors)
        rows = conn.execute(
            f"SELECT * FROM control_states WHERE scope IN ({placeholders})",
            ancestors,
        ).fetchall()
        states = {row["scope"]: self._row_to_dict(row) for row in rows}
        for ancestor in ancestors:
            state = states.get(ancestor)
            if not state:
                continue
            if state["state"] == "killed":
                return state
            if state["state"] == "paused" and not allow_paused:
                return state
        return None

    def _record_event(
        self,
        conn: sqlite3.Connection,
        *,
        scope: str,
        business_slug: str | None,
        event_type: str,
        payload: Any,
    ) -> str:
        event_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO events (id, scope, business_slug, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, scope, business_slug, event_type, _json_dumps(payload), _now()),
        )
        return event_id

    def read(
        self,
        *,
        scope: str = "global",
        query: str = "summary",
        path: str | None = None,
        include: Iterable[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        parsed = _scope_parts(scope)
        query = str(query or "summary").strip().lower()
        include_set = {str(item).strip().lower() for item in (include or []) if str(item).strip()}
        limit = max(1, min(int(limit or 50), 200))

        with self._connect() as conn:
            if query in {"businesses", "list_businesses"} or parsed["kind"] == "global":
                businesses = [
                    self._row_to_dict(row)
                    for row in conn.execute("SELECT * FROM businesses ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
                ]
                controls = [
                    self._row_to_dict(row)
                    for row in conn.execute("SELECT * FROM control_states ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
                ]
                return {"success": True, "scope": "global", "businesses": businesses, "controls": controls}

            slug = str(parsed["business"])
            business = self._ensure_business(conn, slug)

            if query in {"file", "read_file"}:
                if not path:
                    raise TakyonError("path is required for read_file")
                file_path = self._resolve_business_file(slug, path)
                if not file_path.exists() or not file_path.is_file():
                    raise TakyonError(f"file not found: {path}")
                return {"success": True, "scope": scope, "path": path, "content": _read_text_limited(file_path)}

            if query in {"files", "list_files"}:
                rel = path or "."
                directory = self._resolve_business_file(slug, rel)
                if not directory.exists():
                    return {"success": True, "scope": scope, "path": rel, "files": []}
                if not directory.is_dir():
                    raise TakyonError(f"path is not a directory: {rel}")
                files = []
                for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:limit]:
                    files.append({"path": str(child.relative_to(self._business_root(slug))), "type": "dir" if child.is_dir() else "file"})
                return {"success": True, "scope": scope, "path": rel, "files": files}

            workspaces = [
                self._row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM workspaces WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?",
                    (slug, limit),
                ).fetchall()
            ]
            ledger = [
                self._row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM ledger_entries WHERE business_slug = ? ORDER BY created_at DESC LIMIT ?",
                    (slug, limit),
                ).fetchall()
            ]
            events = [
                self._row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT ?",
                    (slug, limit),
                ).fetchall()
            ]
            jobs = [
                self._row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM jobs WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?",
                    (slug, limit),
                ).fetchall()
            ]
            controls = [
                self._row_to_dict(row)
                for row in conn.execute(
                    "SELECT * FROM control_states WHERE scope = ? OR scope LIKE ? ORDER BY updated_at DESC LIMIT ?",
                    (f"business:{slug}", f"business:{slug}/%", limit),
                ).fetchall()
            ]

            brain_index: list[dict[str, str]] = []
            brain_root = self._business_root(slug) / "brain"
            if brain_root.exists():
                for child in sorted(brain_root.rglob("*")):
                    if child.is_file():
                        brain_index.append({"path": str(child.relative_to(self._business_root(slug)))})
                        if len(brain_index) >= limit:
                            break

            response: dict[str, Any] = {
                "success": True,
                "scope": scope,
                "business": business,
                "workspaces": workspaces,
                "controls": controls,
                "brain_index": brain_index,
            }
            if query in {"ledger", "summary"} or "ledger" in include_set:
                response["ledger"] = ledger
            if query in {"events", "summary"} or "events" in include_set:
                response["events"] = events
            if query in {"jobs", "summary"} or "jobs" in include_set:
                response["jobs"] = jobs
            return response

    def commit(
        self,
        *,
        scope: str,
        operations: list[dict[str, Any]],
        idempotency_key: str,
        reason: str = "",
        actor: str = "agent",
    ) -> dict[str, Any]:
        if not idempotency_key or not str(idempotency_key).strip():
            raise TakyonError("idempotency_key is required for every durable Takyon write")
        idempotency_key = str(idempotency_key).strip()
        if len(idempotency_key) > 200:
            raise TakyonError("idempotency_key is too long")
        if not isinstance(operations, list) or not operations:
            raise TakyonError("operations must be a non-empty list")
        parsed = _scope_parts(scope)
        op_hash = _hash_operation({"scope": scope, "operations": operations, "reason": reason, "actor": actor})

        with self._connect() as conn:
            prior = conn.execute("SELECT * FROM idempotency_keys WHERE key = ?", (idempotency_key,)).fetchone()
            if prior:
                if prior["operation_hash"] != op_hash:
                    raise TakyonError("idempotency_key already used for different operations")
                return _json_loads(prior["result_json"], {"success": True, "idempotent": True})

            staged = [self._normalize_operation(conn, parsed, op) for op in operations]

            results: list[dict[str, Any]] = []
            with conn:
                for item in staged:
                    result = self._apply_operation(conn, parsed, item, reason=reason, actor=actor)
                    results.append(result)
                final = {"success": True, "scope": str(parsed["raw"]), "results": results}
                conn.execute(
                    "INSERT INTO idempotency_keys (key, operation_hash, result_json, created_at) VALUES (?, ?, ?, ?)",
                    (idempotency_key, op_hash, _json_dumps(final), _now()),
                )
            return final

    def _normalize_operation(self, conn: sqlite3.Connection, parsed_scope: dict[str, str | None], op: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(op, dict):
            raise TakyonError("each operation must be an object")
        _require_api_access(op)
        action = str(op.get("action") or "").strip()
        if not action:
            raise TakyonError("operation.action is required")

        business = op.get("business") or parsed_scope.get("business")
        business_slug = _slugify(str(business)) if business else None
        target_scope = str(op.get("scope") or parsed_scope["raw"])
        if business_slug and target_scope == "global":
            target_scope = f"business:{business_slug}"

        allowed = {
            "agent.record",
            "artifact.patch",
            "artifact.write",
            "business.upsert",
            "control.set",
            "cron.ensure_ceo_wakeup",
            "event.record",
            "job.enqueue",
            "ledger.allocate",
            "maintenance.gc",
            "memory.write",
            "workspace.upsert",
        }
        if action not in allowed:
            raise TakyonError(f"unsupported operation.action: {action}")

        if action != "business.upsert" and business_slug:
            self._ensure_business(conn, business_slug)
        if action not in {"control.set"}:
            blocker = self._control_blocker(conn, target_scope)
            if blocker:
                raise TakyonError(
                    f"scope {target_scope} is {blocker['state']} by kill switch {blocker['scope']}: {blocker.get('reason') or ''}"
                )
        if action not in {"business.upsert", "control.set", "maintenance.gc"} and not business_slug:
            raise TakyonError(f"{action} requires a business scope")

        normalized = dict(op)
        normalized["action"] = action
        normalized["business_slug"] = business_slug
        normalized["target_scope"] = target_scope
        return normalized

    def _apply_operation(
        self,
        conn: sqlite3.Connection,
        parsed_scope: dict[str, str | None],
        op: dict[str, Any],
        *,
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        action = op["action"]
        slug = op.get("business_slug")
        target_scope = op["target_scope"]

        if action == "business.upsert":
            slug = _slugify(str(op.get("business") or op.get("slug") or parsed_scope.get("business") or op.get("name") or ""))
            name = str(op.get("name") or slug)
            goal = str(op.get("goal") or "")
            budget = op.get("budget")
            metadata = op.get("metadata") or {}
            now = _now()
            existing = self._business(conn, slug)
            if existing:
                conn.execute(
                    "UPDATE businesses SET name = ?, goal = COALESCE(NULLIF(?, ''), goal), budget_json = COALESCE(?, budget_json), metadata_json = ?, updated_at = ? WHERE slug = ?",
                    (name, goal, _json_dumps(budget) if budget is not None else None, _json_dumps(metadata), now, slug),
                )
            else:
                conn.execute(
                    "INSERT INTO businesses (slug, name, goal, status, budget_json, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
                    (slug, name, goal, _json_dumps(budget or {}), _json_dumps(metadata), now, now),
                )
            root = self._business_root(slug)
            (root / "brain").mkdir(parents=True, exist_ok=True)
            index = root / "brain" / "index.md"
            if not index.exists():
                _atomic_write_text(index, f"# {name}\n\nGoal: {goal or 'Unspecified'}\n")
            self._record_event(conn, scope=f"business:{slug}", business_slug=slug, event_type="business.upsert", payload={"reason": reason, "actor": actor})
            return {"action": action, "business": slug, "path": str(root)}

        if action == "control.set":
            state = str(op.get("state") or "").strip().lower()
            if state not in _CONTROL_STATES:
                raise TakyonError(f"control.set state must be one of {sorted(_CONTROL_STATES)}")
            control_scope = str(op.get("scope") or target_scope)
            _scope_parts(control_scope)
            conn.execute(
                "INSERT INTO control_states (scope, state, reason, actor, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET state = excluded.state, reason = excluded.reason, actor = excluded.actor, updated_at = excluded.updated_at",
                (control_scope, state, str(op.get("reason") or reason or ""), actor, _now()),
            )
            business = _scope_parts(control_scope).get("business")
            self._record_event(conn, scope=control_scope, business_slug=business, event_type="control.set", payload={"state": state, "reason": op.get("reason") or reason, "actor": actor})
            return {"action": action, "scope": control_scope, "state": state}

        if action == "maintenance.gc":
            return self._gc(conn, parsed_scope, op)

        assert slug is not None

        if action == "workspace.upsert":
            rel = _safe_relpath(str(op.get("path") or op.get("workspace") or ""), field="workspace path")
            path_text = rel.as_posix()
            kind = str(op.get("kind") or "workspace")
            status = str(op.get("status") or "active")
            budget = op.get("budget")
            metadata = op.get("metadata") or {}
            now = _now()
            workspace_id = op.get("id") or uuid.uuid4().hex
            conn.execute(
                "INSERT INTO workspaces (id, business_slug, path, kind, status, budget_json, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(business_slug, path) DO UPDATE SET kind = excluded.kind, status = excluded.status, budget_json = COALESCE(excluded.budget_json, workspaces.budget_json), metadata_json = excluded.metadata_json, updated_at = excluded.updated_at",
                (workspace_id, slug, path_text, kind, status, _json_dumps(budget) if budget is not None else None, _json_dumps(metadata), now, now),
            )
            (self._business_root(slug) / rel).mkdir(parents=True, exist_ok=True)
            self._record_event(conn, scope=f"business:{slug}/workspace:{path_text}", business_slug=slug, event_type=action, payload={"reason": reason, "actor": actor, "metadata": metadata})
            return {"action": action, "business": slug, "workspace": path_text}

        if action in {"artifact.write", "memory.write"}:
            raw_path = str(op.get("path") or "")
            if action == "memory.write" and not raw_path.startswith("brain/"):
                raw_path = f"brain/{raw_path}"
            file_path = self._resolve_business_file(slug, raw_path)
            content = str(op.get("content") or "")
            mode = str(op.get("mode") or "replace").strip().lower()
            if mode == "append" and file_path.exists():
                existing = file_path.read_text(encoding="utf-8", errors="replace")
                content = existing + content
            elif mode != "replace":
                raise TakyonError("write mode must be 'replace' or 'append'")
            _atomic_write_text(file_path, content)
            rel = str(file_path.relative_to(self._business_root(slug)))
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"path": rel, "reason": reason, "actor": actor})
            return {"action": action, "business": slug, "path": rel}

        if action == "artifact.patch":
            file_path = self._resolve_business_file(slug, str(op.get("path") or ""))
            if not file_path.exists():
                raise TakyonError(f"cannot patch missing file: {op.get('path')}")
            old = str(op.get("old") or "")
            new = str(op.get("new") or "")
            if not old:
                raise TakyonError("artifact.patch requires non-empty old text")
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if old not in content:
                raise TakyonError("artifact.patch old text not found")
            _atomic_write_text(file_path, content.replace(old, new, 1))
            rel = str(file_path.relative_to(self._business_root(slug)))
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"path": rel, "reason": reason, "actor": actor})
            return {"action": action, "business": slug, "path": rel}

        if action == "ledger.allocate":
            amount = float(op.get("amount") or 0)
            if amount < 0:
                raise TakyonError("ledger.allocate amount must be non-negative")
            business = self._ensure_business(conn, slug)
            cap = _budget_amount(op.get("budget") or business.get("budget"))
            if amount > 0 and cap is None:
                raise TakyonError(f"business {slug} has no numeric budget cap; refusing allocation")
            if cap is not None:
                used = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) AS total FROM ledger_entries WHERE business_slug = ? AND status IN ('allocated', 'spent')",
                    (slug,),
                ).fetchone()["total"]
                if float(used or 0) + amount > cap:
                    raise TakyonError(f"allocation would exceed budget cap {cap}: used {used}, requested {amount}")
            entry_id = op.get("id") or uuid.uuid4().hex
            payload = {k: v for k, v in op.items() if k not in {"action", "business_slug", "target_scope"}}
            conn.execute(
                "INSERT INTO ledger_entries (id, scope, business_slug, amount, currency, kind, status, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, target_scope, slug, amount, str(op.get("currency") or "USD"), str(op.get("kind") or "allocation"), str(op.get("status") or "allocated"), _json_dumps(payload), _now()),
            )
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload=payload)
            return {"action": action, "business": slug, "ledger_entry": entry_id, "amount": amount}

        if action == "job.enqueue":
            job_id = op.get("id") or uuid.uuid4().hex
            payload = op.get("payload") or {}
            conn.execute(
                "INSERT INTO jobs (id, scope, business_slug, kind, status, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, target_scope, slug, str(op.get("kind") or "job"), str(op.get("status") or "queued"), _json_dumps(payload), _now(), _now()),
            )
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"job_id": job_id, "kind": op.get("kind"), "reason": reason})
            return {"action": action, "business": slug, "job": job_id}

        if action == "agent.record":
            run_id = op.get("id") or uuid.uuid4().hex
            conn.execute(
                "INSERT INTO agent_runs (id, scope, parent_id, status, prompt, result_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, target_scope, op.get("parent_id"), str(op.get("status") or "recorded"), str(op.get("prompt") or ""), _json_dumps(op.get("result") or {}), _now(), _now()),
            )
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"run_id": run_id, "status": op.get("status")})
            return {"action": action, "business": slug, "agent_run": run_id}

        if action == "event.record":
            event_id = self._record_event(
                conn,
                scope=target_scope,
                business_slug=slug,
                event_type=str(op.get("event_type") or "event"),
                payload=op.get("payload") or {},
            )
            return {"action": action, "business": slug, "event": event_id}

        if action == "cron.ensure_ceo_wakeup":
            result = self._ensure_ceo_cron(slug, schedule=str(op.get("schedule") or "every 6h"), reason=reason)
            self._record_event(conn, scope=f"business:{slug}", business_slug=slug, event_type=action, payload=result)
            return {"action": action, "business": slug, **result}

        raise TakyonError(f"unhandled operation.action: {action}")

    def _gc(self, conn: sqlite3.Connection, parsed_scope: dict[str, str | None], op: dict[str, Any]) -> dict[str, Any]:
        """Prune ephemeral rows. Never deletes ledgers, controls, businesses, or files."""
        older_than_days = max(7, int(op.get("older_than_days") or 90))
        max_delete = max(1, min(int(op.get("max_delete") or 1000), 10_000))
        dry_run = not bool(op.get("confirm"))
        cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - older_than_days * 86400, timezone.utc).isoformat()
        business = parsed_scope.get("business")
        scope_raw = str(parsed_scope["raw"])

        filters = []
        params: list[Any] = []
        if business:
            filters.append("business_slug = ?")
            params.append(str(business))
        elif scope_raw != "global":
            filters.append("scope = ?")
            params.append(scope_raw)
        where_scope = (" AND " + " AND ".join(filters)) if filters else ""

        candidates: dict[str, list[str]] = {}
        queries = {
            "events": f"SELECT id FROM events WHERE created_at < ?{where_scope} ORDER BY created_at ASC LIMIT ?",
            "agent_runs": f"SELECT id FROM agent_runs WHERE created_at < ?{where_scope} ORDER BY created_at ASC LIMIT ?",
            "jobs": (
                "SELECT id FROM jobs WHERE created_at < ? AND status IN "
                "('completed', 'cancelled', 'failed', 'killed')"
                f"{where_scope} ORDER BY created_at ASC LIMIT ?"
            ),
        }
        for table, sql in queries.items():
            rows = conn.execute(sql, [cutoff, *params, max_delete]).fetchall()
            candidates[table] = [row["id"] for row in rows]

        deleted = {table: 0 for table in candidates}
        if not dry_run:
            for table, ids in candidates.items():
                if not ids:
                    continue
                placeholders = ",".join("?" for _ in ids)
                conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
                deleted[table] = len(ids)

        return {
            "action": "maintenance.gc",
            "dry_run": dry_run,
            "older_than_days": older_than_days,
            "cutoff": cutoff,
            "candidates": {table: len(ids) for table, ids in candidates.items()},
            "deleted": deleted,
            "protected": ["businesses", "workspaces", "ledger_entries", "control_states", "idempotency_keys", "files"],
        }

    def _ensure_ceo_cron(self, slug: str, *, schedule: str, reason: str) -> dict[str, Any]:
        blocker: dict[str, Any] | None
        with self._connect() as conn:
            blocker = self._control_blocker(conn, f"business:{slug}")
        if blocker:
            raise TakyonError(f"cannot schedule CEO wakeup; business:{slug} is {blocker['state']}")

        from cron.jobs import create_job, list_jobs, update_job

        name = f"takyon-ceo:{slug}"
        prompt = (
            f"CEO wakeup for business:{slug}.\n"
            "Use the concrete business_* tools to read state, update business memory, create workspaces, enqueue jobs, "
            "allocate budget, and adjust the next wakeup if useful. Decide the highest expected-impact move under "
            "the business goal, budget, evidence, active campaigns, failures, and kill switches. Keep all business "
            "memory inside this business scope."
        )
        enabled_toolsets = ["takyon", "web", "skills", "todo", "delegation"]
        existing = next((job for job in list_jobs(include_disabled=True) if job.get("name") == name), None)
        if existing:
            updated = update_job(
                existing["id"],
                {
                    "prompt": prompt,
                    "schedule": schedule,
                    "skills": ["takyon:ceo"],
                    "enabled_toolsets": enabled_toolsets,
                    "enabled": True,
                    "state": "scheduled",
                },
            )
            return {"cron_job": updated["id"], "schedule": updated.get("schedule_display"), "updated": True}
        job = create_job(
            prompt=prompt,
            schedule=schedule,
            name=name,
            deliver="local",
            skills=["takyon:ceo"],
            enabled_toolsets=enabled_toolsets,
            repeat=None,
        )
        return {"cron_job": job["id"], "schedule": job.get("schedule_display"), "updated": False, "reason": reason}


def _store() -> TakyonStore:
    return TakyonStore()


def _business_scope(args: dict) -> str:
    return f"business:{_slugify(str(args.get('business') or args.get('business_slug') or ''))}"


def _commit_tool(args: dict, operation: dict[str, Any], *, scope: str | None = None) -> str:
    try:
        result = _store().commit(
            scope=scope or _business_scope(args),
            operations=[operation],
            idempotency_key=args.get("idempotency_key") or "",
            reason=args.get("reason") or "",
            actor=args.get("actor") or "agent",
        )
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc), success=False)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


_BUSINESS_PROP = {"type": "string", "description": "Business slug, e.g. latexflow"}
_IDEMPOTENCY_PROP = {"type": "string", "description": "Stable unique key for this exact durable action"}
_REASON_PROP = {"type": "string", "description": "Why this action is being taken"}
_ACTOR_PROP = {"type": "string", "description": "agent, operator, cron, or system"}
_REQUIRES_API_PROP = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Provider aliases required for this operation, e.g. openai, meta, x, stripe, vercel",
}
_REQUIRES_ENV_PROP = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Explicit environment variables required for this operation",
}


def handle_business_list_businesses(args: dict, **_: Any) -> str:
    try:
        return tool_result(_store().read(scope="global", query="list_businesses", limit=args.get("limit") or 50))
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_read_business(args: dict, **_: Any) -> str:
    try:
        return tool_result(
            _store().read(
                scope=_business_scope(args),
                query=args.get("query") or "summary",
                include=args.get("include") or ["ledger", "events", "jobs"],
                limit=args.get("limit") or 50,
            )
        )
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_read_file(args: dict, **_: Any) -> str:
    try:
        return tool_result(_store().read(scope=_business_scope(args), query="read_file", path=args.get("path"), limit=args.get("limit") or 50))
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_list_files(args: dict, **_: Any) -> str:
    try:
        return tool_result(_store().read(scope=_business_scope(args), query="list_files", path=args.get("path") or ".", limit=args.get("limit") or 50))
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_upsert_business(args: dict, **_: Any) -> str:
    operation = {
        "action": "business.upsert",
        "business": args.get("business"),
        "name": args.get("name") or args.get("business"),
        "goal": args.get("goal") or "",
        "budget": args.get("budget"),
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation, scope=f"business:{args.get('business')}")


def handle_business_create_workspace(args: dict, **_: Any) -> str:
    operation = {
        "action": "workspace.upsert",
        "business": args.get("business"),
        "path": args.get("path"),
        "kind": args.get("kind") or "workspace",
        "status": args.get("status") or "active",
        "budget": args.get("budget"),
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation)


def handle_business_write_file(args: dict, **_: Any) -> str:
    operation = {
        "action": "artifact.write",
        "business": args.get("business"),
        "path": args.get("path"),
        "content": args.get("content") or "",
        "mode": args.get("mode") or "replace",
        "requires_api": args.get("requires_api") or [],
        "requires_env": args.get("requires_env") or [],
    }
    return _commit_tool(args, operation)


def handle_business_patch_file(args: dict, **_: Any) -> str:
    operation = {
        "action": "artifact.patch",
        "business": args.get("business"),
        "path": args.get("path"),
        "old": args.get("old"),
        "new": args.get("new") or "",
    }
    return _commit_tool(args, operation)


def handle_business_record_memory(args: dict, **_: Any) -> str:
    operation = {
        "action": "memory.write",
        "business": args.get("business"),
        "path": args.get("path"),
        "content": args.get("content") or "",
        "mode": args.get("mode") or "replace",
    }
    return _commit_tool(args, operation)


def handle_business_allocate_budget(args: dict, **_: Any) -> str:
    operation = {
        "action": "ledger.allocate",
        "business": args.get("business"),
        "amount": args.get("amount"),
        "currency": args.get("currency") or "USD",
        "kind": args.get("kind") or "allocation",
        "status": args.get("status") or "allocated",
        "purpose": args.get("purpose") or "",
        "requires_api": args.get("requires_api") or [],
        "requires_env": args.get("requires_env") or [],
    }
    return _commit_tool(args, operation)


def handle_business_enqueue_job(args: dict, **_: Any) -> str:
    operation = {
        "action": "job.enqueue",
        "business": args.get("business"),
        "scope": args.get("scope") or _business_scope(args),
        "kind": args.get("kind"),
        "status": args.get("status") or "queued",
        "payload": args.get("payload") or {},
        "requires_api": args.get("requires_api") or [],
        "requires_env": args.get("requires_env") or [],
    }
    return _commit_tool(args, operation, scope=operation["scope"])


def handle_business_record_event(args: dict, **_: Any) -> str:
    operation = {
        "action": "event.record",
        "business": args.get("business"),
        "scope": args.get("scope") or _business_scope(args),
        "event_type": args.get("event_type") or "event",
        "payload": args.get("payload") or {},
    }
    return _commit_tool(args, operation, scope=operation["scope"])


def handle_business_record_agent(args: dict, **_: Any) -> str:
    operation = {
        "action": "agent.record",
        "business": args.get("business"),
        "scope": args.get("scope") or _business_scope(args),
        "parent_id": args.get("parent_id"),
        "status": args.get("status") or "recorded",
        "prompt": args.get("prompt") or "",
        "result": args.get("result") or {},
    }
    return _commit_tool(args, operation, scope=operation["scope"])


def handle_business_set_control(args: dict, **_: Any) -> str:
    operation = {
        "action": "control.set",
        "scope": args.get("scope"),
        "state": args.get("state"),
        "reason": args.get("control_reason") or args.get("reason") or "",
    }
    return _commit_tool(args, operation, scope=args.get("scope") or "global")


def handle_business_schedule_ceo_wakeup(args: dict, **_: Any) -> str:
    operation = {
        "action": "cron.ensure_ceo_wakeup",
        "business": args.get("business"),
        "schedule": args.get("schedule") or "every 6h",
    }
    return _commit_tool(args, operation)


def handle_business_gc(args: dict, **_: Any) -> str:
    operation = {
        "action": "maintenance.gc",
        "older_than_days": args.get("older_than_days") or 90,
        "max_delete": args.get("max_delete") or 1000,
        "confirm": bool(args.get("confirm")),
    }
    return _commit_tool(args, operation, scope=args.get("scope") or "global")


def handle_business_registry(args: dict, **_: Any) -> str:
    try:
        snapshot = business_registry_snapshot(
            kind=args.get("kind"),
            category=args.get("category"),
            priority_band=args.get("priority_band"),
        )
        return tool_result({"success": True, **snapshot})
    except Exception as exc:
        return tool_error(str(exc), success=False)


TAKYON_TOOL_DEFINITIONS = [
    {
        "name": "business_registry",
        "description": "Read the business tool registry and Takyon skill registry by category and priority band.",
        "handler": handle_business_registry,
        "schema": _schema(
            "business_registry",
            "Read the business tool and Takyon skill registry.",
            {
                "kind": {"type": "string", "description": "all, tools, or skills"},
                "category": {"type": "string", "description": "Optional category id"},
                "priority_band": {"type": "string", "description": "Optional priority band id"},
            },
            [],
        ),
    },
    {
        "name": "business_list_businesses",
        "description": "List businesses and global control states.",
        "handler": handle_business_list_businesses,
        "schema": _schema("business_list_businesses", "List businesses.", {"limit": {"type": "integer"}}, []),
    },
    {
        "name": "business_read_business",
        "description": "Read one business summary, brain index, workspaces, controls, ledger, jobs, and events.",
        "handler": handle_business_read_business,
        "schema": _schema(
            "business_read_business",
            "Read one business.",
            {"business": _BUSINESS_PROP, "query": {"type": "string"}, "include": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer"}},
            ["business"],
        ),
    },
    {
        "name": "business_read_file",
        "description": "Read a file inside a business scope.",
        "handler": handle_business_read_file,
        "schema": _schema("business_read_file", "Read a business-scoped file.", {"business": _BUSINESS_PROP, "path": {"type": "string"}}, ["business", "path"]),
    },
    {
        "name": "business_list_files",
        "description": "List files or directories inside a business scope.",
        "handler": handle_business_list_files,
        "schema": _schema("business_list_files", "List business-scoped files.", {"business": _BUSINESS_PROP, "path": {"type": "string"}, "limit": {"type": "integer"}}, ["business"]),
    },
    {
        "name": "business_upsert_business",
        "description": "Create or update a business, including goal and optional budget cap.",
        "handler": handle_business_upsert_business,
        "schema": _schema(
            "business_upsert_business",
            "Create or update a business.",
            {"business": _BUSINESS_PROP, "name": {"type": "string"}, "goal": {"type": "string"}, "budget": {"type": "object"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_create_workspace",
        "description": "Create or update an arbitrary business workspace such as a campaign, product, sales, or research folder.",
        "handler": handle_business_create_workspace,
        "schema": _schema(
            "business_create_workspace",
            "Create/update a business workspace.",
            {"business": _BUSINESS_PROP, "path": {"type": "string"}, "kind": {"type": "string"}, "status": {"type": "string"}, "budget": {"type": "object"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "path", "idempotency_key"],
        ),
    },
    {
        "name": "business_write_file",
        "description": "Write or append a file inside a business workspace.",
        "handler": handle_business_write_file,
        "schema": _schema(
            "business_write_file",
            "Write a business-scoped file.",
            {"business": _BUSINESS_PROP, "path": {"type": "string"}, "content": {"type": "string"}, "mode": {"type": "string"}, "requires_api": _REQUIRES_API_PROP, "requires_env": _REQUIRES_ENV_PROP, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "path", "content", "idempotency_key"],
        ),
    },
    {
        "name": "business_patch_file",
        "description": "Patch a file inside a business workspace by replacing one text fragment.",
        "handler": handle_business_patch_file,
        "schema": _schema("business_patch_file", "Patch a business-scoped file.", {"business": _BUSINESS_PROP, "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "path", "old", "idempotency_key"]),
    },
    {
        "name": "business_record_memory",
        "description": "Write flexible per-business memory under brain/ for strategy, pricing, product, distribution, learning, and CEO notes.",
        "handler": handle_business_record_memory,
        "schema": _schema("business_record_memory", "Write business brain memory.", {"business": _BUSINESS_PROP, "path": {"type": "string"}, "content": {"type": "string"}, "mode": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "path", "content", "idempotency_key"]),
    },
    {
        "name": "business_allocate_budget",
        "description": "Allocate or reserve budget under a business cap.",
        "handler": handle_business_allocate_budget,
        "schema": _schema("business_allocate_budget", "Allocate business budget.", {"business": _BUSINESS_PROP, "amount": {"type": "number"}, "currency": {"type": "string"}, "purpose": {"type": "string"}, "kind": {"type": "string"}, "status": {"type": "string"}, "requires_api": _REQUIRES_API_PROP, "requires_env": _REQUIRES_ENV_PROP, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "amount", "idempotency_key"]),
    },
    {
        "name": "business_enqueue_job",
        "description": "Enqueue deterministic work such as ad posting, publishing, vendor calls, builds, deploys, or runner-side tasks.",
        "handler": handle_business_enqueue_job,
        "schema": _schema("business_enqueue_job", "Enqueue deterministic business work.", {"business": _BUSINESS_PROP, "scope": {"type": "string"}, "kind": {"type": "string"}, "payload": {"type": "object"}, "status": {"type": "string"}, "requires_api": _REQUIRES_API_PROP, "requires_env": _REQUIRES_ENV_PROP, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "kind", "idempotency_key"]),
    },
    {
        "name": "business_record_event",
        "description": "Record an evidence, decision, observation, or receipt-like event.",
        "handler": handle_business_record_event,
        "schema": _schema("business_record_event", "Record a business event.", {"business": _BUSINESS_PROP, "scope": {"type": "string"}, "event_type": {"type": "string"}, "payload": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "event_type", "idempotency_key"]),
    },
    {
        "name": "business_record_agent",
        "description": "Record a CEO or delegated subagent run in the business audit trail.",
        "handler": handle_business_record_agent,
        "schema": _schema("business_record_agent", "Record a business agent run.", {"business": _BUSINESS_PROP, "scope": {"type": "string"}, "parent_id": {"type": "string"}, "status": {"type": "string"}, "prompt": {"type": "string"}, "result": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "idempotency_key"]),
    },
    {
        "name": "business_set_control",
        "description": "Set a pause/resume/kill control state at global, business, workspace, job, or agent scope.",
        "handler": handle_business_set_control,
        "schema": _schema("business_set_control", "Set Takyon control state.", {"scope": {"type": "string"}, "state": {"type": "string", "description": "active, paused, or killed"}, "control_reason": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["scope", "state", "idempotency_key"]),
    },
    {
        "name": "business_schedule_ceo_wakeup",
        "description": "Create or update the cron job that wakes the CEO for one business.",
        "handler": handle_business_schedule_ceo_wakeup,
        "schema": _schema("business_schedule_ceo_wakeup", "Schedule CEO cron wakeup.", {"business": _BUSINESS_PROP, "schedule": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "schedule", "idempotency_key"]),
    },
    {
        "name": "business_gc",
        "description": "Conservative cleanup for old ephemeral events, terminal jobs, and agent-run rows. Dry-run unless confirm=true.",
        "handler": handle_business_gc,
        "schema": _schema("business_gc", "Run conservative Takyon GC.", {"scope": {"type": "string"}, "older_than_days": {"type": "integer"}, "max_delete": {"type": "integer"}, "confirm": {"type": "boolean"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["idempotency_key"]),
    },
]
