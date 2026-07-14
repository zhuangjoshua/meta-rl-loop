"""Primary Claude Agent SDK runtime support for Takyon.

This module is deliberately an orchestration adapter, not a second backend.  The
SDK subprocess receives no raw provider key and no direct Takyon authority.  It
can invoke only the exact tool schemas selected by the parent process; every
call crosses a private inherited socket and is dispatched by the existing
Python tool registry inside the caller's scoped ContextVars.

The bridge is kept here, rather than in a model prompt or skill, because scope,
tool availability, and authority are enforcement concerns.
"""

from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import os
import re
import signal
import shutil
import socket
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


SDK_TOOL_BRIDGE_FD_ENV = "TAKYON_SDK_TOOL_BRIDGE_FD"
SDK_SESSION_BRIDGE_FD_ENV = "TAKYON_SDK_SESSION_BRIDGE_FD"
SDK_PROGRESS_PREFIX = "TAKYON_SDK_EVENT "
SDK_SESSION_NAMESPACE = uuid.UUID("c284fcec-f3d0-4a85-ae8f-eb38b68a2b0d")
SDK_MAX_STDOUT_BYTES = 4 * 1024 * 1024
SDK_MAX_STDERR_BYTES = 2 * 1024 * 1024
SDK_MAX_SKILL_RESOURCE_BYTES = 256 * 1024
SDK_RUNTIME_BASELINE_TOOLS = frozenset({"skill_read_resource"})
SDK_ENTRYPOINT_MODES = {
    "interactive": "interactive",
    "bootstrap": "ceo_bootstrap",
    "wake": "ceo_wake",
}
SDK_HANDOFF_CAPABILITY_ADAPTERS = frozenset({"mcp", "sandbox", "web"})
SDK_HANDOFF_CAPABILITY_SCOPES = frozenset(
    {"current_business", "current_workspace", "current_operator", "current_session"}
)
SDK_HANDOFF_CAPABILITY_AUTHORITIES = frozenset(
    {
        "operator_session",
        "explicit_operator_interaction",
        "creative_credit",
        "mobile_release",
        "none",
    }
)
SDK_GLOBAL_OPERATOR_TOOLS = frozenset(
    {
        "business_list_app_connections",
        "business_list_app_directory_entries",
        "business_list_app_media",
        "business_list_app_records",
        "business_list_businesses",
        "business_list_files",
        "business_read_app_account",
        "business_read_app_analytics",
        "business_read_app_directory_entry",
        "business_read_app_profile",
        "business_read_app_record",
        "business_read_business",
        "business_read_channel_credit_budgets",
        "business_read_file",
        "business_read_store_status",
        "business_shopify_read_orders",
        "skill_read_resource",
        "todo",
        "web_extract",
        "web_search",
    }
)
SDK_PACKAGE_VERSION = "0.3.148"
SDK_ZOD_VERSION = "4.4.3"
SDK_SKILL_RESOURCE_GUIDANCE = (
    "When an approved skill references another published file, call "
    "skill_read_resource with the canonical skill name and the skill-relative "
    "path. No host filesystem Read tool is available."
)

# These tools belong to the orchestration layer being replaced.  They may stay
# registered during a reversible canary, but the primary SDK can never see or
# invoke them.
LEGACY_OR_DELEGATING_TOOLS = frozenset(
    {
        "business_claude_agent_task",
        "claude.agent_task",
        "delegate_task",
        "skills_list",
        "skill_view",
    }
)


def _canonical_relative_path(value: object, *, label: str) -> str:
    """Canonicalize one model-controlled relative path or fail closed."""

    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ClaudeSdkRuntimeError(f"{label} must be a non-empty relative path")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ClaudeSdkRuntimeError(f"{label} may not contain '..'")
        parts.append(part)
    if not parts:
        raise ClaudeSdkRuntimeError(f"{label} must name a file")
    return "/".join(parts)


class ClaudeSdkRuntimeError(RuntimeError):
    """Fail-closed primary SDK runtime error."""


class ClaudeSdkProcessStopped(ClaudeSdkRuntimeError):
    """The parent deliberately stopped the SDK process before a final receipt."""

    def __init__(self, reason: str, *, inactivity_timeout: bool = False) -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.inactivity_timeout = bool(inactivity_timeout)


class SessionStoreBackend(Protocol):
    """Cross-host durable storage port for opaque SDK transcript entries.

    Implementations must serialize concurrent appends per key, preserve commit
    order, and deduplicate entries carrying the same non-empty ``uuid``.  The
    runtime process never receives the backend credential.
    """

    def append(
        self, key: Mapping[str, str], entries: Sequence[Mapping[str, Any]]
    ) -> None: ...

    def load(self, key: Mapping[str, str]) -> list[dict[str, Any]] | None: ...

    def list_subkeys(self, key: Mapping[str, str]) -> list[str]: ...

    def delete(self, key: Mapping[str, str]) -> None: ...


class InMemorySessionStoreBackend:
    """Reference backend used by focused tests; never a production fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._uuids: dict[tuple[str, str, str], set[str]] = {}

    @staticmethod
    def _key(key: Mapping[str, str]) -> tuple[str, str, str]:
        return (
            str(key.get("projectKey") or ""),
            str(key.get("sessionId") or ""),
            str(key.get("subpath") or ""),
        )

    def append(
        self, key: Mapping[str, str], entries: Sequence[Mapping[str, Any]]
    ) -> None:
        storage_key = self._key(key)
        with self._lock:
            target = self._entries.setdefault(storage_key, [])
            seen = self._uuids.setdefault(storage_key, set())
            for entry in entries:
                entry_uuid = str(entry.get("uuid") or "").strip()
                if entry_uuid and entry_uuid in seen:
                    continue
                target.append(copy.deepcopy(dict(entry)))
                if entry_uuid:
                    seen.add(entry_uuid)

    def load(self, key: Mapping[str, str]) -> list[dict[str, Any]] | None:
        storage_key = self._key(key)
        with self._lock:
            if storage_key not in self._entries:
                return None
            return copy.deepcopy(self._entries[storage_key])

    def list_subkeys(self, key: Mapping[str, str]) -> list[str]:
        prefix = (str(key.get("projectKey") or ""), str(key.get("sessionId") or ""))
        with self._lock:
            return sorted(
                storage_key[2]
                for storage_key in self._entries
                if storage_key[:2] == prefix and storage_key[2]
            )

    def delete(self, key: Mapping[str, str]) -> None:
        storage_key = self._key(key)
        with self._lock:
            if storage_key[2]:
                self._entries.pop(storage_key, None)
                self._uuids.pop(storage_key, None)
                return
            prefix = storage_key[:2]
            for candidate in [item for item in self._entries if item[:2] == prefix]:
                self._entries.pop(candidate, None)
                self._uuids.pop(candidate, None)


def stable_sdk_session_id(value: object) -> str:
    """Return a stable SDK-compatible UUID for one Takyon session/job identity."""

    text = str(value or "").strip()
    if not text:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(SDK_SESSION_NAMESPACE, f"takyon:{text}"))


def stable_sdk_invocation_id(*, session_id: str, epoch: str) -> str:
    stable_session = stable_sdk_session_id(session_id)
    normalized_epoch = str(epoch or "turn").strip() or "turn"
    # Bootstrap/wake continuations are additional SDK queries inside one
    # Takyon job budget, not fresh spend authorities.  Collapse any diagnostic
    # sub-epoch so retries and same-job continuations reuse the same cumulative
    # Safebox envelope. Interactive turns retain their per-turn epoch.
    epoch_head = normalized_epoch.split(":", 1)[0].lower()
    if epoch_head in {"bootstrap", "ceo_bootstrap"}:
        normalized_epoch = "bootstrap"
    elif epoch_head in {"wake", "ceo_wake"}:
        normalized_epoch = "wake"
    return str(
        uuid.uuid5(SDK_SESSION_NAMESPACE, f"invocation:{stable_session}:{normalized_epoch}")
    )


def _function_record(definition: Mapping[str, Any]) -> Mapping[str, Any]:
    record = definition.get("function")
    return record if isinstance(record, Mapping) else definition


def sdk_tool_definitions(
    *,
    enabled_toolsets: Sequence[str],
    disabled_toolsets: Sequence[str] | None = None,
    excluded_tools: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return the exact existing Takyon schemas exposed through the SDK bridge.

    The model-facing names and JSON schemas come from ``model_tools`` so the SDK
    cannot drift from the guarded handlers used by Hermes during the canary.
    Legacy skill/delegation tools are removed unconditionally.
    """

    from model_tools import get_tool_definitions

    excluded = LEGACY_OR_DELEGATING_TOOLS | {
        str(name or "").strip() for name in excluded_tools if str(name or "").strip()
    }
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in get_tool_definitions(
        enabled_toolsets=list(enabled_toolsets),
        disabled_toolsets=list(disabled_toolsets or ()),
        quiet_mode=True,
    ):
        if not isinstance(raw, Mapping):
            continue
        function = _function_record(raw)
        name = str(function.get("name") or "").strip()
        if not name or name in excluded or name in seen:
            continue
        parameters = function.get("parameters")
        if not isinstance(parameters, Mapping):
            parameters = {"type": "object", "properties": {}}
        selected.append(
            {
                "name": name,
                "description": str(function.get("description") or "").strip(),
                "inputSchema": dict(parameters),
            }
        )
        seen.add(name)
    return selected


