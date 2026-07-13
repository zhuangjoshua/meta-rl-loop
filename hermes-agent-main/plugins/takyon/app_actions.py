from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from croniter import croniter

from . import environment

try:
    import fcntl
except ImportError:  # pragma: no cover - production runtime is POSIX
    fcntl = None  # type: ignore[assignment]


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
_ACTION_BUNDLE_MAX_BYTES = 512 * 1024
_ACTION_BUNDLE_MAX_FILES = 64
_ACTION_BUNDLE_VERSION = 1
_ACTION_MIN_INTERVAL_SECONDS = 15 * 60
_ACTION_CONTEXT_PREFIX = "/api/takyon/apps/{business}"
# Platform rails a server-side action may call. The action runs where these rails are reachable and
# the SERVER is the authority (validate_session + plan/budget per rail), so the shared client's
# ensureRail() — a browser-side UX guard keyed on the product's declared UI features — must not
# pre-block them for actions. media/email gate server-side on declaration, so they are deliberately
# omitted here and follow the surface's declared runtime_features instead.
_ACTION_RUNTIME_RAILS = ("generate", "actions", "records", "search", "connections", "profile", "directory", "egress")
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
const liveBuildId = String(bare.live_build_id || "").trim();
const publicContext = {
  business: bare.business,
  trigger: bare.trigger,
  principal: bare.principal,
  live_build_id: liveBuildId,
  runtime_features: bare.runtime_features ?? [],
  rail_state: bare.rail_state ?? {},
};
// The browser client stamps every action request with the immutable HTML build id. Server actions
// use that SAME client, so propagate the parent build into nested ctx.invokeAction calls instead of
// silently falling back to whichever database pointer happens to be current mid-rollout.
if (liveBuildId) globalThis.__TAKYON_LIVE_BUILD_ID__ = liveBuildId;

// The action runs sandboxed with no cookie, so the shared client's same-origin requests carry no
// auth. Inject the customer's business-scoped Bearer session token, but ONLY for requests to the
// rails origin (baseUrl prefix) so the token can never leak to a declared outbound host. Authority
// stays server-side: the rail still validates the session against this business + enforces budget.
const realFetch = globalThis.fetch.bind(globalThis);
const NativeHeaders = globalThis.Headers;
const NativeURL = globalThis.URL;
const nativeHeadersHas = Function.prototype.call.bind(NativeHeaders.prototype.has);
const nativeHeadersSet = Function.prototype.call.bind(NativeHeaders.prototype.set);
const nativeStringStartsWith = Function.prototype.call.bind(String.prototype.startsWith);
const nativeUrlOrigin = Function.prototype.call.bind(
  Object.getOwnPropertyDescriptor(NativeURL.prototype, "origin").get,
);
const nativeUrlPathname = Function.prototype.call.bind(
  Object.getOwnPropertyDescriptor(NativeURL.prototype, "pathname").get,
);
const railsUrl = baseUrl ? new NativeURL(baseUrl) : null;
const railsOrigin = railsUrl ? nativeUrlOrigin(railsUrl) : "";
const railsPath = railsUrl ? nativeUrlPathname(railsUrl).replace(/\/+$/, "") : "";
const authenticatedFetch = (input, init = {}) => {
  // Snapshot every caller-controlled init getter BEFORE the bearer enters any object, and authorize
  // only string URLs with the exact rails origin + a path-segment boundary. Request-like proxies are
  // deliberately unauthenticated rather than letting an attacker-controlled `.url` getter run here.
  const copiedInit = { ...init };
  let authorizedRailsUrl = false;
  if (railsUrl && sessionToken && typeof input === "string") {
    try {
      const candidate = new NativeURL(input, railsUrl);
      const candidateOrigin = nativeUrlOrigin(candidate);
      const candidatePath = nativeUrlPathname(candidate);
      authorizedRailsUrl = candidateOrigin === railsOrigin
        && (candidatePath === railsPath || nativeStringStartsWith(candidatePath, railsPath + "/"));
    } catch (_error) {
      authorizedRailsUrl = false;
    }
  }
  if (authorizedRailsUrl) {
    const headers = new NativeHeaders(copiedInit.headers || {});
    if (!nativeHeadersHas(headers, "Authorization")) {
      nativeHeadersSet(headers, "Authorization", "Bearer " + sessionToken);
    }
    copiedInit.headers = headers;
  }
  return realFetch(input, copiedInit);
};
// Untrusted action modules load after this wrapper. Make the authenticated fetch binding immutable
// so action code cannot replace it and inspect the closure-injected bearer before ctx rail calls.
Object.defineProperty(globalThis, "fetch", {
  value: authenticatedFetch,
  writable: false,
  configurable: false,
});

// ctx IS the shared runtime client (the SAME createSubuserRuntimeClient the browser UI uses), so
// ctx.generate / ctx.invokeAction / ctx.saveRecord / ctx.listRecords work identically and there is
// one source of truth (runtime-client.js) instead of an asymmetric data bag the action must guess at.
let ctx = { ...publicContext };
if (clientUrl && baseUrl) {
  try {
    const { createSubuserRuntimeClient } = await import(clientUrl);
    const client = createSubuserRuntimeClient({
      runtimeApiBase: baseUrl,
      runtimeFeatures: bare.runtime_features ?? [],
      railState: bare.rail_state ?? {},
      location: { origin: new URL(baseUrl).origin, href: baseUrl },
    });
    ctx = Object.assign(Object.create(client), publicContext);
  } catch (err) {
    console.error("runtime client unavailable: " + (err && err.message));
    ctx = { ...publicContext };
  }
}

