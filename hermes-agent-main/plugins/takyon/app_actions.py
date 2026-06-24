from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from croniter import croniter


_ACTION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_OUTBOUND_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")
_INTERNAL_HOST_SUFFIXES = (".localhost", ".internal", ".local", ".cluster.local")


def _is_blocked_ip_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


def _is_internal_host(hostname: str) -> bool:
    """True if the hostname is loopback / link-local / private / otherwise internal — so it must
    never be added to the deno sandbox's --allow-net allowlist (SSRF guard for both the customer
    outbound_hosts allowlist AND the rails origin). Blocks IP literals directly and resolves bare
    names so a public name pointing at an internal address (169.254.169.254 metadata, loopback,
    RFC1918/RFC4193) is rejected too."""
    h = str(hostname or "").strip().lower().rstrip(".")
    if not h or h in {"localhost", "0.0.0.0"} or h.endswith(_INTERNAL_HOST_SUFFIXES):
        return True

    # bracketed/raw IP literal
    if _is_blocked_ip_address(h.strip("[]")):
        return True
    # bare hostname -> resolve every A/AAAA and reject if ANY is internal (DNS-rebinding defense)
    try:
        for info in socket.getaddrinfo(h, None):
            if _is_blocked_ip_address(str(info[4][0])):
                return True
    except (socket.gaierror, UnicodeError, OSError):
        return False  # transient/invalid DNS is not itself an internal host; literal checks above hold
    return False
_SERVICE_EMAIL_SUFFIX = ".takyon.invalid"
_ACTION_REQUEST_BODY_LIMIT = 8 * 1024 * 1024  # raised from 64KB to allow image-bearing action payloads (identify-rock, solve-homework)
_ACTION_STDOUT_LIMIT = 256 * 1024
_ACTION_STDERR_LIMIT = 16 * 1024
_ACTION_MIN_INTERVAL_SECONDS = 15 * 60
_ACTION_CONTEXT_PREFIX = "/api/takyon/apps/{business}"
_HOST_ROLE_ENV = "TAKYON_HOST_ROLE"
# Platform rails a server-side action may call. The action runs where these rails are reachable and
# the SERVER is the authority (validate_session + plan/budget per rail), so the shared client's
# ensureRail() — a browser-side UX guard keyed on the product's declared UI features — must not
# pre-block them for actions. media/email gate server-side on declaration, so they are deliberately
# omitted here and follow the surface's declared runtime_features instead.
_ACTION_RUNTIME_RAILS = ("generate", "actions", "records", "search", "connections", "profile", "directory")
_ACTION_TRIGGER_ALIASES = {
    "user": "http",
    "manual": "http",
    "invoke": "http",
    "cron": "schedule",
    "scheduled": "schedule",
}
_SYSTEMD_SCOPE_START_FAILURE_MARKERS = (
    "Failed to start transient scope unit",
    "Failed to connect to bus",
    "Interactive authentication required",
    "Access denied",
    "No medium found",
)

_DEFAULT_CONFIG = {
    "rails_base_url": "",
    "invoke_price_microusd": 2_000,
    "http_timeout_seconds": 60,
    "schedule_timeout_seconds": 120,
    "cpu_quota_percent": 50,
    "memory_max_mb": 256,
}

_ACTION_RUNNER_SOURCE = r"""
const [actionUrl, clientUrl] = Deno.args;
if (!actionUrl) {
  console.error("missing action module url");
  Deno.exit(1);
}

const originalConsole = console;
globalThis.console = {
  ...console,
  log: (...args) => originalConsole.error(...args),
  info: (...args) => originalConsole.error(...args),
  debug: (...args) => originalConsole.error(...args),
};

const raw = await new Response(Deno.stdin.readable).text();
let request = {};
if (raw.trim()) {
  request = JSON.parse(raw);
}

const bare = request.ctx ?? {};
const baseUrl = String(bare.base_url || "");
const sessionToken = String(bare.session_token || "");

// The action runs sandboxed with no cookie, so the shared client's same-origin requests carry no
// auth. Inject the customer's business-scoped Bearer session token, but ONLY for requests to the
// rails origin (baseUrl prefix) so the token can never leak to a declared outbound host. Authority
// stays server-side: the rail still validates the session against this business + enforces budget.
const realFetch = globalThis.fetch.bind(globalThis);
globalThis.fetch = (input, init = {}) => {
  const url = typeof input === "string" ? input : (input && input.url) || "";
  if (baseUrl && sessionToken && url.startsWith(baseUrl)) {
    const headers = new Headers(init.headers || (typeof input === "object" ? input.headers : undefined) || {});
    if (!headers.has("Authorization")) headers.set("Authorization", "Bearer " + sessionToken);
    init = { ...init, headers };
  }
  return realFetch(input, init);
};

// ctx IS the shared runtime client (the SAME createSubuserRuntimeClient the browser UI uses), so
// ctx.generate / ctx.invokeAction / ctx.saveRecord / ctx.listRecords work identically and there is
// one source of truth (runtime-client.js) instead of an asymmetric data bag the action must guess at.
let ctx = bare;
if (clientUrl && baseUrl) {
  try {
    const { createSubuserRuntimeClient } = await import(clientUrl);
    const client = createSubuserRuntimeClient({
      runtimeApiBase: baseUrl,
      runtimeFeatures: bare.runtime_features ?? [],
      railState: bare.rail_state ?? {},
      location: { origin: new URL(baseUrl).origin, href: baseUrl },
    });
    ctx = Object.assign(Object.create(client), {
      business: bare.business,
      trigger: bare.trigger,
      principal: bare.principal,
      base_url: baseUrl,
      session_token: sessionToken,
    });
  } catch (err) {
    console.error("runtime client unavailable: " + (err && err.message));
    ctx = bare;
  }
}

// Belt-and-suspenders: if ctx is the bare fallback bag (the shared client failed to load above),
// it has no .generate. Attach the inline generate rail so a generate-only action still works
// without the materialized client. No-op when ctx already provides generate (the normal path).
if (typeof ctx.generate !== "function" && ctx.base_url && ctx.session_token) {
  ctx.generate = async (genPayload = {}) => {
    const res = await fetch(`${ctx.base_url}/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${ctx.session_token}`,
      },
      body: JSON.stringify(genPayload ?? {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(String((data && data.error) || `generate_failed:${res.status}`));
      err.status = res.status;
      err.payload = data;
      throw err;
    }
    return data;
  };
}

const mod = await import(actionUrl);
// Resolve the handler from the common shapes workers write: `export default fn`,
// `export default { run | handler }`, or a named `export { run | handler }`. It is always
// invoked as handler(payload, ctx); the canonical shape is
// `export default async (payload, ctx) => result` (see the worker action contract).
let handler = null;
if (mod) {
  if (typeof mod.default === "function") handler = mod.default;
  else if (mod.default && typeof mod.default === "object") {
    if (typeof mod.default.run === "function") handler = mod.default.run;
    else if (typeof mod.default.handler === "function") handler = mod.default.handler;
  }
  if (!handler && typeof mod.run === "function") handler = mod.run;
  if (!handler && typeof mod.handler === "function") handler = mod.handler;
}
if (typeof handler !== "function") {
  throw new Error("action module must export a handler: default async (payload, ctx) => result");
}

const result = await handler(request.payload ?? {}, ctx);
await Deno.stdout.write(new TextEncoder().encode(JSON.stringify({ ok: true, result })));
"""

_active_business_runs: set[str] = set()
_active_business_runs_lock = threading.Lock()
_LOGGER = logging.getLogger(__name__)


class AppActionError(RuntimeError):
    code = "app_action_error"


class ActionContractError(AppActionError):
    code = "action_contract_error"


class ActionConfigError(AppActionError):
    code = "action_config_error"


class ActionBudgetExceeded(AppActionError):
    code = "action_budget_exceeded"


class ActionAlreadyRunning(AppActionError):
    code = "action_already_running"


class ActionTimeout(AppActionError):
    code = "action_timeout"


class ActionResultTooLarge(AppActionError):
    code = "action_result_too_large"


class ActionReplayConflict(AppActionError):
    """A reused idempotency_key whose prior attempt is in flight or left an indeterminate state.
    Replaying it would either double-execute the action's side effect or re-run after the prior
    reservation already released its budget (ungated spend), so the replay is refused rather than
    re-run. A successful prior attempt is replayed from its receipt instead of raising this."""

    code = "action_replay_conflict"


@dataclass(frozen=True)
class RailsBase:
    origin: str
    hostport: str


def _normalized_host_role() -> str:
    return str(os.getenv(_HOST_ROLE_ENV) or "").strip().lower()


