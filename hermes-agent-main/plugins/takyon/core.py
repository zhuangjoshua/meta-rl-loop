"""Core storage and guardrails for the Takyon business plugin."""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
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
DEFAULT_CLAUDE_AGENT_MODEL = "claude-opus-4-7"
MAX_READ_CHARS = 64_000
MAX_WRITE_CHARS = 1_000_000
CURRENT_BUSINESS_SCHEMA_VERSION = 1
CURRENT_BUSINESS_CAPABILITY_VERSION = 1
BUSINESS_UPGRADE_RECEIPT = "receipts/upgrades/takyon-business-upgrade-v1.json"
NO_PRETEND_PRODUCT_CONTRACT = """Hermes no-pretend product contract:
- You are not allowed to invent backend behavior.
- Never fake auth, sessions, users, entitlements, checkout, subscriptions, outreach sends, deploys, provider calls, metrics, or business outcomes.
- Use canonical Hermes/Takyon runtime tools or endpoints for auth, billing, entitlements, usage, outreach, and receipts.
- If no browser endpoint exists for auth, billing, entitlements, usage, or outreach, build the screen as unavailable/blocking, not fake.
- If a runtime endpoint or provider path is unavailable in this workspace, show a visible DEBUG/blocked state that says the feature is not wired yet.
- Do not use localStorage, demo query parameters, hardcoded test users, or fake checkout URLs to simulate business reality in product source.
"""

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_CONTROL_STATES = {"active", "paused", "killed"}
_BUSINESS_MODES = {"live", "test"}
_BUSINESS_WORK_FOCUS_MODES = {"all", "marketing", "product"}
_DEFAULT_COMPANY_BASE_DOMAIN = "fourmanifold.com"
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

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
    "postmark": ("POSTMARK_SERVER_TOKEN", "POSTMARK_FROM_EMAIL"),
    "stripe": ("STRIPE_SECRET_KEY",),
    "tavily": ("TAVILY_API_KEY",),
    "vercel": ("VERCEL_TOKEN",),
    "x": ("X_API_KEY", "TWITTER_API_KEY", "X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN"),
    "xai": ("XAI_API_KEY",),
}

_JOB_API_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "ai_gateway_setup": ("llm",),
    "ceo_wakeup": ("llm",),
    "community_research": ("tavily",),
    "meta_seedance": ("openai",),
    "product_backend": ("vercel",),
    "product.deploy": ("vercel",),
    "product_ui": ("vercel",),
    "stripe_setup": ("stripe",),
    "website_build_deploy": ("vercel",),
    "x_social": ("x",),
}
_LEGACY_FIXED_STAGE_JOB_KINDS = {"foundation"}


class TakyonError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future(minutes: int = 0, days: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes, days=days)).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _microusd_to_cents(value: int | float | None) -> int:
    return int(round(float(value or 0) / 10_000))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _markdown_scalar(value: Any) -> str:
    if value is None or value == "":
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        scalars = [item for item in value if not isinstance(item, (dict, list, tuple))]
        return ", ".join(_markdown_scalar(item) for item in scalars) if scalars else f"{len(value)} entries"
    if isinstance(value, dict):
        keys = sorted(str(key) for key in value.keys())
        return ", ".join(keys) if keys else "not set"
    return str(value).replace("\n", " ").strip() or "not set"


def _markdown_kv_lines(mapping: Any, *, empty: str = "not set") -> list[str]:
    if not isinstance(mapping, dict) or not mapping:
        return [f"- {empty}"]
    lines: list[str] = []
    for key in sorted(mapping):
        lines.append(f"- {key}: {_markdown_scalar(mapping.get(key))}")
    return lines


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


def _normalize_work_focus(value: Any, *, default: str | None = "all") -> str | None:
    raw = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not raw:
        return default
    aliases = {
        "any": "all",
        "none": "all",
        "off": "all",
        "clear": "all",
        "default": "all",
        "growth": "marketing",
        "market": "marketing",
        "marketing-only": "marketing",
        "marketingonly": "marketing",
        "distribution": "marketing",
        "demand": "marketing",
        "sales": "marketing",
        "product-only": "product",
        "productonly": "product",
        "build": "product",
        "app": "product",
    }
    focus = aliases.get(raw, raw)
    if focus not in _BUSINESS_WORK_FOCUS_MODES:
        raise TakyonError(f"business work focus must be one of {sorted(_BUSINESS_WORK_FOCUS_MODES)}")
    return focus


def _file_slug(value: str, fallback: str = "item") -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-_.")
    return (raw or fallback)[:96]


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


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
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


def _append_jsonl(path: Path, value: Any) -> None:
    line = _json_dumps(value) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _read_text_limited(path: Path, limit: int = MAX_READ_CHARS) -> str:
    data = path.read_text(encoding="utf-8", errors="replace")
    if len(data) > limit:
        return data[:limit] + "\n\n[truncated]"
    return data


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_env_files() -> list[Path]:
    paths: list[Path] = []
    for key in ("TAKYON_ENV_FILE",):
        value = os.getenv(key)
        if value:
            paths.append(Path(value).expanduser())

    root = _repo_root()
    search_roots = [root.parent, root]
    takyon_home = os.getenv("TAKYON_HOME")
    if takyon_home:
        search_roots.append(Path(takyon_home).expanduser())
    for base in search_roots:
        paths.extend([base / ".env.local", base / ".env", base / "secrets" / ".env"])
    return paths


_loaded_env_paths: set[Path] = set()


def load_takyon_env() -> list[str]:
    """Load explicit Takyon env files without overriding process env."""
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


def _runtime_path_prefixes() -> list[Path]:
    takyon_home = Path(os.getenv("TAKYON_HOME") or get_takyon_home()).expanduser()
    return [
        takyon_home / "node" / "bin",
        _repo_root() / "node_modules" / ".bin",
        Path(sys.executable).resolve().parent,
    ]


def _runtime_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    prefixes = [str(path) for path in _runtime_path_prefixes() if path.exists()]
    path = os.pathsep.join([*prefixes, os.getenv("PATH", "")])
    return {**os.environ, **(extra or {}), "PATH": path}


def _resolve_runtime_executable(name: str) -> str | None:
    if name == "python":
        return sys.executable
    prefixes = [str(path) for path in _runtime_path_prefixes() if path.exists()]
    search_path = os.pathsep.join([*prefixes, os.getenv("PATH", "")])
    return shutil.which(name, path=search_path)


def _command_version(command: list[str], *, timeout_seconds: int = 10) -> str | None:
    try:
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=_runtime_env(),
        )
    except Exception:
        return None
    output = (proc.stdout or proc.stderr or "").strip().splitlines()
    return output[0] if output else None


def _runtime_capabilities(names: Iterable[str] | None = None) -> dict[str, Any]:
    requested = list(names or ("node", "npm", "npx", "corepack", "pnpm", "yarn", "bun", "python", "pip", "uv", "git", "rg"))
    capabilities: dict[str, Any] = {}
    for name in requested:
        clean = str(name).strip()
        if not clean:
            continue
        path = _resolve_runtime_executable(clean)
        version: str | None = None
        if clean == "pip":
            pip_path = path
            version = _command_version([sys.executable, "-m", "pip", "--version"])
            path = pip_path or (f"{sys.executable} -m pip" if version else None)
        elif clean == "python":
            version = _command_version([sys.executable, "--version"])
        elif path:
            version = _command_version([path, "--version"])
        capabilities[clean] = {
            "available": bool(path),
            "path": path,
            "version": version,
        }
    return capabilities


def _allow_runtime_installs() -> bool:
    path = get_takyon_home() / "config.yaml"
    if not path.exists():
        return True
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        security = data.get("security") if isinstance(data.get("security"), dict) else {}
        if "allow_runtime_installs" in security:
            return _boolish(security.get("allow_runtime_installs"), default=True)
        if "allow_lazy_installs" in security:
            return _boolish(security.get("allow_lazy_installs"), default=True)
    except Exception:
        return True
    return True


def _ensure_javascript_runtime(*, package_manager: bool = False) -> dict[str, Any]:
    names = ("node", "npm", "npx", "corepack", "pnpm", "yarn", "bun")
    before = _runtime_capabilities(names)
    has_node = bool(before.get("node", {}).get("available"))
    has_package_manager = any(bool(before.get(name, {}).get("available")) for name in ("npm", "pnpm", "yarn", "bun"))
    if has_node and (has_package_manager or not package_manager):
        return {"success": True, "installed": False, "capabilities": before}
    if not _allow_runtime_installs():
        return {
            "success": False,
            "installed": False,
            "capabilities": before,
            "error": "runtime installs are disabled by config",
        }
    helper = _repo_root() / "scripts" / "lib" / "node-bootstrap.sh"
    if not helper.exists():
        return {
            "success": False,
            "installed": False,
            "capabilities": before,
            "error": f"runtime installer missing: {helper}",
        }
    takyon_home = Path(os.getenv("TAKYON_HOME") or get_takyon_home()).expanduser()
    need_package_manager = "1" if package_manager else "0"
    command = (
        f"source {shlex.quote(str(helper))}; "
        f"if [ {need_package_manager} = 1 ] && ! command -v npm >/dev/null 2>&1 "
        f"&& [ ! -x {shlex.quote(str(takyon_home / 'node' / 'bin' / 'npm'))} ]; "
        "then _nb_install_bundled_node; else ensure_node; fi"
    )
    started = _now()
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            text=True,
            capture_output=True,
            timeout=240,
            env=_runtime_env({"TAKYON_HOME": str(takyon_home)}),
        )
        after = _runtime_capabilities(names)
        return {
            "success": proc.returncode == 0,
            "installed": proc.returncode == 0 and before != after,
            "started_at": started,
            "completed_at": _now(),
            "returncode": proc.returncode,
            "stdout": _truncate_text(proc.stdout or "", 4000),
            "stderr": _truncate_text(proc.stderr or "", 4000),
            "capabilities": after,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "installed": False,
            "started_at": started,
            "completed_at": _now(),
            "stdout": _truncate_text(exc.stdout or "", 4000),
            "stderr": _truncate_text(exc.stderr or "", 4000),
            "error": "runtime install timed out",
            "capabilities": _runtime_capabilities(names),
        }


def _model_from_config(*keys: str) -> str:
    """Read a model setting from config.yaml, the shared model source of truth."""
    path = get_takyon_home() / "config.yaml"
    try:
        import yaml

        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        model_data = data.get("model") or {}
        if isinstance(model_data, dict):
            for key in keys:
                value = str(model_data.get(key) or "").strip()
                if value:
                    return value
            return str(model_data.get("default") or model_data.get("model") or "").strip()
    except Exception:
        return ""
    return ""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _boolish(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "confirm", "confirmed"}:
        return True
    if text in {"0", "false", "no", "off", "dry-run", "preview"}:
        return False
    return default


def _missing_env_for_requirement(requirement: str) -> list[str]:
    key = str(requirement or "").strip()
    if not key:
        return []
    alias = _API_ENV_ALIASES.get(key.lower())
    if alias:
        return [] if any(os.getenv(name) for name in alias) else ["/".join(alias)]
    return [] if os.getenv(key) else [key]


def _credential_requirements(op: dict[str, Any]) -> list[str]:
    required_api = list(_as_list(op.get("requires_api")))
    action = str(op.get("action") or "")
    if action == "job.enqueue":
        required_api.extend(_JOB_API_REQUIREMENTS.get(str(op.get("kind") or ""), ()))
    if action != "outreach.local_publish" and str(op.get("provider") or "").strip():
        required_api.append(str(op.get("provider")))
    return [str(req) for req in required_api if str(req).strip()]


def _allow_missing_credentials_in_test_mode(op: dict[str, Any]) -> bool:
    return str(op.get("action") or "") == "job.enqueue"


def _require_api_access(op: dict[str, Any], *, business_mode: str = "live") -> dict[str, Any]:
    load_takyon_env()
    missing: list[str] = []
    required_api = _credential_requirements(op)
    for req in required_api:
        missing.extend(_missing_env_for_requirement(str(req)))
    for req in _as_list(op.get("requires_env")):
        missing.extend(_missing_env_for_requirement(str(req)))
    missing_unique = sorted(set(missing))
    if missing_unique and business_mode == "test" and _allow_missing_credentials_in_test_mode(op):
        return {
            "business_mode": "test",
            "missing_credentials_suppressed": missing_unique,
            "external_side_effects": "suppressed",
            "note": "Test mode recorded this work locally without requiring outbound provider credentials.",
        }
    if missing_unique:
        action = op.get("action") or "<unknown>"
        raise TakyonError(
            f"{action} requires missing API/env credential(s): {', '.join(missing_unique)}"
        )
    return {"business_mode": business_mode, "missing_credentials_suppressed": []}


_PRODUCT_SOURCE_EXTENSIONS = {".html", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}
_PRODUCT_PROJECT_FILENAMES = {
    "package.json",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vite.config.js",
    "vite.config.ts",
    "tsconfig.json",
}
_PRODUCT_SOURCE_SKIP_DIRS = {
    ".git",
    ".next",
    "__fixtures__",
    "build",
    "dist",
    "docs",
    "fixtures",
    "node_modules",
    "references",
}
_PRETEND_PRODUCT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "browser-local auth/session/account state",
        re.compile(
            r"localStorage(?:\.(?:getItem|setItem|removeItem)|\[['\"](?:getItem|setItem|removeItem)['\"]\])"
            r"\(\s*['\"][^'\"]*"
            r"(?:session|auth|account|user|entitlement|subscription|checkout)[^'\"]*['\"]",
            re.IGNORECASE,
        ),
    ),
    (
        "demo login or demo session",
        re.compile(
            r"(?:[?&]demo=|(?:params|searchParams)\.(?:get|has|set)\(\s*['\"]demo['\"]|"
            r"URLSearchParams\([^)]*\)\.(?:get|has|set)\(\s*['\"]demo['\"]|demo@)",
            re.IGNORECASE,
        ),
    ),
    (
        "fake payment or checkout",
        re.compile(
            r"(?:fake\s+(?:checkout|payment|billing)|local://takyon/checkout|"
            r"href\s*=\s*['\"][^'\"]*(?:fake|demo|test)[^'\"]*(?:checkout|billing|stripe)[^'\"]*['\"]|"
            r"(?:checkout|billing|stripe)[^'\"]*(?:fake|demo|test)|stripe_called\s*[:=]\s*false)",
            re.IGNORECASE,
        ),
    ),
    ("hardcoded test account", re.compile(r"\btest[\w.-]*@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE)),
)
_RUNTIME_BACKED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfetch\s*\("),
    re.compile(r"\bXMLHttpRequest\b"),
    re.compile(r"/api/takyon/apps/"),
    re.compile(r"\bHermes\b.*\bruntime\b", re.IGNORECASE),
)
_ACCOUNT_LOADING_TOKENS = {
    "account",
    "account-email",
    "billing",
    "checkout",
    "customer",
    "entitlement",
    "plan",
    "session-email",
    "subscription",
}


def _product_source_is_skipped(path: Path) -> bool:
    return any(part in _PRODUCT_SOURCE_SKIP_DIRS for part in path.parts)


def _source_has_runtime_backing(text: str) -> bool:
    return any(pattern.search(text) for pattern in _RUNTIME_BACKED_PATTERNS)


def _scan_for_pretend_product_state(root: Path, *, limit: int = 25) -> list[dict[str, Any]]:
    """Detect product-source code that pretends real auth/billing/integration state."""
    findings: list[dict[str, Any]] = []
    if not root.exists():
        return findings
    for path in sorted(root.rglob("*")):
        if len(findings) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in _PRODUCT_SOURCE_EXTENSIONS:
            continue
        if _product_source_is_skipped(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        runtime_backed = _source_has_runtime_backing(text)
        for number, line in enumerate(lines, start=1):
            for label, pattern in _PRETEND_PRODUCT_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        {
                            "path": str(path.relative_to(root)),
                            "line": number,
                            "issue": label,
                            "snippet": line.strip()[:240],
                        }
                    )
                    break
            if "Loading..." in line and not runtime_backed:
                window = "\n".join(lines[max(0, number - 5): min(len(lines), number + 4)]).lower()
                if any(token in window for token in _ACCOUNT_LOADING_TOKENS):
                    findings.append(
                        {
                            "path": str(path.relative_to(root)),
                            "line": number,
                            "issue": "unbacked account/billing loading widget",
                            "snippet": line.strip()[:240],
                        }
                    )
            if len(findings) >= limit:
                break
    return findings


def _product_source_files(root: Path, *, limit: int = 200) -> list[str]:
    files: list[str] = []
    if not root.exists() or not root.is_dir():
        return files
    for path in sorted(root.rglob("*")):
        if len(files) >= limit:
            break
        if not path.is_file() or _product_source_is_skipped(path):
            continue
        if path.suffix.lower() in _PRODUCT_SOURCE_EXTENSIONS or path.name in _PRODUCT_PROJECT_FILENAMES:
            files.append(path.relative_to(root).as_posix())
    return files


def _detect_nested_workspace_prefix(root: Path, source_path: str) -> str | None:
    rel = _safe_relpath(source_path or ".", field="source_path")
    if rel.as_posix() in {".", ""}:
        return None
    nested = root / rel
    if nested.exists() and _product_source_files(nested, limit=1):
        return rel.as_posix()
    return None


def _run_verification_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = _now()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=env or _runtime_env(),
        )
        status = "passed" if proc.returncode == 0 else "failed"
        return {
            "command": command,
            "status": status,
            "returncode": proc.returncode,
            "started_at": started,
            "completed_at": _now(),
            "stdout": _truncate_text(proc.stdout or "", 12_000),
            "stderr": _truncate_text(proc.stderr or "", 12_000),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "blocked",
            "returncode": None,
            "started_at": started,
            "completed_at": _now(),
            "stdout": _truncate_text(exc.stdout or "", 12_000),
            "stderr": _truncate_text(exc.stderr or "", 12_000),
            "error": f"timed out after {timeout_seconds}s",
        }


def _node_modules_present(root: Path) -> bool:
    return (root / "node_modules").exists() and any((root / "node_modules").iterdir())


def _javascript_package_manager_name(root: Path, package_data: dict[str, Any]) -> str:
    package_manager = str(package_data.get("packageManager") or "").strip().lower()
    if package_manager:
        return package_manager.split("@", 1)[0]
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    return "npm"


def _javascript_package_manager_command(name: str) -> dict[str, Any]:
    manager = str(name or "npm").strip().lower()
    path = _resolve_runtime_executable(manager)
    if path:
        return {"available": True, "name": manager, "command": [path], "source": "path"}
    if manager in {"pnpm", "yarn"}:
        corepack = _resolve_runtime_executable("corepack")
        if corepack:
            return {"available": True, "name": manager, "command": [corepack, manager], "source": "corepack"}
    return {"available": False, "name": manager, "command": [], "source": "missing"}


def _javascript_install_command(manager: dict[str, Any]) -> list[str]:
    base = list(manager.get("command") or [])
    name = str(manager.get("name") or "npm")
    if name == "npm":
        return [*base, "install", "--ignore-scripts"]
    if name == "pnpm":
        return [*base, "install", "--ignore-scripts"]
    if name == "yarn":
        return [*base, "install", "--ignore-scripts"]
    if name == "bun":
        return [*base, "install", "--ignore-scripts"]
    return [*base, "install"]


def _javascript_run_script_command(manager: dict[str, Any], script: str, *, root: Path) -> list[str] | None:
    base = list(manager.get("command") or [])
    name = str(manager.get("name") or "npm")
    if base:
        if name == "yarn":
            return [*base, script]
        return [*base, "run", script]
    node = _resolve_runtime_executable("node")
    if node and _node_modules_present(root):
        return [node, "--run", script]
    return None