// Belt-and-suspenders: if the shared client failed to load, attach the inline generate rail from
// closure-private authority. Never copy the bearer or private base URL onto handler-visible ctx.
if (typeof ctx.generate !== "function" && baseUrl && sessionToken) {
  ctx.generate = async (genPayload = {}) => {
    const res = await fetch(`${baseUrl}/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${sessionToken}`,
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
_action_bundle_cache_locks: dict[str, threading.Lock] = {}
_action_bundle_cache_locks_guard = threading.Lock()
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
    # Thin shim over the one role truth table (Stage 3): app_actions gates on exact spellings,
    # so it uses the bare (no-alias) view.
    return environment.HostRole.bare()


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


def _typescript_without_comments(source: str) -> str:
    """Mask TypeScript comments while preserving strings and source positions."""
    output = list(source)
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            else:
                output[index] = " "
            index += 1
            continue
        if block_comment:
            output[index] = "\n" if char == "\n" else " "
            if char == "*" and following == "/":
                output[index + 1] = " "
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "/" and following == "/":
            output[index] = output[index + 1] = " "
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            output[index] = output[index + 1] = " "
            block_comment = True
            index += 2
            continue
        index += 1
    return "".join(output)


_ACTION_UI_CALL_PATTERN = re.compile(
    r"\b(?:useDecodedActionRunner|useActionRunner|createActionRunner|invokeAction)"
    r"\s*\(\s*['\"]([a-z][a-z0-9_-]{0,63})['\"]",
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
        for match in _ACTION_UI_CALL_PATTERN.finditer(_typescript_without_comments(text)):
            referenced.add(match.group(1).strip().lower())
    return referenced


# Distinctive runtime-client rail methods an app calls in its own source. Mirrors how action
# names are scanned (_referenced_action_names_in_source) so a built product self-declares the
# data / media / AI / social rails it actually uses — the root cure for
# rail_unavailable:<rail>:undeclared. auth/account/profile/checkout are always-seeded shell
# rails and are intentionally omitted here; `actions` is derived separately from on-disk
# action files. The kit (`_takyon`) — which DEFINES these methods — is skipped by
# _ACTION_SCAN_SKIP_DIRS, so only real call sites in app source match.
def _runtime_rail_usage_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """The (rail, regex) source-scanner pairs, DERIVED from the RuntimeRail registry.

    Stage 6: the per-rail scanner regex is built from each rail's declared
    ``RuntimeRail.client_methods`` in ``plugins.takyon.core`` — the single source of
    truth for which runtime-client methods a rail exposes — so the scanned method set can
    never drift from the declared rail. Imported lazily to avoid an import cycle (core is
    heavy and imports this module indirectly). The emitted regexes are byte-equivalent to
    the pre-Stage-6 hand-written literals; a characterization test pins that equivalence.
    """
    from . import core as _core

    return _core.runtime_rail_usage_patterns()


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
    pending = list(_runtime_rail_usage_patterns())
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


_ACTION_AMBIENT_REFERENCE_PATTERN = re.compile(
    r"(?im)^\s*///\s*<reference\s+(?:lib|types|path)\s*="
)
_ACTION_TYPESCRIPT_SUPPRESSION_PATTERN = re.compile(
    r"(?i)@ts-(?:nocheck|ignore|expect-error)\b"
)
def _typescript_tokens(source: str) -> list[tuple[str, str]]:
    """Small lexical scanner for publish gates; comments and literal bodies are never code."""
    tokens: list[tuple[str, str]] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char == "`":
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == "`":
                    index += 1
                    break
                if source.startswith("${", index):
                    expression_start = index + 2
                    expression_end = _typescript_template_expression_end(source, expression_start)
                    tokens.extend(_typescript_tokens(source[expression_start:expression_end]))
                    index = min(length, expression_end + 1)
                    continue
                index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            value: list[str] = []
            while index < length:
                current = source[index]
                if current == "\\":
                    if index + 1 < length:
                        value.append(source[index + 1])
                    index += 2
                    continue
                if current == quote:
                    index += 1
                    break
                value.append(current)
                index += 1
            tokens.append(("string", "".join(value)))
            continue
        if char.isalpha() or char in {"_", "$"}:
            end = index + 1
            while end < length and (source[end].isalnum() or source[end] in {"_", "$"}):
                end += 1
            tokens.append(("identifier", source[index:end]))
            index = end
            continue
        two = source[index : index + 2]
        if two in {"=>", "?.", "??", "&&", "||", "==", "!=", "<=", ">="}:
            tokens.append(("punct", two))
            index += 2
            continue
        tokens.append(("punct", char))
        index += 1
    return tokens


def _typescript_template_expression_end(source: str, start: int) -> int:
    """Locate the matching ``}`` for one template interpolation without executing its prose."""
    depth = 1
    index = start
    length = len(source)
    while index < length:
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        char = source[index]
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return length


def _typescript_comments(source: str) -> list[str]:
    comments: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            end = length if newline < 0 else newline
            comments.append(source[index:end])
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            close = source.find("*/", index + 2)
            end = length if close < 0 else close + 2
            comments.append(source[index:end])
            index = end
            continue
        char = source[index]
        if char == "`":
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == "`":
                    index += 1
                    break
                if source.startswith("${", index):
                    expression_start = index + 2
                    expression_end = _typescript_template_expression_end(source, expression_start)
                    comments.extend(_typescript_comments(source[expression_start:expression_end]))
                    index = min(length, expression_end + 1)
                    continue
                index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        index += 1
    return comments


def _typescript_module_edges(source: str) -> tuple[list[tuple[str, bool]], bool]:
    """Return literal module edges as ``(target, type_only)`` and a nonliteral-import flag."""
    tokens = _typescript_tokens(source)
    edges: list[tuple[str, bool]] = []
    nonliteral_dynamic = False
    for index, token in enumerate(tokens):
        if token != ("identifier", "import") and token != ("identifier", "export"):
            continue
        keyword = token[1]
        cursor = index + 1
        if cursor >= len(tokens):
            continue
        if keyword == "import" and tokens[cursor] == ("punct", "."):
            continue
        if keyword == "import" and tokens[cursor] == ("punct", "("):
            if cursor + 2 < len(tokens) and tokens[cursor + 1][0] == "string" and tokens[cursor + 2] == ("punct", ")"):
                edges.append((tokens[cursor + 1][1], False))
            else:
                nonliteral_dynamic = True
            continue
        if keyword == "export" and tokens[cursor] == ("identifier", "default"):
            continue
        type_only = tokens[cursor] == ("identifier", "type")
        if type_only:
            cursor += 1
        if cursor < len(tokens) and tokens[cursor][0] == "string":
            edges.append((tokens[cursor][1], type_only))
            continue
        depth = 0
        for probe in range(cursor, min(len(tokens), cursor + 100)):
            kind, value = tokens[probe]
            if value in {"(", "[", "{"}:
                depth += 1
            elif value in {")",
                "]",
                "}",
            }:
                depth = max(0, depth - 1)
            if depth == 0 and value == ";":
                break
            if depth == 0 and kind == "identifier" and value in {"import", "export"} and probe > cursor:
                break
            if depth == 0 and kind == "identifier" and value == "from":
                if probe + 1 < len(tokens) and tokens[probe + 1][0] == "string":
                    edges.append((tokens[probe + 1][1], type_only))
                break
    return edges, nonliteral_dynamic


def _canonical_action_handler_signature(source: str) -> bool:
    tokens = _typescript_tokens(source)

    def matching_paren(open_index: int) -> int | None:
        depth = 0
        for position in range(open_index, len(tokens)):
            value = tokens[position][1]
            if value == "(":
                depth += 1
            elif value == ")":
                depth -= 1
                if depth == 0:
                    return position
        return None

    def canonical_params(open_index: int, close_index: int) -> bool:
        segments: list[list[tuple[str, str]]] = [[]]
        depth = 0
        for item in tokens[open_index + 1 : close_index]:
            if item[1] in {"(", "[", "{", "<"}:
                depth += 1
            elif item[1] in {")",
                "]",
                "}",
                ">",
            }:
                depth = max(0, depth - 1)
            if item[1] == "," and depth == 0:
                segments.append([])
            else:
                segments[-1].append(item)
        if len(segments) != 2:
            return False
        expected = ("TakyonActionPayload", "TakyonActionContext")
        for segment, annotation in zip(segments, expected):
            try:
                colon = next(i for i, item in enumerate(segment) if item[1] == ":")
            except StopIteration:
                return False
            type_tokens = [item[1] for item in segment[colon + 1 :] if item[1] not in {" "}]
            if type_tokens != [annotation]:
                return False
        return True

    for index in range(len(tokens) - 2):
        if tokens[index] != ("identifier", "export") or tokens[index + 1] != ("identifier", "default"):
            continue
        cursor = index + 2
        if cursor < len(tokens) and tokens[cursor] == ("identifier", "async"):
            cursor += 1
        is_function = cursor < len(tokens) and tokens[cursor] == ("identifier", "function")
        if is_function:
            cursor += 1
            if cursor < len(tokens) and tokens[cursor][0] == "identifier":
                cursor += 1
        if cursor >= len(tokens) or tokens[cursor] != ("punct", "("):
            continue
        close = matching_paren(cursor)
        if close is None or not canonical_params(cursor, close):
            continue
        if is_function:
            return True
        if any(item == ("punct", "=>") for item in tokens[close + 1 : close + 20]):
            return True
    return False


def _resolve_bounded_type_import(path: Path, target: str, site_root: Path) -> Path | None:
    """Resolve an erased relative import with TypeScript's common file spellings.

    Runtime imports remain explicit bundled ``.ts`` files below.  Type-only imports are erased, so
    they may use normal TypeScript extensionless syntax, but every candidate must still resolve
    inside product/site.  This intentionally does not implement package or tsconfig path aliases.
    """

    unresolved = path.parent / target
    suffixes = (".ts", ".tsx", ".d.ts")
    candidates = [unresolved]
    if not unresolved.suffix:
        candidates.extend(Path(f"{unresolved}{suffix}") for suffix in suffixes)
        candidates.extend(unresolved / f"index{suffix}" for suffix in suffixes)
    for candidate in candidates:
        try:
            lexical = candidate.absolute()
            relative = lexical.relative_to(site_root)
        except (OSError, ValueError):
            continue
        current = site_root
        symlinked = False
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                symlinked = True
                break
        if symlinked:
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if site_root not in resolved.parents:
            continue
        if resolved.is_file():
            return resolved
    return None


def _validate_bundled_action_imports(actions_root: Path, candidates: list[Path]) -> None:
    """Fail publish when TypeScript accepts a module the immutable runtime bundle omits.

    ``moduleResolution=bundler`` follows package, JSON, and out-of-tree imports during typecheck,
    while the production Deno sandbox is ``--no-remote`` and can read only ``actions/**``.  A green
    typecheck is therefore not execution proof unless every runtime module edge is a literal local
    ``.ts`` path inside that bounded tree.  Validate that graph before the public pointer can move.
    """
    root = actions_root.resolve()
    candidate_roots = {path.resolve() for path in candidates}
    for path in candidates:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ActionContractError(
                f"action module is not readable UTF-8: {path.relative_to(actions_root.parent)}: {exc}"
            ) from exc
        comment_source = "\n".join(_typescript_comments(source))
        if _ACTION_AMBIENT_REFERENCE_PATTERN.search(comment_source):
            raise ActionContractError(
                f"action module {path.relative_to(actions_root.parent).as_posix()} declares a "
                "triple-slash ambient reference; server actions must use only the bounded action type environment"
            )
        if _ACTION_TYPESCRIPT_SUPPRESSION_PATTERN.search(comment_source):
            raise ActionContractError(
                f"action module {path.relative_to(actions_root.parent).as_posix()} uses a TypeScript "
                "suppression directive; server action type errors must block publish"
            )
        edges, nonliteral_dynamic = _typescript_module_edges(source)
        if nonliteral_dynamic:
            raise ActionContractError(
                f"action module {path.relative_to(actions_root.parent).as_posix()} uses a non-literal "
                "dynamic import; action imports must be statically bundled local .ts modules"
            )
        for target, type_only in edges:
            if type_only:
                if not target.startswith(".") or "?" in target or "#" in target:
                    raise ActionContractError(
                        f"action module {path.relative_to(actions_root.parent).as_posix()} type-imports "
                        f"unsupported module {target!r}; type-only imports must stay inside product/site"
                    )
                site_root = actions_root.parent.resolve()
                resolved_type = _resolve_bounded_type_import(path, target, site_root)
                if resolved_type is None:
                    raise ActionContractError(
                        f"action module {path.relative_to(actions_root.parent).as_posix()} type-import "
                        f"{target!r} escapes or is missing from product/site"
                    )
                continue
            if not target.startswith("."):
                raise ActionContractError(
                    f"action module {path.relative_to(actions_root.parent).as_posix()} imports "
                    f"unsupported module {target!r}; only explicit relative .ts imports under actions/** are allowed"
                )
            if "?" in target or "#" in target or not target.endswith(".ts"):
                raise ActionContractError(
                    f"action module {path.relative_to(actions_root.parent).as_posix()} import {target!r} "
                    "must name an explicit bundled .ts file"
                )
            resolved = (path.parent / target).resolve()
            if root not in resolved.parents or resolved not in candidate_roots or not resolved.is_file():
                raise ActionContractError(
                    f"action module {path.relative_to(actions_root.parent).as_posix()} import {target!r} "
                    "escapes or is missing from the immutable actions/** bundle"
                )


def _validate_action_handler_types(actions_root: Path) -> None:
    """Reject the explicit-any escape hatch that makes the separate action tsconfig toothless."""
    for path in sorted(actions_root.rglob("*.ts")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ActionContractError(f"action module is not readable UTF-8: {path.name}: {exc}") from exc
        tokens = _typescript_tokens(source)
        if any(kind == "identifier" and value == "any" for kind, value in tokens):
            raise ActionContractError(
                f"action module {path.relative_to(actions_root.parent).as_posix()} uses explicit `any`; type handlers with "
                "TakyonActionPayload and TakyonActionContext so invalid context methods fail publish"
            )
        if (
            path.parent.resolve() == actions_root.resolve()
            and _ACTION_NAME_RE.match(path.stem.strip().lower())
            and not _canonical_action_handler_signature(source)
        ):
            raise ActionContractError(
                f"action module actions/{path.name} must directly default-export "
                "(payload: TakyonActionPayload, ctx: TakyonActionContext)"
            )


def _json_safe_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ActionContractError(f"{field} must be an object")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ActionContractError(f"{field} must be JSON-serializable") from exc
    return dict(decoded) if isinstance(decoded, dict) else {}


def build_action_bundle(
    site_root: Path,
    workflow: Mapping[str, Any] | None = None,
    *,
    runtime_features: Any = None,
    rail_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile the immutable server-action artifact stored with one live product build.

    Static HTML is already distributed through the build artifact/R2 pointer. Server actions cannot
    depend on a producer host SSHing mutable source into every serving replica: the producer may be
    the Mac or the delayed operator VPS, and the sub-user plane is N-replica. This bounded bundle is
    therefore part of the build identity and is the sole action source consumed at invocation time.
    """
    root = Path(site_root).resolve()
    # TypeScript follows bounded local imports under actions/**.  Bundle the same tree accepted by
    # tsconfig.actions.json, not only top-level entrypoints, or a checked action can publish without
    # the helper module Deno needs on every serving replica.
    candidates = (
        sorted((root / "actions").rglob("*.ts"))
        if (root / "actions").is_dir()
        else []
    )
    if candidates:
        _validate_bundled_action_imports(root / "actions", candidates)
        _validate_action_handler_types(root / "actions")
    runtime_client = root / "_takyon" / "runtime-client.js"
    if runtime_client.is_file():
        candidates.append(runtime_client)
    if len(candidates) > _ACTION_BUNDLE_MAX_FILES:
        raise ActionContractError(
            f"action bundle has {len(candidates)} files; maximum is {_ACTION_BUNDLE_MAX_FILES}"
        )

    files: list[dict[str, str]] = []
    total = 0
    for path in candidates:
        resolved = path.resolve()
        if root not in resolved.parents or path.is_symlink():
            raise ActionContractError(f"action bundle path escaped product/site: {path}")
        rel = resolved.relative_to(root).as_posix()
        if not (rel.startswith("actions/") and rel.endswith(".ts")) and rel != "_takyon/runtime-client.js":
            raise ActionContractError(f"action bundle contains unsupported path: {rel}")
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ActionContractError(f"action bundle file is not readable UTF-8: {rel}: {exc}") from exc
        size = len(content.encode("utf-8"))
        total += size
        if total > _ACTION_BUNDLE_MAX_BYTES:
            raise ActionContractError(
                f"action bundle exceeds {_ACTION_BUNDLE_MAX_BYTES} bytes"
            )
        files.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content": content,
            }
        )

    frozen_workflow = workflow if isinstance(workflow, Mapping) else {}
    frozen_specs = file_backed_action_specs(root, frozen_workflow)
    frozen_outbound_hosts = normalize_outbound_hosts(frozen_workflow.get("outbound_hosts"))
    frozen_runtime_features = sorted(
        {
            str(value or "").strip().lower()
            for value in (runtime_features if isinstance(runtime_features, list) else [])
            if str(value or "").strip()
        }
    )
    frozen_rail_state = _json_safe_mapping(rail_state, field="action rail_state")
    validate_action_contract(
        specs=frozen_specs,
        outbound_hosts=frozen_outbound_hosts,
        runtime_features=frozen_runtime_features,
    )
    payload = {
        "version": _ACTION_BUNDLE_VERSION,
        "files": files,
        "http_action_names": sorted(site_http_action_names(root, frozen_workflow)),
        "execution_contract": {
            "action_specs": frozen_specs,
            "outbound_hosts": frozen_outbound_hosts,
            "runtime_features": frozen_runtime_features,
            "rail_state": frozen_rail_state,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _ACTION_BUNDLE_MAX_BYTES:
        raise ActionContractError(
            f"encoded action bundle exceeds {_ACTION_BUNDLE_MAX_BYTES} bytes"
        )
    return {
        "json": encoded,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "file_count": len(files),
        "http_action_names": payload["http_action_names"],
    }


def _live_action_bundle_row(
    conn: Any,
    *,
    business_slug: str,
    live_build_id: str,
    session_token: str,
) -> Any:
    if not _is_pg_conn(conn):
        return conn.execute(
            "SELECT pb.build_id AS live_build_id, pb.action_bundle_json, "
            "pb.action_bundle_sha256 FROM product_builds pb "
            "JOIN app_surface_contracts surface ON surface.business_slug = pb.business_slug "
            "WHERE pb.business_slug = ? AND pb.build_id = ? "
            "AND ((surface.live_build_id = pb.build_id "
            "AND pb.status = 'live' "
            "AND pb.activation_state IN ('pointer_pending', 'ambiguous', 'live')) "
            "OR (pb.status = 'previous' AND datetime(pb.servable_until) > datetime('now')))",
            (business_slug, live_build_id),
        ).fetchone()
    # App-plane replicas may use either a canonical login or an inherited per-replica scoped
    # login. Route on the actual function privilege, not a role-name guess: the bound SECURITY
    # DEFINER function is the only product_builds read app roles receive, while operator-owned
    # scheduled runs retain their direct control-plane read.
    current = conn.execute(
        "SELECT current_user AS role, "
        "has_function_privilege(current_user, "
        "'takyon_app_live_action_bundle(text,text,text)', 'EXECUTE') AS can_read_action_bundle"
    ).fetchone()
    can_read_action_bundle = str(
        _row_value(current, "can_read_action_bundle", 1) or ""
    ).strip().lower() in {"t", "true", "1"}
    if can_read_action_bundle:
        session_hash = hashlib.sha256(str(session_token or "").encode("utf-8")).hexdigest()
        return conn.execute(
            "SELECT live_build_id, action_bundle_json, action_bundle_sha256 "
            "FROM takyon_app_live_action_bundle(%s, %s, %s)",
            (business_slug, session_hash, live_build_id),
        ).fetchone()
    return conn.execute(
        "SELECT pb.build_id AS live_build_id, pb.action_bundle_json, "
        "pb.action_bundle_sha256 FROM product_builds pb "
        "JOIN app_surface_contracts surface ON surface.business_slug = pb.business_slug "
        "WHERE pb.business_slug = %s AND pb.build_id = %s "
        "AND ((surface.live_build_id = pb.build_id "
        "AND pb.status = 'live' "
        "AND pb.activation_state IN ('pointer_pending', 'ambiguous', 'live')) "
        "OR (pb.status = 'previous' AND pb.servable_until > now()))",
        (business_slug, live_build_id),
    ).fetchone()


def _decode_action_bundle(
    *, encoded: str, expected_digest: str, build_id: str
) -> dict[str, Any]:
    actual_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if not encoded or not expected_digest or actual_digest != expected_digest:
        raise ActionContractError(f"live build {build_id} action bundle digest mismatch")
    if len(encoded.encode("utf-8")) > _ACTION_BUNDLE_MAX_BYTES:
        raise ActionContractError("live action bundle exceeds the runtime size limit")
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ActionContractError("live action bundle is invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("version") != _ACTION_BUNDLE_VERSION:
        raise ActionContractError("live action bundle version is unsupported")
    files = payload.get("files")
    if not isinstance(files, list) or len(files) > _ACTION_BUNDLE_MAX_FILES:
        raise ActionContractError("live action bundle file list is invalid")
    return payload


def _decode_action_execution_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("execution_contract")
    if raw is None:
        # Rolling compatibility for builds published before the immutable contract was embedded.
        return {}
    if not isinstance(raw, Mapping):
        raise ActionContractError("live action bundle execution contract is invalid")
    specs = normalize_action_specs(raw.get("action_specs"))
    outbound_hosts = normalize_outbound_hosts(raw.get("outbound_hosts"))
    runtime_features = sorted(
        {
            str(value or "").strip().lower()
            for value in (raw.get("runtime_features") if isinstance(raw.get("runtime_features"), list) else [])
            if str(value or "").strip()
        }
    )
    rail_state = _json_safe_mapping(raw.get("rail_state"), field="live action rail_state")
    validate_action_contract(
        specs=specs,
        outbound_hosts=outbound_hosts,
        runtime_features=runtime_features,
    )
    return {
        "action_specs": specs,
        "outbound_hosts": outbound_hosts,
        "runtime_features": runtime_features,
        "rail_state": rail_state,
    }


def live_action_bundle_http_action_names(
    conn: Any,
    *,
    business_slug: str,
    live_build_id: str,
) -> set[str]:
    """HTTP action names certified by the exact immutable current/previous build bundle."""
    row = _live_action_bundle_row(
        conn,
        business_slug=business_slug,
        live_build_id=str(live_build_id or "").strip().lower(),
        session_token="",
    )
    if row is None:
        return set()
    build_id = str(_row_value(row, "live_build_id", 0) or "").strip().lower()
    encoded = str(_row_value(row, "action_bundle_json", 1) or "").strip()
    digest = str(_row_value(row, "action_bundle_sha256", 2) or "").strip().lower()
    payload = _decode_action_bundle(
        encoded=encoded,
        expected_digest=digest,
        build_id=build_id,
    )
    return {
        str(name or "").strip().lower()
        for name in (payload.get("http_action_names") or [])
        if _ACTION_NAME_RE.match(str(name or "").strip().lower())
    }


def _action_bundle_cache_lock(cache_root: Path) -> threading.Lock:
    key = str(cache_root)
    with _action_bundle_cache_locks_guard:
        lock = _action_bundle_cache_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _action_bundle_cache_locks[key] = lock
        return lock


def _action_bundle_process_lock_path(cache_root: Path, allowed_root: Path) -> Path:
    digest = hashlib.sha256(str(cache_root).encode("utf-8")).hexdigest()
    return allowed_root / ".locks" / f"{digest}.lock"


@contextmanager
def _hold_action_bundle_cache_lock(cache_root: Path, allowed_root: Path):
    """Serialize immutable-cache installation across threads and Uvicorn worker processes."""
    with _action_bundle_cache_lock(cache_root):
        lock_path = _action_bundle_process_lock_path(cache_root, allowed_root)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _materialize_live_action_bundle(
    store: Any,
    *,
    business_slug: str,
    surface: Mapping[str, Any],
    session_token: str,
) -> tuple[Path, set[str], dict[str, Any]]:
    """Verify and materialize the live build's action bundle into a build-keyed local cache."""
    build_id = str(surface.get("live_build_id") or "").strip().lower()
    if not build_id:
        raise ActionContractError("app surface has no live build for action execution")
    with store._connect() as conn:
        row = _live_action_bundle_row(
            conn,
            business_slug=business_slug,
            live_build_id=build_id,
            session_token=session_token,
        )
    if row is None:
        raise ActionContractError(
            f"live build {build_id} is no longer current or has no authorized action bundle"
        )
    resolved_build_id = str(_row_value(row, "live_build_id", 0) or "").strip().lower()
    if resolved_build_id != build_id:
        raise ActionContractError(
            "live action bundle build mismatch: "
            f"expected {build_id}, resolved {resolved_build_id or 'missing'}"
        )
    encoded = str(_row_value(row, "action_bundle_json", 1) or "").strip()
    expected_digest = str(_row_value(row, "action_bundle_sha256", 2) or "").strip().lower()
    payload = _decode_action_bundle(
        encoded=encoded,
        expected_digest=expected_digest,
        build_id=build_id,
    )
    files = payload.get("files")
    execution_contract = _decode_action_execution_contract(payload)

    allowed_root = (Path(store.root) / "cache" / "action-bundles").resolve()
    # Include the authenticated bundle digest in the cache identity. A build cache is therefore
    # write-once: an in-flight invocation can keep reading its exact directory while another
    # request resolves a different bundle, and no installer ever deletes a directory in use.
    cache_root = (
        allowed_root / business_slug / build_id / expected_digest
    ).resolve()
    if allowed_root not in cache_root.parents:
        raise ActionContractError("action bundle cache path escaped its root")
    normalized_files: list[tuple[str, str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            raise ActionContractError("live action bundle contains an invalid file entry")
        rel = str(item.get("path") or "").strip().replace("\\", "/")
        content = item.get("content")
        digest = str(item.get("sha256") or "").strip().lower()
        if not isinstance(content, str):
            raise ActionContractError(f"live action bundle file has invalid content: {rel}")
        if not (rel.startswith("actions/") and rel.endswith(".ts")) and rel != "_takyon/runtime-client.js":
            raise ActionContractError(f"live action bundle contains unsupported path: {rel}")
        target = (cache_root / rel).resolve()
        if cache_root not in target.parents:
            raise ActionContractError(f"live action bundle path escaped cache: {rel}")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
            raise ActionContractError(f"live action bundle file digest mismatch: {rel}")
        normalized_files.append((rel, content, digest))

    # Different customers may hit a newly deployed replica simultaneously, including through
    # different Uvicorn worker processes. Serialize the first materialization with a process-shared
    # lock that lives outside cache_root, so replacement can never unlink its own lock file.
    with _hold_action_bundle_cache_lock(cache_root, allowed_root):
        marker = cache_root / ".bundle-sha256"
        try:
            cached_ok = marker.is_file() and marker.read_text(encoding="utf-8").strip() == expected_digest
        except OSError:
            cached_ok = False
        if cached_ok:
            for rel, _content, digest in normalized_files:
                try:
                    if hashlib.sha256((cache_root / rel).read_bytes()).hexdigest() != digest:
                        cached_ok = False
                        break
                except OSError:
                    cached_ok = False
                    break

        if cache_root.exists() and not cached_ok:
            raise ActionContractError(
                "immutable action bundle cache is corrupt; refusing to replace an in-use bundle"
            )

        if not cached_ok:
            parent = cache_root.parent
            parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{expected_digest}.", dir=str(parent)))
            try:
                for rel, content, _digest in normalized_files:
                    target = staging / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                (staging / ".bundle-sha256").write_text(expected_digest + "\n", encoding="utf-8")
                (staging / ".execution-contract.json").write_text(
                    json.dumps(execution_contract, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(staging, cache_root)
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)

    certified = {
        str(name or "").strip().lower()
        for name in (payload.get("http_action_names") or [])
        if _ACTION_NAME_RE.match(str(name or "").strip().lower())
    }
    return cache_root, certified, execution_contract


def materialize_live_action_bundle(
    store: Any,
    *,
    business_slug: str,
    surface: Mapping[str, Any],
    session_token: str,
) -> tuple[Path, set[str]]:
    """Compatibility wrapper returning the historical two-value materialization result."""
    cache_root, certified, _execution_contract = _materialize_live_action_bundle(
        store,
        business_slug=business_slug,
        surface=surface,
        session_token=session_token,
    )
    return cache_root, certified


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


def _resolve_deno() -> str | None:
    """Resolve the deno binary across PATH + managed/standard install locations.

    The actions rail (build-time verification AND action exec) must see the same deno
    that core._runtime_capabilities resolves and that scripts/lib/deno-bootstrap.sh
    self-heals into $TAKYON_HOME/deno/bin. A bare shutil.which("deno") misses a
    Takyon-managed install and standard installer paths, which is how a publish got
    silently blocked on a host where deno was installed off-PATH (or freshly
    self-healed but not yet on the process PATH).
    """
    found = shutil.which("deno")
    if found:
        return found
    candidates: list[Path] = []
    takyon_home = os.getenv("TAKYON_HOME")
    if takyon_home:
        candidates.append(Path(takyon_home).expanduser() / "deno" / "bin" / "deno")
    deno_install = os.getenv("DENO_INSTALL")
    if deno_install:
        candidates.append(Path(deno_install).expanduser() / "bin" / "deno")
    home = Path.home()
    candidates.extend([
        home / ".deno" / "bin" / "deno",
        home / ".local" / "bin" / "deno",
        Path("/opt/homebrew/bin/deno"),
        Path("/usr/local/bin/deno"),
    ])
    for cand in candidates:
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except OSError:
            continue
    return None


def _try_ensure_deno_runtime() -> None:
    """Best-effort self-heal of a missing deno; never raises into the caller.

    Imported lazily to avoid a circular import (core imports app_actions at module
    load); a failure here just leaves deno unresolved and the caller blocks as before.
    """
    try:
        from . import core as takyon_core
    except Exception:
        try:
            from plugins.takyon import core as takyon_core
        except Exception:
            return
    try:
        takyon_core._ensure_deno_runtime()
    except Exception:
        pass


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
    deno = _resolve_deno()
    if not deno:
        # The deno requirement is real only when the product actually SHIPS an action —
        # a referenced UI call or a runtime-valid (non "_"-prefixed) action file. The
        # scaffold seeds an ignored actions/_example-generate.ts, so keying on
        # actions_root.exists() made EVERY fresh business unpublishable on a host without
        # deno even when it uses zero actions. Gate on real actions (referenced/file_backed).
        needs_deno = bool(referenced or file_backed)
        if needs_deno:
            # Self-heal a missing deno once (mirrors node bootstrap), then re-resolve, so a
            # host that lacks deno installs it instead of silently stripping the build.
            _try_ensure_deno_runtime()
            deno = _resolve_deno()
        if not deno:
            if needs_deno:
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


def _json_mapping_value(row: Any, *, key: str, index: int) -> dict[str, Any]:
    raw = _row_value(row, key, index)
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _action_usage_metadata(row: Any, *, index: int) -> dict[str, Any]:
    return _json_mapping_value(row, key="metadata", index=index)


def live_action_execution_verification(
    conn: Any,
    business_slug: str,
    actions: Any,
    *,
    live_build_id: str,
) -> dict[str, Any]:
    """Durable proof that one currently-live HTTP action ran as a real product customer.

    Source/build checks establish that an action *could* run.  Verification requires a successful
    ``app_usage_events`` receipt produced by an HTTP invocation from a signed-in product session,
    and that receipt must name the exact immutable live build whose action bundle executed.  A
    service/scheduled run or a successful invocation of an older build cannot verify current action
    execution.  This deliberately does NOT certify a multi-step browser workflow such as save,
    exact-ref reopen, revise, copy, export, and delete; that remains manual browser E2E.
    """
    http_specs = [
        spec
        for spec in normalize_action_specs(actions)
        if str(spec.get("trigger") or "").strip().lower() == "http"
    ]
    action_names = sorted(
        {
            str(spec.get("name") or "").strip().lower()
            for spec in http_specs
            if str(spec.get("name") or "").strip()
        }
    )
    build_id = str(live_build_id or "").strip().lower()
    base = {
        "action_execution_required": True,
        "status": "pending",
        "live_build_id": build_id,
        "actions": action_names,
        "verified_action": "",
        "verified_at": "",
        "receipt_path": "",
    }
    if not action_names:
        return {
            **base,
            "blocker": "no UI-certified HTTP action exists for signed-in live action execution verification",
        }
    if not build_id:
        return {
            **base,
            "blocker": "the product has no immutable live build id to verify",
        }

    bind = "%s" if _is_pg_conn(conn) else "?"
    metadata_column = "metadata" if _is_pg_conn(conn) else "metadata_json"
    # Postgres owns a UUID event id and a separate caller idempotency key. The retired SQLite
    # mirror used that key as `id`, so normalize both shapes to one `reservation_key` result column.
    # Matching the completion event against the UUID would make every real production invocation
    # stay pending forever even though the action succeeded.
    reservation_column = "reservation_key" if _is_pg_conn(conn) else "id"
    query = (
        f"SELECT id, {reservation_column} AS reservation_key, status, error, created_at, "
        f"completed_at, {metadata_column} "
        f"FROM app_usage_events WHERE business_slug = {bind} "
        f"AND purpose = 'action_invoke' AND route = {bind} "
        "ORDER BY COALESCE(completed_at, created_at) DESC, created_at DESC LIMIT 50"
    )
    event_query = (
        f"SELECT payload_json, created_at FROM events WHERE business_slug = {bind} "
        "AND event_type = 'app.action.invoke' ORDER BY created_at DESC LIMIT 100"
    )
    try:
        invocation_events = conn.execute(event_query, (business_slug,)).fetchall()
    except Exception:
        invocation_events = []
    for action_name in action_names:
        rows = conn.execute(
            query,
            (business_slug, f"/api/takyon/apps/{business_slug}/actions/{action_name}"),
        ).fetchall()
        for row in rows or []:
            if str(_row_value(row, "status", 2) or "").strip().lower() != "completed":
                continue
            metadata = _action_usage_metadata(row, index=6)
            if str(metadata.get("trigger") or "").strip().lower() != "http":
                continue
            if str(metadata.get("principal") or "").strip().lower() != "session":
                continue
            if str(metadata.get("live_build_id") or "").strip().lower() != build_id:
                continue
            usage_reservation_key = str(
                _row_value(row, "reservation_key", 1) or ""
            ).strip()
            matching_event: tuple[dict[str, Any], Any] | None = None
            for event_row in invocation_events or []:
                event_payload = _json_mapping_value(event_row, key="payload_json", index=0)
                if str(event_payload.get("action") or "").strip().lower() != action_name:
                    continue
                if str(event_payload.get("trigger") or "").strip().lower() != "http":
                    continue
                if str(event_payload.get("principal") or "").strip().lower() != "session":
                    continue
                if str(event_payload.get("live_build_id") or "").strip().lower() != build_id:
                    continue
                if str(event_payload.get("usage_reservation_key") or "").strip() != usage_reservation_key:
                    continue
                matching_event = (event_payload, event_row)
                break
            if matching_event is None:
                continue
            event_payload, event_row = matching_event
            return {
                **base,
                "status": "action_verified",
                "verified_action": action_name,
                "verified_at": str(
                    _row_value(event_row, "created_at", 1)
                    or _row_value(row, "completed_at", 5)
                    or _row_value(row, "created_at", 4)
                    or ""
                ).strip(),
                "receipt_path": str(
                    event_payload.get("receipt_path")
                    or metadata.get("receipt_path")
                    or ""
                ).strip(),
                "blocker": "",
            }
    return {
        **base,
        "blocker": (
            "no successful signed-in live action execution receipt exists for live build "
            f"{build_id}"
        ),
    }


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


def _legacy_unbound_live_build_id(
    conn: Any,
    *,
    business_slug: str,
    session_token: str,
) -> str:
    """Resolve the one-time DB-stamped legacy build through an authenticated, expiring gate."""
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    if _is_pg_conn(conn):
        row = conn.execute(
            "SELECT live_build_id, legacy_unbound_until "
            "FROM takyon_app_legacy_unbound_live_build(%s, %s)",
            (business_slug, session_hash),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT pb.build_id AS live_build_id, pb.legacy_unbound_until "
            "FROM app_sessions s "
            "JOIN app_users u ON u.business_slug = s.business_slug "
            "AND u.id = s.app_user_id "
            "JOIN app_surface_contracts surface "
            "ON surface.business_slug = s.business_slug "
            "JOIN product_builds pb ON pb.business_slug = surface.business_slug "
            "AND pb.build_id = surface.live_build_id "
            "WHERE s.business_slug = ? AND s.token_hash = ? "
            "AND s.revoked_at IS NULL AND datetime(s.expires_at) > datetime('now') "
            "AND u.status = 'active' AND surface.publish_status = 'published' "
            "AND pb.status = 'live' "
            "AND datetime(pb.legacy_unbound_until) > datetime('now') LIMIT 1",
            (business_slug, session_hash),
        ).fetchone()
    if row is None:
        return ""
    return str(_row_value(row, "live_build_id", 0) or "").strip().lower()


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
    ``ai_gateway._user_monthly_budget_microusd`` so the action reserve path and the gateway path
    share ONE rule: a paid plan grants its FULL monthly ``included_ai_budget_microusd`` (the usage
    gate anchors the matching entitlement-monthly window itself, migration 0063); a free /
    unentitled / absent plan grants 0. NEVER returns None (an uncapped per-user limit would defeat
    the only gate)."""
    try:
        from .ai_gateway import _user_monthly_budget_microusd
    except Exception:
        from plugins.takyon.ai_gateway import _user_monthly_budget_microusd
    return _user_monthly_budget_microusd(plan)


def _resolve_pg_action_usage_limit(
    store: Any,
    conn: Any,
    *,
    business_slug: str,
    app_user_id: str | None,
    app_user_tier: str | None,
    session_token: str | None = None,
) -> tuple[str | None, int]:
    """Resolve ``(tier, per_user_limit_microusd)`` for a billable action reserve.

    GOAL_RULES §3 gap #4: this path MUST require an active entitlement (mirror the gateway) and
    return a CONCRETE per-user limit (plan-derived-or-0), NEVER an unbounded ``None`` that would
    fall through to the (now-removed) per-business pool = ungated spend. A ``service``/null caller
    with no active paid entitlement raises ``ActionBudgetExceeded`` (subscription_required); an
    entitled caller resolves to its paid plan's ``included_ai_budget_microusd``."""
    resolved_user_tier = str(app_user_tier or "").strip() or None
    token = str(session_token or "").strip()
    if token:
        try:
            from .core import _hash_token
        except Exception:
            from plugins.takyon.core import _hash_token

        with store._leaf_conn(conn) as raw:
            row = raw.execute(
                "select app_user_id, tier, plan_key, included_ai_budget_microusd "
                "from takyon_app_action_usage_limit(%s, %s)",
                (business_slug, _hash_token(token)),
            ).fetchone()
        if row is None:
            raise ActionBudgetExceeded(
                "subscription_required: no active paid entitlement for billable action"
            )
        resolved_id = str(row[0] or "").strip()
        if app_user_id and resolved_id and resolved_id != str(app_user_id):
            raise ActionBudgetExceeded("session_user_mismatch: action user does not match session")
        # FULL monthly allowance — the usage gate anchors the matching entitlement-monthly window
        # itself (migration 0063), so no pro-rate here.
        monthly_limit = max(0, int(row[3] or 0))
        return str(row[1] or resolved_user_tier or "") or None, monthly_limit

    leaves = store._app_leaves()
    with store._leaf_conn(conn) as raw:
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


def _validated_action_user(
    store: Any,
    *,
    business_slug: str,
    session_token: str,
) -> dict[str, Any]:
    """Resolve the principal from the server-side session before deriving any idempotency key."""
    try:
        from .core import _PGConn, _hash_token, _now
    except Exception:
        from plugins.takyon.core import _PGConn, _hash_token, _now

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            leaves = store._app_leaves()
            if str(getattr(store, "_database_plane", "") or "").strip().lower() == "app":
                scope = store._pg_app_scope(
                    conn,
                    business_slug,
                    session_token=session_token,
                )
            else:
                # Scheduled actions run on the operator worker with a short-lived service session;
                # that trusted role validates the same session directly and must not be demoted to
                # an app login merely to derive the server-owned principal namespace.
                scope = nullcontext(conn)
            with scope:
                with store._leaf_conn(conn) as leaf:
                    user = leaves["identity"].validate_session(
                        leaf,
                        business_slug,
                        session_token,
                    )
            if user is None:
                raise AppActionError("app account not found")
            return {
                "id": str(user.id),
                "email": str(user.email),
                "tier": str(user.tier),
                "status": str(user.status),
            }
        row = conn.execute(
            "SELECT u.* FROM app_sessions s JOIN app_users u "
            "ON u.business_slug = s.business_slug AND u.id = s.app_user_id "
            "WHERE s.business_slug = ? AND s.token_hash = ? AND s.revoked_at IS NULL "
            "AND s.expires_at > ? AND u.status = 'active' LIMIT 1",
            (business_slug, _hash_token(session_token), _now()),
        ).fetchone()
        user = store._row_to_dict(row) if hasattr(store, "_row_to_dict") else (dict(row) if row else None)
    if not user:
        raise AppActionError("app account not found")
    return dict(user)


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
    expected_live_build_id: str = "",
) -> dict[str, Any]:
    try:
        from .core import _surface_product_workflow_shape
    except Exception:
        from plugins.takyon.core import _surface_product_workflow_shape

    if len(json.dumps(payload).encode("utf-8")) > _ACTION_REQUEST_BODY_LIMIT:
        raise AppActionError("payload too large")
    session_token = str(principal.get("session_token") or "").strip()
    if not session_token:
        raise AppActionError("session_token is required")
    validated_user = _validated_action_user(
        store,
        business_slug=business_slug,
        session_token=session_token,
    )
    claimed_user_id = str((principal.get("user") or {}).get("id") or "").strip()
    validated_user_id = str(validated_user.get("id") or "").strip()
    if not validated_user_id or (claimed_user_id and claimed_user_id != validated_user_id):
        raise AppActionError("session_user_mismatch: action user does not match session")
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
    current_workflow = _surface_product_workflow_shape(dict(surface))
    current_live_build_id = str(surface.get("live_build_id") or "").strip().lower()
    requested_build_id = str(expected_live_build_id or "").strip().lower()
    if requested_build_id and not re.fullmatch(r"[0-9a-f]{32}", requested_build_id):
        raise ActionContractError("expected_live_build_id is invalid")
    if trigger != "schedule" and not requested_build_id:
        with store._connect() as conn:
            legacy_build_id = _legacy_unbound_live_build_id(
                conn,
                business_slug=business_slug,
                session_token=session_token,
            )
        if not legacy_build_id or legacy_build_id != current_live_build_id:
            raise ActionContractError(
                "expected_live_build_id is required for HTTP action invocation"
            )
        _LOGGER.warning(
            "accepting DB-authorized legacy HTTP action invocation for live build %s",
            legacy_build_id,
        )
        requested_build_id = legacy_build_id
    live_build_id = requested_build_id or current_live_build_id
    if not re.fullmatch(r"[0-9a-f]{32}", live_build_id):
        raise ActionContractError("app surface has no valid immutable live build id")
    execution_surface = {**dict(surface), "live_build_id": live_build_id}
    # Every serving replica resolves the exact immutable bundle attached to the live build. This
    # removes producer-host SSH and mutable workspace caches from the execution contract.
    site_root, certified_http_actions, frozen_contract = _materialize_live_action_bundle(
        store,
        business_slug=business_slug,
        surface=execution_surface,
        session_token=session_token,
    )
    workflow = (
        {
            "actions": list(frozen_contract.get("action_specs") or []),
            "outbound_hosts": list(frozen_contract.get("outbound_hosts") or []),
        }
        if frozen_contract
        else current_workflow
    )
    specs = file_backed_action_specs(site_root, workflow)
    outbound_hosts = normalize_outbound_hosts(workflow.get("outbound_hosts"))
    execution_runtime_features = (
        list(frozen_contract.get("runtime_features") or [])
        if frozen_contract
        else list(surface.get("runtime_features") or [])
    )
    execution_rail_state = (
        dict(frozen_contract.get("rail_state") or {})
        if frozen_contract
        else (
            dict(surface.get("rail_state"))
            if isinstance(surface.get("rail_state"), Mapping)
            else {}
        )
    )
    validate_action_contract(
        specs=specs,
        outbound_hosts=outbound_hosts,
        runtime_features=execution_runtime_features,
    )
    spec = next((item for item in specs if str(item.get("name")) == action_name), None)
    expected_trigger = str(spec.get("trigger") or "") if spec is not None else ""
    if spec is not None and expected_trigger != trigger:
        raise ActionContractError(f"action {action_name} is declared for {expected_trigger}, not {trigger}")
    if trigger == "schedule" and spec is None:
        raise ActionContractError(f"schedule action {action_name} must declare a schedule trigger")
    # Customer-invokable (non-schedule) actions must be in the SAME HTTP-certified set the surface
    # declaration gate computes — a real, UI-referenced, handler-backed action. This refuses an
    # invoke of an undeclared / un-exposed / stub action file that merely happens to exist on disk.
    if trigger != "schedule" and action_name not in certified_http_actions:
        raise ActionContractError(f"action {action_name} is not an HTTP-certified action for this product")
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
    caller_idempotency_key = str(idempotency_key or "").strip()
    if not caller_idempotency_key:
        raise AppActionError("idempotency_key is required")
    # A caller key is idempotent only within the immutable build whose code it addresses.  A later
    # build must never replay an earlier build's receipt or usage reservation under the same key.
    usage_business = business_slug
    app_user_id = validated_user_id or None
    app_user_tier = str(validated_user.get("tier") or "") or None
    app_session_token = str(principal.get("session_token") or "").strip()
    # Idempotency is customer-scoped. Without the principal namespace, two customers choosing the
    # same caller key share both the usage reservation and the local receipt, allowing customer B to
    # receive customer A's cached action result. The server-validated app-user id is the authority.
    reservation_key = _action_reservation_key(
        app_user_id=app_user_id,
        principal_kind=str(principal.get("kind") or "service"),
        action_name=action_name,
        live_build_id=live_build_id,
        caller_idempotency_key=caller_idempotency_key,
    )
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
        sync=False,
    )
    request = {
        "payload": payload,
        "ctx": {
            "base_url": f"{base.origin}{_ACTION_CONTEXT_PREFIX.format(business=business_slug)}",
            "session_token": session_token,
            "business": business_slug,
            "trigger": trigger,
            "live_build_id": live_build_id,
            "principal": {
                "kind": str(principal.get("kind") or "session"),
                "id": validated_user_id,
                "email": str(validated_user.get("email") or ""),
            },
            # The shared client's ensureRail() needs the rails declared. Pass the surface's declared
            # features PLUS the platform rails a server action may call (server-gated), so ctx.generate
            # etc. resolve regardless of which UI features the product happened to declare.
            "runtime_features": execution_runtime_features,
            "rail_state": {
                **execution_rail_state,
                **{rail: "live" for rail in _ACTION_RUNTIME_RAILS},
            },
        },
    }
    estimate = int(config["invoke_price_microusd"])
    run_metadata: dict[str, Any] = {}
    verification_metadata = {
        "action": action_name,
        "trigger": trigger,
        "principal": str(principal.get("kind") or "session"),
        "live_build_id": live_build_id,
        "receipt_path": receipt_rel,
    }
    # Acquire the run lock immediately before the guarded block so the finally always releases it
    # (the prior placement leaked the lock if receipt/request setup raised in between).
    _acquire_business_run(run_lock_key)
    reserved = False
    claimed_new = False
    finish_token = secrets.token_urlsafe(32)
    try:
        claim = _claim_action_invocation(
            store,
            business_slug=business_slug,
            app_user_id=str(app_user_id or ""),
            session_token=app_session_token,
            finish_token=finish_token,
            reservation_key=reservation_key,
            action_name=action_name,
            live_build_id=live_build_id,
            receipt_path=receipt_rel,
        )
        claimed_new = bool(claim.get("is_new"))
        if not claimed_new:
            prior_status = str(claim.get("status") or "").strip().lower()
            if prior_status == "completed":
                return {
                    "success": True,
                    "action": action_name,
                    "result": claim.get("result"),
                    "run": claim.get("run") or {},
                    "receipt": claim.get("receipt_path") or receipt_rel,
                }
            if prior_status == "failed":
                raise ActionReplayConflict(
                    "idempotency_key already used; prior attempt failed: "
                    f"{str(claim.get('error') or 'unknown error')}"
                )
            raise ActionReplayConflict(
                "action_replay_in_progress: idempotency_key is already in flight on a serving replica"
            )
        _reserve_usage(
            store,
            usage_business,
            reservation_key=reservation_key,
            app_user_id=app_user_id,
            app_user_tier=app_user_tier,
            session_token=app_session_token,
            estimate_microusd=estimate,
            route=f"/api/takyon/apps/{business_slug}/actions/{action_name}",
            metadata=verification_metadata,
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
            session_token=app_session_token,
            metadata=verification_metadata,
        )
        reserved = False
        _finish_action_invocation(
            store,
            business_slug=business_slug,
            app_user_id=str(app_user_id or ""),
            finish_token=finish_token,
            reservation_key=reservation_key,
            status="completed",
            result=result,
            run=run_metadata,
            receipt_path=receipt_rel,
        )
        claimed_new = False
        receipt = {
            "success": True,
            "business": business_slug,
            "action": action_name,
            "trigger": trigger,
            "principal": str(principal.get("kind") or "session"),
            "live_build_id": live_build_id,
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
                    "live_build_id": live_build_id,
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
                session_token=app_session_token,
                metadata=verification_metadata,
            )
        if claimed_new:
            try:
                _finish_action_invocation(
                    store,
                    business_slug=business_slug,
                    app_user_id=str(app_user_id or ""),
                    finish_token=finish_token,
                    reservation_key=reservation_key,
                    status="failed",
                    run=run_metadata,
                    receipt_path=receipt_rel,
                    error=str(exc),
                )
            except Exception as finish_exc:
                _LOGGER.error(
                    "failed to finalize durable action error receipt: business=%s action=%s err=%s",
                    business_slug,
                    action_name,
                    finish_exc,
                )
            failure_receipt = {
                "success": False,
                "business": business_slug,
                "action": action_name,
                "trigger": trigger,
                "principal": str(principal.get("kind") or "session"),
                "live_build_id": live_build_id,
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


def _action_reservation_key(
    *,
    app_user_id: str | None,
    principal_kind: str,
    action_name: str,
    live_build_id: str,
    caller_idempotency_key: str,
) -> str:
    principal_key = str(app_user_id or "").strip() or f"service:{str(principal_kind or 'service')}"
    principal_namespace = hashlib.sha256(principal_key.encode("utf-8")).hexdigest()[:24]
    action_namespace = hashlib.sha256(str(action_name or "").encode("utf-8")).hexdigest()[:24]
    caller_namespace = hashlib.sha256(
        str(caller_idempotency_key or "").encode("utf-8")
    ).hexdigest()
    return (
        f"principal:{principal_namespace}:build:{live_build_id}:"
        f"action:{action_namespace}:caller:{caller_namespace}"
    )


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        from .core import _atomic_write_text, _json_dumps
    except Exception:
        from plugins.takyon.core import _atomic_write_text, _json_dumps

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _json_dumps(dict(payload)) + "\n")


def _claim_action_invocation(
    store: Any,
    *,
    business_slug: str,
    app_user_id: str,
    session_token: str,
    finish_token: str,
    reservation_key: str,
    action_name: str,
    live_build_id: str,
    receipt_path: str,
) -> dict[str, Any]:
    """Atomically claim one idempotency key across every serving replica."""
    try:
        from .core import _PGConn, _now
    except Exception:
        from plugins.takyon.core import _PGConn, _now

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
            finish_token_hash = hashlib.sha256(finish_token.encode("utf-8")).hexdigest()
            row = conn.execute(
                "SELECT is_new, status, error, result_json, run_json, receipt_path "
                "FROM takyon_app_claim_action_invocation(%s, %s, %s, %s, %s, %s, %s)",
                (
                    business_slug,
                    session_hash,
                    finish_token_hash,
                    reservation_key,
                    action_name,
                    live_build_id,
                    receipt_path,
                ),
            ).fetchone()
        else:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO app_action_invocations ("
                "business_slug, app_user_id, reservation_key, finish_token_hash, action_name, "
                "live_build_id, status, receipt_path, claimed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)",
                (
                    business_slug,
                    app_user_id,
                    reservation_key,
                    hashlib.sha256(finish_token.encode("utf-8")).hexdigest(),
                    action_name,
                    live_build_id,
                    receipt_path,
                    _now(),
                ),
            )
            is_new = int(cursor.rowcount or 0) > 0
            row = conn.execute(
                "SELECT ?, status, error, result_json, run_json, receipt_path, app_user_id, "
                "action_name, live_build_id FROM app_action_invocations "
                "WHERE business_slug = ? AND reservation_key = ?",
                (1 if is_new else 0, business_slug, reservation_key),
            ).fetchone()
            if row is not None and (
                str(_row_value(row, "app_user_id", 6) or "") != app_user_id
                or str(_row_value(row, "action_name", 7) or "") != action_name
                or str(_row_value(row, "live_build_id", 8) or "") != live_build_id
            ):
                raise ActionReplayConflict(
                    "idempotency_key is already bound to a different principal, action, or build"
                )
    if row is None:
        raise ActionReplayConflict("action invocation claim was refused for this product session")
    result_raw = _row_value(row, "result_json", 3)
    run_raw = _row_value(row, "run_json", 4)
    try:
        result = json.loads(str(result_raw)) if result_raw not in {None, ""} else None
    except (TypeError, ValueError, json.JSONDecodeError):
        result = None
    try:
        run = json.loads(str(run_raw)) if run_raw not in {None, ""} else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        run = {}
    return {
        "is_new": bool(_row_value(row, "is_new", 0)),
        "status": str(_row_value(row, "status", 1) or "").strip().lower(),
        "error": str(_row_value(row, "error", 2) or "").strip(),
        "result": result,
        "run": run if isinstance(run, dict) else {},
        "receipt_path": str(_row_value(row, "receipt_path", 5) or receipt_path).strip(),
    }


def _finish_action_invocation(
    store: Any,
    *,
    business_slug: str,
    app_user_id: str,
    finish_token: str,
    reservation_key: str,
    status: str,
    result: Any = None,
    run: Mapping[str, Any] | None = None,
    receipt_path: str,
    error: str = "",
) -> None:
    try:
        from .core import _PGConn, _now
    except Exception:
        from plugins.takyon.core import _PGConn, _now

    result_json = json.dumps(result, ensure_ascii=False, sort_keys=True) if status == "completed" else ""
    run_json = json.dumps(dict(run or {}), ensure_ascii=False, sort_keys=True)
    finish_token_hash = hashlib.sha256(finish_token.encode("utf-8")).hexdigest()
    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            row = conn.execute(
                "SELECT takyon_app_finish_action_invocation(%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    business_slug,
                    finish_token_hash,
                    reservation_key,
                    status,
                    result_json,
                    run_json,
                    receipt_path,
                    error,
                ),
            ).fetchone()
            finished = bool(_row_value(row, "takyon_app_finish_action_invocation", 0)) if row else False
        else:
            cursor = conn.execute(
                "UPDATE app_action_invocations SET status = ?, result_json = NULLIF(?, ''), "
                "run_json = ?, receipt_path = ?, error = NULLIF(?, ''), completed_at = ? "
                "WHERE business_slug = ? AND app_user_id = ? AND reservation_key = ? "
                "AND finish_token_hash = ? AND status = 'running'",
                (
                    status,
                    result_json,
                    run_json,
                    receipt_path,
                    error,
                    _now(),
                    business_slug,
                    app_user_id,
                    reservation_key,
                    finish_token_hash,
                ),
            )
            finished = int(cursor.rowcount or 0) > 0
            if not finished:
                prior = conn.execute(
                    "SELECT status FROM app_action_invocations WHERE business_slug = ? "
                    "AND app_user_id = ? AND reservation_key = ? AND finish_token_hash = ?",
                    (business_slug, app_user_id, reservation_key, finish_token_hash),
                ).fetchone()
                finished = bool(prior and str(_row_value(prior, "status", 0) or "") == status)
    if not finished:
        raise ActionReplayConflict("durable action invocation receipt could not be finalized")


def _reserve_usage(
    store: Any,
    business_slug: str,
    *,
    reservation_key: str,
    app_user_id: str | None,
    app_user_tier: str | None,
    session_token: str | None = None,
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
                            session_token=session_token,
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
                            session_token=session_token,
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
    session_token: str | None = None,
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
                settle_kwargs = {
                    "actual_cost_microusd": actual_microusd,
                    "metadata": dict(metadata),
                }
                if session_token:
                    settle_kwargs["session_token"] = session_token
                app_usage.settle_usage(
                    raw,
                    business_slug,
                    reservation_key,
                    **settle_kwargs,
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
    session_token: str | None = None,
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
                    release_kwargs = {
                        "error": error,
                        "metadata": dict(metadata),
                    }
                    if session_token:
                        release_kwargs["session_token"] = session_token
                    app_usage.release_usage(
                        raw,
                        business_slug,
                        reservation_key,
                        **release_kwargs,
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
    deno = _resolve_deno()
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