def _operator_host_requires_action_sandbox() -> bool:
    return _normalized_host_role() in {"operator", "subuser"}


def _split_outbound_hostport(value: str) -> tuple[str, str | None]:
    text = str(value or "").strip().lower()
    if ":" not in text:
        return text, None
    host, port = text.rsplit(":", 1)
    return host, port or None


def _format_deno_allow_net_ip(addr: str, port: str | None) -> str:
    if port:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return f"{addr}:{port}"
        if ip.version == 6:
            return f"[{addr}]:{port}"
        return f"{addr}:{port}"
    return addr


def _resolved_public_allow_net_entries(value: str) -> list[str]:
    host, port = _split_outbound_hostport(value)
    if not host:
        raise ActionConfigError("product action outbound host is empty")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise ActionConfigError(
            f"product action outbound host could not be resolved: {value}"
        ) from exc

    entries: list[str] = []
    seen: set[str] = set()
    for info in infos:
        addr = str(info[4][0])
        if _is_blocked_ip_address(addr):
            raise ActionConfigError(
                f"product action outbound host resolved to an internal address: {value}"
            )
        formatted = _format_deno_allow_net_ip(addr, port)
        if formatted not in seen:
            seen.add(formatted)
            entries.append(formatted)
    if not entries:
        raise ActionConfigError(
            f"product action outbound host could not be resolved: {value}"
        )
    return entries


def _systemd_user_manager_env() -> dict[str, str]:
    uid = os.getuid()
    runtime_dir = Path("/run/user") / str(uid)
    return {
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_dir}/bus",
    }


def _is_systemd_scope_start_failure(detail: str) -> bool:
    text = str(detail or "").strip()
    if not text:
        return False
    return any(marker in text for marker in _SYSTEMD_SCOPE_START_FAILURE_MARKERS)


def _communicate_action_process(
    command: list[str],
    *,
    request_bytes: bytes,
    timeout_seconds: int,
    env: Mapping[str, str] | None = None,
) -> tuple[int, bytes, bytes]:
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=dict(env) if env else None,
    )
    try:
        stdout, stderr = proc.communicate(input=request_bytes, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        raise ActionTimeout(f"action exceeded the {timeout_seconds}s deadline") from exc
    return proc.returncode, stdout, stderr


def _is_pg_conn(conn: Any) -> bool:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    return isinstance(conn, _PGConn)


def is_service_email(value: Any) -> bool:
    email = str(value or "").strip().lower()
    return email.endswith(_SERVICE_EMAIL_SUFFIX)


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index]


def normalize_outbound_hosts(raw_hosts: Any) -> list[str]:
    values = raw_hosts if isinstance(raw_hosts, list) else []
    seen: set[str] = set()
    hosts: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        hosts.append(text)
    return hosts


def normalize_action_specs(raw_actions: Any) -> list[dict[str, Any]]:
    values = raw_actions if isinstance(raw_actions, list) else []
    specs: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        spec: dict[str, Any] = {}
        name = str(value.get("name") or "").strip().lower()
        trigger = str(value.get("trigger") or "").strip().lower()
        schedule = str(value.get("schedule") or "").strip()
        if trigger:
            trigger = _ACTION_TRIGGER_ALIASES.get(trigger, trigger)
        elif schedule:
            trigger = "schedule"
        else:
            trigger = "http"
        description = str(value.get("description") or "").strip()
        if name:
            spec["name"] = name
        if trigger:
            spec["trigger"] = trigger
        if schedule:
            spec["schedule"] = schedule
        if description:
            spec["description"] = description
        if spec:
            specs.append(spec)
    return specs


def validate_action_contract(
    *,
    specs: list[dict[str, Any]],
    outbound_hosts: list[str],
    runtime_features: list[str],
) -> None:
    names: list[str] = []
    for spec in specs:
        name = str(spec.get("name") or "").strip().lower()
        if not _ACTION_NAME_RE.match(name):
            raise ActionContractError(
                f"product_workflow.actions names must be unique lowercase slugs (a-z, 0-9, -, _), got: {name or '<missing>'}"
            )
        names.append(name)
    if len(set(names)) != len(names):
        raise ActionContractError(
            "product_workflow.actions names must be unique lowercase slugs (a-z, 0-9, -, _)"
        )
    if len(specs) > 10:
        raise ActionContractError(f"product_workflow.actions allows at most 10 actions, got {len(specs)}")
    if len(outbound_hosts) > 8:
        raise ActionContractError(f"product_workflow.outbound_hosts allows at most 8 hosts, got {len(outbound_hosts)}")
    for spec in specs:
        name = str(spec.get("name") or "").strip().lower()
        trigger = str(spec.get("trigger") or "").strip().lower()
        schedule = str(spec.get("schedule") or "").strip()
        if trigger not in {"http", "schedule"}:
            raise ActionContractError(f"product_workflow.actions trigger for {name or '<missing>'} must be http or schedule, got: {trigger or '<missing>'}")
        if trigger == "schedule" and not schedule:
            raise ActionContractError(f"product_workflow.actions schedule for {name} is required when trigger is schedule")
        if trigger == "http" and schedule:
            raise ActionContractError(f"product_workflow.actions schedule for {name} is allowed only for schedule actions")
        if schedule:
            if not croniter.is_valid(schedule):
                raise ActionContractError(f"product_workflow.actions schedule for {name} is not a valid cron expression")
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            itr = croniter(schedule, base)
            first = itr.get_next(datetime)
            second = itr.get_next(datetime)
            third = itr.get_next(datetime)
            if min((second - first).total_seconds(), (third - second).total_seconds()) < _ACTION_MIN_INTERVAL_SECONDS:
                raise ActionContractError(
                    f"product_workflow.actions schedule for {name} fires more often than every 15 minutes; slow it down or make it an http action"
                )
    for value in outbound_hosts:
        if (
            not _OUTBOUND_HOST_RE.match(value)
            or "://" in value
            or "/" in value
            or "*" in value
            or _is_internal_host(value.rsplit(":", 1)[0])
        ):
            raise ActionContractError(
                f"product_workflow.outbound_hosts entries must be bare public hostnames (host or host:port), got: {value}"
            )
    return


def compute_next_run(schedule: str, after: datetime) -> datetime:
    base = after if after.tzinfo else after.replace(tzinfo=timezone.utc)
    return croniter(schedule, base).get_next(datetime)


_ACTION_UI_CALL_PATTERN = re.compile(
    r"\b(?:useActionRunner|createActionRunner|invokeAction)\s*\(\s*['\"]([a-z][a-z0-9_-]{0,63})['\"]",
    re.IGNORECASE,
)
_ACTION_EXPORT_TRIGGER_PATTERN = re.compile(
    r"""(?m)^[ \t]*export\s+const\s+trigger(?:\s*:\s*[^=\n]+)?\s*=\s*['"](?P<trigger>http|schedule)['"]""",
    re.IGNORECASE,
)
_ACTION_EXPORT_SCHEDULE_PATTERN = re.compile(
    r"""(?m)^[ \t]*export\s+const\s+schedule(?:\s*:\s*[^=\n]+)?\s*=\s*['"](?P<schedule>[^'"]+)['"]""",
    re.IGNORECASE,
)
_ACTION_DEFAULT_EXPORT_PATTERN = re.compile(r"""(?m)^[ \t]*export\s+default\b""")
_ACTION_DEFAULT_IDENTIFIER_EXPORT_PATTERN = re.compile(
    r"""(?m)^[ \t]*export\s+default\s+(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)\s*;?\s*$"""
)
_ACTION_DEFAULT_MEMBER_EXPORT_PATTERN = re.compile(
    r"""(?m)^[ \t]*export\s+default\s+(?P<namespace>[A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*default\s*;?\s*$"""
)
_ACTION_DEFAULT_REEXPORT_PATTERN = re.compile(
    r"""(?m)^[ \t]*export\s*\{[^}\n]*\bdefault\b[^}\n]*\}\s*from\s*['"](?P<target>[^'"]+)['"]"""
)
_ACTION_DEFAULT_IMPORT_PATTERN = re.compile(
    r"""(?m)^[ \t]*import\s+(?!type\b)(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)(?:\s*,\s*\{[^}]*\})?\s*from\s*['"](?P<target>[^'"]+)['"]"""
)
_ACTION_NAMESPACE_IMPORT_PATTERN = re.compile(
    r"""(?m)^[ \t]*import\s+\*\s+as\s+(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)\s+from\s*['"](?P<target>[^'"]+)['"]"""
)
_ACTION_NAMED_IMPORT_PATTERN = re.compile(
    r"""(?m)^[ \t]*import\s+(?:type\s+)?\{(?P<imports>[^}]*)\}\s*from\s*['"](?P<target>[^'"]+)['"]"""
)
_ACTION_SCAN_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte"}
_ACTION_SCAN_SKIP_DIRS = {".git", ".next", "_takyon", "build", "dist", "node_modules", "references"}