def _static_surface_can_skip_package_manager(root: Path, scripts: dict[str, Any]) -> bool:
    if not (root / "index.html").exists():
        return False
    if any((root / name).exists() for name in ("next.config.js", "next.config.mjs", "next.config.ts", "vite.config.js", "vite.config.ts", "tsconfig.json")):
        return False
    static_suffixes = {".html", ".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".txt", ".md"}
    for path in root.rglob("*"):
        if not path.is_file() or _product_source_is_skipped(path):
            continue
        if path.suffix.lower() not in static_suffixes:
            return False
    build = str(scripts.get("build") or "").strip()
    if not build:
        return True
    return bool(re.match(r"^(?::|true|echo\b|printf\b|exit\s+0\b)", build))


def _verify_product_surface_path(
    business_root: Path,
    source_path: str,
    *,
    install: bool = True,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    source_rel = _safe_relpath(source_path or "product/site", field="source_path").as_posix()
    root = (business_root / source_rel).resolve()
    result: dict[str, Any] = {
        "source_path": source_rel,
        "absolute_path": str(root),
        "generated_at": _now(),
        "status": "unverified",
        "checks": [],
        "warnings": [],
        "capabilities": _runtime_capabilities(("node", "npm", "npx", "corepack", "pnpm", "yarn", "bun", "python", "pip", "uv")),
    }
    if business_root.resolve() not in (root, *root.parents):
        result.update({"status": "failed", "error": "source path escaped business root"})
        return result
    if not root.exists() or not root.is_dir():
        result.update({"status": "missing", "error": "source path does not exist"})
        return result

    files = _product_source_files(root)
    result["source_file_count"] = len(files)
    result["sample_files"] = files[:25]
    nested = _detect_nested_workspace_prefix(root, source_rel)
    if nested:
        result.update({
            "status": "failed",
            "error": f"source appears nested under duplicate workspace prefix: {nested}",
            "nested_source_path": f"{source_rel}/{nested}",
        })
        return result
    if not files:
        result.update({"status": "missing", "error": "source path contains no recognized product source files"})
        return result

    package_json = root / "package.json"
    if not package_json.exists():
        result.update({"status": "passed", "kind": "static_source_present"})
        return result

    try:
        package_data = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception as exc:
        result.update({"status": "failed", "error": f"package.json is not valid JSON: {exc}"})
        return result
    scripts = package_data.get("scripts") if isinstance(package_data.get("scripts"), dict) else {}
    dependencies = package_data.get("dependencies") if isinstance(package_data.get("dependencies"), dict) else {}
    dev_dependencies = package_data.get("devDependencies") if isinstance(package_data.get("devDependencies"), dict) else {}
    deps = {**dependencies, **dev_dependencies}
    if "next" in deps:
        next_value = str(deps.get("next") or "")
        if re.search(r"\b14\.2\.5\b", next_value):
            result["warnings"].append("next@14.2.5 is known deprecated/vulnerable; update before publication")
    if _static_surface_can_skip_package_manager(root, scripts):
        result["warnings"].append("package.json is present, but this surface is static and has no package-managed build requirement")
        result.update({"status": "passed", "kind": "static_source_present"})
        return result
    package_manager_name = _javascript_package_manager_name(root, package_data)
    package_manager = _javascript_package_manager_command(package_manager_name)
    result["package_manager"] = {key: package_manager.get(key) for key in ("name", "available", "source")}
    if install and not package_manager.get("available"):
        ensure = _ensure_javascript_runtime(package_manager=True)
        result["checks"].append({
            "command": ["takyon", "ensure-runtime", "javascript-package-manager"],
            "status": "passed" if ensure.get("success") else "blocked",
            "result": ensure,
        })
        package_manager = _javascript_package_manager_command(package_manager_name)
        result["package_manager"] = {key: package_manager.get(key) for key in ("name", "available", "source")}
        result["capabilities"] = _runtime_capabilities(("node", "npm", "npx", "corepack", "pnpm", "yarn", "bun", "python", "pip", "uv"))
    if not package_manager.get("available") and not _node_modules_present(root):
        result.update({
            "status": "blocked",
            "error": "javascript package manager is unavailable for dependency installation",
            "missing_capabilities": [package_manager_name],
            "remediation": "Install or enable the declared package manager, or allow Takyon runtime installs so it can provision a local JavaScript runtime/package manager.",
        })
        return result
    if install:
        if package_manager.get("available"):
            install_check = _run_verification_command(_javascript_install_command(package_manager), cwd=root, timeout_seconds=timeout_seconds)
            result["checks"].append(install_check)
            if install_check["status"] != "passed":
                result.update({"status": "failed", "error": "dependency install failed"})
                return result
        else:
            result["warnings"].append("dependency install skipped because no package manager is available; using existing node_modules")
    if "build" not in scripts:
        result.update({"status": "unverified", "error": "package.json has no build script"})
        return result
    build_command = _javascript_run_script_command(package_manager, "build", root=root)
    if not build_command:
        result.update({
            "status": "blocked",
            "error": "no available runtime command for package build script",
            "missing_capabilities": [package_manager_name, "node"],
        })
        return result
    build_check = _run_verification_command(build_command, cwd=root, timeout_seconds=timeout_seconds)
    result["checks"].append(build_check)
    if build_check["status"] != "passed":
        result.update({"status": "failed", "error": "product build failed"})
        return result
    if "typecheck" in scripts:
        typecheck_command = _javascript_run_script_command(package_manager, "typecheck", root=root)
        if not typecheck_command:
            result.update({
                "status": "blocked",
                "error": "no available runtime command for package typecheck script",
                "missing_capabilities": [package_manager_name, "node"],
            })
            return result
        typecheck = _run_verification_command(typecheck_command, cwd=root, timeout_seconds=timeout_seconds)
        result["checks"].append(typecheck)
        if typecheck["status"] != "passed":
            result.update({"status": "failed", "error": "product typecheck failed"})
            return result
    result.update({"status": "passed", "kind": "node_build"})
    return result


def _normalize_billing_interval(value: Any) -> str:
    raw = str(value or "month").strip().lower().replace("-", "_")
    aliases = {
        "monthly": "month",
        "mo": "month",
        "per_month": "month",
        "annual": "year",
        "annually": "year",
        "yearly": "year",
        "yr": "year",
        "per_year": "year",
        "once": "one_time",
        "one-time": "one_time",
        "single": "one_time",
    }
    return aliases.get(raw, raw)


def _plan_validation_warnings(plan_key: str, tier: str, quota: int, allow_overage: bool, metadata: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    normalized_key = _file_slug(plan_key, plan_key)
    normalized_tier = _file_slug(tier, tier)
    if normalized_tier and normalized_key and normalized_tier not in normalized_key and normalized_key not in {"free"}:
        warnings.append("plan_key and entitlement tier differ; this can be valid for billing variants but should be intentional")
    def contains_unlimited(value: Any) -> bool:
        if isinstance(value, str):
            return "unlimited" in value.lower()
        if isinstance(value, (int, float)):
            return value < 0
        if isinstance(value, dict):
            return any(contains_unlimited(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_unlimited(item) for item in value)
        return False
    if contains_unlimited(metadata) and quota > 0 and not allow_overage:
        warnings.append("metadata suggests an unlimited entitlement but included_action_quota is finite and overage is disabled")
    return warnings


_BRAIN_COMPLETION_MARKERS = (
    re.compile(r"\b(?:complete|completed|done|built|published|deployed|wired)\b", re.IGNORECASE),
    re.compile(r"✅"),
)
_BRAIN_COMPLETION_EVIDENCE_TERMS = (
    ("source files", ("source file", "source files", "source_path", "source path")),
    ("runtime/tool endpoint used", ("runtime/tool endpoint", "runtime endpoint", "tool endpoint", "endpoint used", "tool used", "runtime used")),
    ("receipt or test record", ("receipt", "test record", "test_record", "job id", "agent record")),
    ("remaining blocker", ("remaining blocker", "blocker", "blocked", "not wired")),
)


def _validate_brain_index_completion_gate(rel: str, content: str) -> None:
    if rel != "brain/index.md":
        return
    if not any(pattern.search(content) for pattern in _BRAIN_COMPLETION_MARKERS):
        return
    lowered = content.lower()
    missing = [
        label
        for label, needles in _BRAIN_COMPLETION_EVIDENCE_TERMS
        if not any(needle in lowered for needle in needles)
    ]
    if missing:
        raise TakyonError(
            "brain/index.md cannot claim complete/built/done work without a feature evidence ledger. "
            "For each feature list source files, runtime/tool endpoint used, receipt or test record, "
            f"and remaining blocker. Missing: {', '.join(missing)}"
        )


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _truncate_text(value: str, limit: int = 20_000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _random_token() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def _normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise TakyonError("valid email is required")
    return email


def _normalize_domain_name(value: str, *, field: str = "domain") -> str:
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        raise TakyonError(f"{field} is required")
    parsed = urllib.parse.urlparse(raw if "://" in raw else f"//{raw}")
    domain = (parsed.netloc or parsed.path).split("/", 1)[0].strip().rstrip(".")
    if ":" in domain:
        domain = domain.split(":", 1)[0]
    if not _DOMAIN_RE.match(domain):
        raise TakyonError(f"{field} is not a valid DNS name: {value!r}")
    return domain


def _company_base_domain(value: Any = None) -> str:
    load_takyon_env()
    configured = (
        str(value or "").strip()
        or os.getenv("PUBLIC_COMPANY_BASE_DOMAIN", "").strip()
        or os.getenv("TAKYON_COMPANY_BASE_DOMAIN", "").strip()
        or _DEFAULT_COMPANY_BASE_DOMAIN
    )
    return _normalize_domain_name(configured, field="base_domain")


def _business_domain_candidates(slug: str, *, base_domain: Any = None, explicit: Any = None) -> list[str]:
    business = _slugify(slug)
    base = _company_base_domain(base_domain)
    candidates = [f"{business}.{base}"]
    for item in _as_list(explicit):
        raw = str(item or "").strip()
        if not raw:
            continue
        domain = _normalize_domain_name(raw if "." in raw else f"{raw}.{base}", field="subdomain")
        suffix = f".{base}"
        if domain == base or not domain.endswith(suffix):
            raise TakyonError(f"business subdomain must be under {base}: {domain}")
        if domain != f"{business}.{base}" and not domain.endswith(f".{business}.{base}"):
            raise TakyonError(
                f"refusing to delete {domain}; explicit subdomains must belong to business:{business}"
            )
        if domain not in candidates:
            candidates.append(domain)
    return candidates


def _status_rank(status: str) -> int:
    return {"active": 0, "trialing": 0, "past_due": 1, "cancelled": 2, "canceled": 2, "revoked": 3}.get(status, 9)


def _tier_rank(tier: str) -> int:
    return {"owner": 0, "paid": 1, "pro": 1, "free": 2}.get(tier, 5)


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


def _path_starts_with(value: Any, prefixes: tuple[str, ...]) -> bool:
    raw = str(value or "").strip().lstrip("/")
    if not raw:
        return False
    return any(raw == prefix.rstrip("/") or raw.startswith(prefix) for prefix in prefixes)


def _job_kind_matches(kind: Any, needles: tuple[str, ...]) -> bool:
    normalized = str(kind or "").strip().lower().replace("_", "-")
    return any(needle in normalized for needle in needles)


def _enforce_business_work_focus(op: dict[str, Any], focus: str) -> None:
    if focus == "all":
        return
    action = str(op.get("action") or "")
    always_allowed = {
        "agent.record",
        "business.delete",
        "business.focus.set",
        "business.mode.set",
        "business.upsert",
        "control.set",
        "cron.ensure_ceo_wakeup",
        "event.record",
        "ledger.allocate",
        "maintenance.gc",
        "memory.write",
    }
    if action in always_allowed:
        return

    product_actions = {
        "app.budget.set",
        "app.customer.upsert",
        "app.entitlement.upsert",
        "app.plan.upsert",
        "app.surface.upsert",
        "app.usage.record",
    }
    product_paths = ("app/", "product/", "website/")
    marketing_paths = ("campaigns/", "outreach/", "research/", "sales/")

    if focus == "marketing":
        if action in product_actions:
            raise TakyonError(f"business work focus is marketing-only; {action} is product work")
        if action in {"artifact.write", "artifact.patch", "workspace.upsert"}:
            candidate = op.get("path") or op.get("workspace") or op.get("source_path")
            if _path_starts_with(candidate, product_paths):
                raise TakyonError(f"business work focus is marketing-only; {candidate} is product work")
        if action == "job.enqueue" and _job_kind_matches(op.get("kind"), ("product", "website", "stripe", "checkout", "app")):
            raise TakyonError(f"business work focus is marketing-only; job kind {op.get('kind')} is product work")
        return

    if focus == "product":
        if action == "outreach.local_publish":
            raise TakyonError("business work focus is product-only; outreach publication is marketing work")
        if action in {"artifact.write", "artifact.patch", "workspace.upsert"}:
            candidate = op.get("path") or op.get("workspace") or op.get("source_path")
            if _path_starts_with(candidate, marketing_paths):
                raise TakyonError(f"business work focus is product-only; {candidate} is marketing work")
        if action == "job.enqueue" and _job_kind_matches(op.get("kind"), ("ad", "campaign", "community", "distribution", "outreach", "post", "social", "x-social")):
            raise TakyonError(f"business work focus is product-only; job kind {op.get('kind')} is marketing work")


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
              mode TEXT NOT NULL DEFAULT 'live',
              work_focus TEXT NOT NULL DEFAULT 'all',
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
            CREATE TABLE IF NOT EXISTS conversation_threads (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              source TEXT NOT NULL,
              external_id TEXT NOT NULL,
              title TEXT NOT NULL,
              url TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (business_slug, source, external_id),
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS conversation_messages (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              source TEXT NOT NULL,
              external_id TEXT NOT NULL,
              direction TEXT NOT NULL,
              author_label TEXT NOT NULL DEFAULT '',
              body TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'needs_response',
              received_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (business_slug, source, external_id),
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE,
              FOREIGN KEY (thread_id) REFERENCES conversation_threads(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS idempotency_keys (
              key TEXT PRIMARY KEY,
              operation_hash TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_budgets (
              business_slug TEXT PRIMARY KEY,
              status TEXT NOT NULL DEFAULT 'active',
              hard_limit_microusd INTEGER NOT NULL DEFAULT 5000000,
              current_period_start TEXT NOT NULL,
              current_period_end TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_plan_policies (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              plan_key TEXT NOT NULL,
              tier TEXT NOT NULL DEFAULT 'free',
              price_cents INTEGER NOT NULL DEFAULT 0,
              currency TEXT NOT NULL DEFAULT 'usd',
              billing_interval TEXT NOT NULL DEFAULT 'month',
              included_ai_budget_microusd INTEGER NOT NULL DEFAULT 0,
              included_action_quota INTEGER NOT NULL DEFAULT 25,
              allow_overage INTEGER NOT NULL DEFAULT 0,
              stripe_product_id TEXT,
              stripe_price_id TEXT,
              stripe_payment_link_id TEXT,
              stripe_payment_link_url TEXT,
              source TEXT NOT NULL DEFAULT 'takyon',
              notes TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (business_slug, plan_key),
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_surface_contracts (
              business_slug TEXT PRIMARY KEY,
              status TEXT NOT NULL DEFAULT 'draft',
              design_brief_path TEXT NOT NULL DEFAULT 'product/design-brief.md',
              source_path TEXT,
              runtime_api_base TEXT,
              routes_json TEXT,
              theme_json TEXT,
              constraints_json TEXT,
              notes TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_users (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              email TEXT NOT NULL,
              name TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              tier TEXT NOT NULL DEFAULT 'free',
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (business_slug, email),
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_magic_links (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              app_user_id TEXT,
              email TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              purpose TEXT NOT NULL DEFAULT 'login',
              expires_at TEXT NOT NULL,
              used_at TEXT,
              provider_message_id TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE,
              FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_sessions (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              app_user_id TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              expires_at TEXT NOT NULL,
              revoked_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE,
              FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_entitlements (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              app_user_id TEXT NOT NULL,
              tier TEXT NOT NULL DEFAULT 'free',
              status TEXT NOT NULL DEFAULT 'active',
              source TEXT NOT NULL DEFAULT 'manual',
              stripe_customer_id TEXT,
              stripe_subscription_id TEXT,
              stripe_checkout_session_id TEXT,
              plan_key TEXT,
              current_period_end TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE,
              FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_checkout_intents (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              app_user_id TEXT,
              plan_key TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'created',
              client_reference_id TEXT NOT NULL UNIQUE,
              stripe_checkout_session_id TEXT,
              checkout_url TEXT,
              customer_email TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE,
              FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS app_checkout_sessions (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              checkout_intent_id TEXT,
              plan_key TEXT,
              stripe_checkout_session_id TEXT NOT NULL UNIQUE,
              stripe_customer_id TEXT,
              stripe_payment_intent_id TEXT,
              stripe_subscription_id TEXT,
              stripe_invoice_id TEXT,
              mode TEXT,
              payment_status TEXT,
              status TEXT,
              currency TEXT,
              amount_subtotal_cents INTEGER,
              amount_total_cents INTEGER,
              client_reference_id TEXT,
              customer_email TEXT,
              raw_event_id TEXT,
              metadata_json TEXT,
              completed_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE,
              FOREIGN KEY (checkout_intent_id) REFERENCES app_checkout_intents(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS app_revenue_events (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              provider_event_id TEXT,
              stripe_object_type TEXT,
              stripe_object_id TEXT,
              stripe_checkout_session_id TEXT,
              stripe_customer_id TEXT,
              revenue_type TEXT NOT NULL DEFAULT 'checkout',
              status TEXT NOT NULL DEFAULT 'paid',
              currency TEXT NOT NULL DEFAULT 'usd',
              amount_paid_cents INTEGER NOT NULL DEFAULT 0,
              customer_email TEXT,
              occurred_at TEXT NOT NULL,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              UNIQUE (business_slug, provider_event_id, stripe_object_id),
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS app_usage_events (
              id TEXT PRIMARY KEY,
              business_slug TEXT NOT NULL,
              app_user_id TEXT,
              app_user_tier TEXT,
              purpose TEXT NOT NULL,
              route TEXT NOT NULL,
              status TEXT NOT NULL,
              estimated_cost_microusd INTEGER NOT NULL DEFAULT 0,
              actual_cost_microusd INTEGER NOT NULL DEFAULT 0,
              input_tokens INTEGER,
              output_tokens INTEGER,
              provider_request_id TEXT,
              provider TEXT,
              model TEXT,
              metadata_json TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              completed_at TEXT,
              FOREIGN KEY (business_slug) REFERENCES businesses(slug) ON DELETE CASCADE,
              FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS webhook_events (
              id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              provider_event_id TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              processed_at TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              UNIQUE (provider, provider_event_id)
            );
            CREATE INDEX IF NOT EXISTS conversation_threads_business_status_idx
              ON conversation_threads(business_slug, status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS conversation_messages_business_status_idx
              ON conversation_messages(business_slug, status, received_at DESC);
            CREATE INDEX IF NOT EXISTS app_users_business_email_idx ON app_users(business_slug, email);
            CREATE INDEX IF NOT EXISTS app_entitlements_user_idx ON app_entitlements(business_slug, app_user_id, status);
            CREATE INDEX IF NOT EXISTS app_checkout_sessions_business_idx ON app_checkout_sessions(business_slug, created_at DESC);
            CREATE INDEX IF NOT EXISTS app_revenue_events_business_idx ON app_revenue_events(business_slug, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS app_usage_events_business_idx ON app_usage_events(business_slug, created_at DESC);
            """
        )
        self._migrate_db(conn)

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        business_columns = {row["name"] for row in conn.execute("PRAGMA table_info(businesses)").fetchall()}
        if "mode" not in business_columns:
            conn.execute("ALTER TABLE businesses ADD COLUMN mode TEXT NOT NULL DEFAULT 'live'")
            conn.execute("UPDATE businesses SET mode = 'live' WHERE mode IS NULL OR mode NOT IN ('live', 'test')")
        elif conn.execute("SELECT 1 FROM businesses WHERE mode IS NULL OR mode NOT IN ('live', 'test') LIMIT 1").fetchone():
            conn.execute("UPDATE businesses SET mode = 'live' WHERE mode IS NULL OR mode NOT IN ('live', 'test')")
        if "work_focus" not in business_columns:
            conn.execute("ALTER TABLE businesses ADD COLUMN work_focus TEXT NOT NULL DEFAULT 'all'")
            conn.execute("UPDATE businesses SET work_focus = 'all' WHERE work_focus IS NULL OR work_focus NOT IN ('all', 'marketing', 'product')")
        elif conn.execute("SELECT 1 FROM businesses WHERE work_focus IS NULL OR work_focus NOT IN ('all', 'marketing', 'product') LIMIT 1").fetchone():
            conn.execute("UPDATE businesses SET work_focus = 'all' WHERE work_focus IS NULL OR work_focus NOT IN ('all', 'marketing', 'product')")
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(businesses)").fetchall()}
        if "businesses_mode_idx" not in indexes:
            conn.execute("CREATE INDEX businesses_mode_idx ON businesses(mode, updated_at DESC)")
        if "businesses_work_focus_idx" not in indexes:
            conn.execute("CREATE INDEX businesses_work_focus_idx ON businesses(work_focus, updated_at DESC)")

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

    def _conversation_thread_relpath(self, thread: dict[str, Any]) -> str:
        source = _file_slug(str(thread.get("source") or "unknown"), "unknown")
        label = str(thread.get("external_id") or thread.get("title") or thread.get("id") or "thread")
        return f"conversations/{source}/{_file_slug(label, 'thread')}.md"

    def _conversation_corpus_message(self, thread: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "takyon.conversation.message.v1",
            "business": message.get("business_slug") or thread.get("business_slug"),
            "thread_id": thread.get("id"),
            "thread_source": thread.get("source"),
            "thread_external_id": thread.get("external_id"),
            "thread_title": thread.get("title"),
            "thread_url": thread.get("url"),
            "message_id": message.get("id"),
            "source": message.get("source"),
            "external_id": message.get("external_id"),
            "direction": message.get("direction"),
            "status": message.get("status"),
            "author_label": message.get("author_label"),
            "body": message.get("body"),
            "received_at": message.get("received_at"),
            "created_at": message.get("created_at"),
            "updated_at": message.get("updated_at"),
            "consent": "unknown",
            "pii_review": "unreviewed",
        }

    def _append_conversation_message_corpus(self, slug: str, thread: dict[str, Any], message: dict[str, Any]) -> str:
        rel = "conversations/corpus/messages.jsonl"
        _append_jsonl(self._business_root(slug) / rel, self._conversation_corpus_message(thread, message))
        return rel

    def _append_conversation_event_corpus(self, slug: str, event_type: str, payload: Any) -> str:
        rel = "conversations/corpus/events.jsonl"
        _append_jsonl(
            self._business_root(slug) / rel,
            {
                "schema": "takyon.conversation.event.v1",
                "business": slug,
                "event_type": event_type,
                "payload": payload,
                "created_at": _now(),
            },
        )
        return rel

    def _conversation_index(self, conn: sqlite3.Connection, slug: str) -> None:
        rows = [
            self._row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM conversation_threads WHERE business_slug = ? ORDER BY updated_at DESC",
                (slug,),
            ).fetchall()
        ]
        lines = ["# Conversation Index", "", f"Business: {slug}", ""]
        if not rows:
            lines.append("No conversation threads recorded.")
        else:
            for row in rows:
                rel = self._conversation_thread_relpath(row)
                lines.append(f"- [{row['title']}]({rel}) — {row['source']} — {row['status']}")
        lines.extend([
            "",
            "## Permanent Corpus",
            "",
            "- conversations/corpus/messages.jsonl",
            "- conversations/corpus/events.jsonl",
        ])
        _atomic_write_text(self._business_root(slug) / "conversations" / "index.md", "\n".join(lines) + "\n")

    def _rewrite_conversation_thread_file(self, conn: sqlite3.Connection, slug: str, thread_id: str) -> str:
        thread_row = conn.execute(
            "SELECT * FROM conversation_threads WHERE business_slug = ? AND id = ?",
            (slug, thread_id),
        ).fetchone()
        if not thread_row:
            raise TakyonError(f"conversation thread not found: {thread_id}")
        thread = self._row_to_dict(thread_row)
        messages = [
            self._row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM conversation_messages WHERE business_slug = ? AND thread_id = ? ORDER BY received_at ASC, created_at ASC",
                (slug, thread_id),
            ).fetchall()
        ]
        lines = [
            f"# {thread['title']}",
            "",
            f"- Source: {thread['source']}",
            f"- External ID: {thread['external_id']}",
            f"- Status: {thread['status']}",
        ]
        if thread.get("url"):
            lines.append(f"- URL: {thread['url']}")
        lines.extend(["", "## Messages", ""])
        if not messages:
            lines.append("No messages recorded.")
        else:
            for message in messages:
                lines.extend([
                    f"### {message['received_at']} — {message['direction']} — {message['author_label']}",
                    "",
                    f"Status: {message['status']}",
                    "",
                    str(message.get("body") or "").strip() or "(empty)",
                    "",
                ])
        rel = self._conversation_thread_relpath(thread)
        _atomic_write_text(self._business_root(slug) / rel, "\n".join(lines).rstrip() + "\n")
        self._conversation_index(conn, slug)
        return rel

    def _conversation_summary(self, conn: sqlite3.Connection, slug: str, limit: int) -> dict[str, Any]:
        summary_row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM conversation_threads WHERE business_slug = ? AND status = 'active') AS active_threads,
              (SELECT COUNT(*) FROM conversation_messages WHERE business_slug = ? AND direction = 'inbound' AND status = 'needs_response') AS unresolved_messages,
              (SELECT MAX(received_at) FROM conversation_messages WHERE business_slug = ?) AS latest_message_at
            """,
            (slug, slug, slug),
        ).fetchone()
        threads = [
            self._row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM conversation_threads WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?",
                (slug, limit),
            ).fetchall()
        ]
        unresolved = [
            self._row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM conversation_messages WHERE business_slug = ? AND direction = 'inbound' AND status = 'needs_response' ORDER BY received_at DESC LIMIT ?",
                (slug, limit),
            ).fetchall()
        ]
        return {
            "active_threads": int(summary_row["active_threads"] or 0) if summary_row else 0,
            "unresolved_messages": int(summary_row["unresolved_messages"] or 0) if summary_row else 0,
            "latest_message_at": summary_row["latest_message_at"] if summary_row else None,
            "threads": threads,
            "unresolved": unresolved,
            "filesystem_index": "conversations/index.md",
        }

    def _ensure_app_budget(self, conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
        now = _now()
        row = conn.execute("SELECT * FROM app_budgets WHERE business_slug = ?", (slug,)).fetchone()
        if not row:
            start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
            conn.execute(
                "INSERT INTO app_budgets (business_slug, status, hard_limit_microusd, current_period_start, current_period_end, created_at, updated_at) VALUES (?, 'active', ?, ?, ?, ?, ?)",
                (slug, 5_000_000, start.isoformat(), end.isoformat(), now, now),
            )
            row = conn.execute("SELECT * FROM app_budgets WHERE business_slug = ?", (slug,)).fetchone()
        return self._row_to_dict(row)

    def _sync_user_tier(self, conn: sqlite3.Connection, slug: str, user_id: str) -> str:
        rows = [
            self._row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM app_entitlements WHERE business_slug = ? AND app_user_id = ? ORDER BY updated_at DESC",
                (slug, user_id),
            ).fetchall()
        ]
        active = [row for row in rows if row and row.get("status") in {"active", "trialing"}]
        tier = "free"
        if active:
            tier = sorted(active, key=lambda row: _tier_rank(str(row.get("tier") or "free")))[0].get("tier") or "free"
        conn.execute("UPDATE app_users SET tier = ?, updated_at = ? WHERE business_slug = ? AND id = ?", (tier, _now(), slug, user_id))
        return str(tier)

    def _app_surface_contract(self, conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM app_surface_contracts WHERE business_slug = ?", (slug,)).fetchone()
        contract = self._row_to_dict(row)
        if contract:
            return contract
        return {
            "business_slug": slug,
            "status": "missing",
            "design_brief_path": "product/design-brief.md",
            "source_path": None,
            "runtime_api_base": f"/api/takyon/apps/{slug}",
            "routes": [],
            "theme": {"source": "business design brief"},
            "constraints": {
                "no_hardcoded_product_ui": True,
                "backend_runtime_only": True,
            },
            "notes": "No product surface contract has been recorded yet.",
            "metadata": {},
            "created_at": None,
            "updated_at": None,
        }

    def _latest_surface_verification(self, conn: sqlite3.Connection, slug: str, source_path: str | None) -> dict[str, Any] | None:
        if not source_path:
            return None
        rows = conn.execute(
            "SELECT * FROM events WHERE business_slug = ? AND event_type = 'product.surface.verify' ORDER BY created_at DESC LIMIT 25",
            (slug,),
        ).fetchall()
        for row in rows:
            event = self._row_to_dict(row) or {}
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if payload.get("source_path") == source_path:
                return {**payload, "event_created_at": event.get("created_at")}
        return None

    def _surface_status_for_upsert(
        self,
        conn: sqlite3.Connection,
        slug: str,
        requested_status: str,
        source_path: str | None,
        metadata: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        status = str(requested_status or "draft").strip().lower()
        if status != "active":
            return status, metadata
        validation: dict[str, Any] = {"requested_status": "active"}
        if not source_path:
            validation.update({"status": "unverified", "reason": "missing source_path"})
            return "unverified", {**metadata, "takyon_surface_validation": validation}
        root = self._business_root(slug) / source_path
        source_files = _product_source_files(root, limit=2)
        if not root.exists() or not root.is_dir() or not source_files:
            validation.update({"status": "unverified", "reason": "source path is missing or empty", "source_path": source_path})
            return "unverified", {**metadata, "takyon_surface_validation": validation}
        latest = self._latest_surface_verification(conn, slug, source_path)
        if not latest or latest.get("status") != "passed":
            validation.update({
                "status": "unverified",
                "reason": "no passing product.surface.verify receipt for source_path",
                "source_path": source_path,
                "latest_verification": latest,
            })
            return "unverified", {**metadata, "takyon_surface_validation": validation}
        return status, {**metadata, "takyon_surface_validation": {"status": "passed", "receipt": latest.get("receipt_path")}}

    def _rewrite_app_files(self, conn: sqlite3.Connection, slug: str) -> None:
        root = self._business_root(slug) / "app"
        budget = self._ensure_app_budget(conn, slug)
        surface = self._app_surface_contract(conn, slug)
        plans = [
            self._row_to_dict(row)
            for row in conn.execute("SELECT * FROM app_plan_policies WHERE business_slug = ? ORDER BY price_cents ASC, plan_key ASC", (slug,)).fetchall()
        ]
        users = [
            self._row_to_dict(row)
            for row in conn.execute("SELECT id, business_slug, email, name, status, tier, metadata_json, created_at, updated_at FROM app_users WHERE business_slug = ? ORDER BY updated_at DESC LIMIT 200", (slug,)).fetchall()
        ]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_paid_cents), 0) AS cents, COUNT(*) AS count FROM app_revenue_events WHERE business_slug = ?",
            (slug,),
        ).fetchone()
        usage = conn.execute(
            "SELECT COALESCE(SUM(actual_cost_microusd), 0) AS actual, COALESCE(SUM(estimated_cost_microusd), 0) AS estimated, COUNT(*) AS count FROM app_usage_events WHERE business_slug = ? AND created_at >= ?",
            (slug, budget["current_period_start"]),
        ).fetchone()
        checkout_count = conn.execute("SELECT COUNT(*) AS count FROM app_checkout_intents WHERE business_slug = ?", (slug,)).fetchone()

        index = [
            "# App Runtime Source Of Truth",
            "",
            f"Business: {slug}",
            "",
            "This business uses Hermes Takyon app rails for product customer auth, sessions, plan policy, Stripe checkout, entitlements, subscription reconciliation, revenue events, and app usage budget.",
            "",
            "Do not store magic-link tokens, session tokens, Stripe secrets, or customer payment data in business files.",
            "",
            "## Files",
            "",
            "- [Plans](plans.md)",
            "- [Customers](customers.md)",
            "- [Billing](billing.md)",
            "- [Usage Budget](usage.md)",
            "- [Surface Contract](surface.md)",
        ]
        _atomic_write_text(root / "index.md", "\n".join(index) + "\n")

        surface_lines = [
            "# App Surface Contract",
            "",
            f"Business: {slug}",
            "",
            "The shared Hermes app runtime owns backend rails only: auth, sessions, entitlements, checkout, subscription reconciliation, revenue, usage budgets, and webhooks.",
            "",
            "The product's visual design, layout, copy, information architecture, interaction model, and frontend source must come from this business's design brief and product workspace. Do not use a hardcoded Takyon template as the final customer surface.",
            "",
            "## Contract",
            "",
            f"- Status: {surface.get('status') or 'missing'}",
            f"- Design brief path: {surface.get('design_brief_path') or 'product/design-brief.md'}",
            f"- Source path: {surface.get('source_path') or 'not set'}",
            f"- Runtime API base: {surface.get('runtime_api_base') or f'/api/takyon/apps/{slug}'}",
            f"- Notes: {surface.get('notes') or 'not set'}",
            "",
            "## Routes",
            "",
        ]
        routes = surface.get("routes") or []
        if isinstance(routes, list) and routes:
            for route in routes:
                if isinstance(route, dict):
                    path = route.get("path") or route.get("route") or route.get("url") or "route"
                    label = route.get("name") or route.get("kind") or route.get("purpose") or "screen"
                    surface_lines.append(f"- {path}: {label}")
                else:
                    surface_lines.append(f"- {_markdown_scalar(route)}")
        else:
            surface_lines.append("- No frontend routes recorded.")
        surface_lines.extend(["", "## Theme Source", ""])
        surface_lines.extend(_markdown_kv_lines(surface.get("theme"), empty="business design brief"))
        surface_lines.extend(["", "## Constraints", ""])
        surface_lines.extend(_markdown_kv_lines(surface.get("constraints"), empty="no hardcoded product UI"))
        metadata = surface.get("metadata") if isinstance(surface.get("metadata"), dict) else {}
        validation = metadata.get("takyon_surface_validation") if isinstance(metadata.get("takyon_surface_validation"), dict) else {}
        if validation:
            surface_lines.extend(["", "## Verification", ""])
            surface_lines.extend(_markdown_kv_lines(validation, empty="unverified"))
        _atomic_write_text(root / "surface.md", "\n".join(surface_lines).rstrip() + "\n")

        plan_lines = ["# App Plans", "", f"Business: {slug}", ""]
        if not plans:
            plan_lines.append("No app plans configured.")
        for plan in plans:
            plan_lines.extend([
                f"## {plan['plan_key']}",
                "",
                f"- Tier: {plan['tier']}",
                f"- Price: {plan['price_cents']} {plan['currency']} cents",
                f"- Billing interval: {plan['billing_interval']}",
                f"- Included AI budget microusd: {plan['included_ai_budget_microusd']}",
                f"- Included action quota: {plan['included_action_quota']}",
                f"- Overage: {'allowed' if plan['allow_overage'] else 'not allowed'}",
                f"- Stripe price: {plan.get('stripe_price_id') or 'not linked'}",
                "",
            ])
        _atomic_write_text(root / "plans.md", "\n".join(plan_lines).rstrip() + "\n")

        customer_lines = ["# App Customers", "", f"Business: {slug}", ""]
        if not users:
            customer_lines.append("No product customers recorded.")
        for user in users:
            customer_lines.append(f"- {user['email']} — {user['status']} — {user['tier']} — {user['id']}")
        _atomic_write_text(root / "customers.md", "\n".join(customer_lines).rstrip() + "\n")

        billing_lines = [
            "# App Billing",
            "",
            f"Business: {slug}",
            "",
            f"- Revenue events: {int(revenue['count'] or 0)}",
            f"- Revenue cents: {int(revenue['cents'] or 0)}",
            f"- Checkout intents: {int(checkout_count['count'] or 0)}",
        ]
        _atomic_write_text(root / "billing.md", "\n".join(billing_lines) + "\n")

        usage_lines = [
            "# App Usage Budget",
            "",
            f"Business: {slug}",
            "",
            f"- Status: {budget['status']}",
            f"- Hard limit microusd: {budget['hard_limit_microusd']}",
            f"- Current period: {budget['current_period_start']} to {budget['current_period_end']}",
            f"- Usage events this period: {int(usage['count'] or 0)}",
            f"- Estimated cost microusd: {int(usage['estimated'] or 0)}",
            f"- Actual cost microusd: {int(usage['actual'] or 0)}",
        ]
        _atomic_write_text(root / "usage.md", "\n".join(usage_lines) + "\n")

    def _app_summary(self, conn: sqlite3.Connection, slug: str, limit: int) -> dict[str, Any]:
        budget = self._ensure_app_budget(conn, slug)
        usage = conn.execute(
            "SELECT COALESCE(SUM(actual_cost_microusd), 0) AS actual, COALESCE(SUM(estimated_cost_microusd), 0) AS estimated, COUNT(*) AS count FROM app_usage_events WHERE business_slug = ? AND created_at >= ?",
            (slug, budget["current_period_start"]),
        ).fetchone()
        revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_paid_cents), 0) AS cents, COUNT(*) AS count FROM app_revenue_events WHERE business_slug = ?",
            (slug,),
        ).fetchone()
        return {
            "budget": budget,
            "surface_contract": self._app_surface_contract(conn, slug),
            "usage_this_period": {
                "events": int(usage["count"] or 0),
                "estimated_cost_microusd": int(usage["estimated"] or 0),
                "actual_cost_microusd": int(usage["actual"] or 0),
            },
            "revenue": {"events": int(revenue["count"] or 0), "amount_paid_cents": int(revenue["cents"] or 0)},
            "plans": [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM app_plan_policies WHERE business_slug = ? ORDER BY price_cents ASC, plan_key ASC LIMIT ?", (slug, limit)).fetchall()
            ],
            "customers": [
                self._row_to_dict(row)
                for row in conn.execute("SELECT id, business_slug, email, name, status, tier, metadata_json, created_at, updated_at FROM app_users WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?", (slug, limit)).fetchall()
            ],
            "entitlements": [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM app_entitlements WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?", (slug, limit)).fetchall()
            ],
            "recent_checkouts": [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM app_checkout_intents WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?", (slug, limit)).fetchall()
            ],
            "filesystem_index": "app/index.md",
        }

    def calculate_pulse(self, slug: str, *, limit: int = 10) -> dict[str, Any]:
        slug = _slugify(slug)
        limit = max(1, min(int(limit or 10), 50))
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()

        with self._connect() as conn:
            business = self._ensure_business(conn, slug)
            created_at = str(business.get("created_at") or now)
            created_dt = _parse_iso_datetime(created_at) or now_dt
            previous_row = self._row_to_dict(conn.execute(
                "SELECT * FROM events WHERE business_slug = ? AND event_type = 'business.pulse.snapshot' ORDER BY created_at DESC LIMIT 1",
                (slug,),
            ).fetchone())
            previous_payload = (previous_row or {}).get("payload") or {}
            previous_payload_dict = previous_payload if isinstance(previous_payload, dict) else {}
            previous_pulse = previous_payload_dict.get("pulse") if isinstance(previous_payload_dict.get("pulse"), dict) else previous_payload_dict
            previous_generated_at = (previous_pulse or {}).get("generated_at") or previous_payload_dict.get("generated_at") or (previous_row or {}).get("created_at")
            previous_dt = _parse_iso_datetime(previous_generated_at) or created_dt
            if previous_dt > now_dt:
                previous_dt = created_dt

            windows = {
                "current_wake_interval": {"start": previous_dt.isoformat(), "end": now},
                "since_business_created": {"start": created_dt.isoformat(), "end": now},
                "lifetime": {"start": created_dt.isoformat(), "end": now},
            }

            def one(sql: str, params: tuple[Any, ...]) -> sqlite3.Row:
                return conn.execute(sql, params).fetchone()

            def rows(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
                return [self._row_to_dict(row) for row in conn.execute(sql, params).fetchall()]

            def window_metrics(start: str, end: str) -> dict[str, Any]:
                usage = one(
                    """
                    SELECT COUNT(*) AS events,
                           COUNT(DISTINCT app_user_id) AS active_users,
                           COALESCE(SUM(estimated_cost_microusd), 0) AS estimated_cost_microusd,
                           COALESCE(SUM(actual_cost_microusd), 0) AS actual_cost_microusd
                    FROM app_usage_events
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                    """,
                    (slug, start, end),
                )
                activation = one(
                    """
                    SELECT COUNT(*) AS events,
                           COUNT(DISTINCT app_user_id) AS users
                    FROM app_usage_events
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                      AND lower(COALESCE(purpose, '')) = 'activation'
                    """,
                    (slug, start, end),
                )
                meaningful = one(
                    """
                    SELECT COUNT(DISTINCT app_user_id) AS users
                    FROM app_usage_events
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                      AND lower(COALESCE(purpose, '')) NOT IN ('', 'page_view', 'view', 'visit', 'heartbeat')
                    """,
                    (slug, start, end),
                )
                customers = one(
                    "SELECT COUNT(*) AS users FROM app_users WHERE business_slug = ? AND created_at >= ? AND created_at <= ?",
                    (slug, start, end),
                )
                checkouts = one(
                    """
                    SELECT COUNT(*) AS intents,
                           SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                           SUM(CASE WHEN status = 'test_local' THEN 1 ELSE 0 END) AS test_local
                    FROM app_checkout_intents
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                    """,
                    (slug, start, end),
                )
                revenue = one(
                    """
                    SELECT COUNT(*) AS events,
                           COALESCE(SUM(amount_paid_cents), 0) AS amount_paid_cents,
                           COUNT(DISTINCT customer_email) AS paying_emails
                    FROM app_revenue_events
                    WHERE business_slug = ? AND occurred_at >= ? AND occurred_at <= ?
                    """,
                    (slug, start, end),
                )
                conversations = one(
                    """
                    SELECT COUNT(*) AS messages,
                           SUM(CASE WHEN direction = 'inbound' THEN 1 ELSE 0 END) AS inbound_messages,
                           SUM(CASE WHEN direction = 'outbound' THEN 1 ELSE 0 END) AS outbound_messages,
                           SUM(CASE WHEN direction = 'inbound' AND status = 'needs_response' THEN 1 ELSE 0 END) AS unresolved_inbound
                    FROM conversation_messages
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                    """,
                    (slug, start, end),
                )
                threads = one(
                    "SELECT COUNT(*) AS threads FROM conversation_threads WHERE business_slug = ? AND created_at >= ? AND created_at <= ?",
                    (slug, start, end),
                )
                jobs = one(
                    """
                    SELECT COUNT(*) AS jobs,
                           SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                           SUM(CASE WHEN status IN ('failed', 'error', 'blocked') THEN 1 ELSE 0 END) AS blocked_or_failed,
                           SUM(CASE WHEN status IN ('done', 'completed', 'succeeded') THEN 1 ELSE 0 END) AS completed
                    FROM jobs
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                    """,
                    (slug, start, end),
                )
                ledger = one(
                    """
                    SELECT COUNT(*) AS entries,
                           COALESCE(SUM(amount), 0) AS amount_total,
                           COALESCE(SUM(CASE WHEN lower(status) IN ('spent', 'paid', 'used', 'completed') THEN amount ELSE 0 END), 0) AS amount_spent,
                           COALESCE(SUM(CASE WHEN lower(status) NOT IN ('spent', 'paid', 'used', 'completed') THEN amount ELSE 0 END), 0) AS amount_reserved
                    FROM ledger_entries
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                    """,
                    (slug, start, end),
                )
                route_costs = rows(
                    """
                    SELECT route, purpose, COUNT(*) AS events,
                           COUNT(DISTINCT app_user_id) AS users,
                           COALESCE(SUM(estimated_cost_microusd), 0) AS estimated_cost_microusd,
                           COALESCE(SUM(actual_cost_microusd), 0) AS actual_cost_microusd
                    FROM app_usage_events
                    WHERE business_slug = ? AND created_at >= ? AND created_at <= ?
                    GROUP BY route, purpose
                    ORDER BY events DESC
                    LIMIT ?
                    """,
                    (slug, start, end, limit),
                )
                actual_cost = int(usage["actual_cost_microusd"] or 0)
                estimated_cost = int(usage["estimated_cost_microusd"] or 0)
                cost_cents = _microusd_to_cents(actual_cost or estimated_cost)
                revenue_cents = int(revenue["amount_paid_cents"] or 0)
                paying_emails = int(revenue["paying_emails"] or 0)
                active_users = int(usage["active_users"] or 0)
                usage_events = int(usage["events"] or 0)
                return {
                    "activation": {
                        "activation_events": int(activation["events"] or 0),
                        "activated_users": int(activation["users"] or 0),
                        "meaningful_usage_users": int(meaningful["users"] or 0),
                    },
                    "conversion": {
                        "visitors": {"status": "missing"},
                        "new_users": int(customers["users"] or 0),
                        "checkout_intents": int(checkouts["intents"] or 0),
                        "completed_checkouts": int(checkouts["completed"] or 0),
                        "test_local_checkouts": int(checkouts["test_local"] or 0),
                    },
                    "revenue": {
                        "events": int(revenue["events"] or 0),
                        "amount_paid_cents": revenue_cents,
                        "paying_emails": paying_emails,
                        "arpu_cents": int(round(revenue_cents / paying_emails)) if paying_emails else None,
                    },
                    "margin": {
                        "revenue_cents": revenue_cents,
                        "usage_cost_cents": cost_cents,
                        "gross_after_usage_cost_cents": revenue_cents - cost_cents,
                        "payment_fee_estimate": {"status": "missing"},
                    },
                    "usage_cost": {
                        "events": usage_events,
                        "active_users": active_users,
                        "estimated_cost_microusd": estimated_cost,
                        "actual_cost_microusd": actual_cost,
                        "cost_per_active_user_microusd": int(round((actual_cost or estimated_cost) / active_users)) if active_users else None,
                        "by_route": route_costs,
                    },
                    "budget_burn": {
                        "ledger_entries": int(ledger["entries"] or 0),
                        "ledger_amount_total": float(ledger["amount_total"] or 0),
                        "ledger_amount_reserved": float(ledger["amount_reserved"] or 0),
                        "ledger_amount_spent": float(ledger["amount_spent"] or 0),
                    },
                    "cac": {
                        "status": "missing",
                        "reason": "campaign spend and paid-customer attribution are not yet linked in canonical metadata",
                    },
                    "payback": {
                        "status": "missing",
                        "reason": "CAC or gross profit per customer per month is unavailable",
                    },
                    "sales_signal": {
                        "threads": int(threads["threads"] or 0),
                        "messages": int(conversations["messages"] or 0),
                        "inbound_messages": int(conversations["inbound_messages"] or 0),
                        "outbound_messages": int(conversations["outbound_messages"] or 0),
                        "unresolved_inbound": int(conversations["unresolved_inbound"] or 0),
                        "booked_call_rate": {"status": "missing"},
                        "close_rate": {"status": "missing"},
                    },
                    "retention": {
                        "active_users": active_users,
                        "repeat_usage_users": int(one(
                            """
                            SELECT COUNT(*) AS users FROM (
                              SELECT app_user_id
                              FROM app_usage_events
                              WHERE business_slug = ? AND created_at >= ? AND created_at <= ? AND app_user_id IS NOT NULL
                              GROUP BY app_user_id
                              HAVING COUNT(*) >= 2
                            )
                            """,
                            (slug, start, end),
                        )["users"] or 0),
                    },
                    "engagement": {
                        "core_actions_per_active_user": round(usage_events / active_users, 2) if active_users else None,
                        "usage_events": usage_events,
                    },
                    "pricing_pressure": {
                        "upgrade_downgrade_churn": {"status": "missing"},
                        "support_burden_by_tier": {"status": "missing"},
                    },
                }

            active_entitlements = one(
                """
                SELECT COUNT(DISTINCT app_user_id) AS paid_customers
                FROM app_entitlements
                WHERE business_slug = ? AND status IN ('active', 'trialing') AND tier IN ('paid', 'pro', 'team', 'owner')
                """,
                (slug,),
            )
            mrr = one(
                """
                SELECT COALESCE(SUM(
                    CASE
                      WHEN p.billing_interval = 'year' THEN p.price_cents / 12.0
                      WHEN p.billing_interval = 'month' THEN p.price_cents
                      ELSE 0
                    END
                ), 0) AS mrr_cents
                FROM app_entitlements e
                JOIN app_plan_policies p
                  ON p.business_slug = e.business_slug AND p.plan_key = e.plan_key
                WHERE e.business_slug = ? AND e.status IN ('active', 'trialing')
                """,
                (slug,),
            )
            app_budget = self._ensure_app_budget(conn, slug)
            app_usage_total = one(
                "SELECT COALESCE(SUM(actual_cost_microusd), 0) AS actual, COALESCE(SUM(estimated_cost_microusd), 0) AS estimated FROM app_usage_events WHERE business_slug = ? AND created_at >= ?",
                (slug, app_budget["current_period_start"]),
            )
            current_jobs = one(
                "SELECT SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued FROM jobs WHERE business_slug = ?",
                (slug,),
            )
            business_budget_amount = _budget_amount(business.get("budget"))
            computed_windows = {name: {**bounds, "metrics": window_metrics(bounds["start"], bounds["end"])} for name, bounds in windows.items()}
            lifetime = computed_windows["lifetime"]["metrics"]
            summary = {
                "users": int(one("SELECT COUNT(*) AS count FROM app_users WHERE business_slug = ?", (slug,))["count"] or 0),
                "paid_customers": int(active_entitlements["paid_customers"] or 0),
                "mrr_cents": int(round(float(mrr["mrr_cents"] or 0))),
                "arr_cents": int(round(float(mrr["mrr_cents"] or 0) * 12)),
                "revenue_cents": int(lifetime["revenue"]["amount_paid_cents"]),
                "checkout_intents": int(lifetime["conversion"]["checkout_intents"]),
                "usage_events": int(lifetime["usage_cost"]["events"]),
                "actual_cost_microusd": int(lifetime["usage_cost"]["actual_cost_microusd"]),
                "inbound_messages": int(lifetime["sales_signal"]["inbound_messages"]),
                "unresolved_inbound": int(lifetime["sales_signal"]["unresolved_inbound"]),
                "queued_jobs": int(current_jobs["queued"] or 0),
            }
            previous_summary = (previous_pulse or {}).get("summary") if isinstance(previous_pulse, dict) else {}
            comparable_keys = ("users", "paid_customers", "mrr_cents", "arr_cents", "revenue_cents", "checkout_intents", "usage_events", "actual_cost_microusd", "inbound_messages", "unresolved_inbound")
            if previous_row is None:
                deltas = {"status": "baseline"}
            else:
                deltas = {
                    "status": "computed",
                    **{
                        key: summary.get(key, 0) - int((previous_summary or {}).get(key) or 0)
                        for key in comparable_keys
                        if isinstance(summary.get(key), int)
                    },
                }
            evidence_score = 0
            if business.get("goal"):
                evidence_score = max(evidence_score, 1)
            if int(lifetime["sales_signal"]["inbound_messages"] or 0):
                evidence_score = max(evidence_score, 3)
            if int(lifetime["usage_cost"]["events"] or 0):
                evidence_score = max(evidence_score, 4)
            if int(lifetime["revenue"]["amount_paid_cents"] or 0):
                evidence_score = max(evidence_score, 5)
            recent_event_types = rows(
                """
                SELECT event_type, COUNT(*) AS count
                FROM events
                WHERE business_slug = ? AND created_at >= ?
                GROUP BY event_type
                ORDER BY count DESC, event_type ASC
                LIMIT ?
                """,
                (slug, windows["current_wake_interval"]["start"], limit),
            )
            return {
                "success": True,
                "business": slug,
                "generated_at": now,
                "is_first_pulse": previous_row is None,
                "previous_pulse": {
                    "event_id": (previous_row or {}).get("id"),
                    "generated_at": previous_generated_at,
                    "created_at": (previous_row or {}).get("created_at"),
                    "status": "missing" if previous_row is None else "present",
                },
                "windows": computed_windows,
                "summary": summary,
                "deltas_from_previous_pulse": deltas,
                "current_state": {
                    "business_age_hours": round((now_dt - created_dt).total_seconds() / 3600, 2),
                    "wake_interval_hours": round((now_dt - previous_dt).total_seconds() / 3600, 2),
                    "app_budget": {
                        "status": app_budget["status"],
                        "hard_limit_microusd": int(app_budget["hard_limit_microusd"] or 0),
                        "spent_microusd": int((app_usage_total["actual"] or 0) or (app_usage_total["estimated"] or 0)),
                        "remaining_microusd": int(app_budget["hard_limit_microusd"] or 0) - int((app_usage_total["actual"] or 0) or (app_usage_total["estimated"] or 0)),
                    },
                    "business_budget": {"amount": business_budget_amount, "status": "missing" if business_budget_amount is None else "present"},
                    "active_paid_customers": int(active_entitlements["paid_customers"] or 0),
                    "mrr_cents": summary["mrr_cents"],
                    "arr_cents": summary["arr_cents"],
                },
                "missing_metrics": [
                    "visitors",
                    "campaign-attributed-cac",
                    "payment-fee-estimate",
                    "booked-call-rate",
                    "close-rate",
                    "upgrade-downgrade-churn-history",
                    "support-burden-by-tier",
                ],
                "recent_event_types": recent_event_types,
                "evidence_strength": {
                    "score": evidence_score,
                    "scale": "0 none, 1 operator hypothesis, 2 market evidence, 3 user reply, 4 usage, 5 paid revenue",
                },
                "storage": {
                    "raw_sources": ["state.sqlite3", "events", "app_* tables", "conversation_* tables", "ledger_entries", "jobs"],
                    "snapshot_event_type": "business.pulse.snapshot",
                    "human_summary_path": "brain/pulse.md",
                    "business_model_path": "brain/business-model.md",
                },
            }

    def _sync_business_ceo_cron_control(self, slug: str, state: str, reason: str) -> dict[str, Any]:
        from cron.jobs import list_jobs, pause_job, resume_job

        name = f"takyon-ceo:{_slugify(slug)}"
        existing = next((job for job in list_jobs(include_disabled=True) if job.get("name") == name), None)
        if not existing:
            return {"cron_job": None, "changed": False}
        if state == "active":
            updated = resume_job(existing["id"])
        else:
            updated = pause_job(existing["id"], reason=reason)
        return {
            "cron_job": existing["id"],
            "changed": bool(updated),
            "enabled": bool(updated.get("enabled", False)) if updated else bool(existing.get("enabled", False)),
            "state": updated.get("state") if updated else existing.get("state"),
        }

    def _filesystem_summary(self, root: Path) -> dict[str, Any]:
        if not root.exists():
            return {"path": str(root), "exists": False, "files": 0, "dirs": 0}
        files = 0
        dirs = 0
        for child in root.rglob("*"):
            if child.is_dir():
                dirs += 1
            else:
                files += 1
        return {"path": str(root), "exists": True, "files": files, "dirs": dirs}

    def _business_cron_jobs(self, slug: str) -> list[dict[str, Any]]:
        from cron.jobs import list_jobs

        business = _slugify(slug)
        expected_name = f"takyon-ceo:{business}"
        matches: list[dict[str, Any]] = []
        for job in list_jobs(include_disabled=True):
            name = str(job.get("name") or "")
            origin = job.get("origin") if isinstance(job.get("origin"), dict) else {}
            if name == expected_name or str(origin.get("business") or "") == business:
                matches.append(job)
        return matches

    def _delete_business_crons(self, slug: str, *, confirm: bool) -> dict[str, Any]:
        jobs = self._business_cron_jobs(slug)
        summary = [
            {
                "id": job.get("id"),
                "name": job.get("name"),
                "state": job.get("state"),
                "schedule": job.get("schedule_display") or job.get("schedule"),
            }
            for job in jobs
        ]
        if not confirm:
            return {"matched": summary, "removed": []}

        from cron.jobs import remove_job

        removed = []
        for job in jobs:
            removed.append({
                "id": job.get("id"),
                "name": job.get("name"),
                "removed": remove_job(str(job.get("id") or "")),
            })
        return {"matched": summary, "removed": removed}

    def _delete_vercel_project_domain(self, domain: str) -> dict[str, Any]:
        load_takyon_env()
        token = os.getenv("VERCEL_TOKEN")
        project = os.getenv("VERCEL_PROJECT_ID")
        team = os.getenv("VERCEL_TEAM_ID")
        if not token:
            raise TakyonError("domain cleanup requires VERCEL_TOKEN")
        if not project:
            raise TakyonError("domain cleanup requires VERCEL_PROJECT_ID")

        query = urllib.parse.urlencode({"teamId": team}) if team else ""
        url = (
            "https://api.vercel.com/v9/projects/"
            f"{urllib.parse.quote(project, safe='')}/domains/{urllib.parse.quote(domain, safe='')}"
            f"{'?' + query if query else ''}"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps({"removeRedirects": True}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
                return {
                    "domain": domain,
                    "provider": "vercel",
                    "status": "removed",
                    "http_status": int(getattr(response, "status", 200) or 200),
                    "external_side_effects": "deleted",
                }
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                return {
                    "domain": domain,
                    "provider": "vercel",
                    "status": "not_found",
                    "http_status": 404,
                    "external_side_effects": "none",
                }
            raise TakyonError(f"Vercel domain cleanup failed for {domain}: {exc.code} {body}") from exc

    def _delete_business_domains(self, domains: list[str], *, confirm: bool) -> dict[str, Any]:
        if not confirm:
            return {"provider": "vercel", "candidates": domains, "results": []}
        results = [self._delete_vercel_project_domain(domain) for domain in domains]
        return {"provider": "vercel", "candidates": domains, "results": results}

    def _business_delete_db_counts(self, conn: sqlite3.Connection, slug: str) -> dict[str, int]:
        business = _slugify(slug)
        scope = f"business:{business}"
        scope_like = f"{scope}/%"
        counts: dict[str, int] = {}
        by_business = [
            "businesses",
            "workspaces",
            "jobs",
            "ledger_entries",
            "events",
            "conversation_threads",
            "conversation_messages",
            "app_budgets",
            "app_plan_policies",
            "app_surface_contracts",
            "app_users",
            "app_magic_links",
            "app_sessions",
            "app_entitlements",
            "app_checkout_intents",
            "app_checkout_sessions",
            "app_revenue_events",
            "app_usage_events",
        ]
        for table in by_business:
            key = "slug" if table == "businesses" else "business_slug"
            counts[table] = int(
                conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {key} = ?", (business,)).fetchone()["count"]
            )
        for table in ("jobs", "ledger_entries", "events"):
            counts[table] = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE business_slug = ? OR scope = ? OR scope LIKE ?",
                    (business, scope, scope_like),
                ).fetchone()["count"]
            )
        counts["agent_runs"] = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM agent_runs WHERE scope = ? OR scope LIKE ?",
                (scope, scope_like),
            ).fetchone()["count"]
        )
        counts["control_states"] = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM control_states WHERE scope = ? OR scope LIKE ?",
                (scope, scope_like),
            ).fetchone()["count"]
        )
        return counts

    def _delete_business_db_rows(self, conn: sqlite3.Connection, slug: str) -> dict[str, int]:
        business = _slugify(slug)
        scope = f"business:{business}"
        scope_like = f"{scope}/%"
        deleted: dict[str, int] = {}

        for table in ("agent_runs", "control_states"):
            cursor = conn.execute(f"DELETE FROM {table} WHERE scope = ? OR scope LIKE ?", (scope, scope_like))
            deleted[table] = int(cursor.rowcount or 0)
        for table in ("jobs", "ledger_entries", "events"):
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE business_slug = ? OR scope = ? OR scope LIKE ?",
                (business, scope, scope_like),
            )
            deleted[table] = int(cursor.rowcount or 0)
        cursor = conn.execute("DELETE FROM businesses WHERE slug = ?", (business,))
        deleted["businesses"] = int(cursor.rowcount or 0)
        return deleted

    def _delete_business(self, conn: sqlite3.Connection, op: dict[str, Any], *, reason: str, actor: str) -> dict[str, Any]:
        slug = _slugify(str(op.get("business_slug") or op.get("business") or ""))
        confirm = _boolish(op.get("confirm"), default=False)
        delete_files = _boolish(op.get("delete_files"), default=True)
        delete_cron = _boolish(op.get("delete_cron"), default=True)
        delete_domains = _boolish(op.get("delete_domains"), default=True)

        business = self._ensure_business(conn, slug)
        root = self._business_root(slug).resolve()
        businesses_root = (self.root / "businesses").resolve()
        if businesses_root not in (root, *root.parents):
            raise TakyonError("refusing to delete filesystem outside Takyon businesses root")

        domains = (
            _business_domain_candidates(
                slug,
                base_domain=op.get("base_domain"),
                explicit=op.get("subdomains") or op.get("domains"),
            )
            if delete_domains
            else []
        )
        filesystem = self._filesystem_summary(root)
        cron_preview = self._delete_business_crons(slug, confirm=False) if delete_cron else {"matched": [], "removed": []}
        db_counts = self._business_delete_db_counts(conn, slug)

        result: dict[str, Any] = {
            "action": "business.delete",
            "business": slug,
            "dry_run": not confirm,
            "business_record": business,
            "filesystem": filesystem,
            "cron": cron_preview,
            "domains": {"provider": "vercel", "candidates": domains, "results": []},
            "database": {"candidates": db_counts, "deleted": {}},
        }
        if not confirm:
            result["next_step"] = "rerun with confirm=true or --confirm to permanently delete"
            return result

        if domains:
            result["domains"] = self._delete_business_domains(domains, confirm=True)
        if delete_cron:
            result["cron"] = self._delete_business_crons(slug, confirm=True)
        if delete_files and root.exists():
            shutil.rmtree(root)
            result["filesystem"] = {**filesystem, "removed": True}
        elif delete_files:
            result["filesystem"] = {**filesystem, "removed": False}
        else:
            result["filesystem"] = {**filesystem, "removed": False, "skipped": True}

        deleted = self._delete_business_db_rows(conn, slug)
        result["database"] = {"candidates": db_counts, "deleted": deleted}
        self._record_event(
            conn,
            scope="global",
            business_slug=None,
            event_type="business.delete",
            payload={
                "business": slug,
                "reason": reason,
                "actor": actor,
                "filesystem": result["filesystem"],
                "cron": result["cron"],
                "domains": result["domains"],
                "database": result["database"],
            },
        )
        return result

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

            if query in {"conversation", "conversations", "conversation_threads"}:
                return {
                    "success": True,
                    "scope": scope,
                    "business": business,
                    "conversations": self._conversation_summary(conn, slug, limit),
                }

            if query in {"app", "app_runtime", "customers", "entitlements", "billing", "plans"}:
                self._rewrite_app_files(conn, slug)
                return {
                    "success": True,
                    "scope": scope,
                    "business": business,
                    "app": self._app_summary(conn, slug, limit),
                }

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
            if query in {"conversations", "summary"} or "conversations" in include_set:
                response["conversations"] = self._conversation_summary(conn, slug, limit)
            if query in {"app", "summary"} or "app" in include_set:
                self._rewrite_app_files(conn, slug)
                response["app"] = self._app_summary(conn, slug, limit)
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
            "app.budget.set",
            "app.customer.upsert",
            "app.entitlement.upsert",
            "app.plan.upsert",
            "app.surface.upsert",
            "app.usage.record",
            "artifact.patch",
            "artifact.write",
            "business.delete",
            "business.focus.set",
            "business.upsert",
            "business.mode.set",
            "conversation.message.record",
            "conversation.message.status.set",
            "conversation.thread.upsert",
            "control.set",
            "cron.ensure_ceo_wakeup",
            "event.record",
            "job.enqueue",
            "ledger.allocate",
            "maintenance.gc",
            "memory.write",
            "outreach.local_publish",
            "workspace.upsert",
        }
        if action not in allowed:
            raise TakyonError(f"unsupported operation.action: {action}")
        if action == "job.enqueue":
            kind = str(op.get("kind") or "").strip()
            if kind in _LEGACY_FIXED_STAGE_JOB_KINDS:
                raise TakyonError(f"legacy fixed-stage request kind is not allowed: {kind}")

        if action != "business.upsert" and business_slug:
            self._ensure_business(conn, business_slug)
        business_mode = "live"
        if business_slug and action != "business.upsert":
            business = self._ensure_business(conn, business_slug)
            business_mode = str(business.get("mode") or "live")
            _enforce_business_work_focus(op, str(business.get("work_focus") or "all"))
        credential_gate = _require_api_access(op, business_mode=business_mode)
        if action not in {"business.delete", "control.set"}:
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
        normalized["business_mode"] = business_mode
        normalized["credential_gate"] = credential_gate
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
            if not isinstance(metadata, dict):
                metadata = {"value": metadata}
            mode = str(op.get("mode") or "").strip().lower()
            if mode and mode not in _BUSINESS_MODES:
                raise TakyonError(f"business mode must be one of {sorted(_BUSINESS_MODES)}")
            work_focus = _normalize_work_focus(op.get("work_focus"), default=None)
            now = _now()
            existing = self._business(conn, slug)
            if existing:
                conn.execute(
                    "UPDATE businesses SET name = ?, goal = COALESCE(NULLIF(?, ''), goal), mode = COALESCE(NULLIF(?, ''), mode), work_focus = COALESCE(NULLIF(?, ''), work_focus), budget_json = COALESCE(?, budget_json), metadata_json = ?, updated_at = ? WHERE slug = ?",
                    (name, goal, mode, work_focus or "", _json_dumps(budget) if budget is not None else None, _json_dumps(metadata), now, slug),
                )
            else:
                conn.execute(
                    "INSERT INTO businesses (slug, name, goal, status, mode, work_focus, budget_json, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                    (slug, name, goal, mode or "live", work_focus or "all", _json_dumps(budget or {}), _json_dumps(metadata), now, now),
                )
            root = self._business_root(slug)
            (root / "brain").mkdir(parents=True, exist_ok=True)
            index = root / "brain" / "index.md"
            if not index.exists():
                _atomic_write_text(index, f"# {name}\n\nGoal: {goal or 'Unspecified'}\n")
            self._record_event(conn, scope=f"business:{slug}", business_slug=slug, event_type="business.upsert", payload={"reason": reason, "actor": actor})
            return {"action": action, "business": slug, "path": str(root)}

        if action == "business.focus.set":
            focus = _normalize_work_focus(op.get("work_focus") or op.get("focus"))
            now = _now()
            conn.execute("UPDATE businesses SET work_focus = ?, updated_at = ? WHERE slug = ?", (focus, now, slug))
            cron = self._refresh_business_ceo_cron_prompt(str(slug))
            self._record_event(
                conn,
                scope=f"business:{slug}",
                business_slug=slug,
                event_type=action,
                payload={"work_focus": focus, "reason": reason, "actor": actor, "cron": cron},
            )
            return {"action": action, "business": slug, "work_focus": focus, "cron": cron}

        if action == "business.mode.set":
            mode = str(op.get("mode") or "").strip().lower()
            if mode not in _BUSINESS_MODES:
                raise TakyonError(f"business mode must be one of {sorted(_BUSINESS_MODES)}")
            now = _now()
            conn.execute("UPDATE businesses SET mode = ?, updated_at = ? WHERE slug = ?", (mode, now, slug))
            self._record_event(
                conn,
                scope=f"business:{slug}",
                business_slug=slug,
                event_type=action,
                payload={"mode": mode, "reason": reason, "actor": actor},
            )
            return {"action": action, "business": slug, "mode": mode}

        if action == "business.delete":
            return self._delete_business(conn, op, reason=reason, actor=actor)

        if action == "control.set":
            state = str(op.get("state") or "").strip().lower()
            if state not in _CONTROL_STATES:
                raise TakyonError(f"control.set state must be one of {sorted(_CONTROL_STATES)}")
            control_scope = str(op.get("scope") or target_scope)
            control_parts = _scope_parts(control_scope)
            conn.execute(
                "INSERT INTO control_states (scope, state, reason, actor, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET state = excluded.state, reason = excluded.reason, actor = excluded.actor, updated_at = excluded.updated_at",
                (control_scope, state, str(op.get("reason") or reason or ""), actor, _now()),
            )
            business = control_parts.get("business")
            cron = (
                self._sync_business_ceo_cron_control(
                    business,
                    state,
                    str(op.get("reason") or reason or ""),
                )
                if business and control_parts.get("kind") == "business"
                else None
            )
            self._record_event(conn, scope=control_scope, business_slug=business, event_type="control.set", payload={"state": state, "reason": op.get("reason") or reason, "actor": actor})
            return {"action": action, "scope": control_scope, "state": state, "cron": cron}

        if action == "maintenance.gc":
            return self._gc(conn, parsed_scope, op)

        assert slug is not None

        if action == "app.budget.set":
            amount = int(float(op.get("hard_limit_microusd") or op.get("amount_microusd") or 0))
            if amount < 0:
                raise TakyonError("app budget limit must be non-negative")
            now = _now()
            current = self._ensure_app_budget(conn, slug)
            conn.execute(
                "UPDATE app_budgets SET hard_limit_microusd = ?, status = ?, updated_at = ? WHERE business_slug = ?",
                (amount, str(op.get("status") or current.get("status") or "active"), now, slug),
            )
            self._rewrite_app_files(conn, slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"hard_limit_microusd": amount, "reason": reason, "actor": actor})
            return {"action": action, "business": slug, "hard_limit_microusd": amount}

        if action == "app.surface.upsert":
            status = str(op.get("status") or "draft").strip().lower()
            if not status:
                raise TakyonError("surface status is required")
            design_brief_path = _safe_relpath(str(op.get("design_brief_path") or "product/design-brief.md"), field="design_brief_path").as_posix()
            source_path = None
            if op.get("source_path"):
                source_path = _safe_relpath(str(op.get("source_path")), field="source_path").as_posix()
            runtime_api_base = str(op.get("runtime_api_base") or f"/api/takyon/apps/{slug}").strip()
            routes = op.get("routes") if op.get("routes") is not None else []
            theme = op.get("theme") if op.get("theme") is not None else {"source": "business design brief"}
            constraints = op.get("constraints") if op.get("constraints") is not None else {}
            if not isinstance(routes, (list, dict)):
                raise TakyonError("surface routes must be an object or list")
            if not isinstance(theme, dict):
                raise TakyonError("surface theme must be an object")
            if not isinstance(constraints, dict):
                raise TakyonError("surface constraints must be an object")
            constraints = {
                **constraints,
                "no_hardcoded_product_ui": True,
                "backend_runtime_only": True,
            }
            metadata = op.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {"value": metadata}
            status, metadata = self._surface_status_for_upsert(conn, slug, status, source_path, metadata)
            now = _now()
            conn.execute(
                """
                INSERT INTO app_surface_contracts (
                  business_slug, status, design_brief_path, source_path, runtime_api_base,
                  routes_json, theme_json, constraints_json, notes, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_slug) DO UPDATE SET
                  status = excluded.status,
                  design_brief_path = excluded.design_brief_path,
                  source_path = excluded.source_path,
                  runtime_api_base = excluded.runtime_api_base,
                  routes_json = excluded.routes_json,
                  theme_json = excluded.theme_json,
                  constraints_json = excluded.constraints_json,
                  notes = excluded.notes,
                  metadata_json = excluded.metadata_json,
                  updated_at = excluded.updated_at
                """,
                (
                    slug,
                    status,
                    design_brief_path,
                    source_path,
                    runtime_api_base,
                    _json_dumps(routes),
                    _json_dumps(theme),
                    _json_dumps(constraints),
                    str(op.get("notes") or ""),
                    _json_dumps(metadata),
                    now,
                    now,
                ),
            )
            self._rewrite_app_files(conn, slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"status": status, "design_brief_path": design_brief_path, "source_path": source_path, "metadata": metadata})
            return {"action": action, "business": slug, "status": status, "surface_contract": "app/surface.md"}

        if action == "app.plan.upsert":
            plan_key = _file_slug(str(op.get("plan_key") or "free"), "free")
            tier = str(op.get("tier") or plan_key or "free")
            price_cents = int(float(op.get("price_cents") or op.get("price_usd_cents") or 0))
            if price_cents < 0:
                raise TakyonError("plan price must be non-negative")
            interval = _normalize_billing_interval(op.get("billing_interval") or "month")
            if interval not in {"month", "year", "one_time"}:
                raise TakyonError("billing_interval must be one of: month, year, one_time")
            included_action_quota = int(op.get("included_action_quota") or 25)
            allow_overage = bool(op.get("allow_overage"))
            metadata = op.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {"value": metadata}
            warnings = _plan_validation_warnings(plan_key, tier, included_action_quota, allow_overage, metadata)
            if warnings:
                validation = metadata.get("takyon_plan_validation") if isinstance(metadata.get("takyon_plan_validation"), dict) else {}
                metadata = {
                    **metadata,
                    "takyon_plan_validation": {
                        **validation,
                        "status": "warning",
                        "warnings": [*validation.get("warnings", []), *warnings] if isinstance(validation.get("warnings"), list) else warnings,
                    },
                }
            now = _now()
            plan_id = op.get("id") or uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO app_plan_policies (
                  id, business_slug, plan_key, tier, price_cents, currency, billing_interval,
                  included_ai_budget_microusd, included_action_quota, allow_overage,
                  stripe_product_id, stripe_price_id, stripe_payment_link_id, stripe_payment_link_url,
                  source, notes, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_slug, plan_key) DO UPDATE SET
                  tier = excluded.tier,
                  price_cents = excluded.price_cents,
                  currency = excluded.currency,
                  billing_interval = excluded.billing_interval,
                  included_ai_budget_microusd = excluded.included_ai_budget_microusd,
                  included_action_quota = excluded.included_action_quota,
                  allow_overage = excluded.allow_overage,
                  stripe_product_id = COALESCE(excluded.stripe_product_id, app_plan_policies.stripe_product_id),
                  stripe_price_id = COALESCE(excluded.stripe_price_id, app_plan_policies.stripe_price_id),
                  stripe_payment_link_id = COALESCE(excluded.stripe_payment_link_id, app_plan_policies.stripe_payment_link_id),
                  stripe_payment_link_url = COALESCE(excluded.stripe_payment_link_url, app_plan_policies.stripe_payment_link_url),
                  source = excluded.source,
                  notes = excluded.notes,
                  metadata_json = excluded.metadata_json,
                  updated_at = excluded.updated_at
                """,
                (
                    plan_id,
                    slug,
                    plan_key,
                    tier,
                    price_cents,
                    str(op.get("currency") or "usd").lower(),
                    interval,
                    int(float(op.get("included_ai_budget_microusd") or 0)),
                    included_action_quota,
                    1 if allow_overage else 0,
                    op.get("stripe_product_id"),
                    op.get("stripe_price_id"),
                    op.get("stripe_payment_link_id"),
                    op.get("stripe_payment_link_url"),
                    str(op.get("source") or "takyon"),
                    str(op.get("notes") or ""),
                    _json_dumps(metadata),
                    now,
                    now,
                ),
            )
            self._rewrite_app_files(conn, slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"plan_key": plan_key, "price_cents": price_cents})
            return {"action": action, "business": slug, "plan_key": plan_key}

        if action == "app.customer.upsert":
            email = _normalize_email(str(op.get("email") or ""))
            now = _now()
            user_id = op.get("id") or uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO app_users (id, business_slug, email, name, status, tier, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_slug, email) DO UPDATE SET
                  name = COALESCE(excluded.name, app_users.name),
                  status = excluded.status,
                  tier = COALESCE(NULLIF(excluded.tier, ''), app_users.tier),
                  metadata_json = excluded.metadata_json,
                  updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    slug,
                    email,
                    op.get("name"),
                    str(op.get("status") or "active"),
                    str(op.get("tier") or "free"),
                    _json_dumps(op.get("metadata") or {}),
                    now,
                    now,
                ),
            )
            row = self._row_to_dict(conn.execute("SELECT * FROM app_users WHERE business_slug = ? AND email = ?", (slug, email)).fetchone())
            self._rewrite_app_files(conn, slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"app_user_id": row["id"], "email": email})
            return {"action": action, "business": slug, "app_user_id": row["id"], "email": email}

        if action == "app.entitlement.upsert":
            user_id = str(op.get("app_user_id") or "")
            if not user_id and op.get("email"):
                email = _normalize_email(str(op.get("email")))
                user_result = self._apply_operation(
                    conn,
                    parsed_scope,
                    {
                        "action": "app.customer.upsert",
                        "business_slug": slug,
                        "target_scope": target_scope,
                        "email": email,
                        "tier": op.get("tier") or "free",
                        "metadata": {"source": "entitlement_upsert"},
                    },
                    reason=reason,
                    actor=actor,
                )
                user_id = str(user_result["app_user_id"])
            if not user_id:
                raise TakyonError("app entitlement requires app_user_id or email")
            if not conn.execute("SELECT 1 FROM app_users WHERE business_slug = ? AND id = ?", (slug, user_id)).fetchone():
                raise TakyonError(f"app user not found: {user_id}")
            now = _now()
            entitlement_id = op.get("id") or uuid.uuid4().hex
            tier_value = str(op.get("tier") or "free")
            source_value = str(op.get("source") or "manual")
            metadata = op.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {"value": metadata}
            has_stripe_evidence = bool(op.get("stripe_customer_id") or op.get("stripe_subscription_id") or op.get("stripe_checkout_session_id"))
            explicit_non_billing = bool(metadata.get("non_billing") or source_value in {"internal", "owner", "comp", "test"})
            if tier_value not in {"", "free"} and source_value == "manual" and not has_stripe_evidence and not explicit_non_billing:
                raise TakyonError(
                    "manual paid entitlement would fake billing state; use Stripe/webhook evidence or an explicit non-billing source"
                )
            conn.execute(
                """
                INSERT INTO app_entitlements (
                  id, business_slug, app_user_id, tier, status, source,
                  stripe_customer_id, stripe_subscription_id, stripe_checkout_session_id,
                  plan_key, current_period_end, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entitlement_id,
                    slug,
                    user_id,
                    tier_value,
                    str(op.get("status") or "active"),
                    source_value,
                    op.get("stripe_customer_id"),
                    op.get("stripe_subscription_id"),
                    op.get("stripe_checkout_session_id"),
                    op.get("plan_key"),
                    op.get("current_period_end"),
                    _json_dumps(metadata),
                    now,
                    now,
                ),
            )
            tier = self._sync_user_tier(conn, slug, user_id)
            self._rewrite_app_files(conn, slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"app_user_id": user_id, "tier": tier, "source": source_value})
            return {"action": action, "business": slug, "app_user_id": user_id, "entitlement": entitlement_id, "tier": tier}

        if action == "app.usage.record":
            app_user_id = op.get("app_user_id")
            if app_user_id and not conn.execute("SELECT 1 FROM app_users WHERE business_slug = ? AND id = ?", (slug, app_user_id)).fetchone():
                raise TakyonError(f"app user not found: {app_user_id}")
            budget = self._ensure_app_budget(conn, slug)
            actual = int(float(op.get("actual_cost_microusd") or 0))
            estimated = int(float(op.get("estimated_cost_microusd") or actual or 0))
            if actual < 0 or estimated < 0:
                raise TakyonError("usage costs must be non-negative")
            used = conn.execute(
                "SELECT COALESCE(SUM(actual_cost_microusd), 0) AS total FROM app_usage_events WHERE business_slug = ? AND created_at >= ?",
                (slug, budget["current_period_start"]),
            ).fetchone()["total"]
            if int(used or 0) + actual > int(budget["hard_limit_microusd"] or 0):
                raise TakyonError(f"app usage would exceed budget cap {budget['hard_limit_microusd']} microusd")
            event_id = op.get("id") or uuid.uuid4().hex
            now = _now()
            conn.execute(
                """
                INSERT INTO app_usage_events (
                  id, business_slug, app_user_id, app_user_tier, purpose, route, status,
                  estimated_cost_microusd, actual_cost_microusd, input_tokens, output_tokens,
                  provider_request_id, provider, model, metadata_json, error, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    slug,
                    app_user_id,
                    op.get("app_user_tier"),
                    str(op.get("purpose") or "product_usage"),
                    str(op.get("route") or "app"),
                    str(op.get("status") or "completed"),
                    estimated,
                    actual,
                    op.get("input_tokens"),
                    op.get("output_tokens"),
                    op.get("provider_request_id"),
                    op.get("provider"),
                    op.get("model"),
                    _json_dumps(op.get("metadata") or {}),
                    op.get("error"),
                    now,
                    op.get("completed_at") or now,
                ),
            )
            self._rewrite_app_files(conn, slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"usage_event": event_id, "actual_cost_microusd": actual})
            return {"action": action, "business": slug, "usage_event": event_id, "actual_cost_microusd": actual}

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
            rel = str(file_path.relative_to(self._business_root(slug)))
            _validate_brain_index_completion_gate(rel, content)
            _atomic_write_text(file_path, content)
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"path": rel, "reason": reason, "actor": actor})
            if rel == "brain/pulse.md":
                self._record_event(
                    conn,
                    scope=f"business:{slug}",
                    business_slug=slug,
                    event_type="business.pulse.snapshot",
                    payload={
                        "generated_at": _now(),
                        "pulse_path": rel,
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "source": "brain/pulse.md write",
                    },
                )
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
            updated_content = content.replace(old, new, 1)
            rel = str(file_path.relative_to(self._business_root(slug)))
            _validate_brain_index_completion_gate(rel, updated_content)
            _atomic_write_text(file_path, updated_content)
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"path": rel, "reason": reason, "actor": actor})
            if rel == "brain/pulse.md":
                self._record_event(
                    conn,
                    scope=f"business:{slug}",
                    business_slug=slug,
                    event_type="business.pulse.snapshot",
                    payload={
                        "generated_at": _now(),
                        "pulse_path": rel,
                        "content_sha256": hashlib.sha256(updated_content.encode("utf-8")).hexdigest(),
                        "source": "brain/pulse.md patch",
                    },
                )
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
            payload = dict(op.get("payload") or {})
            credential_gate = op.get("credential_gate") or {}
            suppressed = credential_gate.get("missing_credentials_suppressed") or []
            if suppressed:
                payload.setdefault("business_mode", op.get("business_mode") or "test")
                payload.setdefault("external_side_effects", "suppressed")
                payload.setdefault("missing_credentials_suppressed", suppressed)
                payload.setdefault("test_mode_note", credential_gate.get("note") or "Recorded locally in test mode.")
            conn.execute(
                "INSERT INTO jobs (id, scope, business_slug, kind, status, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, target_scope, slug, str(op.get("kind") or "job"), str(op.get("status") or "queued"), _json_dumps(payload), _now(), _now()),
            )
            event_payload = {"job_id": job_id, "kind": op.get("kind"), "reason": reason}
            if suppressed:
                event_payload["missing_credentials_suppressed"] = suppressed
                event_payload["external_side_effects"] = "suppressed"
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload=event_payload)
            result = {"action": action, "business": slug, "job": job_id}
            if suppressed:
                result["missing_credentials_suppressed"] = suppressed
                result["external_side_effects"] = "suppressed"
            return result

        if action == "outreach.local_publish":
            business = self._ensure_business(conn, slug)
            if str(business.get("mode") or "live") != "test":
                raise TakyonError("outreach.local_publish requires business mode 'test'")
            body = str(op.get("body") or "").strip()
            if not body:
                raise TakyonError("outreach.local_publish body is required")
            channel = _file_slug(str(op.get("channel") or op.get("provider") or "outreach"), "outreach")
            target = str(op.get("target") or op.get("recipient") or "local-target").strip() or "local-target"
            subject = str(op.get("subject") or op.get("title") or f"Test outreach to {target}").strip()
            provider = str(op.get("provider") or channel).strip()
            metadata = op.get("metadata") or {}
            publish_id = str(op.get("id") or uuid.uuid4().hex)
            created_at = _now()
            file_stem = f"{created_at[:10]}-{_file_slug(target, 'target')}-{publish_id[:8]}"
            rel = f"outreach/local-published/{channel}/{file_stem}.md"
            receipt_rel = f"receipts/outreach/{publish_id}.json"
            file_lines = [
                f"# {subject}",
                "",
                f"- Business: {slug}",
                "- Mode: test",
                f"- Channel: {channel}",
                f"- Provider: {provider}",
                f"- Target: {target}",
                f"- Local publish id: {publish_id}",
                f"- Created at: {created_at}",
                "- External side effects: suppressed",
                "",
                "## Body",
                "",
                body,
                "",
            ]
            _atomic_write_text(self._business_root(slug) / rel, "\n".join(file_lines).rstrip() + "\n")
            receipt = {
                "id": publish_id,
                "business": slug,
                "mode": "test",
                "channel": channel,
                "provider": provider,
                "target": target,
                "subject": subject,
                "artifact_path": rel,
                "external_side_effects": "suppressed",
                "sent": False,
                "created_at": created_at,
                "metadata": metadata,
            }
            _atomic_write_text(self._business_root(slug) / receipt_rel, _json_dumps(receipt) + "\n")

            source = _file_slug(f"test-{channel}", "test-outreach")
            thread_external_id = str(op.get("thread_external_id") or f"{source}:{_file_slug(target, 'target')}")
            now = created_at
            thread_id = str(op.get("thread_id") or uuid.uuid4().hex)
            existing_message = conn.execute(
                "SELECT 1 FROM conversation_messages WHERE business_slug = ? AND source = ? AND external_id = ?",
                (slug, source, str(op.get("external_id") or f"{publish_id}:local-outbound")),
            ).fetchone()
            conn.execute(
                "INSERT INTO conversation_threads (id, business_slug, source, external_id, title, url, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?) "
                "ON CONFLICT(business_slug, source, external_id) DO UPDATE SET title = excluded.title, url = COALESCE(excluded.url, conversation_threads.url), status = 'active', updated_at = excluded.updated_at",
                (thread_id, slug, source, thread_external_id, subject, rel, now, now),
            )
            thread = self._row_to_dict(conn.execute(
                "SELECT * FROM conversation_threads WHERE business_slug = ? AND source = ? AND external_id = ?",
                (slug, source, thread_external_id),
            ).fetchone())
            message_id = str(op.get("message_id") or uuid.uuid4().hex)
            message_external_id = str(op.get("external_id") or f"{publish_id}:local-outbound")
            conn.execute(
                "INSERT INTO conversation_messages (id, business_slug, thread_id, source, external_id, direction, author_label, body, status, received_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'outbound', 'Takyon local publish', ?, 'responded', ?, ?, ?) "
                "ON CONFLICT(business_slug, source, external_id) DO UPDATE SET body = excluded.body, status = excluded.status, updated_at = excluded.updated_at",
                (message_id, slug, thread["id"], source, message_external_id, body, now, now, now),
            )
            row = self._row_to_dict(conn.execute(
                "SELECT * FROM conversation_messages WHERE business_slug = ? AND source = ? AND external_id = ?",
                (slug, source, message_external_id),
            ).fetchone())
            mirror = self._rewrite_conversation_thread_file(conn, slug, str(thread["id"]))
            corpus = None
            if not existing_message:
                corpus = self._append_conversation_message_corpus(slug, thread, row)
            self._append_conversation_event_corpus(slug, action, {"receipt": receipt_rel, "thread": thread["id"], "message": row["id"]})
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload=receipt)
            return {
                "action": action,
                "business": slug,
                "mode": "test",
                "local_publish_id": publish_id,
                "artifact": rel,
                "receipt": receipt_rel,
                "thread": thread["id"],
                "message": row["id"],
                "conversation_file": mirror,
                "conversation_corpus": corpus or "conversations/corpus/messages.jsonl",
                "external_side_effects": "suppressed",
                "sent": False,
            }

        if action == "conversation.thread.upsert":
            source = _file_slug(str(op.get("source") or "unknown"), "unknown")
            title = str(op.get("title") or op.get("external_id") or source).strip() or source
            external_id = str(op.get("external_id") or title).strip()
            status = str(op.get("status") or "active").strip().lower()
            if status not in {"active", "paused", "archived"}:
                raise TakyonError("conversation thread status must be active, paused, or archived")
            thread_id = str(op.get("id") or uuid.uuid4().hex)
            now = _now()
            conn.execute(
                "INSERT INTO conversation_threads (id, business_slug, source, external_id, title, url, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(business_slug, source, external_id) DO UPDATE SET title = excluded.title, url = COALESCE(excluded.url, conversation_threads.url), status = excluded.status, updated_at = excluded.updated_at",
                (thread_id, slug, source, external_id, title, op.get("url"), status, now, now),
            )
            row = self._row_to_dict(conn.execute(
                "SELECT * FROM conversation_threads WHERE business_slug = ? AND source = ? AND external_id = ?",
                (slug, source, external_id),
            ).fetchone())
            file_path = self._rewrite_conversation_thread_file(conn, slug, str(row["id"]))
            self._append_conversation_event_corpus(slug, action, {"thread": row["id"], "source": source, "external_id": external_id, "status": row["status"]})
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"thread": row["id"], "source": source})
            return {"action": action, "business": slug, "thread": row["id"], "file": file_path}

        if action == "conversation.message.record":
            source = _file_slug(str(op.get("source") or "unknown"), "unknown")
            direction = str(op.get("direction") or "inbound").strip().lower()
            if direction not in {"inbound", "outbound", "internal"}:
                raise TakyonError("conversation message direction must be inbound, outbound, or internal")
            status = str(op.get("status") or ("needs_response" if direction == "inbound" else "responded")).strip().lower()
            if status not in {"needs_response", "responded", "ignored", "archived"}:
                raise TakyonError("conversation message status must be needs_response, responded, ignored, or archived")
            thread_external_id = str(op.get("thread_external_id") or op.get("thread_id") or op.get("thread_title") or "thread").strip()
            thread_title = str(op.get("thread_title") or thread_external_id).strip() or thread_external_id
            now = _now()
            thread = None
            if op.get("thread_id"):
                thread = self._row_to_dict(conn.execute(
                    "SELECT * FROM conversation_threads WHERE business_slug = ? AND id = ?",
                    (slug, str(op.get("thread_id"))),
                ).fetchone())
            if not thread:
                thread = self._row_to_dict(conn.execute(
                    "SELECT * FROM conversation_threads WHERE business_slug = ? AND source = ? AND external_id = ?",
                    (slug, source, thread_external_id),
                ).fetchone())
            if not thread:
                thread_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO conversation_threads (id, business_slug, source, external_id, title, url, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                    (thread_id, slug, source, thread_external_id, thread_title, op.get("url"), now, now),
                )
                thread = self._row_to_dict(conn.execute(
                    "SELECT * FROM conversation_threads WHERE id = ?",
                    (thread_id,),
                ).fetchone())
            message_external_id = str(op.get("external_id") or f"{thread['id']}:{direction}:{op.get('received_at') or now}:{str(op.get('body') or '')[:80]}").strip()
            message_id = str(op.get("id") or uuid.uuid4().hex)
            received_at = str(op.get("received_at") or now)
            existing_message = conn.execute(
                "SELECT 1 FROM conversation_messages WHERE business_slug = ? AND source = ? AND external_id = ?",
                (slug, source, message_external_id),
            ).fetchone()
            conn.execute(
                "INSERT INTO conversation_messages (id, business_slug, thread_id, source, external_id, direction, author_label, body, status, received_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(business_slug, source, external_id) DO UPDATE SET body = excluded.body, status = CASE WHEN conversation_messages.status = 'responded' THEN conversation_messages.status ELSE excluded.status END, updated_at = excluded.updated_at",
                (message_id, slug, thread["id"], source, message_external_id, direction, str(op.get("author_label") or direction), str(op.get("body") or ""), status, received_at, now, now),
            )
            conn.execute("UPDATE conversation_threads SET updated_at = ? WHERE id = ?", (now, thread["id"]))
            row = self._row_to_dict(conn.execute(
                "SELECT * FROM conversation_messages WHERE business_slug = ? AND source = ? AND external_id = ?",
                (slug, source, message_external_id),
            ).fetchone())
            file_path = self._rewrite_conversation_thread_file(conn, slug, str(thread["id"]))
            corpus = None
            if not existing_message:
                corpus = self._append_conversation_message_corpus(slug, thread, row)
            self._append_conversation_event_corpus(slug, action, {"thread": thread["id"], "message": row["id"], "direction": direction, "status": row["status"]})
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"thread": thread["id"], "message": row["id"], "direction": direction, "status": row["status"]})
            return {"action": action, "business": slug, "thread": thread["id"], "message": row["id"], "file": file_path, "status": row["status"], "conversation_corpus": corpus or "conversations/corpus/messages.jsonl"}

        if action == "conversation.message.status.set":
            status = str(op.get("status") or "").strip().lower()
            if status not in {"needs_response", "responded", "ignored", "archived"}:
                raise TakyonError("conversation message status must be needs_response, responded, ignored, or archived")
            message = None
            if op.get("message_id"):
                message = self._row_to_dict(conn.execute(
                    "SELECT * FROM conversation_messages WHERE business_slug = ? AND id = ?",
                    (slug, str(op.get("message_id"))),
                ).fetchone())
            if not message:
                source = _file_slug(str(op.get("source") or "unknown"), "unknown")
                external_id = str(op.get("external_id") or "").strip()
                if not external_id:
                    raise TakyonError("conversation status update requires message_id or source/external_id")
                message = self._row_to_dict(conn.execute(
                    "SELECT * FROM conversation_messages WHERE business_slug = ? AND source = ? AND external_id = ?",
                    (slug, source, external_id),
                ).fetchone())
            if not message:
                raise TakyonError("conversation message not found")
            now = _now()
            conn.execute("UPDATE conversation_messages SET status = ?, updated_at = ? WHERE business_slug = ? AND id = ?", (status, now, slug, message["id"]))
            row = self._row_to_dict(conn.execute("SELECT * FROM conversation_messages WHERE business_slug = ? AND id = ?", (slug, message["id"])).fetchone())
            file_path = self._rewrite_conversation_thread_file(conn, slug, str(row["thread_id"]))
            self._append_conversation_event_corpus(slug, action, {"message": row["id"], "status": status, "reason": reason, "actor": actor})
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"message": row["id"], "status": status, "reason": reason, "actor": actor})
            return {"action": action, "business": slug, "message": row["id"], "status": status, "file": file_path}

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

    def _ceo_cron_prompt(self, slug: str) -> str:
        return (
            f"CEO wakeup for business:{slug}.\n"
            "Start with business_calculate_pulse, then use takyon:business-pulse to write brain/pulse.md and record "
            "a business.pulse.snapshot event. Use concrete business_* tools to read state, update business memory, "
            "create workspaces, enqueue jobs, allocate budget, and adjust the next wakeup if useful. Decide the highest "
            "expected-impact move under the business goal, budget, evidence, active campaigns, failures, and kill switches. Keep all business "
            "memory inside this business scope. Read prior wake/traction notes from brain/wake_journal.md and compare "
            "this state to those notes, including business "
            "age, app/customer/revenue/usage signals, conversations, job progress, blockers, and stale assumptions. "
            "After reading business state, honor the business work_focus field as an operator constraint: "
            "marketing means choose only marketing, demand, research, outreach, pricing, conversion, campaign, or sales work; "
            "product means choose only product, offer, app runtime, checkout, surface, build, verification, or product-support work; "
            "all means no focus restriction. Safety/control reads, pulse, blocker recording, and changing the focus are always allowed. "
            "Use first-class business tools for requested videos/images, local outreach publication, websites, deploys, checkout, provider calls, and other concrete artifacts; if a gate is missing, report the gate instead of substituting a Markdown brief. "
            "Do not narrate private setup with phrases like 'Good, I have the full business context' or 'Now I will'. "
            "Think holistically about whether the business or current strategy has gotten stale from wake cadence, "
            "elapsed time, and traction movement; if stale, make a drastic strategic change instead of continuing "
            "the same motion. "
            "Append a compact wake snapshot to brain/wake_journal.md for future comparison. Never delete prior pulse, "
            "metric, event, conversation, ledger, job, or wake data during a wake. "
            "Honor business mode: in test mode, keep product/website build and "
            "publication, app rails, receipts, conversations, and follow-up review active. Suppress external outreach, "
            "acquisition, paid spend, customer charging, and outreach/marketing email delivery."
        )

    def _ceo_cron_toolsets(self) -> list[str]:
        return ["takyon", "web", "skills", "todo", "delegation"]

    def _refresh_business_ceo_cron_prompt(self, slug: str) -> dict[str, Any]:
        from cron.jobs import list_jobs, update_job

        name = f"takyon-ceo:{slug}"
        existing = next((job for job in list_jobs(include_disabled=True) if job.get("name") == name), None)
        if not existing:
            return {"updated": False, "reason": "no_existing_ceo_cron"}
        updated = update_job(
            existing["id"],
            {
                "prompt": self._ceo_cron_prompt(slug),
                "skills": ["takyon:ceo"],
                "enabled_toolsets": self._ceo_cron_toolsets(),
            },
        )
        return {
            "updated": bool(updated),
            "cron_job": existing["id"],
            "schedule": (updated or existing).get("schedule_display"),
        }

    def _ensure_ceo_cron(self, slug: str, *, schedule: str, reason: str) -> dict[str, Any]:
        blocker: dict[str, Any] | None
        with self._connect() as conn:
            blocker = self._control_blocker(conn, f"business:{slug}")
        if blocker:
            raise TakyonError(f"cannot schedule CEO wakeup; business:{slug} is {blocker['state']}")

        from cron.jobs import create_job, list_jobs, update_job

        name = f"takyon-ceo:{slug}"
        prompt = self._ceo_cron_prompt(slug)
        enabled_toolsets = self._ceo_cron_toolsets()
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


_CREATIVE_ASSET_KINDS = {"video": "mp4", "image": "png"}
_CREATIVE_ASSET_CHANNELS = {"meta", "tiktok", "x", "linkedin"}
_CREATIVE_ASSET_FORMATS = {"ugc"}


def _normalize_creative_asset_choice(value: Any, allowed: set[str], *, field: str) -> str:
    clean = str(value or "").strip().lower().replace("_", "-")
    if clean not in allowed:
        raise TakyonError(f"{field} must be one of {sorted(allowed)}")
    return clean


def _creative_asset_prompt(args: dict[str, Any]) -> str:
    prompt = str(args.get("prompt") or args.get("generation_prompt") or "").strip()
    if prompt:
        return prompt
    parts: list[str] = []
    script = str(args.get("script") or "").strip()
    if script:
        parts.extend(["Script:", script])
    shot_list = args.get("shot_list") or args.get("shots")
    if isinstance(shot_list, str):
        shot_text = shot_list.strip()
    elif isinstance(shot_list, (list, tuple)):
        shot_text = "\n".join(f"- {str(item).strip()}" for item in shot_list if str(item).strip())
    else:
        shot_text = ""
    if shot_text:
        parts.extend(["Shot list:", shot_text])
    return "\n\n".join(parts).strip()


def _creative_asset_relpath(
    *,
    args: dict[str, Any],
    kind: str,
    channel: str,
    format_name: str,
    asset_id: str,
) -> str:
    default_ext = _CREATIVE_ASSET_KINDS[kind]
    output_path = str(args.get("output_path") or "").strip()
    if output_path:
        rel = _safe_relpath(output_path, field="output_path").as_posix()
        if Path(rel).suffix:
            return rel
        return f"{rel}.{default_ext}"
    campaign = _file_slug(str(args.get("campaign") or "default"), "default")
    return f"campaigns/{campaign}/creatives/{channel}-{format_name}/{asset_id}.{default_ext}"


def _creative_asset_source_bytes(source: str) -> bytes:
    raw = str(source or "").strip()
    if not raw:
        raise TakyonError("provider result did not include a generated asset path or URL")
    if raw.startswith("data:"):
        try:
            _, payload = raw.split(",", 1)
            return base64.b64decode(payload)
        except Exception as exc:
            raise TakyonError("provider returned an invalid data URL") from exc
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        request = urllib.request.Request(raw, headers={"User-Agent": "Takyon/creative-asset"})
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    path = Path(raw).expanduser()
    if not path.exists() or not path.is_file():
        raise TakyonError(f"provider asset file not found: {raw}")
    return path.read_bytes()


_CREATIVE_ASSET_ERROR_TYPES = {
    "api_key_missing",
    "budget_missing",
    "model_error",
    "provider_not_registered",
    "provider_unavailable",
}


def _creative_asset_error_type(message: str) -> str:
    prefix = str(message or "").split(":", 1)[0].strip()
    return prefix if prefix in _CREATIVE_ASSET_ERROR_TYPES else "creative_asset_error"


def _provider_failure_message(result: dict[str, Any], *, kind: str, expected_provider: str = "") -> str:
    error_type = str(result.get("error_type") or "provider_error").strip() or "provider_error"
    provider = str(result.get("provider") or expected_provider or "").strip()
    model = str(result.get("model") or "").strip()
    message = str(result.get("error") or f"{kind} generation failed").strip()
    if error_type in {"provider_not_registered", "no_provider_configured"}:
        label = "provider_not_registered"
    elif error_type in {"auth_required", "missing_api_key", "missing_env"}:
        label = "api_key_missing"
    elif error_type in {"provider_unavailable"}:
        label = "provider_unavailable"
    else:
        label = "model_error"
    parts = [f"{label}: {kind} generation failed"]
    if provider:
        parts.append(f"provider={provider}")
    if model:
        parts.append(f"model={model}")
    parts.append(message)
    return "; ".join(parts)


def _parse_provider_result(raw: str, *, kind: str, expected_provider: str = "") -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except Exception as exc:
        raise TakyonError(f"{kind} generation returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise TakyonError(f"{kind} generation returned a non-object result")
    if not result.get("success"):
        raise TakyonError(_provider_failure_message(result, kind=kind, expected_provider=expected_provider))
    asset_value = result.get(kind) or result.get("url") or result.get("path")
    if not asset_value:
        raise TakyonError(f"{kind} generation result did not include {kind}, url, or path")
    result["_asset_source"] = str(asset_value)
    return result


def _stripe_request(path: str, params: dict[str, Any]) -> dict[str, Any]:
    load_takyon_env()
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        raise TakyonError("Stripe action requires STRIPE_SECRET_KEY")
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.stripe.com/v1/{path.lstrip('/')}",
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TakyonError(f"Stripe {path} failed: {exc.code} {body}") from exc


def _verify_stripe_signature(raw_body: str, signature: str, secret: str) -> None:
    parts: dict[str, list[str]] = {}
    for part in str(signature or "").split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        parts.setdefault(key, []).append(value)
    timestamp = parts.get("t", [""])[0]
    signatures = parts.get("v1", [])
    if not timestamp or not signatures:
        raise TakyonError("invalid Stripe signature header")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            raise TakyonError("Stripe signature timestamp is outside tolerance")
    except ValueError as exc:
        raise TakyonError("invalid Stripe signature timestamp") from exc
    expected = hmac.new(secret.encode("utf-8"), f"{timestamp}.{raw_body}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise TakyonError("Stripe signature verification failed")


def _stripe_object_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _subscription_entitlement_status(status: str) -> str:
    if status in {"active", "trialing"}:
        return "active"
    if status in {"canceled", "cancelled"}:
        return "cancelled"
    return "past_due"


def _postmark_magic_link(email: str, product_name: str, link: str) -> str | None:
    load_takyon_env()
    token = os.getenv("POSTMARK_SERVER_TOKEN")
    from_email = os.getenv("POSTMARK_FROM_EMAIL")
    if not token or not from_email:
        raise TakyonError("magic-link email requires POSTMARK_SERVER_TOKEN and POSTMARK_FROM_EMAIL")
    payload = {
        "From": from_email,
        "To": email,
        "Subject": f"Sign in to {product_name}",
        "TextBody": f"Use this secure link to sign in to {product_name}:\n\n{link}\n\nThis link expires in 15 minutes and can be used once.",
        "HtmlBody": f"<p>Use this secure link to sign in to {product_name}:</p><p><a href=\"{link}\">Sign in to {product_name}</a></p><p>This link expires in 15 minutes and can be used once.</p>",
    }
    request = urllib.request.Request(
        "https://api.postmarkapp.com/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-Postmark-Server-Token": token, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
            return body.get("MessageID")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TakyonError(f"Postmark magic link failed: {exc.code} {body}") from exc


def _ensure_stripe_price(conn: sqlite3.Connection, slug: str, plan: dict[str, Any], business_name: str) -> dict[str, Any]:
    if plan.get("stripe_price_id"):
        return plan
    if int(plan.get("price_cents") or 0) <= 0:
        raise TakyonError("paid checkout requires a plan with price_cents > 0")
    metadata = {"business": slug, "plan_key": plan["plan_key"], "source": "takyon_app"}
    product = _stripe_request("products", {
        "name": f"{business_name} {plan['plan_key']}",
        "metadata[business]": slug,
        "metadata[plan_key]": plan["plan_key"],
        "metadata[source]": metadata["source"],
    })
    price_params: dict[str, Any] = {
        "product": product["id"],
        "currency": plan.get("currency") or "usd",
        "unit_amount": int(plan.get("price_cents") or 0),
        "metadata[business]": slug,
        "metadata[plan_key]": plan["plan_key"],
        "metadata[source]": metadata["source"],
    }
    if plan.get("billing_interval") != "one_time":
        price_params["recurring[interval]"] = "year" if plan.get("billing_interval") == "year" else "month"
    price = _stripe_request("prices", price_params)
    conn.execute(
        "UPDATE app_plan_policies SET stripe_product_id = ?, stripe_price_id = ?, updated_at = ? WHERE business_slug = ? AND plan_key = ?",
        (product["id"], price["id"], _now(), slug, plan["plan_key"]),
    )
    plan["stripe_product_id"] = product["id"]
    plan["stripe_price_id"] = price["id"]
    return plan


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


def handle_business_calculate_pulse(args: dict, **_: Any) -> str:
    try:
        return tool_result(_store().calculate_pulse(str(args.get("business") or ""), limit=args.get("limit") or 10))
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_check_runtime_capabilities(args: dict, **_: Any) -> str:
    try:
        requested = [
            str(item).strip()
            for item in _as_list(args.get("capabilities") or args.get("commands"))
            if str(item).strip()
        ]
        ecosystems = [
            str(item).strip().lower()
            for item in _as_list(args.get("ecosystems") or args.get("ecosystem") or args.get("ensure"))
            if str(item).strip()
        ]
        if not requested:
            requested = ["node", "npm", "npx", "corepack", "pnpm", "yarn", "bun", "python", "pip", "uv", "git", "rg"]

        ensure_results: list[dict[str, Any]] = []
        for ecosystem in ecosystems:
            if ecosystem in {"javascript", "js", "node"}:
                ensure_results.append({"ecosystem": ecosystem, **_ensure_javascript_runtime(package_manager=False)})
            elif ecosystem in {"javascript-package-manager", "package-manager", "package_manager", "node-package-manager"}:
                ensure_results.append({"ecosystem": ecosystem, **_ensure_javascript_runtime(package_manager=True)})
            elif ecosystem in {"python", "py"}:
                ensure_results.append({
                    "ecosystem": ecosystem,
                    "success": bool(_resolve_runtime_executable("python")),
                    "installed": False,
                    "capabilities": _runtime_capabilities(("python", "pip", "uv")),
                    "error": None if _resolve_runtime_executable("python") else "python runtime is unavailable",
                })
            else:
                ensure_results.append({
                    "ecosystem": ecosystem,
                    "success": False,
                    "installed": False,
                    "error": "unknown ecosystem; inspect explicit capabilities instead",
                })

        capabilities = _runtime_capabilities(requested)
        missing = [name for name, info in capabilities.items() if not info.get("available")]
        return tool_result({
            "success": True,
            "capabilities": capabilities,
            "missing_capabilities": missing,
            "ensure": ensure_results,
            "runtime_installs_allowed": _allow_runtime_installs(),
            "note": (
                "Capability results are evidence for the CEO. Missing runtimes or package managers "
                "should be repaired, provisioned, or recorded as exact blockers; they are not product strategy."
            ),
        })
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
        "mode": args.get("mode"),
        "work_focus": args.get("work_focus") or args.get("focus"),
        "budget": args.get("budget"),
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation, scope=f"business:{args.get('business')}")


def handle_business_delete_business(args: dict, **_: Any) -> str:
    operation = {
        "action": "business.delete",
        "business": args.get("business"),
        "confirm": args.get("confirm"),
        "delete_files": args.get("delete_files") if args.get("delete_files") is not None else True,
        "delete_cron": args.get("delete_cron") if args.get("delete_cron") is not None else True,
        "delete_domains": args.get("delete_domains") if args.get("delete_domains") is not None else True,
        "base_domain": args.get("base_domain"),
        "subdomains": args.get("subdomains") or args.get("domains") or [],
    }
    return _commit_tool(args, operation)


def handle_business_set_mode(args: dict, **_: Any) -> str:
    operation = {
        "action": "business.mode.set",
        "business": args.get("business"),
        "mode": args.get("mode"),
    }
    return _commit_tool(args, operation)


def handle_business_set_work_focus(args: dict, **_: Any) -> str:
    operation = {
        "action": "business.focus.set",
        "business": args.get("business"),
        "work_focus": args.get("work_focus") or args.get("focus"),
    }
    return _commit_tool(args, operation)


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


def handle_business_configure_app_budget(args: dict, **_: Any) -> str:
    operation = {
        "action": "app.budget.set",
        "business": args.get("business"),
        "hard_limit_microusd": args.get("hard_limit_microusd"),
        "status": args.get("status") or "active",
    }
    return _commit_tool(args, operation)


def handle_business_upsert_app_surface_contract(args: dict, **_: Any) -> str:
    operation = {
        "action": "app.surface.upsert",
        "business": args.get("business"),
        "status": args.get("status") or "draft",
        "design_brief_path": args.get("design_brief_path") or "product/design-brief.md",
        "source_path": args.get("source_path"),
        "runtime_api_base": args.get("runtime_api_base"),
        "routes": args.get("routes") or [],
        "theme": args.get("theme") or {"source": "business design brief"},
        "constraints": args.get("constraints") or {},
        "notes": args.get("notes") or "",
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation)


def handle_business_verify_product_surface(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or ""))
        if not business:
            raise TakyonError("business is required")
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise TakyonError("idempotency_key is required")
        source_path = str(args.get("source_path") or "").strip()
        surface: dict[str, Any] = {}
        if not source_path:
            summary = store.read(scope=f"business:{business}", query="summary", include=["app"])
            app = summary.get("app") if isinstance(summary.get("app"), dict) else {}
            surface = app.get("surface") or app.get("surface_contract") or {}
            source_path = str(surface.get("source_path") or "product/site")
        install = bool(args.get("install", True))
        timeout_seconds = _clamp_int(args.get("timeout_seconds"), default=180, minimum=15, maximum=900)
        verification = _verify_product_surface_path(
            store._business_root(business),
            source_path,
            install=install,
            timeout_seconds=timeout_seconds,
        )
        receipt_id = uuid.uuid4().hex
        receipt_path = f"receipts/product-surface/{receipt_id}.json"
        verification = {**verification, "business": business, "receipt_path": receipt_path}
        operations: list[dict[str, Any]] = [
            {
                "action": "artifact.write",
                "business": business,
                "path": receipt_path,
                "content": json.dumps(verification, indent=2, ensure_ascii=False) + "\n",
            },
            {
                "action": "event.record",
                "business": business,
                "event_type": "product.surface.verify",
                "payload": {
                    "source_path": verification.get("source_path"),
                    "status": verification.get("status"),
                    "kind": verification.get("kind"),
                    "error": verification.get("error"),
                    "warnings": verification.get("warnings") or [],
                    "receipt_path": receipt_path,
                },
            },
        ]
        if bool(args.get("activate_on_success", True)) and verification.get("status") == "passed":
            if not surface:
                summary = store.read(scope=f"business:{business}", query="summary", include=["app"])
                app = summary.get("app") if isinstance(summary.get("app"), dict) else {}
                surface = app.get("surface") or app.get("surface_contract") or {}
            operations.append(
                {
                    "action": "app.surface.upsert",
                    "business": business,
                    "status": "active",
                    "design_brief_path": surface.get("design_brief_path") or "product/design-brief.md",
                    "source_path": verification.get("source_path"),
                    "runtime_api_base": surface.get("runtime_api_base"),
                    "routes": surface.get("routes") or [],
                    "theme": surface.get("theme") or {"source": "business design brief"},
                    "constraints": surface.get("constraints") or {},
                    "notes": surface.get("notes") or "",
                    "metadata": {**(surface.get("metadata") if isinstance(surface.get("metadata"), dict) else {}), "verification_receipt": receipt_path},
                }
            )
        result = store.commit(
            scope=f"business:{business}",
            operations=operations,
            idempotency_key=idempotency_key,
            reason=args.get("reason") or "product surface verification",
            actor=args.get("actor") or "agent",
        )
        return tool_result({"success": True, "business": business, "verification": verification, "result": result})
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_upsert_app_plan(args: dict, **_: Any) -> str:
    operation = {
        "action": "app.plan.upsert",
        "business": args.get("business"),
        "plan_key": args.get("plan_key"),
        "tier": args.get("tier"),
        "price_cents": args.get("price_cents"),
        "currency": args.get("currency") or "usd",
        "billing_interval": args.get("billing_interval") or "month",
        "included_ai_budget_microusd": args.get("included_ai_budget_microusd") or 0,
        "included_action_quota": args.get("included_action_quota") or 25,
        "allow_overage": bool(args.get("allow_overage")),
        "stripe_product_id": args.get("stripe_product_id"),
        "stripe_price_id": args.get("stripe_price_id"),
        "source": args.get("source") or "takyon",
        "notes": args.get("notes") or "",
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation)


def handle_business_upsert_app_customer(args: dict, **_: Any) -> str:
    operation = {
        "action": "app.customer.upsert",
        "business": args.get("business"),
        "email": args.get("email"),
        "name": args.get("name"),
        "status": args.get("status") or "active",
        "tier": args.get("tier") or "free",
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation)


def handle_business_grant_app_entitlement(args: dict, **_: Any) -> str:
    operation = {
        "action": "app.entitlement.upsert",
        "business": args.get("business"),
        "app_user_id": args.get("app_user_id"),
        "email": args.get("email"),
        "tier": args.get("tier") or "free",
        "status": args.get("status") or "active",
        "source": args.get("source") or "manual",
        "plan_key": args.get("plan_key"),
        "current_period_end": args.get("current_period_end"),
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation)


def handle_business_request_app_magic_link(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or ""))
        email = _normalize_email(str(args.get("email") or ""))
        origin = str(args.get("origin") or "").rstrip("/")
        app_slug = _file_slug(str(args.get("app_slug") or business), business)
        send_email = bool(args.get("send_email"))
        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            _enforce_business_work_focus(
                {"action": "app.customer.upsert", "business": business},
                str(business_row.get("work_focus") or "all"),
            )
            test_mode = str(business_row.get("mode") or "live") == "test"
            now = _now()
            user_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO app_users (id, business_slug, email, name, status, tier, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', 'free', ?, ?, ?) "
                "ON CONFLICT(business_slug, email) DO UPDATE SET status = 'active', updated_at = excluded.updated_at",
                (user_id, business, email, args.get("name"), _json_dumps({"source": "magic_link"}), now, now),
            )
            user = store._row_to_dict(conn.execute("SELECT * FROM app_users WHERE business_slug = ? AND email = ?", (business, email)).fetchone())
            token = _random_token()
            link = f"{origin}/api/takyon/apps/{app_slug}/auth/verify?token={urllib.parse.quote(token)}" if origin else ""
            provider_message_id = None
            email_sent = False
            if send_email:
                if test_mode:
                    provider_message_id = f"test-mode-suppressed:{uuid.uuid4().hex}"
                else:
                    product_name = str(args.get("product_name") or business)
                    provider_message_id = _postmark_magic_link(email, product_name, link or token)
                    email_sent = True
            link_id = uuid.uuid4().hex
            expires_at = _future(minutes=15)
            conn.execute(
                "INSERT INTO app_magic_links (id, business_slug, app_user_id, email, token_hash, purpose, expires_at, provider_message_id, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (link_id, business, user["id"], email, _hash_token(token), str(args.get("purpose") or "login"), expires_at, provider_message_id, _json_dumps({"app_slug": app_slug, "email_requested": send_email, "email_sent": email_sent, "external_side_effects": "suppressed" if test_mode and send_email else "none" if not send_email else "sent"}), now),
            )
            if test_mode and send_email:
                receipt_rel = f"receipts/app-magic-link/{link_id}.json"
                _atomic_write_text(store._business_root(business) / receipt_rel, _json_dumps({
                    "id": link_id,
                    "business": business,
                    "mode": "test",
                    "email": email,
                    "provider": "postmark",
                    "external_side_effects": "suppressed",
                    "sent": False,
                    "created_at": now,
                }) + "\n")
            store._record_event(conn, scope=f"business:{business}/app", business_slug=business, event_type="app.magic_link.request", payload={"email": email, "sent": email_sent, "requested_send": send_email, "provider_message_id": provider_message_id, "external_side_effects": "suppressed" if test_mode and send_email else "sent" if email_sent else "none"})
            store._rewrite_app_files(conn, business)
        return tool_result({"success": True, "business": business, "email": email, "magic_link_id": link_id, "token": token, "verify_url": link, "expires_at": expires_at, "email_sent": email_sent, "email_requested": send_email, "provider_message_id": provider_message_id, "external_side_effects": "suppressed" if test_mode and send_email else "sent" if email_sent else "none"})
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_verify_app_magic_link(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or ""))
        token = str(args.get("token") or "").strip()
        if not token:
            raise TakyonError("token is required")
        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            _enforce_business_work_focus(
                {"action": "app.customer.upsert", "business": business},
                str(business_row.get("work_focus") or "all"),
            )
            link = store._row_to_dict(conn.execute(
                "SELECT * FROM app_magic_links WHERE business_slug = ? AND token_hash = ? AND used_at IS NULL AND expires_at > ? LIMIT 1",
                (business, _hash_token(token), _now()),
            ).fetchone())
            if not link:
                raise TakyonError("magic link is invalid, expired, or already used")
            now = _now()
            conn.execute("UPDATE app_magic_links SET used_at = ? WHERE id = ?", (now, link["id"]))
            user = store._row_to_dict(conn.execute("SELECT * FROM app_users WHERE business_slug = ? AND id = ?", (business, link["app_user_id"])).fetchone())
            if not user:
                raise TakyonError("magic link user is missing")
            existing_free = conn.execute(
                "SELECT 1 FROM app_entitlements WHERE business_slug = ? AND app_user_id = ? AND source = 'manual' AND tier = 'free' LIMIT 1",
                (business, user["id"]),
            ).fetchone()
            if not existing_free:
                conn.execute(
                    "INSERT INTO app_entitlements (id, business_slug, app_user_id, tier, status, source, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'free', 'active', 'manual', ?, ?, ?)",
                    (uuid.uuid4().hex, business, user["id"], _json_dumps({"source": "magic_link"}), now, now),
                )
            session_token = _random_token()
            session_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO app_sessions (id, business_slug, app_user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, business, user["id"], _hash_token(session_token), _future(days=30), now),
            )
            tier = store._sync_user_tier(conn, business, user["id"])
            store._record_event(conn, scope=f"business:{business}/app", business_slug=business, event_type="app.magic_link.verify", payload={"app_user_id": user["id"], "session_id": session_id})
            store._rewrite_app_files(conn, business)
        return tool_result({"success": True, "business": business, "app_user_id": user["id"], "email": user["email"], "tier": tier, "session_id": session_id, "session_token": session_token, "expires_at": _future(days=30)})
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_read_app_account(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or ""))
        with store._connect() as conn:
            store._ensure_business(conn, business)
            user = None
            if args.get("session_token"):
                user = store._row_to_dict(conn.execute(
                    "SELECT u.* FROM app_sessions s JOIN app_users u ON u.id = s.app_user_id WHERE s.business_slug = ? AND s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.status = 'active' LIMIT 1",
                    (business, _hash_token(str(args.get("session_token"))), _now()),
                ).fetchone())
            elif args.get("app_user_id"):
                user = store._row_to_dict(conn.execute("SELECT * FROM app_users WHERE business_slug = ? AND id = ?", (business, str(args.get("app_user_id")))).fetchone())
            elif args.get("email"):
                user = store._row_to_dict(conn.execute("SELECT * FROM app_users WHERE business_slug = ? AND email = ?", (business, _normalize_email(str(args.get("email"))))).fetchone())
            if not user:
                raise TakyonError("app account not found")
            entitlements = [store._row_to_dict(row) for row in conn.execute("SELECT * FROM app_entitlements WHERE business_slug = ? AND app_user_id = ? ORDER BY updated_at DESC", (business, user["id"])).fetchall()]
            budget = store._ensure_app_budget(conn, business)
            usage = conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(estimated_cost_microusd), 0) AS estimated, COALESCE(SUM(actual_cost_microusd), 0) AS actual FROM app_usage_events WHERE business_slug = ? AND app_user_id = ? AND created_at >= ?",
                (business, user["id"], budget["current_period_start"]),
            ).fetchone()
            revenue = conn.execute("SELECT COALESCE(SUM(amount_paid_cents), 0) AS cents, COUNT(*) AS count FROM app_revenue_events WHERE business_slug = ? AND lower(customer_email) = lower(?)", (business, user["email"])).fetchone()
        return tool_result({"success": True, "business": business, "user": user, "entitlements": entitlements, "usage_this_period": {"events": int(usage["count"] or 0), "estimated_cost_microusd": int(usage["estimated"] or 0), "actual_cost_microusd": int(usage["actual"] or 0)}, "revenue": {"events": int(revenue["count"] or 0), "amount_paid_cents": int(revenue["cents"] or 0)}})
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_create_app_checkout(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or ""))
        plan_key = _file_slug(str(args.get("plan_key") or ""), "plan")
        if not plan_key:
            raise TakyonError("plan_key is required")
        customer_email = _normalize_email(str(args.get("customer_email"))) if args.get("customer_email") else None
        success_url = str(args.get("success_url") or "").strip()
        cancel_url = str(args.get("cancel_url") or "").strip()
        if not success_url or not cancel_url:
            raise TakyonError("success_url and cancel_url are required")
        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            _enforce_business_work_focus(
                {"action": "app.entitlement.upsert", "business": business},
                str(business_row.get("work_focus") or "all"),
            )
            test_mode = str(business_row.get("mode") or "live") == "test"
            plan = store._row_to_dict(conn.execute("SELECT * FROM app_plan_policies WHERE business_slug = ? AND plan_key = ?", (business, plan_key)).fetchone())
            if not plan:
                raise TakyonError(f"app plan not found: {plan_key}")
            if not test_mode:
                plan = _ensure_stripe_price(conn, business, plan, str(business_row.get("name") or business))
            mode = "payment" if plan.get("billing_interval") == "one_time" else "subscription"
            intent_id = uuid.uuid4().hex
            client_reference_id = uuid.uuid4().hex
            now = _now()
            conn.execute(
                "INSERT INTO app_checkout_intents (id, business_slug, app_user_id, plan_key, status, client_reference_id, customer_email, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, 'created', ?, ?, ?, ?, ?)",
                (intent_id, business, args.get("app_user_id"), plan_key, client_reference_id, customer_email, _json_dumps(args.get("metadata") or {}), now, now),
            )
            params: dict[str, Any] = {
                "mode": mode,
                "line_items[0][price]": plan["stripe_price_id"],
                "line_items[0][quantity]": 1,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "client_reference_id": client_reference_id,
                "metadata[business]": business,
                "metadata[plan_key]": plan_key,
                "metadata[checkout_intent_id]": intent_id,
                "metadata[source]": "takyon_app",
            }
            if customer_email:
                params["customer_email"] = customer_email
            if mode == "subscription":
                params["subscription_data[metadata][business]"] = business
                params["subscription_data[metadata][plan_key]"] = plan_key
                params["subscription_data[metadata][checkout_intent_id]"] = intent_id
            else:
                params["payment_intent_data[metadata][business]"] = business
                params["payment_intent_data[metadata][plan_key]"] = plan_key
                params["payment_intent_data[metadata][checkout_intent_id]"] = intent_id
            if test_mode:
                checkout_url = f"local://takyon/checkout/{business}/{intent_id}"
                conn.execute(
                    "UPDATE app_checkout_intents SET status = 'test_local', checkout_url = ?, updated_at = ? WHERE id = ?",
                    (checkout_url, _now(), intent_id),
                )
                receipt_rel = f"receipts/app-checkout/{intent_id}.json"
                _atomic_write_text(store._business_root(business) / receipt_rel, _json_dumps({
                    "id": intent_id,
                    "business": business,
                    "mode": "test",
                    "plan_key": plan_key,
                    "customer_email": customer_email,
                    "external_side_effects": "suppressed",
                    "stripe_called": False,
                    "checkout_url": checkout_url,
                    "client_reference_id": client_reference_id,
                    "created_at": now,
                }) + "\n")
                store._record_event(conn, scope=f"business:{business}/app", business_slug=business, event_type="app.checkout.create", payload={"plan_key": plan_key, "intent_id": intent_id, "external_side_effects": "suppressed", "receipt": receipt_rel})
                store._rewrite_app_files(conn, business)
                return tool_result({"success": True, "business": business, "mode": "test", "plan_key": plan_key, "checkout_intent_id": intent_id, "stripe_checkout_session_id": None, "checkout_url": checkout_url, "client_reference_id": client_reference_id, "external_side_effects": "suppressed"})
            session = _stripe_request("checkout/sessions", params)
            conn.execute(
                "UPDATE app_checkout_intents SET status = 'pending', stripe_checkout_session_id = ?, checkout_url = ?, updated_at = ? WHERE id = ?",
                (session.get("id"), session.get("url"), _now(), intent_id),
            )
            store._record_event(conn, scope=f"business:{business}/app", business_slug=business, event_type="app.checkout.create", payload={"plan_key": plan_key, "intent_id": intent_id, "stripe_checkout_session_id": session.get("id")})
            store._rewrite_app_files(conn, business)
        return tool_result({"success": True, "business": business, "plan_key": plan_key, "checkout_intent_id": intent_id, "stripe_checkout_session_id": session.get("id"), "checkout_url": session.get("url"), "client_reference_id": client_reference_id})
    except Exception as exc:
        return tool_error(str(exc), success=False)


def _process_checkout_completed(conn: sqlite3.Connection, store: TakyonStore, event: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    metadata = session.get("metadata") or {}
    intent_id = metadata.get("checkout_intent_id")
    intent = None
    if intent_id:
        intent = store._row_to_dict(conn.execute("SELECT * FROM app_checkout_intents WHERE id = ?", (intent_id,)).fetchone())
    if not intent and session.get("client_reference_id"):
        intent = store._row_to_dict(conn.execute("SELECT * FROM app_checkout_intents WHERE client_reference_id = ?", (session.get("client_reference_id"),)).fetchone())
    if not intent:
        return {"recorded": False, "reason": "missing_checkout_intent"}
    business = intent["business_slug"]
    customer_email = session.get("customer_details", {}).get("email") or session.get("customer_email") or intent.get("customer_email")
    customer_id = _stripe_object_id(session.get("customer"))
    subscription_id = _stripe_object_id(session.get("subscription"))
    payment_intent_id = _stripe_object_id(session.get("payment_intent"))
    invoice_id = _stripe_object_id(session.get("invoice"))
    completed_at = datetime.fromtimestamp(int(event.get("created") or time.time()), timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO app_checkout_sessions (
          id, business_slug, checkout_intent_id, plan_key, stripe_checkout_session_id,
          stripe_customer_id, stripe_payment_intent_id, stripe_subscription_id, stripe_invoice_id,
          mode, payment_status, status, currency, amount_subtotal_cents, amount_total_cents,
          client_reference_id, customer_email, raw_event_id, metadata_json, completed_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_checkout_session_id) DO UPDATE SET
          payment_status = excluded.payment_status,
          status = excluded.status,
          stripe_subscription_id = excluded.stripe_subscription_id,
          stripe_invoice_id = excluded.stripe_invoice_id,
          completed_at = excluded.completed_at,
          updated_at = excluded.updated_at
        """,
        (
            uuid.uuid4().hex,
            business,
            intent["id"],
            intent["plan_key"],
            session["id"],
            customer_id,
            payment_intent_id,
            subscription_id,
            invoice_id,
            session.get("mode"),
            session.get("payment_status"),
            session.get("status"),
            session.get("currency"),
            session.get("amount_subtotal"),
            session.get("amount_total"),
            session.get("client_reference_id"),
            customer_email,
            event.get("id"),
            _json_dumps(metadata),
            completed_at,
            _now(),
            _now(),
        ),
    )
    conn.execute("UPDATE app_checkout_intents SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?", (completed_at, _now(), intent["id"]))
    app_user_id = None
    if customer_email and (subscription_id or session.get("payment_status") == "paid"):
        email = _normalize_email(customer_email)
        conn.execute(
            "INSERT INTO app_users (id, business_slug, email, status, tier, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'active', 'paid', ?, ?, ?) "
            "ON CONFLICT(business_slug, email) DO UPDATE SET tier = 'paid', status = 'active', updated_at = excluded.updated_at",
            (uuid.uuid4().hex, business, email, _json_dumps({"source": "stripe_checkout"}), _now(), _now()),
        )
        user = store._row_to_dict(conn.execute("SELECT * FROM app_users WHERE business_slug = ? AND email = ?", (business, email)).fetchone())
        app_user_id = user["id"]
        conn.execute(
            "INSERT INTO app_entitlements (id, business_slug, app_user_id, tier, status, source, stripe_customer_id, stripe_subscription_id, stripe_checkout_session_id, plan_key, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'paid', 'active', 'stripe', ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, business, app_user_id, customer_id, subscription_id, session["id"], intent["plan_key"], _json_dumps({"raw_event_id": event.get("id")}), _now(), _now()),
        )
        store._sync_user_tier(conn, business, app_user_id)
    if session.get("currency") and session.get("payment_status") == "paid":
        conn.execute(
            "INSERT OR IGNORE INTO app_revenue_events (id, business_slug, provider_event_id, stripe_object_type, stripe_object_id, stripe_checkout_session_id, stripe_customer_id, revenue_type, status, currency, amount_paid_cents, customer_email, occurred_at, metadata_json, created_at) VALUES (?, ?, ?, 'checkout.session', ?, ?, ?, 'checkout', ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, business, event.get("id"), session["id"], session["id"], customer_id, session.get("payment_status") or "paid", session.get("currency") or "usd", int(session.get("amount_total") or 0), customer_email, completed_at, _json_dumps(metadata), _now()),
        )
    store._record_event(conn, scope=f"business:{business}/app", business_slug=business, event_type="app.checkout.completed", payload={"stripe_checkout_session_id": session["id"], "app_user_id": app_user_id})
    store._rewrite_app_files(conn, business)
    return {"recorded": True, "business": business, "app_user_id": app_user_id}


def _process_subscription_event(conn: sqlite3.Connection, store: TakyonStore, subscription: dict[str, Any]) -> dict[str, Any]:
    subscription_id = subscription.get("id")
    if not subscription_id:
        return {"recorded": False, "reason": "missing_subscription_id"}
    status = _subscription_entitlement_status(str(subscription.get("status") or ""))
    customer_id = _stripe_object_id(subscription.get("customer"))
    current_period_end = None
    if isinstance(subscription.get("current_period_end"), (int, float)):
        current_period_end = datetime.fromtimestamp(int(subscription["current_period_end"]), timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT business_slug, app_user_id FROM app_entitlements WHERE source = 'stripe' AND stripe_subscription_id = ?",
        (subscription_id,),
    ).fetchall()
    updated: list[dict[str, str]] = []
    for row in rows:
        business = row["business_slug"]
        app_user_id = row["app_user_id"]
        conn.execute(
            "UPDATE app_entitlements SET status = ?, stripe_customer_id = COALESCE(?, stripe_customer_id), current_period_end = COALESCE(?, current_period_end), metadata_json = ?, updated_at = ? WHERE business_slug = ? AND app_user_id = ? AND stripe_subscription_id = ?",
            (status, customer_id, current_period_end, _json_dumps({"stripe_subscription_status": subscription.get("status"), "cancel_at_period_end": subscription.get("cancel_at_period_end")}), _now(), business, app_user_id, subscription_id),
        )
        tier = store._sync_user_tier(conn, business, app_user_id)
        store._rewrite_app_files(conn, business)
        updated.append({"business": business, "app_user_id": app_user_id, "tier": tier})
    return {"recorded": bool(updated), "updated": updated}


def handle_business_record_stripe_webhook(args: dict, **_: Any) -> str:
    store = _store()
    try:
        load_takyon_env()
        raw_body = args.get("raw_body")
        signature = args.get("stripe_signature")
        if raw_body and signature:
            secret = os.getenv("STRIPE_WEBHOOK_SECRET")
            if not secret:
                raise TakyonError("Stripe webhook verification requires STRIPE_WEBHOOK_SECRET")
            _verify_stripe_signature(str(raw_body), str(signature), secret)
            event = json.loads(str(raw_body))
        else:
            event = args.get("event") or args.get("event_payload")
        if not isinstance(event, dict):
            raise TakyonError("Stripe event payload is required")
        event_id = str(event.get("id") or uuid.uuid4().hex)
        event_type = str(event.get("type") or "")
        with store._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO webhook_events (id, provider, provider_event_id, payload_json, created_at) VALUES (?, 'stripe', ?, ?, ?)",
                (uuid.uuid4().hex, event_id, _json_dumps(event), _now()),
            )
            processed: dict[str, Any] = {"ignored": event_type}
            obj = ((event.get("data") or {}).get("object") if isinstance(event.get("data"), dict) else None) or {}
            if event_type == "checkout.session.completed" and isinstance(obj, dict):
                processed = _process_checkout_completed(conn, store, event, obj)
            elif event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"} and isinstance(obj, dict):
                processed = _process_subscription_event(conn, store, obj)
            conn.execute("UPDATE webhook_events SET processed_at = ?, error = NULL WHERE provider = 'stripe' AND provider_event_id = ?", (_now(), event_id))
        return tool_result({"success": True, "provider_event_id": event_id, "type": event_type, "processed": processed})
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_record_app_usage(args: dict, **_: Any) -> str:
    operation = {
        "action": "app.usage.record",
        "business": args.get("business"),
        "app_user_id": args.get("app_user_id"),
        "app_user_tier": args.get("app_user_tier"),
        "purpose": args.get("purpose") or "product_usage",
        "route": args.get("route") or "app",
        "status": args.get("status") or "completed",
        "estimated_cost_microusd": args.get("estimated_cost_microusd") or 0,
        "actual_cost_microusd": args.get("actual_cost_microusd") or 0,
        "input_tokens": args.get("input_tokens"),
        "output_tokens": args.get("output_tokens"),
        "provider_request_id": args.get("provider_request_id"),
        "provider": args.get("provider"),
        "model": args.get("model"),
        "metadata": args.get("metadata") or {},
        "error": args.get("error"),
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


def handle_business_publish_test_outreach(args: dict, **_: Any) -> str:
    operation = {
        "action": "outreach.local_publish",
        "business": args.get("business"),
        "channel": args.get("channel") or args.get("provider"),
        "provider": args.get("provider"),
        "target": args.get("target") or args.get("recipient"),
        "recipient": args.get("recipient"),
        "subject": args.get("subject") or args.get("title"),
        "body": args.get("body"),
        "thread_external_id": args.get("thread_external_id"),
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation)


def handle_business_publish_outreach(args: dict, **_: Any) -> str:
    try:
        store = _store()
        business = _slugify(str(args.get("business") or args.get("business_slug") or ""))
        if not business:
            raise TakyonError("business is required")
        body = str(args.get("body") or args.get("content") or "").strip()
        if not body:
            raise TakyonError("body is required")
        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            business_mode = str(business_row.get("mode") or "live")

        canonical_args = dict(args)
        canonical_args["business"] = business
        canonical_args["body"] = body
        if business_mode == "test":
            return handle_business_publish_test_outreach(canonical_args)

        channel = str(args.get("channel") or args.get("provider") or "outreach").strip()
        provider = str(args.get("provider") or channel).strip()
        requires_api = [
            str(item).strip()
            for item in _as_list(args.get("requires_api"))
            if str(item).strip()
        ]
        requires_env = [
            str(item).strip()
            for item in _as_list(args.get("requires_env"))
            if str(item).strip()
        ]
        if provider:
            requires_api.append(provider)
        if not requires_api and not requires_env:
            raise TakyonError("live outreach publish requires provider, requires_api, or requires_env")

        payload = {
            "channel": channel,
            "provider": provider,
            "target": args.get("target") or args.get("recipient"),
            "recipient": args.get("recipient"),
            "subject": args.get("subject") or args.get("title"),
            "body": body,
            "thread_external_id": args.get("thread_external_id"),
            "metadata": args.get("metadata") or {},
            "requested_external_side_effect": "publish_outreach",
        }
        operation = {
            "action": "job.enqueue",
            "business": business,
            "scope": args.get("scope") or f"business:{business}",
            "kind": args.get("kind") or f"{_file_slug(channel, 'outreach')}.publish_outreach",
            "status": args.get("status") or "pending",
            "payload": payload,
            "requires_api": sorted(set(requires_api)),
            "requires_env": sorted(set(requires_env)),
        }
        return _commit_tool(canonical_args, operation, scope=operation["scope"])
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_generate_creative_asset(args: dict, **_: Any) -> str:
    try:
        store = _store()
        business = _slugify(str(args.get("business") or ""))
        kind = _normalize_creative_asset_choice(args.get("kind"), _CREATIVE_ASSET_KINDS.keys(), field="kind")
        channel = _normalize_creative_asset_choice(args.get("channel"), _CREATIVE_ASSET_CHANNELS, field="channel")
        format_name = _normalize_creative_asset_choice(args.get("format") or "ugc", _CREATIVE_ASSET_FORMATS, field="format")
        prompt = _creative_asset_prompt(args)
        if not prompt:
            raise TakyonError("prompt, script, or shot_list is required")
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise TakyonError("idempotency_key is required")
        budget_usd = float(args.get("budget_usd") or args.get("estimated_cost_usd") or 0)
        if budget_usd <= 0:
            raise TakyonError("budget_missing: business_generate_creative_asset requires budget_usd > 0 before provider-backed creative generation")
        asset_id = _file_slug(str(args.get("asset_id") or idempotency_key), "asset")
        rel = _creative_asset_relpath(
            args=args,
            kind=kind,
            channel=channel,
            format_name=format_name,
            asset_id=asset_id,
        )
        receipt_rel = f"receipts/creative-assets/{asset_id}.json"

        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            business_mode = str(business_row.get("mode") or "live")

        provider = str(args.get("provider") or "").strip()
        requires_api = [str(item).strip() for item in _as_list(args.get("requires_api")) if str(item).strip()]
        if provider:
            requires_api.append(provider)
        try:
            _require_api_access(
                {
                    "action": "creative.asset.generate",
                    "business": business,
                    "provider": provider,
                    "requires_api": requires_api,
                    "requires_env": args.get("requires_env") or [],
                },
                business_mode=business_mode,
            )
        except TakyonError as exc:
            message = str(exc)
            if "missing API/env credential" in message:
                raise TakyonError(f"api_key_missing: {message}") from exc
            raise

        asset_path = store._resolve_business_file(business, rel)
        receipt_path = store._resolve_business_file(business, receipt_rel)
        if asset_path.exists() and receipt_path.exists():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except Exception:
                receipt = {}
            return tool_result({
                "success": True,
                "action": "business_generate_creative_asset",
                "business": business,
                "idempotent": True,
                "kind": kind,
                "channel": channel,
                "format": format_name,
                "path": rel,
                "receipt": receipt_rel,
                "deliverable": {"kind": kind, "path": rel, "receipt": receipt_rel},
                "provider_result": receipt.get("provider_result", {}),
            })

        store.commit(
            scope=f"business:{business}",
            operations=[
                {
                    "action": "ledger.allocate",
                    "business": business,
                    "amount": budget_usd,
                    "currency": str(args.get("currency") or "USD"),
                    "purpose": f"{channel} {format_name} {kind} creative asset generation",
                    "kind": "creative_asset_generation",
                    "status": "allocated",
                    "requires_api": requires_api,
                    "requires_env": args.get("requires_env") or [],
                }
            ],
            idempotency_key=f"{idempotency_key}:budget",
            reason=args.get("reason") or "generate local creative asset",
            actor=args.get("actor") or "agent",
        )

        if kind == "video":
            from tools.video_generation_tool import _handle_video_generate

            provider_raw = _handle_video_generate(
                {
                    "prompt": prompt,
                    "image_url": args.get("image_url"),
                    "reference_image_urls": args.get("reference_image_urls"),
                    "duration": args.get("duration"),
                    "aspect_ratio": args.get("aspect_ratio"),
                    "resolution": args.get("resolution"),
                    "negative_prompt": args.get("negative_prompt"),
                    "audio": args.get("audio"),
                    "seed": args.get("seed"),
                    "model": args.get("model"),
                }
            )
        else:
            from tools.image_generation_tool import _handle_image_generate

            provider_raw = _handle_image_generate(
                {
                    "prompt": prompt,
                    "aspect_ratio": args.get("aspect_ratio"),
                    "model": args.get("model"),
                }
            )

        provider_result = _parse_provider_result(provider_raw, kind=kind, expected_provider=provider)
        _atomic_write_bytes(asset_path, _creative_asset_source_bytes(provider_result["_asset_source"]))

        created_at = _now()
        receipt = {
            "id": asset_id,
            "business": business,
            "mode": business_mode,
            "kind": kind,
            "channel": channel,
            "format": format_name,
            "campaign": _file_slug(str(args.get("campaign") or "default"), "default"),
            "path": rel,
            "receipt": receipt_rel,
            "prompt": prompt,
            "script": str(args.get("script") or ""),
            "shot_list": args.get("shot_list") or args.get("shots") or [],
            "provider": provider or provider_result.get("provider") or "",
            "model": args.get("model") or provider_result.get("model") or "",
            "budget_usd": budget_usd,
            "external_side_effects": "local_asset_only",
            "posted": False,
            "created_at": created_at,
            "provider_result": {k: v for k, v in provider_result.items() if k != "_asset_source"},
        }
        _atomic_write_text(receipt_path, _json_dumps(receipt) + "\n")

        store.commit(
            scope=f"business:{business}/campaign:{receipt['campaign']}",
            operations=[
                {
                    "action": "event.record",
                    "business": business,
                    "scope": f"business:{business}/campaign:{receipt['campaign']}",
                    "event_type": "creative.asset.generated",
                    "payload": receipt,
                }
            ],
            idempotency_key=f"{idempotency_key}:event",
            reason=args.get("reason") or "generated local creative asset",
            actor=args.get("actor") or "agent",
        )
        return tool_result({
            "success": True,
            "action": "business_generate_creative_asset",
            "business": business,
            "kind": kind,
            "channel": channel,
            "format": format_name,
            "path": rel,
            "receipt": receipt_rel,
            "deliverable": {"kind": kind, "path": rel, "receipt": receipt_rel},
            "external_side_effects": "local_asset_only",
            "posted": False,
        })
    except Exception as exc:
        message = str(exc)
        return tool_error(message, success=False, error_type=_creative_asset_error_type(message))


def handle_business_upsert_conversation_thread(args: dict, **_: Any) -> str:
    operation = {
        "action": "conversation.thread.upsert",
        "business": args.get("business"),
        "source": args.get("source"),
        "external_id": args.get("external_id"),
        "title": args.get("title"),
        "url": args.get("url"),
        "status": args.get("status") or "active",
    }
    return _commit_tool(args, operation)


def handle_business_record_conversation_message(args: dict, **_: Any) -> str:
    operation = {
        "action": "conversation.message.record",
        "business": args.get("business"),
        "source": args.get("source"),
        "thread_id": args.get("thread_id"),
        "thread_external_id": args.get("thread_external_id"),
        "thread_title": args.get("thread_title"),
        "url": args.get("url"),
        "external_id": args.get("external_id"),
        "direction": args.get("direction") or "inbound",
        "author_label": args.get("author_label") or "",
        "body": args.get("body") or "",
        "status": args.get("status"),
        "received_at": args.get("received_at"),
    }
    return _commit_tool(args, operation)


def handle_business_update_conversation_message_status(args: dict, **_: Any) -> str:
    operation = {
        "action": "conversation.message.status.set",
        "business": args.get("business"),
        "message_id": args.get("message_id"),
        "source": args.get("source"),
        "external_id": args.get("external_id"),
        "status": args.get("status"),
    }
    return _commit_tool(args, operation)


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


def _business_version(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _detect_legacy_product_site(root: Path) -> str:
    for rel in ("product/site", "site"):
        candidate = root / rel
        if candidate.exists() and candidate.is_dir() and _product_source_files(candidate, limit=1):
            return rel
    return ""


def _legacy_surface_routes(site_root: Path) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for child in sorted(site_root.glob("*.html"), key=lambda path: path.name.lower())[:20]:
        if child.name == "index.html":
            route = "/"
            label = "Landing"
        else:
            route = f"/{child.stem}"
            label = child.stem.replace("-", " ").replace("_", " ").title()
        routes.append({"path": route, "name": label, "source": str(child.relative_to(site_root))})
    return routes or [{"path": "/", "name": "Product surface", "source": "."}]


def _legacy_distribution_mappings(root: Path) -> list[dict[str, str]]:
    distribution = root / "distribution"
    if not distribution.exists() or not distribution.is_dir():
        return []
    mappings: list[dict[str, str]] = []
    for child in sorted(distribution.rglob("*.md"), key=lambda path: path.as_posix())[:100]:
        if not child.is_file():
            continue
        rel = str(child.relative_to(root))
        name = child.name.lower()
        parts = {part.lower() for part in child.parts}
        if {"creative", "creatives"}.intersection(parts) or any(token in name for token in ("ad", "ugc", "video", "creative")):
            mapped_as = "local_creative_brief"
        elif "posts" in parts or any(token in name for token in ("post", "outreach", "launch")):
            mapped_as = "local_post"
        else:
            mapped_as = "distribution_note"
        mappings.append({"path": rel, "mapped_as": mapped_as})
    return mappings


def _legacy_asset_paths(root: Path) -> list[str]:
    suffixes = {".gif", ".jpg", ".jpeg", ".mov", ".mp4", ".png", ".webm"}
    skip_dirs = {".git", ".next", "node_modules", "venv", ".venv"}
    assets: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skip_dirs and not name.startswith(".cache")]
        for filename in sorted(filenames):
            if len(assets) >= 100:
                return assets
            child = Path(dirpath) / filename
            if child.suffix.lower() not in suffixes:
                continue
            if not child.is_file():
                continue
            try:
                rel = str(child.relative_to(root))
            except ValueError:
                continue
            if rel.startswith("receipts/"):
                continue
            assets.append(rel)
    return assets


def _plan_business_upgrade(conn: sqlite3.Connection, store: TakyonStore, business: dict[str, Any]) -> dict[str, Any]:
    slug = str(business.get("slug") or "")
    root = store._business_root(slug)
    metadata = business.get("metadata") if isinstance(business.get("metadata"), dict) else {}
    schema_version = _business_version(metadata.get("takyon_schema_version"))
    capability_version = _business_version(metadata.get("takyon_capability_version"))
    product_site = _detect_legacy_product_site(root)
    surface_row = conn.execute("SELECT business_slug FROM app_surface_contracts WHERE business_slug = ?", (slug,)).fetchone()
    distribution_mappings = _legacy_distribution_mappings(root)
    existing_assets = _legacy_asset_paths(root)
    receipt_exists = (root / BUSINESS_UPGRADE_RECEIPT).exists()

    actions: list[str] = []
    if schema_version < CURRENT_BUSINESS_SCHEMA_VERSION or capability_version < CURRENT_BUSINESS_CAPABILITY_VERSION:
        actions.append("set_business_versions")
    if product_site and surface_row is None:
        actions.append("record_legacy_product_surface")
    if distribution_mappings and not receipt_exists:
        actions.append("map_legacy_distribution_files")
    if existing_assets and not receipt_exists:
        actions.append("index_existing_local_assets")
    if actions and not receipt_exists:
        actions.append("write_upgrade_receipt")

    seen: set[str] = set()
    deduped_actions = [action for action in actions if not (action in seen or seen.add(action))]
    return {
        "business": slug,
        "current_schema_version": schema_version,
        "target_schema_version": CURRENT_BUSINESS_SCHEMA_VERSION,
        "current_capability_version": capability_version,
        "target_capability_version": CURRENT_BUSINESS_CAPABILITY_VERSION,
        "status": "needs_upgrade" if deduped_actions else "current",
        "actions": deduped_actions,
        "detected": {
            "product_site": product_site,
            "distribution_mappings": distribution_mappings,
            "existing_local_assets": existing_assets,
            "upgrade_receipt": BUSINESS_UPGRADE_RECEIPT if receipt_exists else "",
        },
    }


def _apply_business_upgrade(conn: sqlite3.Connection, store: TakyonStore, plan: dict[str, Any]) -> dict[str, Any]:
    slug = str(plan["business"])
    actions = list(plan.get("actions") or [])
    if not actions:
        return {**plan, "changed": False}

    root = store._business_root(slug)
    business = store._ensure_business(conn, slug)
    metadata = business.get("metadata") if isinstance(business.get("metadata"), dict) else {}
    now = _now()
    receipt_rel = BUSINESS_UPGRADE_RECEIPT
    metadata = {
        **metadata,
        "takyon_schema_version": CURRENT_BUSINESS_SCHEMA_VERSION,
        "takyon_capability_version": CURRENT_BUSINESS_CAPABILITY_VERSION,
        "takyon_last_upgrade": {
            "name": "takyon-business-upgrade-v1",
            "receipt": receipt_rel,
            "updated_at": now,
        },
    }
    conn.execute(
        "UPDATE businesses SET metadata_json = ?, updated_at = ? WHERE slug = ?",
        (_json_dumps(metadata), now, slug),
    )

    product_site = str((plan.get("detected") or {}).get("product_site") or "")
    if product_site and "record_legacy_product_surface" in actions:
        site_root = root / product_site
        conn.execute(
            """
            INSERT INTO app_surface_contracts (
              business_slug, status, design_brief_path, source_path, runtime_api_base,
              routes_json, theme_json, constraints_json, notes, metadata_json, created_at, updated_at
            )
            VALUES (?, 'legacy_detected', 'product/design-brief.md', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(business_slug) DO NOTHING
            """,
            (
                slug,
                product_site,
                f"/api/takyon/apps/{slug}",
                _json_dumps(_legacy_surface_routes(site_root)),
                _json_dumps({"source": "legacy product site"}),
                _json_dumps({"no_hardcoded_product_ui": True, "backend_runtime_only": True}),
                "Detected by Takyon business upgrade v1 from existing product source. Not a deploy or verification receipt.",
                _json_dumps({"takyon_upgrade": "takyon-business-upgrade-v1", "legacy_detected": True}),
                now,
                now,
            ),
        )
        store._rewrite_app_files(conn, slug)

    receipt = {
        "schema": "takyon.business_upgrade.v1",
        "business": slug,
        "schema_version": CURRENT_BUSINESS_SCHEMA_VERSION,
        "capability_version": CURRENT_BUSINESS_CAPABILITY_VERSION,
        "actions": actions,
        "detected": plan.get("detected") or {},
        "invented_assets": False,
        "fake_receipts": False,
        "created_at": now,
    }
    _atomic_write_text(root / receipt_rel, _json_dumps(receipt) + "\n")
    store._record_event(
        conn,
        scope=f"business:{slug}",
        business_slug=slug,
        event_type="business.upgrade",
        payload={"receipt": receipt_rel, "actions": actions, "schema_version": CURRENT_BUSINESS_SCHEMA_VERSION, "capability_version": CURRENT_BUSINESS_CAPABILITY_VERSION},
    )
    return {**plan, "changed": True, "receipt": receipt_rel}


def upgrade_businesses(
    *,
    store: TakyonStore | None = None,
    businesses: Iterable[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    store = store or _store()
    requested = [_slugify(str(item)) for item in (businesses or []) if str(item).strip()]
    with store._connect() as conn:
        if requested:
            placeholders = ",".join("?" for _ in requested)
            rows = conn.execute(f"SELECT * FROM businesses WHERE slug IN ({placeholders}) ORDER BY updated_at DESC", requested).fetchall()
        else:
            rows = conn.execute("SELECT * FROM businesses ORDER BY updated_at DESC").fetchall()
        found = {str(row["slug"]) for row in rows}
        missing = [slug for slug in requested if slug not in found]
        if missing:
            raise TakyonError(f"business not found: {', '.join(missing)}")
        plans = [_plan_business_upgrade(conn, store, store._row_to_dict(row) or {}) for row in rows]
        if not dry_run:
            with conn:
                plans = [_apply_business_upgrade(conn, store, plan) for plan in plans]
    return {
        "success": True,
        "action": "business_upgrade_businesses",
        "dry_run": bool(dry_run),
        "schema_version": CURRENT_BUSINESS_SCHEMA_VERSION,
        "capability_version": CURRENT_BUSINESS_CAPABILITY_VERSION,
        "businesses": plans,
    }


def handle_business_upgrade_businesses(args: dict, **_: Any) -> str:
    try:
        dry_run = not bool(args.get("apply") or args.get("confirm"))
        businesses = args.get("businesses") or args.get("business")
        if isinstance(businesses, str):
            businesses = [businesses] if businesses.strip() else []
        result = upgrade_businesses(businesses=businesses or [], dry_run=dry_run)
        return tool_result(result)
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_registry(args: dict, **_: Any) -> str:
    try:
        snapshot = business_registry_snapshot(
            kind=args.get("kind"),
            category=args.get("category"),
            priority_band=args.get("priority_band"),
        )
        try:
            from tools.video_generation_tool import get_video_generation_capability_snapshot

            snapshot["runtime_capabilities"] = {
                "video_generation": get_video_generation_capability_snapshot(),
            }
        except Exception as exc:
            snapshot["runtime_capabilities"] = {
                "video_generation": {
                    "available": False,
                    "gate": "capability_probe_failed",
                    "error": str(exc),
                    "summary": f"video generation capability probe failed: {exc}",
                }
            }
        return tool_result({"success": True, **snapshot})
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_claude_agent_task(args: dict, **_: Any) -> str:
    """Run a general Claude Agent SDK worker inside one business filesystem."""
    store = _store()
    try:
        business = _slugify(str(args.get("business") or args.get("business_slug") or ""))
        instruction = str(args.get("instruction") or "").strip()
        if not instruction:
            raise TakyonError("instruction is required")

        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise TakyonError("idempotency_key is required")

        workspace_raw = str(args.get("workspace") or ".").strip() or "."
        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            _enforce_business_work_focus(
                {"action": "workspace.upsert", "business": business, "workspace": workspace_raw},
                str(business_row.get("work_focus") or "all"),
            )
        load_takyon_env()
        _require_api_access({"action": "agent.record", "business": business, "requires_api": ["anthropic"]})
        store.read(scope=f"business:{business}", query="summary", limit=20)

        business_root = store._business_root(business).resolve()
        workspace_path = business_root if workspace_raw in {".", ""} else store._resolve_business_file(business, workspace_raw).resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)
        if not workspace_path.is_dir():
            raise TakyonError(f"workspace is not a directory: {workspace_raw}")
        if business_root not in (workspace_path, *workspace_path.parents):
            raise TakyonError("workspace escaped business root")

        budget_usd = _clamp_float(args.get("budget_usd"), default=2.0, minimum=0.05, maximum=25.0)
        budget = store.commit(
            scope=f"business:{business}",
            operations=[
                {
                    "action": "ledger.allocate",
                    "business": business,
                    "amount": budget_usd,
                    "currency": "USD",
                    "kind": "claude_agent_sdk",
                    "status": "spent",
                    "purpose": f"Claude Agent SDK task in {workspace_raw}",
                    "requires_api": ["anthropic"],
                }
            ],
            idempotency_key=f"{idempotency_key}:claude-sdk-budget",
            reason=args.get("reason") or "Claude Agent SDK task budget",
            actor=args.get("actor") or "agent",
        )

        node = _resolve_runtime_executable("node")
        if not node:
            ensure_runtime = _ensure_javascript_runtime(package_manager=False)
            node = _resolve_runtime_executable("node")
        else:
            ensure_runtime = {"success": True, "installed": False, "capabilities": _runtime_capabilities(("node", "npm", "npx", "corepack", "pnpm", "yarn", "bun"))}
        if not node:
            raise TakyonError(
                "javascript runtime unavailable for Claude Agent SDK tasks: "
                f"{ensure_runtime.get('error') or 'node is missing'}"
            )
        script = _repo_root() / "scripts" / "takyon-claude-agent-task.mjs"
        if not script.exists():
            raise TakyonError(f"Claude Agent SDK helper missing: {script}")

        max_turns = _clamp_int(args.get("max_turns"), default=12, minimum=1, maximum=40)
        timeout_ms = _clamp_int(args.get("timeout_ms"), default=300_000, minimum=30_000, maximum=1_800_000)
        model = str(
            args.get("model")
            or os.getenv("TAKYON_CLAUDE_AGENT_MODEL")
            or _model_from_config("claude_agent_default", "deep_work_default")
            or DEFAULT_CLAUDE_AGENT_MODEL
        ).strip()
        worker_instruction = instruction.rstrip() + "\n\n" + NO_PRETEND_PRODUCT_CONTRACT
        payload = {
            "business": business,
            "workspace": workspace_raw,
            "cwd": str(workspace_path),
            "root": str(business_root),
            "instruction": worker_instruction,
            "model": model,
            "maxTurns": max_turns,
            "timeoutMs": timeout_ms,
            "maxBudgetUsd": budget_usd,
        }

        proc = subprocess.run(
            [node, str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=str(_repo_root()),
            timeout=(timeout_ms / 1000.0) + 15,
            env=_runtime_env({"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"}),
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        try:
            sdk_result = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            sdk_result = {"success": False, "raw_stdout": _truncate_text(stdout)}
        if proc.returncode != 0:
            sdk_result.setdefault("success", False)
            sdk_result["error"] = _truncate_text(stderr or sdk_result.get("error") or f"node exited {proc.returncode}", 8000)
        pretend_findings = _scan_for_pretend_product_state(workspace_path) if sdk_result.get("success") else []
        if pretend_findings:
            sdk_result["success"] = False
            sdk_result["pretend_product_findings"] = pretend_findings
            sdk_result["error"] = (
                "Claude Agent SDK output blocked by Hermes no-pretend contract: "
                "product source contains fake/demo auth, account, checkout, or integration state. "
                "Use real Hermes runtime calls or a visible DEBUG/blocked state instead."
            )
        verification: dict[str, Any] | None = None
        verify_surface = bool(args.get("verify_surface"))
        if not args.get("verify_surface") and workspace_raw not in {".", ""}:
            normalized_workspace = workspace_raw.strip("/").lower()
            verify_surface = normalized_workspace == "product" or normalized_workspace.startswith("product/") or normalized_workspace in {"site", "website"}
        if sdk_result.get("success") and verify_surface:
            verification = _verify_product_surface_path(
                business_root,
                workspace_raw,
                install=bool(args.get("install", True)),
                timeout_seconds=_clamp_int(args.get("verification_timeout_seconds"), default=180, minimum=15, maximum=900),
            )
            receipt_id = hashlib.sha256(f"{idempotency_key}:surface-verification:{workspace_raw}".encode("utf-8")).hexdigest()[:32]
            verification = {
                **verification,
                "business": business,
                "receipt_path": f"receipts/product-surface/{receipt_id}.json",
                "source": "business_claude_agent_task",
            }
        status = "completed" if sdk_result.get("success") else "failed"
        if verification and verification.get("status") != "passed":
            status = "blocked"

        record_operations: list[dict[str, Any]] = []
        if verification:
            record_operations.extend(
                [
                    {
                        "action": "artifact.write",
                        "business": business,
                        "path": verification["receipt_path"],
                        "content": json.dumps(verification, indent=2, ensure_ascii=False) + "\n",
                    },
                    {
                        "action": "event.record",
                        "business": business,
                        "event_type": "product.surface.verify",
                        "payload": {
                            "source_path": verification.get("source_path"),
                            "status": verification.get("status"),
                            "kind": verification.get("kind"),
                            "error": verification.get("error"),
                            "warnings": verification.get("warnings") or [],
                            "receipt_path": verification.get("receipt_path"),
                        },
                    },
                ]
            )
        record_operations.append(
            {
                "action": "agent.record",
                "business": business,
                "scope": f"business:{business}/workspace:{workspace_raw}",
                "status": status,
                "prompt": worker_instruction,
                "result": {
                    "source": "claude-agent-sdk",
                    "workspace": workspace_raw,
                    "model": model,
                    "summary": sdk_result.get("summary") or "",
                    "error": sdk_result.get("error") or None,
                    "pretend_product_findings": pretend_findings,
                    "verification": verification,
                },
            }
        )
        agent_record = store.commit(
            scope=f"business:{business}",
            operations=record_operations,
            idempotency_key=f"{idempotency_key}:claude-sdk-agent-record",
            reason=args.get("reason") or "Claude Agent SDK task record",
            actor=args.get("actor") or "agent",
        )

        return tool_result(
            {
                "success": bool(sdk_result.get("success")),
                "business": business,
                "workspace": workspace_raw,
                "source": "claude-agent-sdk",
                "model": model,
                "budget": budget,
                "agent_record": agent_record,
                "verification": verification,
                "summary": sdk_result.get("summary") or "",
                "error": sdk_result.get("error"),
                "pretend_product_findings": pretend_findings,
            }
        )
    except subprocess.TimeoutExpired as exc:
        return tool_error(f"Claude Agent SDK task timed out: {exc}", success=False)
    except Exception as exc:
        return tool_error(str(exc), success=False)


_CONVERSATION_AGENT_TASK_TYPES = {
    "triage",
    "cluster",
    "draft_replies",
    "respond",
    "extract_learnings",
    "identify_leads",
}


def _read_conversation_agent_actions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise TakyonError("conversation agent actions.json must be an object")
    return data


def handle_business_conversation_agent_task(args: dict, **_: Any) -> str:
    """Delegate bounded conversation response work to a business-scoped worker."""
    store = _store()
    try:
        business = _slugify(str(args.get("business") or args.get("business_slug") or ""))
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise TakyonError("idempotency_key is required")
        task_type = _file_slug(str(args.get("task_type") or "triage"), "triage")
        if task_type not in _CONVERSATION_AGENT_TASK_TYPES:
            raise TakyonError(f"conversation task_type must be one of {sorted(_CONVERSATION_AGENT_TASK_TYPES)}")
        objective = str(args.get("objective") or "").strip() or f"{task_type} conversation backlog"
        limit = _clamp_int(args.get("limit"), default=100, minimum=1, maximum=500)
        direction = str(args.get("direction") or "inbound").strip().lower()
        status = str(args.get("status") or "needs_response").strip().lower()
        source_filter = str(args.get("source") or "").strip()
        max_actions = _clamp_int(args.get("max_actions"), default=20, minimum=0, maximum=100)
        apply_actions = bool(args.get("apply_actions") or args.get("execute_actions"))
        allow_outbound_messages = bool(args.get("allow_outbound_messages", True))
        allow_external_jobs = bool(args.get("allow_external_jobs", False))

        task_id = hashlib.sha256(f"{business}:{idempotency_key}:conversation-agent".encode("utf-8")).hexdigest()
        workspace_raw = str(args.get("workspace") or f"conversations/tasks/{_now()[:10]}-{task_type}-{task_id[:8]}").strip()
        with store._connect() as conn:
            store._ensure_business(conn, business)
        workspace_path = store._resolve_business_file(business, workspace_raw)
        workspace_path.mkdir(parents=True, exist_ok=True)
        if not workspace_path.is_dir():
            raise TakyonError(f"conversation agent workspace is not a directory: {workspace_raw}")

        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            filters = ["m.business_slug = ?"]
            params: list[Any] = [business]
            if direction and direction != "all":
                filters.append("m.direction = ?")
                params.append(direction)
            if status and status != "all":
                filters.append("m.status = ?")
                params.append(status)
            if source_filter:
                filters.append("m.source = ?")
                params.append(_file_slug(source_filter, "source"))
            where = " AND ".join(filters)
            rows = conn.execute(
                f"""
                SELECT
                  m.*,
                  t.source AS thread_source,
                  t.external_id AS thread_external_id,
                  t.title AS thread_title,
                  t.url AS thread_url
                FROM conversation_messages m
                JOIN conversation_threads t ON t.id = m.thread_id
                WHERE {where}
                ORDER BY m.received_at DESC, m.created_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            messages = [store._row_to_dict(row) for row in rows]
            summary = store._conversation_summary(conn, business, limit=20)
            brain_index = []
            brain_root = store._business_root(business) / "brain"
            if brain_root.exists():
                brain_index = [
                    str(path.relative_to(store._business_root(business)))
                    for path in sorted(brain_root.rglob("*"))
                    if path.is_file()
                ][:50]

        input_md = [
            f"# Conversation Agent Task: {task_type}",
            "",
            f"- Business: {business}",
            f"- Business mode: {business_row.get('mode') or 'live'}",
            f"- Objective: {objective}",
            f"- Selected messages: {len(messages)}",
            f"- Direction filter: {direction}",
            f"- Status filter: {status}",
            f"- Source filter: {source_filter or 'all'}",
            f"- Max structured actions Takyon may apply: {max_actions}",
            f"- Apply actions requested: {'yes' if apply_actions else 'no'}",
            f"- Allow local outbound conversation records: {'yes' if allow_outbound_messages else 'no'}",
            f"- Allow queued external send/post jobs: {'yes' if allow_external_jobs else 'no'}",
            "",
            "## Business",
            "",
            f"- Name: {business_row.get('name') or business}",
            f"- Goal: {business_row.get('goal') or 'not set'}",
            "",
            "## Inbox Summary",
            "",
            f"- Active threads: {summary.get('active_threads')}",
            f"- Unresolved messages: {summary.get('unresolved_messages')}",
            f"- Latest message: {summary.get('latest_message_at') or 'none'}",
            "",
            "## Brain Files",
            "",
            *(f"- {item}" for item in brain_index),
            "",
            "## Required Outputs",
            "",
            "- `triage.md`: compressed findings, priority groups, recommended CEO-level decisions.",
            "- `drafts.md`: human-readable drafts or response principles when useful.",
            "- `learnings.md`: reusable objections, language, opportunities, and strategy updates.",
            "- `actions.json`: optional structured actions Takyon can apply after this worker returns.",
        ]
        _atomic_write_text(workspace_path / "input.md", "\n".join(input_md).rstrip() + "\n")
        _atomic_write_text(workspace_path / "messages.jsonl", "".join(_json_dumps(message) + "\n" for message in messages))
        _atomic_write_text(
            workspace_path / "actions.json",
            _json_dumps({
                "outbound_messages": [],
                "status_updates": [],
                "job_requests": [],
                "memory_updates": [],
            }) + "\n",
        )

        instruction = "\n".join([
            "Use the takyon:conversation-response method for this bounded task.",
            "Read input.md and messages.jsonl in the current workspace.",
            "Do not try to read every historical conversation unless the task slice requires it.",
            "Use business impact, volume, recency, risk, budget, and the stated objective to decide what matters.",
            "You may triage, batch, sample, ignore, escalate, learn from, or draft replies; do not assume every message deserves a response.",
            "Do not perform external side effects. If external sending/posting is warranted, add a guarded job request to actions.json instead of claiming it happened.",
            "Write triage.md, drafts.md, learnings.md, and actions.json.",
            "actions.json schema: outbound_messages[], status_updates[], job_requests[], memory_updates[].",
            "outbound_messages are local conversation records, not external sends; include source, thread_external_id or thread_id, thread_title, body, and optional external_id.",
            "status_updates include message_id or source/external_id plus status: needs_response, responded, ignored, or archived.",
            "job_requests include kind, payload, requires_api[], requires_env[].",
            "memory_updates include path, content, and optional mode.",
            f"Task objective: {objective}",
        ])
        worker_raw = handle_business_claude_agent_task(
            {
                "business": business,
                "workspace": workspace_raw,
                "instruction": instruction,
                "budget_usd": args.get("budget_usd") or 2.0,
                "model": args.get("model"),
                "max_turns": args.get("max_turns") or 12,
                "timeout_ms": args.get("timeout_ms") or 300_000,
                "idempotency_key": f"{idempotency_key}:conversation-worker",
                "reason": args.get("reason") or "conversation response agent task",
                "actor": args.get("actor") or "agent",
            }
        )
        worker = json.loads(worker_raw)
        action_commit = None
        action_error = None
        action_counts: dict[str, int] = {}
        if worker.get("success") and apply_actions and max_actions > 0:
            try:
                actions = _read_conversation_agent_actions(workspace_path / "actions.json")
                operations: list[dict[str, Any]] = []
                for item in list(actions.get("outbound_messages") or [])[:max_actions]:
                    if not allow_outbound_messages or not isinstance(item, dict):
                        continue
                    operations.append({
                        "action": "conversation.message.record",
                        "business": business,
                        "source": item.get("source") or source_filter or "conversation-agent",
                        "thread_id": item.get("thread_id"),
                        "thread_external_id": item.get("thread_external_id"),
                        "thread_title": item.get("thread_title") or item.get("subject") or "Response agent thread",
                        "url": item.get("url"),
                        "external_id": item.get("external_id") or f"{task_id}:outbound:{len(operations)}",
                        "direction": "outbound",
                        "author_label": item.get("author_label") or "Takyon response agent",
                        "body": item.get("body") or "",
                        "status": item.get("status") or "responded",
                    })
                for item in list(actions.get("status_updates") or [])[:max_actions]:
                    if not isinstance(item, dict):
                        continue
                    operations.append({
                        "action": "conversation.message.status.set",
                        "business": business,
                        "message_id": item.get("message_id"),
                        "source": item.get("source"),
                        "external_id": item.get("external_id"),
                        "status": item.get("status"),
                    })
                if allow_external_jobs:
                    for item in list(actions.get("job_requests") or [])[:max_actions]:
                        if not isinstance(item, dict):
                            continue
                        operations.append({
                            "action": "job.enqueue",
                            "business": business,
                            "scope": f"business:{business}/workspace:{workspace_raw}",
                            "kind": item.get("kind") or "conversation_response_send",
                            "payload": item.get("payload") or {},
                            "requires_api": item.get("requires_api") or [],
                            "requires_env": item.get("requires_env") or [],
                        })
                for item in list(actions.get("memory_updates") or [])[:max_actions]:
                    if not isinstance(item, dict):
                        continue
                    operations.append({
                        "action": "memory.write",
                        "business": business,
                        "path": item.get("path") or "conversation-learnings.md",
                        "content": item.get("content") or "",
                        "mode": item.get("mode") or "replace",
                    })
                if operations:
                    action_commit = store.commit(
                        scope=f"business:{business}/workspace:{workspace_raw}",
                        operations=operations[:max_actions],
                        idempotency_key=f"{idempotency_key}:conversation-actions",
                        reason=args.get("reason") or "apply conversation response agent actions",
                        actor=args.get("actor") or "conversation-agent",
                    )
                    for result in action_commit.get("results") or []:
                        key = str(result.get("action") or "unknown")
                        action_counts[key] = action_counts.get(key, 0) + 1
            except Exception as exc:
                action_error = str(exc)

        event_payload = {
            "task_type": task_type,
            "objective": objective,
            "workspace": workspace_raw,
            "selected_messages": len(messages),
            "apply_actions": apply_actions,
            "action_counts": action_counts,
            "action_error": action_error,
            "worker_success": bool(worker.get("success")),
        }
        event_record = store.commit(
            scope=f"business:{business}/workspace:{workspace_raw}",
            operations=[{"action": "event.record", "business": business, "event_type": "conversation.agent_task", "payload": event_payload}],
            idempotency_key=f"{idempotency_key}:conversation-event",
            reason=args.get("reason") or "record conversation response agent task",
            actor=args.get("actor") or "agent",
        )

        return tool_result({
            "success": bool(worker.get("success")) and not action_error,
            "business": business,
            "task_type": task_type,
            "workspace": workspace_raw,
            "input": f"{workspace_raw}/input.md",
            "messages": f"{workspace_raw}/messages.jsonl",
            "actions": f"{workspace_raw}/actions.json",
            "selected_messages": len(messages),
            "worker": worker,
            "action_commit": action_commit,
            "action_error": action_error,
            "event_record": event_record,
        })
    except Exception as exc:
        return tool_error(str(exc), success=False)


TAKYON_TOOL_DEFINITIONS = [
    {
        "name": "business_registry",
        "description": "Read the business tool registry, Takyon skill registry, and runtime capability snapshot such as video_generation openai/sora availability.",
        "handler": handle_business_registry,
        "schema": _schema(
            "business_registry",
            "Read the business tool and Takyon skill registry plus runtime capabilities.",
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
        "name": "business_calculate_pulse",
        "description": "Read-only deterministic pulse calculation from canonical business state, app metrics, conversations, jobs, ledger, and events.",
        "handler": handle_business_calculate_pulse,
        "schema": _schema("business_calculate_pulse", "Calculate a business pulse without mutating state.", {"business": _BUSINESS_PROP, "limit": {"type": "integer", "description": "Top grouped rows to return; default 10"}}, ["business"]),
    },
    {
        "name": "business_check_runtime_capabilities",
        "description": "Inspect local runtimes, package managers, and command capabilities; optionally run guarded local provisioning for supported ecosystems.",
        "handler": handle_business_check_runtime_capabilities,
        "schema": _schema(
            "business_check_runtime_capabilities",
            "Check runtimes and package-manager capabilities for product builds, app verification, and scoped workers.",
            {
                "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Executable or capability names to inspect, such as node, npm, python, uv, git, or rg."},
                "ecosystems": {"type": "array", "items": {"type": "string"}, "description": "Optional ecosystems to ensure when supported, such as javascript, javascript-package-manager, or python."},
                "ensure": {"type": "string", "description": "Single ecosystem alias to ensure; use ecosystems for more than one."},
            },
            [],
        ),
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
            {"business": _BUSINESS_PROP, "name": {"type": "string"}, "goal": {"type": "string"}, "mode": {"type": "string", "description": "Optional initial mode: live or test"}, "work_focus": {"type": "string", "description": "Optional work focus: all, marketing, or product"}, "budget": {"type": "object"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_delete_business",
        "description": "Dry-run or permanently delete one business, including filesystem, CEO cron jobs, and its fourmanifold.com/Vercel subdomain.",
        "handler": handle_business_delete_business,
        "schema": _schema(
            "business_delete_business",
            "Delete a business and owned runtime artifacts. Dry-run unless confirm=true.",
            {
                "business": _BUSINESS_PROP,
                "confirm": {"type": "boolean", "description": "Required true for permanent deletion; false previews only"},
                "delete_files": {"type": "boolean", "description": "Delete .takyon/businesses/<business> filesystem tree; default true"},
                "delete_cron": {"type": "boolean", "description": "Delete Takyon CEO cron jobs for this business; default true"},
                "delete_domains": {"type": "boolean", "description": "Remove the business subdomain from the Vercel project; default true"},
                "base_domain": {"type": "string", "description": "Base domain for business subdomains; defaults to PUBLIC_COMPANY_BASE_DOMAIN or fourmanifold.com"},
                "subdomains": {"type": "array", "items": {"type": "string"}, "description": "Additional explicit business-owned subdomains under the base domain"},
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_set_mode",
        "description": "Set one business to live or test mode. Test mode keeps local work and cron active while suppressing outbound side effects.",
        "handler": handle_business_set_mode,
        "schema": _schema(
            "business_set_mode",
            "Set business live/test mode.",
            {"business": _BUSINESS_PROP, "mode": {"type": "string", "description": "live or test"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "mode", "idempotency_key"],
        ),
    },
    {
        "name": "business_set_work_focus",
        "description": "Set one business to all, marketing-only, or product-only work focus for future CEO turns and cron wakes.",
        "handler": handle_business_set_work_focus,
        "schema": _schema(
            "business_set_work_focus",
            "Set business work focus.",
            {
                "business": _BUSINESS_PROP,
                "work_focus": {"type": "string", "description": "all, marketing, or product"},
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "work_focus", "idempotency_key"],
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
        "name": "business_configure_app_budget",
        "description": "Set the business product app's overall usage budget cap for one business.",
        "handler": handle_business_configure_app_budget,
        "schema": _schema("business_configure_app_budget", "Set product app budget cap.", {"business": _BUSINESS_PROP, "hard_limit_microusd": {"type": "integer"}, "status": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "hard_limit_microusd", "idempotency_key"]),
    },
    {
        "name": "business_upsert_app_surface_contract",
        "description": "Record the business-owned product surface contract: design brief, source path, runtime API base, routes, theme source, and UI constraints.",
        "handler": handle_business_upsert_app_surface_contract,
        "schema": _schema(
            "business_upsert_app_surface_contract",
            "Create/update product app surface contract.",
            {
                "business": _BUSINESS_PROP,
                "status": {"type": "string"},
                "design_brief_path": {"type": "string"},
                "source_path": {"type": "string"},
                "runtime_api_base": {"type": "string"},
                "routes": {"type": "array", "items": {"type": "object"}},
                "theme": {"type": "object"},
                "constraints": {"type": "object"},
                "notes": {"type": "string"},
                "metadata": {"type": "object"},
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_verify_product_surface",
        "description": "Verify that a business product/website source path exists and builds or record a concrete blocker receipt.",
        "handler": handle_business_verify_product_surface,
        "schema": _schema(
            "business_verify_product_surface",
            "Verify product surface source/build and write a receipt.",
            {
                "business": _BUSINESS_PROP,
                "source_path": {"type": "string", "description": "Business-relative source path; defaults to the app surface contract source_path"},
                "install": {"type": "boolean", "description": "Run package install before build when package.json exists; default true"},
                "timeout_seconds": {"type": "integer", "description": "Per command timeout; default 180"},
                "activate_on_success": {"type": "boolean", "description": "Mark the app surface active when verification passes; default true"},
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_upsert_app_plan",
        "description": "Create or update a business product app plan policy, including Stripe price linkage and included usage.",
        "handler": handle_business_upsert_app_plan,
        "schema": _schema("business_upsert_app_plan", "Create/update product app plan.", {"business": _BUSINESS_PROP, "plan_key": {"type": "string"}, "tier": {"type": "string", "description": "Entitlement tier unlocked by this plan"}, "price_cents": {"type": "integer"}, "currency": {"type": "string"}, "billing_interval": {"type": "string", "enum": ["month", "year", "one_time"], "description": "Canonical interval. Common aliases like monthly/yearly/once are normalized."}, "included_ai_budget_microusd": {"type": "integer"}, "included_action_quota": {"type": "integer"}, "allow_overage": {"type": "boolean"}, "stripe_product_id": {"type": "string"}, "stripe_price_id": {"type": "string"}, "notes": {"type": "string"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "plan_key", "idempotency_key"]),
    },
    {
        "name": "business_upsert_app_customer",
        "description": "Create or update a product subuser/customer for one business app.",
        "handler": handle_business_upsert_app_customer,
        "schema": _schema("business_upsert_app_customer", "Create/update product app customer.", {"business": _BUSINESS_PROP, "email": {"type": "string"}, "name": {"type": "string"}, "status": {"type": "string"}, "tier": {"type": "string"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "email", "idempotency_key"]),
    },
    {
        "name": "business_grant_app_entitlement",
        "description": "Grant a product customer a free or explicit non-billing entitlement. Paid billing entitlements require Stripe/webhook evidence.",
        "handler": handle_business_grant_app_entitlement,
        "schema": _schema("business_grant_app_entitlement", "Grant product app entitlement.", {"business": _BUSINESS_PROP, "app_user_id": {"type": "string"}, "email": {"type": "string"}, "tier": {"type": "string"}, "status": {"type": "string"}, "source": {"type": "string"}, "plan_key": {"type": "string"}, "current_period_end": {"type": "string"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "tier", "idempotency_key"]),
    },
    {
        "name": "business_request_app_magic_link",
        "description": "Create a one-use product customer magic-link token and optionally send it via Postmark.",
        "handler": handle_business_request_app_magic_link,
        "schema": _schema("business_request_app_magic_link", "Request product app magic link.", {"business": _BUSINESS_PROP, "email": {"type": "string"}, "name": {"type": "string"}, "origin": {"type": "string"}, "app_slug": {"type": "string"}, "product_name": {"type": "string"}, "send_email": {"type": "boolean"}, "purpose": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "email"]),
    },
    {
        "name": "business_verify_app_magic_link",
        "description": "Consume a product customer magic link and create a 30-day app session token.",
        "handler": handle_business_verify_app_magic_link,
        "schema": _schema("business_verify_app_magic_link", "Verify product app magic link.", {"business": _BUSINESS_PROP, "token": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "token"]),
    },
    {
        "name": "business_read_app_account",
        "description": "Read a product customer account, entitlements, revenue, and usage by session token, app user id, or email.",
        "handler": handle_business_read_app_account,
        "schema": _schema("business_read_app_account", "Read product app account.", {"business": _BUSINESS_PROP, "session_token": {"type": "string"}, "app_user_id": {"type": "string"}, "email": {"type": "string"}}, ["business"]),
    },
    {
        "name": "business_create_app_checkout",
        "description": "Create a Stripe Checkout session for a business product app plan and record the checkout intent.",
        "handler": handle_business_create_app_checkout,
        "schema": _schema("business_create_app_checkout", "Create product app Stripe checkout.", {"business": _BUSINESS_PROP, "plan_key": {"type": "string"}, "success_url": {"type": "string"}, "cancel_url": {"type": "string"}, "customer_email": {"type": "string"}, "app_user_id": {"type": "string"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "plan_key", "success_url", "cancel_url"]),
    },
    {
        "name": "business_record_stripe_webhook",
        "description": "Verify and reconcile Stripe webhook events into app checkout sessions, entitlements, subscription status, and revenue.",
        "handler": handle_business_record_stripe_webhook,
        "schema": _schema("business_record_stripe_webhook", "Record/reconcile Stripe webhook.", {"raw_body": {"type": "string"}, "stripe_signature": {"type": "string"}, "event": {"type": "object"}, "event_payload": {"type": "object"}}, []),
    },
    {
        "name": "business_record_app_usage",
        "description": "Record product app usage under the business app budget cap.",
        "handler": handle_business_record_app_usage,
        "schema": _schema("business_record_app_usage", "Record product app usage.", {"business": _BUSINESS_PROP, "app_user_id": {"type": "string"}, "app_user_tier": {"type": "string"}, "purpose": {"type": "string"}, "route": {"type": "string"}, "status": {"type": "string"}, "estimated_cost_microusd": {"type": "integer"}, "actual_cost_microusd": {"type": "integer"}, "input_tokens": {"type": "integer"}, "output_tokens": {"type": "integer"}, "provider_request_id": {"type": "string"}, "provider": {"type": "string"}, "model": {"type": "string"}, "metadata": {"type": "object"}, "error": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "purpose", "route", "idempotency_key"]),
    },
    {
        "name": "business_enqueue_job",
        "description": "Record a guarded request for external work such as ad posting, publishing, vendor calls, builds, or deploys.",
        "handler": handle_business_enqueue_job,
        "schema": _schema("business_enqueue_job", "Record a guarded business work request.", {"business": _BUSINESS_PROP, "scope": {"type": "string"}, "kind": {"type": "string"}, "payload": {"type": "object"}, "status": {"type": "string"}, "requires_api": _REQUIRES_API_PROP, "requires_env": _REQUIRES_ENV_PROP, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "kind", "idempotency_key"]),
    },
    {
        "name": "business_publish_outreach",
        "description": "Publish outreach through one mode-aware intent: test mode creates a local suppressed receipt and conversation mirror; live mode records a gated provider publish job.",
        "handler": handle_business_publish_outreach,
        "schema": _schema(
            "business_publish_outreach",
            "Publish outreach using the business mode bright line.",
            {"business": _BUSINESS_PROP, "channel": {"type": "string"}, "provider": {"type": "string"}, "target": {"type": "string"}, "recipient": {"type": "string"}, "subject": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "content": {"type": "string"}, "thread_external_id": {"type": "string"}, "metadata": {"type": "object"}, "kind": {"type": "string"}, "status": {"type": "string"}, "requires_api": _REQUIRES_API_PROP, "requires_env": _REQUIRES_ENV_PROP, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "body", "idempotency_key"],
        ),
    },
    {
        "name": "business_publish_test_outreach",
        "description": "In test mode, publish outreach locally, create a suppressed-side-effect receipt, and mirror it into business conversations without sending externally.",
        "handler": handle_business_publish_test_outreach,
        "schema": _schema(
            "business_publish_test_outreach",
            "Publish test outreach locally without sending.",
            {"business": _BUSINESS_PROP, "channel": {"type": "string"}, "provider": {"type": "string"}, "target": {"type": "string"}, "recipient": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "thread_external_id": {"type": "string"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "body", "idempotency_key"],
        ),
    },
    {
        "name": "business_generate_creative_asset",
        "description": "Generate a provider-backed image or video creative as a local business-scoped asset with a receipt; posting and ad spend stay separate queued/gated work.",
        "handler": handle_business_generate_creative_asset,
        "schema": _schema(
            "business_generate_creative_asset",
            "Generate a local business creative asset.",
            {
                "business": _BUSINESS_PROP,
                "kind": {"type": "string", "description": "video or image"},
                "channel": {"type": "string", "description": "meta, tiktok, x, or linkedin"},
                "format": {"type": "string", "description": "creative format, currently ugc"},
                "campaign": {"type": "string", "description": "Campaign workspace name; default is default"},
                "asset_id": {"type": "string", "description": "Optional stable asset id; defaults from idempotency_key"},
                "prompt": {"type": "string", "description": "Generation prompt. If omitted, script and shot_list are combined."},
                "script": {"type": "string", "description": "UGC script or voiceover text"},
                "shot_list": {"type": "array", "items": {"type": "string"}, "description": "Ordered UGC shots or beats"},
                "provider": {"type": "string", "description": "Provider credential alias to gate, e.g. fal, openai, or xai. The active generator backend still comes from Takyon tools config."},
                "model": {"type": "string", "description": "Optional model override passed to the generator"},
                "output_path": {"type": "string", "description": "Optional business-relative output path; default campaigns/<campaign>/creatives/<channel>-ugc/<asset-id>.<ext>"},
                "budget_usd": {"type": "number", "description": "Required spend allocation under the business budget cap before calling a provider"},
                "image_url": {"type": "string", "description": "Optional source image URL for image-to-video"},
                "reference_image_urls": {"type": "array", "items": {"type": "string"}},
                "duration": {"type": "integer"},
                "aspect_ratio": {"type": "string"},
                "resolution": {"type": "string"},
                "negative_prompt": {"type": "string"},
                "audio": {"type": "boolean"},
                "seed": {"type": "integer"},
                "requires_api": _REQUIRES_API_PROP,
                "requires_env": _REQUIRES_ENV_PROP,
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "kind", "channel", "format", "budget_usd", "idempotency_key"],
        ),
    },
    {
        "name": "business_claude_agent_task",
        "description": "Run a general Claude Agent SDK worker inside one business workspace with path containment, Anthropic credential checks, budget allocation, and an agent-run audit record.",
        "handler": handle_business_claude_agent_task,
        "schema": _schema(
            "business_claude_agent_task",
            "Run a scoped Claude Agent SDK task for a business.",
            {
                "business": _BUSINESS_PROP,
                "workspace": {"type": "string", "description": "Business-relative workspace directory; default '.'"},
                "instruction": {"type": "string", "description": "Bounded task for the Claude SDK worker"},
                "budget_usd": {"type": "number", "description": "Per-task spend reservation, default 2.0 and capped at 25.0"},
                "model": {"type": "string", "description": "Optional Claude model override"},
                "max_turns": {"type": "integer", "description": "SDK turn cap, default 12"},
                "timeout_ms": {"type": "integer", "description": "Wall-clock timeout, default 300000"},
                "verify_surface": {"type": "boolean", "description": "Verify product/website source after edits and write a receipt; product/* workspaces default to verification"},
                "install": {"type": "boolean", "description": "Run package install before build during verification; default true"},
                "verification_timeout_seconds": {"type": "integer", "description": "Per verification command timeout; default 180"},
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "instruction", "idempotency_key"],
        ),
    },
    {
        "name": "business_conversation_agent_task",
        "description": "Delegate bounded per-business conversation response work to a scoped worker, optionally applying capped local conversation actions through guarded tools.",
        "handler": handle_business_conversation_agent_task,
        "schema": _schema(
            "business_conversation_agent_task",
            "Run a scoped conversation response agent task for a business.",
            {
                "business": _BUSINESS_PROP,
                "task_type": {"type": "string", "description": "triage, cluster, draft_replies, respond, extract_learnings, or identify_leads"},
                "objective": {"type": "string", "description": "Business objective for this conversation slice"},
                "source": {"type": "string", "description": "Optional source/channel filter"},
                "direction": {"type": "string", "description": "Message direction filter; default inbound, or all"},
                "status": {"type": "string", "description": "Message status filter; default needs_response, or all"},
                "limit": {"type": "integer", "description": "Maximum messages to pass to the worker, default 100 and capped at 500"},
                "workspace": {"type": "string", "description": "Optional business-relative task workspace"},
                "apply_actions": {"type": "boolean", "description": "Apply capped actions from actions.json after worker completes"},
                "allow_outbound_messages": {"type": "boolean", "description": "Allow local outbound conversation records from actions.json"},
                "allow_external_jobs": {"type": "boolean", "description": "Allow queued external send/post job requests from actions.json"},
                "max_actions": {"type": "integer", "description": "Maximum structured actions to apply, default 20 and capped at 100"},
                "budget_usd": {"type": "number", "description": "Per-task spend reservation, default 2.0 and capped by the underlying worker"},
                "model": {"type": "string", "description": "Optional Claude model override"},
                "max_turns": {"type": "integer", "description": "SDK turn cap, default 12"},
                "timeout_ms": {"type": "integer", "description": "Wall-clock timeout, default 300000"},
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_upsert_conversation_thread",
        "description": "Create or update a business-owned conversation thread and its Markdown mirror under conversations/.",
        "handler": handle_business_upsert_conversation_thread,
        "schema": _schema(
            "business_upsert_conversation_thread",
            "Create/update a business conversation thread.",
            {"business": _BUSINESS_PROP, "source": {"type": "string"}, "external_id": {"type": "string"}, "title": {"type": "string"}, "url": {"type": "string"}, "status": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "source", "title", "idempotency_key"],
        ),
    },
    {
        "name": "business_record_conversation_message",
        "description": "Record an inbound, outbound, or internal conversation message; unresolved inbound replies are stored as needs_response and mirrored to the business filesystem.",
        "handler": handle_business_record_conversation_message,
        "schema": _schema(
            "business_record_conversation_message",
            "Record a business conversation message.",
            {"business": _BUSINESS_PROP, "source": {"type": "string"}, "thread_id": {"type": "string"}, "thread_external_id": {"type": "string"}, "thread_title": {"type": "string"}, "url": {"type": "string"}, "external_id": {"type": "string"}, "direction": {"type": "string"}, "author_label": {"type": "string"}, "body": {"type": "string"}, "status": {"type": "string"}, "received_at": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "source", "thread_title", "body", "idempotency_key"],
        ),
    },
    {
        "name": "business_update_conversation_message_status",
        "description": "Update one business conversation message status without rewriting the message body.",
        "handler": handle_business_update_conversation_message_status,
        "schema": _schema(
            "business_update_conversation_message_status",
            "Update a business conversation message status.",
            {"business": _BUSINESS_PROP, "message_id": {"type": "string"}, "source": {"type": "string"}, "external_id": {"type": "string"}, "status": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "status", "idempotency_key"],
        ),
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
    {
        "name": "business_upgrade_businesses",
        "description": "Dry-run or apply idempotent compatibility migrations for old businesses without inventing generated assets or fake receipts.",
        "handler": handle_business_upgrade_businesses,
        "schema": _schema(
            "business_upgrade_businesses",
            "Upgrade business compatibility metadata and mirrors.",
            {
                "businesses": {"type": "array", "items": {"type": "string"}, "description": "Optional business slugs; omit for all businesses"},
                "business": _BUSINESS_PROP,
                "apply": {"type": "boolean", "description": "False/default previews only; true applies migrations and writes receipts"},
                "confirm": {"type": "boolean", "description": "Alias for apply=true"},
            },
            [],
        ),
    },
]