def enforce_sdk_mode_tool_policy(
    *,
    manifest_path: str | os.PathLike[str],
    mode: str,
    tool_definitions: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], SdkModeToolPolicy]:
    """Apply the compiled HANDOFF policy before schemas reach the SDK.

    Skills remain globally discoverable, while their mode selection and tool
    capabilities are independently enforced. Missing or drifted policy is a
    deployment error, never permission to expose a broader tool set.
    """

    normalized_mode = {
        "ceo_bootstrap": "bootstrap",
        "ceo_wake": "wake",
        "bootstrap": "bootstrap",
        "wake": "wake",
        "interactive": "interactive",
    }.get(str(mode or "").strip().lower(), "")
    if not normalized_mode:
        raise ClaudeSdkRuntimeError(f"unsupported SDK HANDOFF mode: {mode!r}")
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ClaudeSdkRuntimeError(
            "approved skill manifest is unreadable for mode-policy enforcement"
        ) from exc
    if not isinstance(manifest, Mapping):
        raise ClaudeSdkRuntimeError("approved skill manifest must be an object")
    capability_bindings = manifest.get("capability_bindings")
    capability_tools = manifest.get("capability_tools")
    mode_policies = manifest.get("mode_tool_policy")
    if (
        not isinstance(capability_bindings, Mapping)
        or not isinstance(capability_tools, Mapping)
        or not isinstance(mode_policies, Mapping)
    ):
        raise ClaudeSdkRuntimeError(
            "approved skill manifest omits compiled HANDOFF tool policy"
        )
    if set(capability_bindings) != set(capability_tools):
        raise ClaudeSdkRuntimeError(
            "approved skill manifest capability bindings drifted from capability tools"
        )
    for capability, raw_binding in capability_bindings.items():
        if not str(capability or "").strip() or not isinstance(raw_binding, Mapping):
            raise ClaudeSdkRuntimeError(
                "SDK HANDOFF capability_bindings contains an invalid entry"
            )
        if set(raw_binding) != {"adapter", "tools", "scope", "authority"}:
            raise ClaudeSdkRuntimeError(
                f"SDK HANDOFF capability {capability!r} has invalid binding fields"
            )
        adapter = str(raw_binding.get("adapter") or "").strip()
        scope = str(raw_binding.get("scope") or "").strip()
        authority = str(raw_binding.get("authority") or "").strip()
        raw_tools = raw_binding.get("tools")
        compiled_tools = capability_tools.get(capability)
        if (
            adapter not in SDK_HANDOFF_CAPABILITY_ADAPTERS
            or scope not in SDK_HANDOFF_CAPABILITY_SCOPES
            or authority not in SDK_HANDOFF_CAPABILITY_AUTHORITIES
            or not isinstance(raw_tools, list)
            or not raw_tools
            or any(not isinstance(tool, str) or not tool.strip() for tool in raw_tools)
            or len(raw_tools) != len(set(raw_tools))
            or raw_tools != compiled_tools
        ):
            raise ClaudeSdkRuntimeError(
                f"SDK HANDOFF capability {capability!r} has an invalid or drifted binding"
            )
    raw_policy = mode_policies.get(normalized_mode)
    if not isinstance(raw_policy, Mapping):
        raise ClaudeSdkRuntimeError(
            f"approved skill manifest omits {normalized_mode} tool policy"
        )

    def string_list(field: str) -> tuple[str, ...]:
        value = raw_policy.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ClaudeSdkRuntimeError(
                f"SDK HANDOFF {normalized_mode}.{field} must be a string list"
            )
        normalized = tuple(item.strip() for item in value)
        if len(normalized) != len(set(normalized)):
            raise ClaudeSdkRuntimeError(
                f"SDK HANDOFF {normalized_mode}.{field} must not contain duplicates"
            )
        return normalized

    raw_inventory = manifest.get("model_tool_inventory")
    inventory_digest = str(manifest.get("model_tool_inventory_digest") or "")
    if not isinstance(raw_inventory, list) or any(
        not str(item or "").strip() for item in raw_inventory
    ):
        raise ClaudeSdkRuntimeError("approved skill manifest has no model tool inventory")
    manifest_inventory = {str(item).strip() for item in raw_inventory}
    computed_digest = "sha256:" + hashlib.sha256(
        "\0".join(sorted(manifest_inventory)).encode("utf-8")
    ).hexdigest()
    if computed_digest != inventory_digest:
        raise ClaudeSdkRuntimeError("approved skill model tool inventory digest drifted")

    allowed_skills = string_list("allowed_skills")
    baseline_tools = string_list("baseline_tools")
    allowed_tools = string_list("allowed_tools")
    denied_capabilities = string_list("denied_capabilities")
    denied_tools = string_list("denied_tools")
    denied_write_paths = string_list("denied_write_paths")
    raw_skills = manifest.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise ClaudeSdkRuntimeError("approved skill manifest has no skills list")
    manifest_skills: set[str] = set()
    expected_allowed_skills: set[str] = set()
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, Mapping):
            raise ClaudeSdkRuntimeError("approved skill manifest contains an invalid skill")
        name = str(raw_skill.get("name") or "").strip()
        raw_modes = raw_skill.get("allowed_modes")
        if (
            not name
            or name in manifest_skills
            or not isinstance(raw_modes, list)
            or not raw_modes
            or any(
                not isinstance(skill_mode, str)
                or skill_mode not in SDK_ENTRYPOINT_MODES
                for skill_mode in raw_modes
            )
            or len(raw_modes) != len(set(raw_modes))
        ):
            raise ClaudeSdkRuntimeError(
                "approved skill manifest contains invalid per-skill allowed_modes"
            )
        manifest_skills.add(name)
        if normalized_mode in raw_modes:
            expected_allowed_skills.add(name)
    missing_allowed = expected_allowed_skills - set(allowed_skills)
    unexpected_allowed = set(allowed_skills) - expected_allowed_skills
    if missing_allowed or unexpected_allowed or not allowed_skills:
        raise ClaudeSdkRuntimeError(
            "SDK HANDOFF allowed_skills drifted from per-skill allowed_modes: "
            f"missing={sorted(missing_allowed)}, extra={sorted(unexpected_allowed)}"
        )
    unknown_capabilities = set(denied_capabilities) - {
        str(name or "").strip() for name in capability_tools
    }
    if unknown_capabilities:
        raise ClaudeSdkRuntimeError(
            "SDK HANDOFF denied_capabilities are unresolved: "
            + ", ".join(sorted(unknown_capabilities))
        )
    capability_tool_names: set[str] = set()
    for capability, raw_tools in capability_tools.items():
        if not str(capability or "").strip() or not isinstance(raw_tools, list):
            raise ClaudeSdkRuntimeError(
                "SDK HANDOFF capability_tools contains an invalid entry"
            )
        if any(not str(tool or "").strip() for tool in raw_tools):
            raise ClaudeSdkRuntimeError(
                f"SDK HANDOFF capability {capability!r} contains an invalid tool"
            )
        capability_tool_names.update(str(tool).strip() for tool in raw_tools)
    inventory = {
        str(definition.get("name") or "").strip()
        for definition in tool_definitions
        if str(definition.get("name") or "").strip()
    }
    allowed = set(allowed_tools)
    if not set(baseline_tools) <= allowed:
        raise ClaudeSdkRuntimeError(
            f"SDK HANDOFF {normalized_mode} baseline_tools escape allowed_tools"
        )
    unresolved_allowed = allowed - capability_tool_names
    absent_from_manifest_inventory = allowed - manifest_inventory
    missing_from_inventory = allowed - inventory
    forbidden_allowed = allowed & LEGACY_OR_DELEGATING_TOOLS
    if (
        unresolved_allowed
        or absent_from_manifest_inventory
        or missing_from_inventory
        or forbidden_allowed
    ):
        raise ClaudeSdkRuntimeError(
            "SDK HANDOFF allowed_tools are unresolved or forbidden: "
            + ", ".join(
                sorted(
                    unresolved_allowed
                    | absent_from_manifest_inventory
                    | missing_from_inventory
                    | forbidden_allowed
                )
            )
        )
    if not allowed:
        raise ClaudeSdkRuntimeError(
            f"SDK HANDOFF {normalized_mode}.allowed_tools may not be empty"
        )
    baseline_missing = SDK_RUNTIME_BASELINE_TOOLS - inventory
    if baseline_missing:
        raise ClaudeSdkRuntimeError(
            "SDK runtime baseline tools are missing: "
            + ", ".join(sorted(baseline_missing))
        )
    expected = allowed | set(SDK_RUNTIME_BASELINE_TOOLS)
    filtered = [
        dict(definition)
        for definition in tool_definitions
        if str(definition.get("name") or "").strip() in expected
    ]
    filtered_names = {
        str(definition.get("name") or "").strip() for definition in filtered
    }
    if filtered_names != expected:
        raise ClaudeSdkRuntimeError(
            f"SDK HANDOFF {normalized_mode} tool exposure does not equal its exact allowlist"
        )
    handoff_guidance = str(manifest.get("handoff_guidance") or "").strip()
    if not handoff_guidance:
        raise ClaudeSdkRuntimeError("approved skill manifest has no HANDOFF guidance")
    policy = SdkModeToolPolicy(
        mode=normalized_mode,
        allowed_skills=allowed_skills,
        baseline_tools=baseline_tools,
        allowed_tools=allowed_tools,
        denied_capabilities=denied_capabilities,
        denied_tools=denied_tools,
        denied_write_paths=denied_write_paths,
        handoff_guidance=handoff_guidance,
    )
    return filtered, policy