def _referenced_action_names_in_source(site_root: Path, *, limit: int = 300) -> set[str]:
    """Action names a product's UI actually invokes through the runtime client."""
    referenced: set[str] = set()
    if not site_root.exists():
        return referenced
    scanned = 0
    for path in sorted(site_root.rglob("*")):
        if scanned >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in _ACTION_SCAN_SOURCE_SUFFIXES:
            continue
        if _ACTION_SCAN_SKIP_DIRS & set(path.relative_to(site_root).parts):
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _ACTION_UI_CALL_PATTERN.finditer(text):
            referenced.add(match.group(1).strip().lower())
    return referenced


# Distinctive runtime-client rail methods an app calls in its own source. Mirrors how action
# names are scanned (_referenced_action_names_in_source) so a built product self-declares the
# data / media / AI / social rails it actually uses — the root cure for
# rail_unavailable:<rail>:undeclared. auth/account/profile/checkout are always-seeded shell
# rails and are intentionally omitted here; `actions` is derived separately from on-disk
# action files. The kit (`_takyon`) — which DEFINES these methods — is skipped by
# _ACTION_SCAN_SKIP_DIRS, so only real call sites in app source match.
_RUNTIME_RAIL_USAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("records", re.compile(r"\b(?:listRecords|getRecord|saveRecord|deleteRecord)\s*\(")),
    (
        "directory",
        re.compile(
            r"\b(?:listDirectory|getDirectoryMe|getDirectoryEntry|updateDirectoryMe|disableDirectoryMe)\s*\("
        ),
    ),
    ("media", re.compile(r"\b(?:uploadMedia|deleteMedia)\s*\(")),
    ("connections", re.compile(r"\b(?:listConnections|actOnConnection)\s*\(")),
    ("generate", re.compile(r"\b(?:ctx|client|runtime|rt)\.generate\s*\(")),
    ("search", re.compile(r"\b(?:ctx|client|runtime|rt)\.search\s*\(")),
)


def referenced_runtime_rails_in_source(site_root: Path, *, limit: int = 400) -> set[str]:
    """Runtime rails a product's own source actually calls through the shared runtime client.

    Scans the app-authored source (skipping the kit, build output, and vendored deps) for the
    distinctive runtime-client rail methods, so the surface contract's declared
    runtime_features can be DERIVED from real usage — declared >= used by construction,
    symmetric with how `actions` is derived from on-disk action files. Returns a subset of the
    data / media / AI / social rails; never the always-seeded shell rails.
    """
    used: set[str] = set()
    if not site_root.exists():
        return used
    pending = list(_RUNTIME_RAIL_USAGE_PATTERNS)
    scanned = 0
    for path in sorted(site_root.rglob("*")):
        if scanned >= limit or not pending:
            break
        if not path.is_file() or path.suffix.lower() not in _ACTION_SCAN_SOURCE_SUFFIXES:
            continue
        if _ACTION_SCAN_SKIP_DIRS & set(path.relative_to(site_root).parts):
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        still_pending: list[tuple[str, re.Pattern[str]]] = []
        for rail, pattern in pending:
            if pattern.search(text):
                used.add(rail)
            else:
                still_pending.append((rail, pattern))
        pending = still_pending
    return used


def _file_backed_action_names(site_root: Path, *, limit: int = 300) -> set[str]:
    """Action files physically present under product/site/actions."""
    names: set[str] = set()
    actions_root = site_root / "actions"
    if not actions_root.exists():
        return names
    scanned = 0
    for path in sorted(actions_root.glob("*.ts")):
        if scanned >= limit:
            break
        if not path.is_file():
            continue
        scanned += 1
        name = path.stem.strip().lower()
        if _ACTION_NAME_RE.match(name):
            names.add(name)
    return names


def _reexports_product_client_code(target: str) -> bool:
    normalized = str(target or "").strip().replace("\\", "/")
    return (
        normalized.startswith("src/")
        or normalized.startswith("./src/")
        or normalized.startswith("../src/")
        or "/src/" in normalized
    )


def _imported_runtime_identifiers_from_product_src(text: str) -> dict[str, str]:
    imported: dict[str, str] = {}

    def remember(identifier: str, target: str) -> None:
        name = str(identifier or "").strip()
        src = str(target or "").strip()
        if not name or not _reexports_product_client_code(src):
            return
        imported[name] = src

    for pattern in (_ACTION_DEFAULT_IMPORT_PATTERN, _ACTION_NAMESPACE_IMPORT_PATTERN):
        for match in pattern.finditer(text):
            remember(str(match.group("identifier") or ""), str(match.group("target") or ""))

    for match in _ACTION_NAMED_IMPORT_PATTERN.finditer(text):
        target = str(match.group("target") or "")
        if not _reexports_product_client_code(target):
            continue
        for raw_item in str(match.group("imports") or "").split(","):
            item = raw_item.strip()
            if not item or item.startswith("type "):
                continue
            if " as " in item:
                local_name = item.split(" as ", 1)[1].strip()
            else:
                local_name = item
            if local_name:
                imported[local_name] = target
    return imported


def _action_handler_blocker(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "cannot be read to verify a default-exported backend handler"
    reexport_match = _ACTION_DEFAULT_REEXPORT_PATTERN.search(text)
    if reexport_match:
        target = str(reexport_match.group("target") or "").strip()
        if _reexports_product_client_code(target):
            return f"re-exports client code from `{target}`; implement a real backend handler in this file"
        return ""
    imported_runtime_identifiers = _imported_runtime_identifiers_from_product_src(text)
    default_identifier_match = _ACTION_DEFAULT_IDENTIFIER_EXPORT_PATTERN.search(text)
    if default_identifier_match:
        identifier = str(default_identifier_match.group("identifier") or "").strip()
        target = imported_runtime_identifiers.get(identifier)
        if target:
            return f"default-exports `{identifier}` imported from client code `{target}`; implement a real backend handler in this file"
    default_member_match = _ACTION_DEFAULT_MEMBER_EXPORT_PATTERN.search(text)
    if default_member_match:
        namespace = str(default_member_match.group("namespace") or "").strip()
        target = imported_runtime_identifiers.get(namespace)
        if target:
            return f"default-exports `{namespace}.default` from client code `{target}`; implement a real backend handler in this file"
    if _ACTION_DEFAULT_EXPORT_PATTERN.search(text):
        return ""
    return "does not default export a backend handler"


def _workflow_action_specs_by_name(workflow: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    workflow = workflow if isinstance(workflow, Mapping) else {}
    return {
        str(spec.get("name") or "").strip().lower(): dict(spec)
        for spec in normalize_action_specs(workflow.get("actions"))
        if str(spec.get("name") or "").strip()
    }


def _action_spec_from_file(path: Path, *, fallback_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"name": path.stem.strip().lower()}
    fallback_trigger = str((fallback_spec or {}).get("trigger") or "").strip().lower()
    fallback_schedule = str((fallback_spec or {}).get("schedule") or "").strip()
    fallback_description = str((fallback_spec or {}).get("description") or "").strip()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    trigger_match = _ACTION_EXPORT_TRIGGER_PATTERN.search(text)
    schedule_match = _ACTION_EXPORT_SCHEDULE_PATTERN.search(text)
    schedule = str(schedule_match.group("schedule")).strip() if schedule_match else fallback_schedule
    trigger = (
        str(trigger_match.group("trigger")).strip().lower()
        if trigger_match
        else ("schedule" if schedule else (fallback_trigger or "http"))
    )
    if trigger:
        spec["trigger"] = trigger
    if schedule:
        spec["schedule"] = schedule
    if fallback_description:
        spec["description"] = fallback_description
    return spec


def file_backed_action_specs(
    site_root: Path,
    workflow: Mapping[str, Any] | None = None,
    *,
    limit: int = 300,
) -> list[dict[str, Any]]:
    """Action specs derived from real files, with workflow metadata only as compatibility fallback."""
    workflow_specs = _workflow_action_specs_by_name(workflow)
    actions_root = site_root / "actions"
    if not actions_root.exists():
        return []
    specs: list[dict[str, Any]] = []
    scanned = 0
    for path in sorted(actions_root.glob("*.ts")):
        if scanned >= limit:
            break
        if not path.is_file():
            continue
        scanned += 1
        name = path.stem.strip().lower()
        if not _ACTION_NAME_RE.match(name):
            continue
        specs.append(_action_spec_from_file(path, fallback_spec=workflow_specs.get(name)))
    return specs


def site_http_action_names(site_root: Path, surface: Mapping[str, Any]) -> set[str]:
    """HTTP-runnable action files physically present for a product/site workspace."""
    workflow = surface.get("product_workflow") if isinstance(surface.get("product_workflow"), Mapping) else {}
    file_backed_specs = file_backed_action_specs(site_root, workflow)
    if not file_backed_specs:
        return set()
    schedule_only = {
        str(spec.get("name") or "").strip().lower()
        for spec in file_backed_specs
        if str(spec.get("trigger") or "").strip().lower() == "schedule"
    }
    file_backed = {
        str(spec.get("name") or "").strip().lower()
        for spec in file_backed_specs
        if str(spec.get("name") or "").strip()
        and not _action_handler_blocker(site_root / "actions" / f"{str(spec.get('name') or '').strip().lower()}.ts")
    }
    http_runnable = {name for name in file_backed if name not in schedule_only}
    if not http_runnable:
        return set()
    referenced = _referenced_action_names_in_source(site_root)
    if referenced:
        return referenced & http_runnable
    return http_runnable


def surface_http_action_names(
    *,
    store: Any,
    business: str,
    surface: Mapping[str, Any],
    source_path: str,
) -> set[str]:
    """HTTP-runnable action files that make the actions rail truthfully callable."""
    business_root = store._business_root(business)
    site_root = (business_root / source_path) if source_path else (business_root / "product" / "site")
    return site_http_action_names(site_root, surface)


def action_refresh_blocker(*, store: Any, business: str, surface: Mapping[str, Any], source_path: str) -> str:
    """Minimal action-rail blocker: referenced UI actions must resolve to real backend handlers."""
    business_root = store._business_root(business)
    site_root = (business_root / source_path) if source_path else (business_root / "product" / "site")
    workflow = surface.get("product_workflow") if isinstance(surface.get("product_workflow"), Mapping) else {}
    referenced = _referenced_action_names_in_source(site_root)
    actions_root = site_root / "actions"
    file_backed = _file_backed_action_names(site_root)
    specs_by_name = {
        str(spec.get("name") or "").strip().lower(): spec
        for spec in file_backed_action_specs(site_root, workflow)
        if str(spec.get("name") or "").strip()
    }
    if not shutil.which("deno"):
        if referenced or actions_root.exists():
            return "actions rail requires the deno runtime on this host"
        return ""
    for action_name in sorted(referenced):
        if action_name not in file_backed:
            return f"product UI invokes action `{action_name}` but product/site/actions/{action_name}.ts does not exist"
        handler_blocker = _action_handler_blocker(actions_root / f"{action_name}.ts")
        if handler_blocker:
            return (
                f"product UI invokes action `{action_name}` but "
                f"product/site/actions/{action_name}.ts {handler_blocker}"
            )
        if str((specs_by_name.get(action_name) or {}).get("trigger") or "").strip().lower() == "schedule":
            return (
                f"product UI invokes action `{action_name}` but product/site/actions/{action_name}.ts "
                "is schedule-only; browser-triggered actions must remain HTTP actions"
            )
    return ""


def summarize_action_invocations(conn: Any, business_slug: str, actions: Any) -> list[dict[str, Any]]:
    specs = normalize_action_specs(actions)
    if not specs:
        return []

    bind = "%s" if _is_pg_conn(conn) else "?"
    query = (
        "SELECT status, error, created_at, completed_at "
        f"FROM app_usage_events WHERE business_slug = {bind} AND purpose = 'action_invoke' AND route = {bind} "
        "ORDER BY COALESCE(completed_at, created_at) DESC, created_at DESC LIMIT 1"
    )

    summaries: list[dict[str, Any]] = []
    for spec in specs:
        action_name = str(spec.get("name") or "").strip().lower()
        row = conn.execute(
            query,
            (business_slug, f"/api/takyon/apps/{business_slug}/actions/{action_name}"),
        ).fetchone()
        if row is None:
            summaries.append(
                {
                    "name": action_name,
                    "trigger": str(spec.get("trigger") or "").strip(),
                    "last_status": "never",
                    "last_invoked_at": "",
                    "last_error": "",
                }
            )
            continue
        raw_status = str(_row_value(row, "status", 0) or "").strip().lower()
        summaries.append(
            {
                "name": action_name,
                "trigger": str(spec.get("trigger") or "").strip(),
                "last_status": "ok" if raw_status == "completed" else "failed",
                "last_invoked_at": str(
                    _row_value(row, "completed_at", 3) or _row_value(row, "created_at", 2) or ""
                ).strip(),
                "last_error": str(_row_value(row, "error", 1) or "").strip(),
            }
        )
    return summaries


def _action_runtime_config() -> dict[str, Any]:
    config = dict(_DEFAULT_CONFIG)
    try:
        from takyon_cli.config import load_config

        loaded = load_config() or {}
        plugins = loaded.get("plugins") if isinstance(loaded, Mapping) else {}
        takyon_config = plugins.get("takyon") if isinstance(plugins, Mapping) else {}
        action_config = takyon_config.get("app_actions") if isinstance(takyon_config, Mapping) else {}
        if isinstance(action_config, Mapping):
            config.update({key: value for key, value in action_config.items() if value is not None})
    except Exception:
        pass
    env_overrides = {
        "rails_base_url": os.getenv("TAKYON_APP_ACTIONS_RAILS_BASE_URL"),
        "invoke_price_microusd": os.getenv("TAKYON_APP_ACTIONS_INVOKE_PRICE_MICROUSD"),
        "http_timeout_seconds": os.getenv("TAKYON_APP_ACTIONS_HTTP_TIMEOUT_SECONDS"),
        "schedule_timeout_seconds": os.getenv("TAKYON_APP_ACTIONS_SCHEDULE_TIMEOUT_SECONDS"),
        "cpu_quota_percent": os.getenv("TAKYON_APP_ACTIONS_CPU_QUOTA_PERCENT"),
        "memory_max_mb": os.getenv("TAKYON_APP_ACTIONS_MEMORY_MAX_MB"),
    }
    for key, value in env_overrides.items():
        if value not in (None, ""):
            config[key] = value
    config["invoke_price_microusd"] = _bounded_int(config.get("invoke_price_microusd"), default=2_000, minimum=0, maximum=10_000_000)
    config["http_timeout_seconds"] = _bounded_int(config.get("http_timeout_seconds"), default=60, minimum=1, maximum=120)
    config["schedule_timeout_seconds"] = _bounded_int(config.get("schedule_timeout_seconds"), default=120, minimum=1, maximum=120)
    config["cpu_quota_percent"] = _bounded_int(config.get("cpu_quota_percent"), default=50, minimum=1, maximum=100)
    config["memory_max_mb"] = _bounded_int(config.get("memory_max_mb"), default=256, minimum=64, maximum=16_384)
    config["rails_base_url"] = str(config.get("rails_base_url") or "").strip()
    return config


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _parse_rails_base(value: str, *, key_name: str = "plugins.takyon.app_actions.rails_base_url") -> RailsBase:
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise ActionConfigError(f"{key_name} is required for scheduled actions")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ActionConfigError(f"{key_name} must be an origin like scheme://host[:port]")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ActionConfigError(f"{key_name} must be an origin only (scheme://host[:port])")
    # SSRF guard: the rails origin's host is added to the deno sandbox --allow-net allowlist, so a
    # request-derived/attacker-controlled bound_origin must never be an internal host (cloud metadata,
    # loopback, RFC1918/RFC4193) — same denylist the customer outbound_hosts allowlist enforces.
    if _is_internal_host(parsed.hostname):
        raise ActionConfigError(f"{key_name} must be a public origin, not an internal/loopback host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    hostport = f"{parsed.hostname}:{port}"
    origin = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        origin = f"{origin}:{parsed.port}"
    return RailsBase(origin=origin, hostport=hostport)


def resolve_rails_base(*, bound_origin: str = "") -> RailsBase:
    config = _action_runtime_config()
    configured = str(config.get("rails_base_url") or "").strip()
    if configured:
        return _parse_rails_base(configured)
    if bound_origin:
        return _parse_rails_base(bound_origin, key_name="bound action origin")
    raise ActionConfigError("plugins.takyon.app_actions.rails_base_url is required for scheduled actions")


def get_or_create_service_principal(store: Any, conn: Any, business_slug: str) -> dict[str, Any]:
    try:
        from .core import _json_dumps, _now, uuid
    except Exception:
        from plugins.takyon.core import _json_dumps, _now, uuid

    email = f"scheduler@service.{business_slug}.takyon.invalid"
    existing = store._row_to_dict(
        conn.execute(
            "SELECT * FROM app_users WHERE business_slug = ? AND email = ?",
            (business_slug, email),
        ).fetchone()
    )
    if existing:
        return existing
    now = _now()
    app_user_id = uuid.uuid4().hex
    metadata_column = "metadata" if _is_pg_conn(conn) else "metadata_json"
    conn.execute(
        f"INSERT INTO app_users (id, business_slug, email, name, status, tier, {metadata_column}, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'active', 'service', ?, ?, ?)",
        (
            app_user_id,
            business_slug,
            email,
            "Action Scheduler",
            _json_dumps({"service": "action_scheduler"}),
            now,
            now,
        ),
    )
    created = store._row_to_dict(
        conn.execute(
            "SELECT * FROM app_users WHERE business_slug = ? AND id = ?",
            (business_slug, app_user_id),
        ).fetchone()
    )
    if not created:
        raise AppActionError("failed to create service principal")
    return created


def _require_active_entitlement(entitlement) -> None:
    """Mirror of ``ai_gateway._require_active_entitlement`` for the ACTION reserve path
    (GOAL_RULES §3 gap #4). A billable action with NO active paid entitlement behind it must be
    REFUSED before any spend is held — never silently funded by a per-business pool (invariant 9
    removed that pool). Raises ``ActionBudgetExceeded`` carrying ``subscription_required`` so the
    runtime surfaces the same 402-equivalent the gateway does."""
    if entitlement is None:
        raise ActionBudgetExceeded("subscription_required: no active paid entitlement for billable action")


def _plan_derived_user_limit_microusd(plan) -> int:
    """CENTRALIZED per-user-limit resolution (GOAL_RULES §3 gap #4: "centralize per-user-limit
    resolution ... unify to plan-derived-or-0"). Delegates to THE canonical resolver
    ``ai_gateway._user_weekly_budget_microusd`` so the action reserve path and the gateway path
    share ONE rule: a paid plan grants its ``included_ai_budget_microusd`` pro-rated to the weekly
    usage window (× 7/30); a free / unentitled / absent plan grants 0. NEVER returns None (an
    uncapped per-user limit would defeat the only gate)."""
    try:
        from .ai_gateway import _user_weekly_budget_microusd
    except Exception:
        from plugins.takyon.ai_gateway import _user_weekly_budget_microusd
    return _user_weekly_budget_microusd(plan)


def _resolve_pg_action_usage_limit(
    store: Any,
    conn: Any,
    *,
    business_slug: str,
    app_user_id: str | None,
    app_user_tier: str | None,
) -> tuple[str | None, int]:
    """Resolve ``(tier, per_user_limit_microusd)`` for a billable action reserve.

    GOAL_RULES §3 gap #4: this path MUST require an active entitlement (mirror the gateway) and
    return a CONCRETE per-user limit (plan-derived-or-0), NEVER an unbounded ``None`` that would
    fall through to the (now-removed) per-business pool = ungated spend. A ``service``/null caller
    with no active paid entitlement raises ``ActionBudgetExceeded`` (subscription_required); an
    entitled caller resolves to its paid plan's ``included_ai_budget_microusd``."""
    resolved_user_tier = str(app_user_tier or "").strip() or None

    leaves = store._app_leaves()
    with store._leaf_conn(conn) as raw:
        app_user = (
            leaves["identity"].get_app_user(raw, business_slug, app_user_id=app_user_id)
            if app_user_id
            else None
        )
        if app_user is not None:
            resolved_user_tier = resolved_user_tier or str(getattr(app_user, "tier", "") or "").strip() or None
        entitlement = (
            leaves["entitlements"].get_active_entitlement(raw, business_slug, app_user_id)
            if app_user_id
            else None
        )
        # Require an active paid entitlement before any billable spend is held (gap #4): a
        # service/null-subuser action with no entitlement is refused, never pool-funded.
        _require_active_entitlement(entitlement)
        plan = None
        if entitlement is not None and getattr(entitlement, "plan_key", None):
            plan = leaves["entitlements"].get_plan_policy(raw, business_slug, entitlement.plan_key)
        if plan is None and resolved_user_tier:
            for candidate in leaves["entitlements"].list_plan_policies(raw, business_slug):
                if str(getattr(candidate, "tier", "") or "") == resolved_user_tier:
                    plan = candidate
                    break
    return resolved_user_tier, _plan_derived_user_limit_microusd(plan)


def reconcile_action_schedules(
    conn: Any,
    business_slug: str,
    workflow: Mapping[str, Any] | None,
    *,
    site_root: Path,
) -> None:
    try:
        from .core import _now
    except Exception:
        from plugins.takyon.core import _now

    workflow = workflow if isinstance(workflow, Mapping) else {}
    schedule_specs = [
        spec for spec in file_backed_action_specs(site_root, workflow)
        if str(spec.get("trigger") or "").strip().lower() == "schedule"
    ]
    desired = {str(spec["name"]): str(spec["schedule"]) for spec in schedule_specs}
    now = _now()
    is_pg = _is_pg_conn(conn)
    enabled_true = "true" if is_pg else "1"
    next_now = datetime.now(timezone.utc)
    now_value = now if is_pg else now
    rows = conn.execute(
        "SELECT action_name, cron_schedule FROM app_action_schedules WHERE business_slug = ?",
        (business_slug,),
    ).fetchall()
    existing = {
        str(row["action_name"]): str(row["cron_schedule"] or "")
        for row in rows
    }
    for action_name, schedule in desired.items():
        next_run = compute_next_run(schedule, next_now)
        next_run_value = next_run if is_pg else next_run.isoformat()
        if action_name in existing:
            if existing[action_name] == schedule:
                conn.execute(
                    f"UPDATE app_action_schedules SET enabled = {enabled_true}, updated_at = ? "
                    "WHERE business_slug = ? AND action_name = ?",
                    (now_value, business_slug, action_name),
                )
            else:
                conn.execute(
                    f"UPDATE app_action_schedules SET cron_schedule = ?, enabled = {enabled_true}, next_run_at = ?, updated_at = ? "
                    "WHERE business_slug = ? AND action_name = ?",
                    (schedule, next_run_value, now_value, business_slug, action_name),
                )
        else:
            conn.execute(
                "INSERT INTO app_action_schedules (business_slug, action_name, cron_schedule, enabled, next_run_at, created_at, updated_at) "
                f"VALUES (?, ?, ?, {enabled_true}, ?, ?, ?)",
                (business_slug, action_name, schedule, next_run_value, now_value, now_value),
            )
    for action_name in set(existing) - set(desired):
        conn.execute(
            f"UPDATE app_action_schedules SET enabled = {'false' if is_pg else '0'}, updated_at = ? WHERE business_slug = ? AND action_name = ?",
            (now_value, business_slug, action_name),
        )


def dispatch_due_action_schedules(store: Any, now: datetime, enqueue: Callable[[dict[str, Any]], None]) -> int:
    due: list[dict[str, Any]] = []
    with store._connect() as conn:
        is_pg = _is_pg_conn(conn)
        if is_pg:
            rows = conn.execute(
                "SELECT business_slug, action_name, cron_schedule, next_run_at "
                "FROM app_action_schedules WHERE enabled = true AND next_run_at <= ? "
                "ORDER BY next_run_at ASC FOR UPDATE SKIP LOCKED",
                (now,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT business_slug, action_name, cron_schedule, next_run_at "
                "FROM app_action_schedules WHERE enabled = 1 AND next_run_at <= ? "
                "ORDER BY next_run_at ASC",
                (now.isoformat(),),
            ).fetchall()
        for row in rows:
            business_slug = str(row["business_slug"])
            action_name = str(row["action_name"])
            cron_schedule = str(row["cron_schedule"])
            next_run_raw = row["next_run_at"]
            next_run = (
                next_run_raw
                if isinstance(next_run_raw, datetime)
                else datetime.fromisoformat(str(next_run_raw)).astimezone(timezone.utc)
            )
            window_key = f"action-sched:{business_slug}:{action_name}:{next_run.astimezone(timezone.utc).strftime('%Y%m%d%H%M')}"
            advanced = compute_next_run(cron_schedule, max(now, next_run))
            conn.execute(
                "UPDATE app_action_schedules SET next_run_at = ?, updated_at = ? "
                "WHERE business_slug = ? AND action_name = ?",
                (
                    advanced if is_pg else advanced.isoformat(),
                    now if is_pg else now.isoformat(),
                    business_slug,
                    action_name,
                ),
            )
            due.append(
                {
                    "business_slug": business_slug,
                    "action_name": action_name,
                    "window_key": window_key,
                }
            )
    for item in due:
        enqueue(item)
    return len(due)


def execute_scheduled_action(store: Any, business_slug: str, action_name: str, window_key: str) -> dict[str, Any]:
    principal: dict[str, Any] | None = None
    session_token = ""
    try:
        with store._connect() as conn:
            principal = get_or_create_service_principal(store, conn, business_slug)
            session_token = _mint_service_session(conn, business_slug, str(principal["id"]))
        result = invoke_action(
            store,
            business_slug=business_slug,
            action_name=action_name,
            payload={},
            principal={
                "kind": "service",
                "session_token": session_token,
                "user": principal,
            },
            trigger="schedule",
            idempotency_key=window_key,
            bound_origin="",
        )
        with store._connect() as conn:
            _update_schedule_status(conn, business_slug, action_name, status="completed", error="")
        return result
    except Exception as exc:
        with store._connect() as conn:
            _update_schedule_status(conn, business_slug, action_name, status="failed", error=str(exc))
        raise
    finally:
        if session_token:
            with store._connect() as conn:
                _revoke_session(conn, business_slug, session_token)


def invoke_action(
    store: Any,
    *,
    business_slug: str,
    action_name: str,
    payload: Any,
    principal: Mapping[str, Any],
    trigger: str,
    idempotency_key: str,
    bound_origin: str = "",
) -> dict[str, Any]:
    try:
        from .core import _surface_product_workflow_shape
    except Exception:
        from plugins.takyon.core import _surface_product_workflow_shape

    if len(json.dumps(payload).encode("utf-8")) > _ACTION_REQUEST_BODY_LIMIT:
        raise AppActionError("payload too large")
    # App-plane surface read: this path serves product customer (and scheduled
    # service) requests, which carry a business-scoped session, not an operator
    # identity. store.read(scope="business:...") is operator-gated and would 400
    # every customer invoke; _app_surface_contract is the same app-plane read the
    # other customer handlers use. Tenant isolation is enforced upstream by the
    # caller's validate_session(business, token) / service principal.
    with store._connect() as conn:
        surface = store._app_surface_contract(conn, business_slug)
    if not isinstance(surface, Mapping) or str(surface.get("status") or "").strip() == "missing":
        raise ActionContractError("app surface contract is missing")
    workflow = _surface_product_workflow_shape(dict(surface))
    site_root = store._business_root(business_slug) / "product" / "site"
    specs = file_backed_action_specs(site_root, workflow)
    outbound_hosts = normalize_outbound_hosts(workflow.get("outbound_hosts"))
    validate_action_contract(specs=specs, outbound_hosts=outbound_hosts, runtime_features=list(surface.get("runtime_features") or []))
    spec = next((item for item in specs if str(item.get("name")) == action_name), None)
    expected_trigger = str(spec.get("trigger") or "") if spec is not None else ""
    if spec is not None and expected_trigger != trigger:
        raise ActionContractError(f"action {action_name} is declared for {expected_trigger}, not {trigger}")
    if trigger == "schedule" and spec is None:
        raise ActionContractError(f"schedule action {action_name} must declare a schedule trigger")
    # Customer-invokable (non-schedule) actions must be in the SAME HTTP-certified set the surface
    # declaration gate computes — a real, UI-referenced, handler-backed action. This refuses an
    # invoke of an undeclared / un-exposed / stub action file that merely happens to exist on disk.
    if trigger != "schedule" and action_name not in site_http_action_names(site_root, surface):
        raise ActionContractError(f"action {action_name} is not an HTTP-certified action for this product")
    session_token = str(principal.get("session_token") or "").strip()
    if not session_token:
        raise AppActionError("session_token is required")
    # Pin the action hairpin (ctx.saveRecord / listRecords / generate) to the business's OWN
    # published origin, not the request-derived inbound origin. A product reached via a non-product
    # or legacy host (e.g. app.fourmanifold.com or a now-dark *.fourmanifold.com) would otherwise
    # make the hairpin POST to that dead host and 404 (non_json_response:404), breaking every save.
    # The surface contract carries the canonical product URL; resolve_rails_base/_parse_rails_base
    # still validate scheme+host and enforce the internal-host SSRF denylist on it.
    _surface_origin = str(surface.get("public_url") or surface.get("publish_target") or "").strip()
    base = resolve_rails_base(bound_origin=(_surface_origin or bound_origin))
    actions_dir = site_root / "actions"
    action_path = actions_dir / f"{action_name}.ts"
    if not action_path.exists():
        raise ActionContractError(f"action {action_name} has no file at product/site/actions/{action_name}.ts")
    reservation_key = str(idempotency_key or "").strip()
    if not reservation_key:
        raise AppActionError("idempotency_key is required")
    usage_business = business_slug
    app_user_id = str((principal.get("user") or {}).get("id") or "") or None
    app_user_tier = str((principal.get("user") or {}).get("tier") or "") or None
    # Run lock keyed per (business, customer) so one customer's action cannot block another's (see
    # _acquire_business_run). Service / scheduled runs (no app_user_id) share a per-kind key.
    run_lock_key = f"{business_slug}\x1f{app_user_id or ('service:' + str(principal.get('kind') or 'service'))}"
    config = _action_runtime_config()
    timeout_seconds = (
        int(config["schedule_timeout_seconds"])
        if trigger == "schedule"
        else int(config["http_timeout_seconds"])
    )
    receipt_rel = _receipt_relpath(business_slug, action_name, reservation_key)
    receipt_abs = store._resolve_business_file(
        business_slug,
        receipt_rel,
        require_output_root=True,
    )
    request = {
        "payload": payload,
        "ctx": {
            "base_url": f"{base.origin}{_ACTION_CONTEXT_PREFIX.format(business=business_slug)}",
            "session_token": session_token,
            "business": business_slug,
            "trigger": trigger,
            "principal": {
                "kind": str(principal.get("kind") or "session"),
                "id": str((principal.get("user") or {}).get("id") or ""),
                "email": str((principal.get("user") or {}).get("email") or ""),
            },
            # The shared client's ensureRail() needs the rails declared. Pass the surface's declared
            # features PLUS the platform rails a server action may call (server-gated), so ctx.generate
            # etc. resolve regardless of which UI features the product happened to declare.
            "runtime_features": list(surface.get("runtime_features") or []),
            "rail_state": {
                **(surface.get("rail_state") if isinstance(surface.get("rail_state"), dict) else {}),
                **{rail: "live" for rail in _ACTION_RUNTIME_RAILS},
            },
        },
    }
    estimate = int(config["invoke_price_microusd"])
    run_metadata: dict[str, Any] = {}
    # Acquire the run lock immediately before the guarded block so the finally always releases it
    # (the prior placement leaked the lock if receipt/request setup raised in between).
    _acquire_business_run(run_lock_key)
    reserved = False
    try:
        # ── Idempotency replay gate ──────────────────────────────────────────────────────────
        # A reused idempotency_key must NEVER re-execute the action's side effect. A terminal
        # receipt is the durable proof a prior attempt already finished: replay a success, surface
        # a prior failure — either way do not re-run. If no terminal receipt exists but a usage
        # reservation for the key is already on record, the prior attempt is in flight / crashed
        # mid-run; refuse rather than double-execute (or re-run ungated after a budget release).
        if receipt_abs.exists():
            cached = _read_receipt_payload(receipt_abs)
            if cached is not None:
                if cached.get("success"):
                    return {
                        "success": True,
                        "action": action_name,
                        "result": cached.get("result"),
                        "run": cached.get("run") or {},
                        "receipt": receipt_rel,
                    }
                raise ActionReplayConflict(
                    f"idempotency_key already used; prior attempt failed: "
                    f"{str(cached.get('error') or 'unknown error')}"
                )
        if _reservation_exists(store, usage_business, reservation_key):
            raise ActionReplayConflict("action_replay_in_progress: idempotency_key is already in flight")
        _reserve_usage(
            store,
            usage_business,
            reservation_key=reservation_key,
            app_user_id=app_user_id,
            app_user_tier=app_user_tier,
            estimate_microusd=estimate,
            route=f"/api/takyon/apps/{business_slug}/actions/{action_name}",
            metadata={"trigger": trigger, "principal": str(principal.get("kind") or "session")},
        )
        reserved = True
        result, run_metadata = _run_action_subprocess(
            action_path=action_path,
            base=base,
            outbound_hosts=outbound_hosts,
            request=request,
            timeout_seconds=timeout_seconds,
            cpu_quota_percent=int(config["cpu_quota_percent"]),
            memory_max_mb=int(config["memory_max_mb"]),
        )
        _settle_usage(
            store,
            usage_business,
            reservation_key=reservation_key,
            actual_microusd=estimate,
            metadata={"action": action_name, "trigger": trigger},
        )
        receipt = {
            "success": True,
            "business": business_slug,
            "action": action_name,
            "trigger": trigger,
            "principal": str(principal.get("kind") or "session"),
            "result": result,
            "run": run_metadata,
            "receipt_path": receipt_rel,
        }
        _write_receipt(receipt_abs, receipt)
        with store._connect() as conn:
            store._record_event(
                conn,
                scope=f"business:{business_slug}/app",
                business_slug=business_slug,
                event_type="app.action.invoke",
                payload={
                    "action": action_name,
                    "trigger": trigger,
                    "principal": str(principal.get("kind") or "session"),
                    "receipt_path": receipt_rel,
                    "usage_reservation_key": reservation_key,
                },
            )
        return {
            "success": True,
            "action": action_name,
            "result": result,
            "run": run_metadata,
            "receipt": receipt_rel,
        }
    except Exception as exc:
        # Only the fresh path (we actually reserved + ran) releases the reservation and writes a
        # failure receipt. A replay conflict / cached-failure replay raised before reserving must
        # NOT release the in-flight reservation or clobber the prior terminal receipt.
        if reserved:
            _release_usage(
                store,
                usage_business,
                reservation_key=reservation_key,
                error=str(exc),
                metadata={"action": action_name, "trigger": trigger},
            )
            failure_receipt = {
                "success": False,
                "business": business_slug,
                "action": action_name,
                "trigger": trigger,
                "principal": str(principal.get("kind") or "session"),
                "error": str(exc),
                "run": run_metadata,
                "receipt_path": receipt_rel,
            }
            _write_receipt(receipt_abs, failure_receipt)
        raise
    finally:
        _release_business_run(run_lock_key)


def _mint_service_session(conn: Any, business_slug: str, app_user_id: str) -> str:
    try:
        from .core import _future, _hash_token, _now, _random_token, uuid
    except Exception:
        from plugins.takyon.core import _future, _hash_token, _now, _random_token, uuid

    session_token = _random_token()
    conn.execute(
        "INSERT INTO app_sessions (id, business_slug, app_user_id, token_hash, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex,
            business_slug,
            app_user_id,
            _hash_token(session_token),
            _future(minutes=15),
            _now(),
        ),
    )
    return session_token


def _revoke_session(conn: Any, business_slug: str, session_token: str) -> None:
    try:
        from .core import _hash_token, _now
    except Exception:
        from plugins.takyon.core import _hash_token, _now

    conn.execute(
        "UPDATE app_sessions SET revoked_at = ? WHERE business_slug = ? AND token_hash = ? AND revoked_at IS NULL",
        (_now(), business_slug, _hash_token(session_token)),
    )


def _update_schedule_status(conn: Any, business_slug: str, action_name: str, *, status: str, error: str) -> None:
    try:
        from .core import _now
    except Exception:
        from plugins.takyon.core import _now

    conn.execute(
        "UPDATE app_action_schedules SET last_run_at = ?, last_status = ?, last_error = ?, updated_at = ? "
        "WHERE business_slug = ? AND action_name = ?",
        (_now(), status, error, _now(), business_slug, action_name),
    )


def _receipt_relpath(business_slug: str, action_name: str, reservation_key: str) -> str:
    digest = hashlib.sha256(f"{business_slug}:{action_name}:{reservation_key}".encode("utf-8")).hexdigest()[:16]
    return f"metrics/receipts/app-actions/{action_name}-{digest}.json"


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        from .core import _atomic_write_text, _json_dumps
    except Exception:
        from plugins.takyon.core import _atomic_write_text, _json_dumps

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _json_dumps(dict(payload)) + "\n")


def _read_receipt_payload(path: Path) -> dict[str, Any] | None:
    """Read a terminal action receipt for idempotent replay. Returns None on any read/parse error
    so a corrupt receipt falls through to the reservation-existence check rather than crashing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _reservation_exists(store: Any, business_slug: str, reservation_key: str) -> bool:
    """True if a usage reservation for this idempotency key already exists (any status). This is
    the durable, cross-process replay signal: a reused key whose reservation is present but has no
    terminal receipt is in flight (or crashed mid-run), and must not be re-executed."""
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn
    try:
        with store._connect() as conn:
            if isinstance(conn, _PGConn):
                with store._leaf_conn(conn) as raw:
                    row = raw.execute(
                        "select 1 from app_usage_events "
                        "where business_slug = %s and reservation_key = %s limit 1",
                        (business_slug, reservation_key),
                    ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM app_usage_events WHERE id = ? LIMIT 1",
                    (reservation_key,),
                ).fetchone()
            return row is not None
    except Exception:
        # Fail OPEN to the pre-fix behavior (treat as fresh) rather than block a legitimate request
        # on a transient read error; the reserve below is still atomically idempotent on the key.
        return False


def _reserve_usage(
    store: Any,
    business_slug: str,
    *,
    reservation_key: str,
    app_user_id: str | None,
    app_user_tier: str | None,
    estimate_microusd: int,
    route: str,
    metadata: Mapping[str, Any],
    purpose: str = "action_invoke",
) -> None:
    try:
        from .core import _PGConn, _json_dumps, _now
        from . import app_usage
    except Exception:
        from plugins.takyon.core import _PGConn, _json_dumps, _now
        from plugins.takyon import app_usage

    try:
        with store._connect() as conn:
            if isinstance(conn, _PGConn):
                try:
                    if estimate_microusd > 0:
                        # Billable spend: require an active paid entitlement and resolve a concrete
                        # plan-derived-or-0 per-user limit (GOAL_RULES §3 gap #4 — no ungated pool
                        # fall-through). A free (zero-cost) action moves no money, so it is not
                        # gated on a subscription.
                        resolved_user_tier, user_monthly_limit_microusd = _resolve_pg_action_usage_limit(
                            store,
                            conn,
                            business_slug=business_slug,
                            app_user_id=app_user_id,
                            app_user_tier=app_user_tier,
                        )
                    else:
                        resolved_user_tier = str(app_user_tier or "").strip() or None
                        user_monthly_limit_microusd = None
                    with store._leaf_conn(conn) as raw:
                        app_usage.reserve_usage(
                            raw,
                            business_slug,
                            estimated_cost_microusd=estimate_microusd,
                            reservation_key=reservation_key,
                            app_user_id=app_user_id,
                            user_monthly_limit_microusd=user_monthly_limit_microusd,
                            app_user_tier=resolved_user_tier,
                            purpose=purpose,
                            route=route,
                            metadata=dict(metadata),
                        )
                except (app_usage.AppBudgetExceeded, app_usage.AppUserBudgetExceeded) as exc:
                    raise ActionBudgetExceeded(str(exc)) from exc
            else:
                # GOAL_RULES §3 gap #4 (SQLite parity with the PG branch above): a billable
                # (positive-estimate) action MUST be backed by an active paid entitlement. After
                # invariant 9 removed the flat per-business pool cap, the SQLite budget opens with a
                # NULL cap too, so without this a service/null-subuser (or unentitled) action reserve
                # would fall straight through to the insert = unbounded ungated spend. Mirror
                # `_require_active_entitlement`: a positive estimate with no active, tier-conferring
                # entitlement behind its sub-user is refused (subscription_required). A zero-cost
                # (free) action moves no money and is not gated. The pool-cap check below still
                # applies when an operator set an explicit cap.
                if estimate_microusd > 0:
                    entitled = None
                    if app_user_id:
                        entitled = conn.execute(
                            "SELECT 1 FROM app_entitlements "
                            "WHERE business_slug = ? AND app_user_id = ? "
                            "AND status IN ('active', 'trialing') "
                            "AND lower(COALESCE(tier, '')) NOT IN ('', 'free', 'none', 'unentitled') "
                            "LIMIT 1",
                            (business_slug, app_user_id),
                        ).fetchone()
                    if entitled is None:
                        raise ActionBudgetExceeded(
                            "subscription_required: no active paid entitlement for billable action"
                        )
                budget = store._ensure_app_budget(conn, business_slug)
                # Per-business pool gate: ONLY when an explicit cap is set (invariant 9 — NULL =
                # no pool cap, the per-subuser subscription gate is then the sole budget gate).
                pool_cap = budget["hard_limit_microusd"]
                if pool_cap is not None:
                    committed = conn.execute(
                        "SELECT COALESCE(SUM(CASE "
                        "WHEN status = 'reserved' THEN estimated_cost_microusd "
                        "WHEN status = 'completed' THEN actual_cost_microusd "
                        "ELSE 0 END), 0) AS total "
                        "FROM app_usage_events WHERE business_slug = ? AND created_at >= ?",
                        (business_slug, budget["current_period_start"]),
                    ).fetchone()
                    if int(committed["total"] or 0) + estimate_microusd > int(pool_cap):
                        raise ActionBudgetExceeded(
                            f"app usage would exceed budget cap {pool_cap} microusd"
                        )
                existing = conn.execute(
                    "SELECT id FROM app_usage_events WHERE id = ?",
                    (reservation_key,),
                ).fetchone()
                if existing is None:
                    now = _now()
                    conn.execute(
                        "INSERT INTO app_usage_events ("
                        "id, business_slug, app_user_id, app_user_tier, purpose, route, status, "
                        "estimated_cost_microusd, actual_cost_microusd, metadata_json, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, 0, ?, ?)",
                        (
                            reservation_key,
                            business_slug,
                            app_user_id,
                            app_user_tier,
                            purpose,
                            route,
                            estimate_microusd,
                            _json_dumps(dict(metadata)),
                            now,
                        ),
                    )
    except ActionBudgetExceeded:
        raise
    except Exception as exc:
        raise AppActionError(str(exc)) from exc


def _settle_usage(
    store: Any,
    business_slug: str,
    *,
    reservation_key: str,
    actual_microusd: int,
    metadata: Mapping[str, Any],
) -> None:
    try:
        from .core import _PGConn, _json_dumps, _now
        from . import app_usage
    except Exception:
        from plugins.takyon.core import _PGConn, _json_dumps, _now
        from plugins.takyon import app_usage

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            with store._leaf_conn(conn) as raw:
                app_usage.settle_usage(
                    raw,
                    business_slug,
                    reservation_key,
                    actual_cost_microusd=actual_microusd,
                    metadata=dict(metadata),
                )
        else:
            conn.execute(
                "UPDATE app_usage_events SET status = 'completed', actual_cost_microusd = ?, metadata_json = ?, completed_at = ? "
                "WHERE id = ?",
                (actual_microusd, _json_dumps(dict(metadata)), _now(), reservation_key),
            )


def _release_usage(
    store: Any,
    business_slug: str,
    *,
    reservation_key: str,
    error: str,
    metadata: Mapping[str, Any],
) -> None:
    try:
        from .core import _PGConn, _json_dumps, _now
        from . import app_usage
    except Exception:
        from plugins.takyon.core import _PGConn, _json_dumps, _now
        from plugins.takyon import app_usage

    try:
        with store._connect() as conn:
            if isinstance(conn, _PGConn):
                with store._leaf_conn(conn) as raw:
                    app_usage.release_usage(
                        raw,
                        business_slug,
                        reservation_key,
                        error=error,
                        metadata=dict(metadata),
                    )
            else:
                conn.execute(
                    "UPDATE app_usage_events SET status = 'failed', actual_cost_microusd = 0, error = ?, metadata_json = ?, completed_at = ? "
                    "WHERE id = ? AND status = 'reserved'",
                    (error, _json_dumps(dict(metadata)), _now(), reservation_key),
                )
    except Exception:
        pass


def _run_action_subprocess(
    *,
    action_path: Path,
    base: RailsBase,
    outbound_hosts: list[str],
    request: Mapping[str, Any],
    timeout_seconds: int,
    cpu_quota_percent: int,
    memory_max_mb: int,
) -> tuple[Any, dict[str, Any]]:
    deno = shutil.which("deno")
    if not deno:
        raise ActionConfigError("deno is not installed on this host")
    with tempfile.TemporaryDirectory(prefix="takyon-app-actions-") as tempdir:
        runner_path = Path(tempdir) / "runner.mjs"
        runner_path.write_text(_ACTION_RUNNER_SOURCE, encoding="utf-8")
        request_bytes = json.dumps(request).encode("utf-8")
        # The shared runtime client (the SAME module the browser uses) lives in the materialized kit
        # dir beside the product site (product/site/_takyon/runtime-client.js). Pass it to the runner
        # so ctx IS that client. Read-only; if the workspace predates materialization the runner
        # gracefully falls back to the plain ctx bag.
        client_path = action_path.parent.parent / "_takyon" / "runtime-client.js"
        client_available = client_path.is_file()
        read_roots = [str(runner_path.parent), str(action_path.parent)]
        if client_available:
            read_roots.append(str(client_path.parent))
        allow_read = ",".join(read_roots)
        allow_net_hosts = [base.hostport]
        for host in outbound_hosts:
            allow_net_hosts.extend(_resolved_public_allow_net_entries(host))
        deno_command = [
            deno,
            "run",
            "--quiet",
            "--no-prompt",
            "--no-remote",
            "--deny-write",
            f"--allow-read={allow_read}",
            f"--allow-net={','.join(allow_net_hosts)}",
            str(runner_path),
            action_path.resolve().as_uri(),
        ]
        if client_available:
            deno_command.append(client_path.resolve().as_uri())
        sandbox_required = _operator_host_requires_action_sandbox()
        isolation = "subprocess"
        command = list(deno_command)
        proc_env: dict[str, str] | None = None
        fallback_reason: str | None = None
        systemd_run = shutil.which("systemd-run")
        if platform.system() == "Linux" and systemd_run:
            proc_env = dict(os.environ)
            proc_env.update(_systemd_user_manager_env())
            isolation = "systemd-user-scope"
            command = [
                systemd_run,
                "--user",
                "--scope",
                "--quiet",
                "-p",
                f"CPUQuota={cpu_quota_percent}%",
                "-p",
                f"MemoryMax={memory_max_mb}M",
                "-p",
                "TasksMax=32",
                "--",
                *deno_command,
            ]
        elif sandbox_required:
            raise ActionConfigError(
                "managed host requires product actions to run inside a user-scoped systemd sandbox"
            )
        else:
            fallback_reason = "user-scoped systemd sandbox unavailable on this host"
            _LOGGER.warning("App action sandbox unavailable; falling back to plain subprocess: %s", fallback_reason)
        returncode, stdout, stderr = _communicate_action_process(
            command,
            request_bytes=request_bytes,
            timeout_seconds=timeout_seconds,
            env=proc_env,
        )
        stderr_text = stderr.decode("utf-8", errors="replace")
        if len(stdout) > _ACTION_STDOUT_LIMIT:
            raise ActionResultTooLarge("action stdout exceeded 256 KB")
        if returncode != 0 and proc_env and _is_systemd_scope_start_failure(
            stderr_text.strip() or stdout.decode("utf-8", errors="replace").strip()
        ):
            detail = stderr_text.strip() or stdout.decode("utf-8", errors="replace").strip() or "failed to create user-scoped systemd sandbox"
            if sandbox_required:
                raise ActionConfigError(
                    f"managed host requires the user-scoped systemd sandbox for product actions: {detail[:_ACTION_STDERR_LIMIT]}"
                )
            fallback_reason = detail[:_ACTION_STDERR_LIMIT]
            _LOGGER.warning("App action sandbox unavailable; falling back to plain subprocess: %s", fallback_reason)
            isolation = "subprocess-fallback"
            command = list(deno_command)
            proc_env = None
            returncode, stdout, stderr = _communicate_action_process(
                command,
                request_bytes=request_bytes,
                timeout_seconds=timeout_seconds,
            )
            stderr_text = stderr.decode("utf-8", errors="replace")
        elif fallback_reason:
            isolation = "subprocess-fallback"
        if returncode != 0:
            detail = stderr_text.strip() or stdout.decode("utf-8", errors="replace").strip() or "action subprocess failed"
            raise AppActionError(detail[:_ACTION_STDERR_LIMIT])
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise AppActionError("action runner returned an invalid payload")
        metadata = {
            "command": command,
            "timeout_seconds": timeout_seconds,
            "isolation": isolation,
            "stderr": stderr_text[:_ACTION_STDERR_LIMIT],
        }
        if fallback_reason:
            metadata["sandbox_fallback_reason"] = fallback_reason
        return payload.get("result"), metadata


def _acquire_business_run(run_key: str) -> None:
    """In-process run lock. `run_key` is scoped per (business, customer) — NOT per business — so a
    slow or hostile action from one customer cannot block every other customer of the same
    business. A single customer still cannot run two concurrent actions (same key)."""
    with _active_business_runs_lock:
        if run_key in _active_business_runs:
            raise ActionAlreadyRunning("action_already_running")
        _active_business_runs.add(run_key)


def _release_business_run(run_key: str) -> None:
    with _active_business_runs_lock:
        _active_business_runs.discard(run_key)