def build_primary_sdk_env(
    *,
    business: str | None,
    operator_user_id: str,
    invocation_id: str,
    max_total_cost_microusd: int,
    max_cost_microusd: int,
    model: str | None = None,
) -> dict[str, str]:
    """Build a key-free SDK environment backed by one Safebox capability.

    Root-scope turns are supported by the existing Safebox session-token rail;
    business turns are ownership-bound by that same rail.  No raw Anthropic,
    DeepSeek, or Claude credential is inherited.
    """

    from takyon_constants import get_takyon_home

    from . import safebox
    from .core import _claude_agent_model_aliases, _resolve_claude_agent_model

    owner = str(operator_user_id or "").strip()
    if not owner:
        raise ClaudeSdkRuntimeError("primary SDK turn requires operator_user_id")
    resolved_model = _resolve_claude_agent_model(model)
    broker_url = str(safebox.provider_proxy_base_url() or "").strip().rstrip("/")
    if not broker_url:
        raise ClaudeSdkRuntimeError("primary SDK turn requires the Safebox provider proxy")
    try:
        capability = str(
            safebox.mint_operator_session_token(
                str(business or "").strip() or None,
                owner,
                invocation_id=str(invocation_id),
                max_total_cost_microusd=int(max_total_cost_microusd),
                max_cost_microusd=int(max_cost_microusd),
            )
            or ""
        ).strip()
    except Exception as exc:  # fail closed; never fall back to a raw provider key
        raise ClaudeSdkRuntimeError(
            "Safebox refused the primary SDK operator.session capability"
        ) from exc
    if not capability:
        raise ClaudeSdkRuntimeError(
            "Safebox returned no primary SDK operator.session capability"
        )

    config_dir = Path(get_takyon_home()) / "claude-agent-sdk"
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Allowlist process mechanics instead of copying the operator environment.
    # In particular, database URLs, Safebox transport/authority tokens, and
    # unrelated provider credentials must never enter the model subprocess.
    env = {
        key: value
        for key in (
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "SHELL",
            "TERM",
            "TMPDIR",
            "TMP",
            "TEMP",
            "USER",
            "TAKYON_CLAUDE_CODE_EXECUTABLE",
        )
        if (value := str(os.environ.get(key) or "").strip())
    }
    env.update(
        {
            "ANTHROPIC_BASE_URL": broker_url,
            # This is a short-lived, scoped Safebox capability, not a raw key.
            "ANTHROPIC_API_KEY": capability,
            "CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-primary-agent",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            "CLAUDE_CONFIG_DIR": str(config_dir),
            **_claude_agent_model_aliases(resolved_model),
        }
    )
    return env


@dataclass(frozen=True)
class ToolBridgeScope:
    operator_user_id: str
    business: str = ""
    session_id: str = ""
    session_project_key: str = ""
    task_id: str = ""
    user_task: str = ""


@dataclass(frozen=True)
class SdkModeToolPolicy:
    mode: str
    allowed_skills: tuple[str, ...]
    baseline_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    denied_capabilities: tuple[str, ...]
    denied_tools: tuple[str, ...]
    denied_write_paths: tuple[str, ...]
    handoff_guidance: str


def _skill_resource_tool_definition() -> dict[str, Any]:
    return {
        "name": "skill_read_resource",
        "description": (
            "Read one UTF-8 reference file explicitly published for an approved skill. "
            "Both the skill name and the path relative to that skill are required."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["skill", "path"],
            "properties": {
                "skill": {"type": "string", "minLength": 1},
                "path": {"type": "string", "minLength": 1},
            },
        },
    }


def _build_skill_resource_reader(
    *,
    plugin_root: Path,
    manifest_path: Path,
    allowed_skills: Iterable[str],
) -> Callable[[Mapping[str, Any]], str]:
    """Build an exact active-mode manifest reader; no arbitrary skill read exists."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ClaudeSdkRuntimeError(
            "approved skill manifest is unreadable for resource enforcement"
        ) from exc
    raw_skills = manifest.get("skills") if isinstance(manifest, Mapping) else None
    if not isinstance(raw_skills, list):
        raise ClaudeSdkRuntimeError("approved skill manifest has no skills list")
    active_skills = {
        str(name or "").strip()
        for name in allowed_skills
        if str(name or "").strip()
    }
    if not active_skills:
        raise ClaudeSdkRuntimeError(
            "approved skill resource reader requires active-mode skills"
        )
    manifest_skill_names: set[str] = set()
    approved: dict[str, tuple[str, frozenset[str]]] = {}
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, Mapping):
            raise ClaudeSdkRuntimeError("approved skill manifest has an invalid skill")
        name = str(raw_skill.get("name") or "").strip()
        plugin_path = _canonical_relative_path(
            raw_skill.get("plugin_path"), label=f"skill {name or '<empty>'} plugin_path"
        )
        raw_files = raw_skill.get("publish_files")
        if not name or not isinstance(raw_files, list) or not raw_files:
            raise ClaudeSdkRuntimeError(
                f"approved skill {name or '<empty>'} has no publish_files allowlist"
            )
        if name in manifest_skill_names:
            raise ClaudeSdkRuntimeError(
                f"approved skill manifest contains duplicate skill {name!r}"
            )
        manifest_skill_names.add(name)
        files: set[str] = set()
        for raw_file in raw_files:
            relative = _canonical_relative_path(
                raw_file, label=f"approved skill {name} resource"
            )
            # The manifest stores full paths relative to the plugin root.
            files.add(relative)
        if name in active_skills:
            approved[name] = (plugin_path, frozenset(files))
    unknown_active_skills = active_skills - manifest_skill_names
    if unknown_active_skills:
        raise ClaudeSdkRuntimeError(
            "active-mode skill resources drifted from the approved manifest: "
            + ", ".join(sorted(unknown_active_skills))
        )
    root = plugin_root.resolve(strict=True)

    def read_resource(args: Mapping[str, Any]) -> str:
        skill = str(args.get("skill") or "").strip()
        if skill not in approved:
            raise ClaudeSdkRuntimeError("skill resource is not manifest-approved")
        relative = _canonical_relative_path(
            args.get("path"), label="skill resource path"
        )
        plugin_path, allowed_files = approved[skill]
        published_relative = f"{plugin_path}/{relative}"
        if published_relative not in allowed_files:
            raise ClaudeSdkRuntimeError("skill resource is not published in the manifest")
        candidate = root.joinpath(*plugin_path.split("/"), *relative.split("/"))
        current = root
        for component in (*plugin_path.split("/"), *relative.split("/")):
            current = current / component
            try:
                info = current.lstat()
            except OSError as exc:
                raise ClaudeSdkRuntimeError("published skill resource is unavailable") from exc
            if stat.S_ISLNK(info.st_mode):
                raise ClaudeSdkRuntimeError("published skill resources may not be symlinks")
        try:
            candidate.resolve(strict=True).relative_to(root)
            info = candidate.stat()
        except (OSError, ValueError) as exc:
            raise ClaudeSdkRuntimeError("published skill resource escaped its plugin") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ClaudeSdkRuntimeError("published skill resource is not a regular file")
        if info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise ClaudeSdkRuntimeError("executable skill resources may not be read")
        if info.st_size > SDK_MAX_SKILL_RESOURCE_BYTES:
            raise ClaudeSdkRuntimeError("published skill resource exceeds its byte limit")
        try:
            data = candidate.read_bytes()
            if len(data) > SDK_MAX_SKILL_RESOURCE_BYTES:
                raise ClaudeSdkRuntimeError(
                    "published skill resource exceeds its byte limit"
                )
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ClaudeSdkRuntimeError(
                "published skill resource must be UTF-8 text"
            ) from exc

    return read_resource


class ScopedToolBridge:
    """Private parent-owned RPC bridge for in-process Takyon tool dispatch."""

    def __init__(
        self,
        *,
        tool_definitions: Sequence[Mapping[str, Any]],
        scope: ToolBridgeScope,
        on_tool_start: Callable[[str, str, Mapping[str, Any]], None] | None = None,
        on_tool_complete: Callable[[str, str, Mapping[str, Any], str], None] | None = None,
        dispatcher: Callable[..., str] | None = None,
        session_store: SessionStoreBackend | None = None,
        denied_write_paths: Sequence[str] = (),
        skill_resource_reader: Callable[[Mapping[str, Any]], str] | None = None,
    ) -> None:
        self.tool_definitions = [dict(item) for item in tool_definitions]
        self.scope = scope
        self.on_tool_start = on_tool_start
        self.on_tool_complete = on_tool_complete
        self._dispatcher = dispatcher
        self._session_store = session_store
        self._skill_resource_reader = skill_resource_reader
        self._denied_write_paths = tuple(
            sorted(
                {
                    _canonical_relative_path(path, label="denied write path")
                    for path in denied_write_paths
                }
            )
        )
        self._allowed = {
            str(item.get("name") or "").strip()
            for item in self.tool_definitions
            if str(item.get("name") or "").strip()
        }
        self._parent_socket, self._child_socket = socket.socketpair()
        self._child_socket.set_inheritable(True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._context = contextvars.copy_context()

    @property
    def child_fd(self) -> int:
        return int(self._child_socket.fileno())

    def start(self) -> "ScopedToolBridge":
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._serve,
                name=f"takyon-sdk-tool-bridge-{self.scope.session_id or 'turn'}",
                daemon=True,
            )
            self._thread.start()
        return self

    def close_child_in_parent(self) -> None:
        try:
            self._child_socket.close()
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        for candidate in (self._parent_socket, self._child_socket):
            try:
                candidate.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                candidate.close()
            except OSError:
                pass
        # A synchronous guarded tool may still be committing a durable side
        # effect when the model subprocess is cancelled. Python cannot safely
        # kill that thread. Pin the parent job claim by refusing to return until
        # the exact tool dispatch exits; jobs.run_one continues heartbeating the
        # handler during this join, so no retry can overlap the old side effect.
        while self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def __enter__(self) -> "ScopedToolBridge":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _serve(self) -> None:
        try:
            reader = self._parent_socket.makefile("r", encoding="utf-8")
            writer = self._parent_socket.makefile("w", encoding="utf-8")
            with reader, writer:
                while not self._stop.is_set():
                    line = reader.readline()
                    if not line:
                        break
                    try:
                        request = json.loads(line)
                        if not isinstance(request, Mapping):
                            raise ValueError("tool bridge request must be an object")
                        response = self._context.copy().run(
                            self._dispatch_request, dict(request)
                        )
                    except Exception as exc:  # return a model-visible error; keep bridge alive
                        request_id = ""
                        try:
                            request_id = str(request.get("id") or "")  # type: ignore[union-attr]
                        except Exception:
                            pass
                        response = {
                            "id": request_id,
                            "ok": False,
                            "error": str(exc),
                        }
                    writer.write(json.dumps(response, ensure_ascii=False) + "\n")
                    writer.flush()
        except OSError:
            return

    def _dispatch_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(request.get("id") or "").strip()
        request_type = str(request.get("type") or "").strip()
        if request_type in {
            "session_append",
            "session_load",
            "session_list_subkeys",
            "session_delete",
        }:
            return self._dispatch_session_request(request_id, request_type, request)
        if request_type != "tool":
            raise ClaudeSdkRuntimeError("unsupported SDK bridge request type")
        name = str(request.get("name") or "").strip()
        if not name or name not in self._allowed:
            raise ClaudeSdkRuntimeError(f"SDK tool is not allowed in this mode: {name or '<empty>'}")
        raw_args = request.get("args")
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        if name.startswith("business_") and self.scope.business:
            requested_business = str(args.get("business") or "").strip()
            if requested_business and requested_business != self.scope.business:
                raise ClaudeSdkRuntimeError(
                    f"cross-business SDK tool call refused: {requested_business!r}"
                )
            args["business"] = self.scope.business
        if name in {"business_write_file", "business_patch_file"}:
            requested_path = _canonical_relative_path(
                args.get("path"), label="business write path"
            )
            if any(
                requested_path == prefix
                or requested_path.startswith(prefix + "/")
                for prefix in self._denied_write_paths
            ):
                raise ClaudeSdkRuntimeError(
                    f"SDK mode policy refuses write path: {requested_path!r}"
                )
        tool_use_id = str(request.get("toolUseId") or request_id).strip()
        if callable(self.on_tool_start):
            self.on_tool_start(tool_use_id, name, args)

        if name == "skill_read_resource":
            if self._skill_resource_reader is None:
                raise ClaudeSdkRuntimeError(
                    "approved skill resource reader is not configured"
                )
            text = self._skill_resource_reader(args)
            if callable(self.on_tool_complete):
                self.on_tool_complete(tool_use_id, name, args, text)
            return {"id": request_id, "ok": True, "result": text}

        dispatcher = self._dispatcher
        if dispatcher is None:
            from model_tools import handle_function_call

            dispatcher = handle_function_call
        result = dispatcher(
            name,
            args,
            task_id=self.scope.task_id or None,
            tool_call_id=tool_use_id or None,
            session_id=self.scope.session_id or None,
            user_task=self.scope.user_task or None,
            enabled_tools=sorted(self._allowed),
        )
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        if callable(self.on_tool_complete):
            self.on_tool_complete(tool_use_id, name, args, text)
        return {"id": request_id, "ok": True, "result": text}

    def _session_key(
        self, raw_key: object, *, allow_subpath: bool = True
    ) -> dict[str, str]:
        if not isinstance(raw_key, Mapping):
            raise ClaudeSdkRuntimeError("SDK SessionStore key must be an object")
        expected_project = str(self.scope.session_project_key or "").strip()
        expected_session = str(self.scope.session_id or "").strip()
        project_key = str(raw_key.get("projectKey") or "").strip()
        session_id = str(raw_key.get("sessionId") or "").strip()
        raw_subpath = str(raw_key.get("subpath") or "").strip()
        subpath = (
            _canonical_relative_path(raw_subpath, label="SDK SessionStore subpath")
            if raw_subpath
            else ""
        )
        if not expected_project or project_key != expected_project:
            raise ClaudeSdkRuntimeError("cross-project SDK SessionStore access refused")
        if not expected_session or session_id != expected_session:
            raise ClaudeSdkRuntimeError("cross-session SDK SessionStore access refused")
        if subpath and not allow_subpath:
            raise ClaudeSdkRuntimeError("SDK SessionStore list key may not have a subpath")
        return {
            "projectKey": project_key,
            "sessionId": session_id,
            **({"subpath": subpath} if subpath else {}),
        }

    @staticmethod
    def _session_entries(raw_entries: object) -> list[dict[str, Any]]:
        if not isinstance(raw_entries, list) or len(raw_entries) > 2000:
            raise ClaudeSdkRuntimeError(
                "SDK SessionStore append entries must be a bounded array"
            )
        entries: list[dict[str, Any]] = []
        seen_batch_uuids: set[str] = set()
        for index, raw_entry in enumerate(raw_entries):
            if not isinstance(raw_entry, Mapping):
                raise ClaudeSdkRuntimeError(
                    f"SDK SessionStore entry {index} must be an object"
                )
            entry = copy.deepcopy(dict(raw_entry))
            if not str(entry.get("type") or "").strip():
                raise ClaudeSdkRuntimeError(
                    f"SDK SessionStore entry {index} has no type"
                )
            entry_uuid = str(entry.get("uuid") or "").strip()
            if entry_uuid and entry_uuid in seen_batch_uuids:
                continue
            if entry_uuid:
                seen_batch_uuids.add(entry_uuid)
            # Prove the payload remains plain JSON before handing it to a
            # durable adapter.  No pickle or provider-specific object crosses.
            json.dumps(entry, ensure_ascii=False)
            entries.append(entry)
        return entries

    def _dispatch_session_request(
        self,
        request_id: str,
        request_type: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        store = self._session_store
        if store is None:
            raise ClaudeSdkRuntimeError(
                "primary SDK SessionStore backend is not configured"
            )
        key = self._session_key(
            request.get("key"),
            allow_subpath=request_type not in {"session_list_subkeys", "session_delete"},
        )
        if request_type == "session_append":
            store.append(key, self._session_entries(request.get("entries")))
            result: object = {"appended": True}
        elif request_type == "session_load":
            loaded = store.load(key)
            if loaded is not None:
                loaded = self._session_entries(loaded)
            result = loaded
        elif request_type == "session_list_subkeys":
            subkeys = store.list_subkeys(key)
            if not isinstance(subkeys, list):
                raise ClaudeSdkRuntimeError(
                    "primary SDK SessionStore list_subkeys returned invalid data"
                )
            result = sorted(
                {
                    str(subpath or "").strip()
                    for subpath in subkeys
                    if str(subpath or "").strip()
                }
            )
        else:
            store.delete(key)
            result = {"deleted": True}
        return {"id": request_id, "ok": True, "result": result}


def primary_sdk_session_project_key(
    *, operator_user_id: str, business: str | None
) -> str:
    """Stable tenant key independent of the Mac/VPS workspace path."""

    owner = str(operator_user_id or "").strip()
    if not owner:
        raise ClaudeSdkRuntimeError(
            "primary SDK SessionStore requires operator_user_id"
        )
    slug = str(business or "").strip() or "operator"
    return f"takyon:operator:{owner}:business:{slug}"


def _primary_sdk_install_paths() -> tuple[Path, Path, Path]:
    runtime_root = Path(__file__).resolve().parents[2]
    entrypoint = runtime_root / "scripts" / "takyon-claude-primary-entrypoint.mjs"
    configured_plugin = str(
        os.environ.get("TAKYON_CLAUDE_SKILLS_PLUGIN") or ""
    ).strip()
    if not configured_plugin:
        raise ClaudeSdkRuntimeError(
            "TAKYON_CLAUDE_SKILLS_PLUGIN must point to an immutable published plugin"
        )
    plugin = Path(configured_plugin).expanduser().resolve()
    configured_manifest = str(
        os.environ.get("TAKYON_CLAUDE_SKILLS_MANIFEST") or ""
    ).strip()
    manifest = (
        Path(configured_manifest).expanduser().resolve()
        if configured_manifest
        else plugin / "approved-skills.json"
    )
    for label, candidate in (
        ("primary SDK entrypoint", entrypoint),
        ("published skill plugin", plugin),
        ("approved skill manifest", manifest),
    ):
        if not candidate.exists():
            raise ClaudeSdkRuntimeError(f"{label} does not exist: {candidate}")
    return entrypoint, plugin, manifest


def _regular_locked_module(path: Path, *, label: str, runtime_root: Path) -> Path:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        resolved.relative_to(runtime_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ClaudeSdkRuntimeError(f"{label} is not contained in the Node runtime") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ClaudeSdkRuntimeError(f"{label} must be a regular non-symlink file")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ClaudeSdkRuntimeError(f"{label} may not be group/world writable")
    return resolved


def _package_version(package_json: Path, *, label: str, expected: str) -> None:
    try:
        value = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ClaudeSdkRuntimeError(f"{label} package metadata is unreadable") from exc
    actual = str(value.get("version") or "") if isinstance(value, Mapping) else ""
    if actual != expected:
        raise ClaudeSdkRuntimeError(
            f"{label} version must be exactly {expected}; found {actual or '<missing>'}"
        )


def _primary_sdk_node_runtime(*, child_path: str) -> tuple[str, Path, Path]:
    """Resolve and verify the sealed production Node dependency runtime."""

    configured = str(os.environ.get("TAKYON_CLAUDE_NODE_RUNTIME") or "").strip()
    if configured:
        runtime_root = Path(configured).expanduser().resolve()
    else:
        allow_dev = str(
            os.environ.get("TAKYON_CLAUDE_SDK_ALLOW_REPO_NODE_MODULES") or ""
        ).strip() == "1"
        if not allow_dev or str(os.environ.get("TAKYON_ENV") or "").lower() in {
            "prod",
            "production",
        }:
            raise ClaudeSdkRuntimeError(
                "TAKYON_CLAUDE_NODE_RUNTIME is required; repo node_modules need the explicit dev fallback"
            )
        runtime_root = Path(__file__).resolve().parents[2]
    if not runtime_root.is_dir():
        raise ClaudeSdkRuntimeError(
            f"primary SDK Node runtime does not exist: {runtime_root}"
        )
    sdk_package = runtime_root / "node_modules/@anthropic-ai/claude-agent-sdk"
    zod_package = runtime_root / "node_modules/zod"
    sdk_module = _regular_locked_module(
        sdk_package / "sdk.mjs",
        label="Claude Agent SDK module",
        runtime_root=runtime_root,
    )
    zod_module = _regular_locked_module(
        zod_package / "index.js",
        label="Zod module",
        runtime_root=runtime_root,
    )
    _package_version(
        sdk_package / "package.json",
        label="Claude Agent SDK",
        expected=SDK_PACKAGE_VERSION,
    )
    _package_version(
        zod_package / "package.json",
        label="Zod",
        expected=SDK_ZOD_VERSION,
    )
    for env_name, expected_path in (
        ("TAKYON_CLAUDE_AGENT_SDK_MODULE", sdk_module),
        ("TAKYON_CLAUDE_ZOD_MODULE", zod_module),
    ):
        supplied = str(os.environ.get(env_name) or "").strip()
        if supplied and Path(supplied).expanduser().resolve() != expected_path:
            raise ClaudeSdkRuntimeError(f"{env_name} conflicts with the sealed Node runtime")
    node = str(os.environ.get("TAKYON_NODE_EXECUTABLE") or "").strip()
    if not node:
        node = str(shutil.which("node", path=child_path) or "")
    if not node:
        raise ClaudeSdkRuntimeError("primary SDK requires Node.js >=20")
    try:
        completed = subprocess.run(
            [node, "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0,
        )
        match = re.fullmatch(r"v(\d+)(?:\.\d+){2}\s*", completed.stdout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaudeSdkRuntimeError("primary SDK Node.js version check failed") from exc
    if match is None or int(match.group(1)) < 20:
        raise ClaudeSdkRuntimeError("primary SDK requires Node.js >=20")
    return node, sdk_module, zod_module


def _strict_json_document(text: str) -> Mapping[str, Any]:
    decoder = json.JSONDecoder()
    source = str(text or "").strip()
    if not source:
        raise ClaudeSdkRuntimeError("primary SDK subprocess returned no receipt")
    try:
        value, offset = decoder.raw_decode(source)
    except json.JSONDecodeError as exc:
        raise ClaudeSdkRuntimeError(
            "primary SDK subprocess returned invalid JSON"
        ) from exc
    if source[offset:].strip():
        raise ClaudeSdkRuntimeError(
            "primary SDK subprocess returned trailing stdout data"
        )
    if not isinstance(value, Mapping):
        raise ClaudeSdkRuntimeError(
            "primary SDK subprocess receipt must be an object"
        )
    return value


def _redact_sdk_text(value: object, secrets: Iterable[str]) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _terminate_process_group(
    process: subprocess.Popen[str], *, grace_seconds: float = 10.0
) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=max(0.1, float(grace_seconds)))
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    process.wait(timeout=5.0)


def run_primary_sdk_subprocess(
    *,
    business: str,
    operator_user_id: str,
    system_prompt: str,
    user_prompt: str,
    enabled_toolsets: Sequence[str],
    disabled_toolsets: Sequence[str] = (),
    excluded_tools: Iterable[str] = (),
    invocation_allowed_tools: Iterable[str] | None = None,
    workspace_root: str | os.PathLike[str],
    session_id: str,
    resume_session: bool,
    session_store: SessionStoreBackend,
    task_id: str = "",
    mode: str,
    epoch: str,
    operation: str = "turn",
    max_turns: int,
    max_budget_usd: float,
    effort: str = "high",
    inactivity_limit: float = 0.0,
    stop_probe: Callable[[float, float], str | None] | None = None,
    active_work_probe: Callable[[], bool] | None = None,
    steer_probe: Callable[[], Sequence[str] | str | None] | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    on_tool_start: Callable[[str, str, Mapping[str, Any]], None] | None = None,
    on_tool_complete: Callable[[str, str, Mapping[str, Any], str], None]
    | None = None,
) -> dict[str, Any]:
    """Run one primary SDK turn behind a private parent-owned bridge.

    There is no implicit fallback. Any missing immutable plugin, durable
    SessionStore, broker capability, malformed receipt, timeout, or process
    failure raises. The caller may explicitly select the separate Hermes
    canary path before entering this function.
    """

    if session_store is None:
        raise ClaudeSdkRuntimeError(
            "primary SDK requires a cross-host durable SessionStore backend"
        )
    runtime_operation = str(operation or "turn").strip().lower()
    if runtime_operation not in {"turn", "compact"}:
        raise ClaudeSdkRuntimeError(
            f"unsupported primary SDK operation: {runtime_operation!r}"
        )
    if runtime_operation == "compact" and not resume_session:
        raise ClaudeSdkRuntimeError(
            "primary SDK manual compaction requires an exact resumed session"
        )
    if runtime_operation == "compact" and not re.fullmatch(
        r"/compact(?: [^\r\n\x00]{1,500})?", str(user_prompt or "").strip()
    ):
        raise ClaudeSdkRuntimeError(
            "primary SDK manual compaction prompt must be canonical /compact [focus]"
        )
    stable_session = stable_sdk_session_id(session_id)
    invocation_id = stable_sdk_invocation_id(
        session_id=stable_session,
        epoch=epoch,
    )
    try:
        total_ceiling_microusd = int(
            (Decimal(str(max_budget_usd)) * Decimal(1_000_000)).quantize(
                Decimal("1"), rounding=ROUND_CEILING
            )
        )
        configured_per_call = Decimal(
            str(os.environ.get("TAKYON_PRIMARY_AGENT_PER_CALL_MAX_BUDGET_USD") or "0")
        )
    except (InvalidOperation, ValueError) as exc:
        raise ClaudeSdkRuntimeError("primary SDK budget is invalid") from exc
    if total_ceiling_microusd <= 0:
        raise ClaudeSdkRuntimeError("primary SDK budget must be positive")
    per_call_ceiling_microusd = (
        int(
            (configured_per_call * Decimal(1_000_000)).quantize(
                Decimal("1"), rounding=ROUND_CEILING
            )
        )
        if configured_per_call > 0
        else total_ceiling_microusd
    )
    per_call_ceiling_microusd = min(
        total_ceiling_microusd, per_call_ceiling_microusd
    )
    project_key = primary_sdk_session_project_key(
        operator_user_id=operator_user_id,
        business=business,
    )
    workspace = Path(workspace_root).expanduser().resolve()
    if not workspace.is_dir():
        raise ClaudeSdkRuntimeError(
            f"primary SDK workspace does not exist: {workspace}"
        )
    entrypoint, plugin, manifest = _primary_sdk_install_paths()
    unfiltered_tool_definitions = sdk_tool_definitions(
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        excluded_tools=excluded_tools,
    )
    unfiltered_tool_definitions.append(_skill_resource_tool_definition())
    tool_definitions, mode_policy = enforce_sdk_mode_tool_policy(
        manifest_path=manifest,
        mode=mode,
        tool_definitions=unfiltered_tool_definitions,
    )
    if invocation_allowed_tools is not None:
        phase_allowed = {
            str(name or "").strip()
            for name in invocation_allowed_tools
            if str(name or "").strip()
        }
        mode_allowed = {
            str(definition.get("name") or "").strip()
            for definition in tool_definitions
        }
        escaped = phase_allowed - mode_allowed
        if escaped:
            raise ClaudeSdkRuntimeError(
                "phase tool allowlist escapes the HANDOFF mode: "
                + ", ".join(sorted(escaped))
            )
        if not phase_allowed:
            raise ClaudeSdkRuntimeError("phase tool allowlist may not be empty")
        tool_definitions = [
            definition
            for definition in tool_definitions
            if str(definition.get("name") or "").strip() in phase_allowed
        ]
    if runtime_operation == "compact":
        tool_definitions = []
    if not tool_definitions and runtime_operation != "compact":
        raise ClaudeSdkRuntimeError(
            "primary SDK invocation resolved no scoped Takyon tools"
        )
    skill_resource_reader = _build_skill_resource_reader(
        plugin_root=plugin,
        manifest_path=manifest,
        allowed_skills=mode_policy.allowed_skills,
    )
    env = build_primary_sdk_env(
        business=business,
        operator_user_id=operator_user_id,
        invocation_id=invocation_id,
        max_total_cost_microusd=total_ceiling_microusd,
        max_cost_microusd=per_call_ceiling_microusd,
    )
    node, sdk_module, zod_module = _primary_sdk_node_runtime(
        child_path=str(env.get("PATH") or "")
    )
    env["TAKYON_CLAUDE_AGENT_SDK_MODULE"] = str(sdk_module)
    env["TAKYON_CLAUDE_ZOD_MODULE"] = str(zod_module)
    request: dict[str, Any] = {
        "prompt": str(user_prompt or ""),
        "systemPrompt": (
            str(system_prompt or "").rstrip()
            + "\n\n"
            + mode_policy.handoff_guidance
            + "\n"
            + SDK_SKILL_RESOURCE_GUIDANCE
            + "\nActive HANDOFF mode: "
            + mode_policy.mode
            + ". Skills approved for this mode: "
            + ", ".join(mode_policy.allowed_skills)
            + "."
        ),
        "cwd": str(workspace),
        "workspaceRoot": str(workspace),
        "configDir": env["CLAUDE_CONFIG_DIR"],
        "pluginPath": str(plugin),
        "manifestPath": str(manifest),
        # The parent policy uses the agnostic HANDOFF modes while the private
        # subprocess boundary names the exact Takyon invocation kind.  Derive
        # the wire value from the already validated policy instead of trusting
        # a second caller-controlled spelling.
        "mode": SDK_ENTRYPOINT_MODES[mode_policy.mode],
        "operation": runtime_operation,
        "epoch": str(epoch or mode or ""),
        "sessionId": "" if resume_session else stable_session,
        "resumeSessionId": stable_session if resume_session else "",
        "sessionProjectKey": project_key,
        "maxTurns": int(max_turns),
        "maxBudgetUsd": float(max_budget_usd),
        "effort": str(effort or "high"),
        "toolDefinitions": tool_definitions,
        "pathToClaudeCodeExecutable": str(
            env.get("TAKYON_CLAUDE_CODE_EXECUTABLE") or ""
        ),
    }
    scope = ToolBridgeScope(
        operator_user_id=str(operator_user_id),
        business=str(business),
        session_id=stable_session,
        session_project_key=project_key,
        task_id=str(task_id or ""),
        user_task=str(user_prompt or ""),
    )
    activity_lock = threading.Lock()
    last_activity = time.monotonic()

    def touch_activity() -> None:
        nonlocal last_activity
        with activity_lock:
            last_activity = time.monotonic()

    def tool_started(
        tool_use_id: str, name: str, args: Mapping[str, Any]
    ) -> None:
        touch_activity()
        if callable(on_tool_start):
            on_tool_start(tool_use_id, name, args)

    def tool_completed(
        tool_use_id: str, name: str, args: Mapping[str, Any], result: str
    ) -> None:
        touch_activity()
        if callable(on_tool_complete):
            on_tool_complete(tool_use_id, name, args, result)

    tool_bridge = ScopedToolBridge(
        tool_definitions=tool_definitions,
        scope=scope,
        on_tool_start=tool_started,
        on_tool_complete=tool_completed,
        denied_write_paths=mode_policy.denied_write_paths,
        skill_resource_reader=skill_resource_reader,
    )
    try:
        session_bridge = ScopedToolBridge(
            tool_definitions=[],
            scope=scope,
            session_store=session_store,
        )
    except BaseException:
        tool_bridge.close()
        raise
    try:
        tool_bridge.start()
        session_bridge.start()
    except BaseException:
        session_bridge.close()
        tool_bridge.close()
        raise
    if tool_bridge.child_fd == session_bridge.child_fd:
        session_bridge.close()
        tool_bridge.close()
        raise ClaudeSdkRuntimeError(
            "tool and SessionStore bridges must use distinct descriptors"
        )
    env[SDK_TOOL_BRIDGE_FD_ENV] = str(tool_bridge.child_fd)
    env[SDK_SESSION_BRIDGE_FD_ENV] = str(session_bridge.child_fd)
    process: subprocess.Popen[str] | None = None
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    output_overflow = threading.Event()
    redaction_secrets = (env.get("ANTHROPIC_API_KEY", ""),)

    def append_bounded(
        target: list[str], value: str, limit: int
    ) -> None:
        current = sum(len(part.encode("utf-8", "replace")) for part in target)
        encoded = value.encode("utf-8", "replace")
        if current + len(encoded) > limit:
            output_overflow.set()
            return
        target.append(value)

    try:
        process = subprocess.Popen(
            [node, str(entrypoint)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            pass_fds=(tool_bridge.child_fd, session_bridge.child_fd),
            start_new_session=True,
        )
        tool_bridge.close_child_in_parent()
        session_bridge.close_child_in_parent()
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ClaudeSdkRuntimeError(
                "primary SDK subprocess pipes were not created"
            )
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()

        stdout_context = contextvars.copy_context()
        stderr_context = contextvars.copy_context()

        def read_stdout() -> None:
            assert process is not None and process.stdout is not None
            for chunk in iter(lambda: process.stdout.read(65536), ""):
                append_bounded(stdout_parts, chunk, SDK_MAX_STDOUT_BYTES)

        def read_stderr() -> None:
            assert process is not None and process.stderr is not None
            for line in process.stderr:
                safe_line = _redact_sdk_text(line.rstrip("\n"), redaction_secrets)
                if safe_line.startswith(SDK_PROGRESS_PREFIX):
                    raw_event = safe_line[len(SDK_PROGRESS_PREFIX) :]
                    try:
                        event = json.loads(raw_event)
                    except json.JSONDecodeError:
                        append_bounded(
                            stderr_parts,
                            "invalid prefixed SDK progress event\n",
                            SDK_MAX_STDERR_BYTES,
                        )
                        continue
                    touch_activity()
                    if isinstance(event, Mapping) and callable(progress_callback):
                        progress_callback(dict(event))
                    continue
                append_bounded(
                    stderr_parts,
                    safe_line + "\n",
                    SDK_MAX_STDERR_BYTES,
                )

        stdout_thread = threading.Thread(
            target=lambda: stdout_context.run(read_stdout),
            name="takyon-primary-sdk-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=lambda: stderr_context.run(read_stderr),
            name="takyon-primary-sdk-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        started = time.monotonic()
        stopped_reason = ""
        inactivity_timeout = False
        while process.poll() is None:
            if output_overflow.is_set():
                stopped_reason = "primary SDK subprocess output exceeded its bounded limit"
                break
            now = time.monotonic()
            with activity_lock:
                idle = max(0.0, now - last_activity)
            try:
                active_work = bool(
                    callable(active_work_probe) and active_work_probe()
                )
            except Exception as exc:
                stopped_reason = (
                    f"primary SDK active-work proof failed: {exc}"
                )
                break
            if (
                inactivity_limit
                and inactivity_limit > 0
                and idle >= inactivity_limit
                and not active_work
            ):
                stopped_reason = (
                    f"primary SDK turn idle past {int(inactivity_limit)}s inactivity limit"
                )
                inactivity_timeout = True
                break
            if callable(stop_probe):
                stopped_reason = str(stop_probe(now - started, idle) or "").strip()
                if stopped_reason:
                    break
            if callable(steer_probe):
                try:
                    pending_steers = steer_probe()
                except Exception as exc:
                    stopped_reason = f"primary SDK steer source failed: {exc}"
                    break
                if isinstance(pending_steers, str):
                    pending_steers = [pending_steers]
                for steer_text in pending_steers or ():
                    clean_steer = str(steer_text or "")
                    if not clean_steer.strip():
                        continue
                    if len(clean_steer.encode("utf-8")) > 32 * 1024:
                        stopped_reason = "primary SDK steer payload exceeded 32768 bytes"
                        break
                    try:
                        process.stdin.write(
                            json.dumps(
                                {"type": "steer", "text": clean_steer},
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        process.stdin.flush()
                        touch_activity()
                    except (BrokenPipeError, OSError) as exc:
                        stopped_reason = f"primary SDK steer channel failed: {exc}"
                        break
                if stopped_reason:
                    break
            time.sleep(0.25)
        if stopped_reason:
            _terminate_process_group(process)
        else:
            process.wait()
        stdout_thread.join(timeout=5.0)
        stderr_thread.join(timeout=5.0)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            _terminate_process_group(process, grace_seconds=1.0)
            raise ClaudeSdkRuntimeError(
                "primary SDK subprocess output readers did not terminate"
            )
        if stopped_reason:
            raise ClaudeSdkProcessStopped(
                stopped_reason, inactivity_timeout=inactivity_timeout
            )
        receipt = _strict_json_document("".join(stdout_parts))
        if receipt.get("ok") is not True:
            error = receipt.get("error")
            error_message = (
                str(error.get("message") or "")
                if isinstance(error, Mapping)
                else ""
            )
            diagnostic = "".join(stderr_parts).strip()
            detail = error_message or diagnostic or (
                f"primary SDK subprocess exited {process.returncode}"
            )
            raise ClaudeSdkRuntimeError(
                _redact_sdk_text(detail, redaction_secrets)
            )
        if process.returncode != 0:
            raise ClaudeSdkRuntimeError(
                f"primary SDK subprocess exited {process.returncode} despite a success receipt"
            )
        result = receipt.get("result")
        if not isinstance(result, Mapping):
            raise ClaudeSdkRuntimeError(
                "primary SDK success receipt omitted its result"
            )
        returned_session = str(result.get("session_id") or "").strip()
        if returned_session != stable_session:
            raise ClaudeSdkRuntimeError(
                "primary SDK success receipt returned the wrong session"
            )
        if str(result.get("operation") or "").strip() != runtime_operation:
            raise ClaudeSdkRuntimeError(
                "primary SDK success receipt returned the wrong operation"
            )
        if runtime_operation == "compact":
            compact_receipt = result.get("compact_receipt")
            if not isinstance(compact_receipt, Mapping):
                raise ClaudeSdkRuntimeError(
                    "primary SDK manual compaction omitted its boundary receipt"
                )
            if str(compact_receipt.get("trigger") or "") != "manual":
                raise ClaudeSdkRuntimeError(
                    "primary SDK manual compaction returned the wrong trigger"
                )
            try:
                pre_tokens = int(compact_receipt.get("pre_tokens"))
                post_tokens_raw = compact_receipt.get("post_tokens")
                post_tokens = (
                    None if post_tokens_raw is None else int(post_tokens_raw)
                )
            except (TypeError, ValueError) as exc:
                raise ClaudeSdkRuntimeError(
                    "primary SDK manual compaction returned invalid token counts"
                ) from exc
            if pre_tokens < 0 or (post_tokens is not None and post_tokens < 0):
                raise ClaudeSdkRuntimeError(
                    "primary SDK manual compaction returned invalid token counts"
                )
        returned = dict(result)
        returned["invocation_id"] = invocation_id
        returned["invocation_total_ceiling_microusd"] = total_ceiling_microusd
        returned["invocation_per_call_ceiling_microusd"] = (
            per_call_ceiling_microusd
        )
        return returned
    except BaseException:
        # KeyboardInterrupt/SystemExit bypass Exception.  The SDK child owns a
        # detached process group, so it must be terminated before the private
        # tool bridge is closed or the model can outlive its authority channel.
        if process is not None and process.poll() is None:
            _terminate_process_group(process)
        raise
    finally:
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        session_bridge.close()
        tool_bridge.close()


__all__ = [
    "ClaudeSdkProcessStopped",
    "ClaudeSdkRuntimeError",
    "InMemorySessionStoreBackend",
    "LEGACY_OR_DELEGATING_TOOLS",
    "SDK_GLOBAL_OPERATOR_TOOLS",
    "SDK_PROGRESS_PREFIX",
    "SDK_SESSION_BRIDGE_FD_ENV",
    "SDK_TOOL_BRIDGE_FD_ENV",
    "SdkModeToolPolicy",
    "ScopedToolBridge",
    "SessionStoreBackend",
    "ToolBridgeScope",
    "build_primary_sdk_env",
    "enforce_sdk_mode_tool_policy",
    "primary_sdk_session_project_key",
    "run_primary_sdk_subprocess",
    "sdk_tool_definitions",
    "stable_sdk_session_id",
    "stable_sdk_invocation_id",
]
