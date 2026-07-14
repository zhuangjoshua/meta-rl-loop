"""
Cron job scheduler - executes due jobs.

Provides tick() which checks for due jobs and runs them. The gateway
calls this every 60 seconds from a background thread.

Uses a file-based lock (~/.takyon/cron/.tick.lock) so only one tick
runs at a time if multiple processes overlap.
"""

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext

# fcntl is Unix-only; on Windows use msvcrt for file locking
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for imports BEFORE repo-level imports.
# Without this, standalone invocations (e.g. after `takyon update` reloads
# the module) fail with ModuleNotFoundError for takyon_time et al.
sys.path.insert(0, str(Path(__file__).parent.parent))

from takyon_constants import get_takyon_home
from takyon_cli._subprocess_compat import windows_hide_flags
from takyon_cli.config import load_config, _expand_env_vars
from takyon_time import now as _takyon_now

logger = logging.getLogger(__name__)


class CronPromptInjectionBlocked(Exception):
    """Raised by _build_job_prompt when the fully-assembled prompt trips the
    injection scanner. Caught in run_job so the operator sees a clean
    "job blocked" delivery instead of the scheduler crashing.

    Assembled-prompt scanning covers the user prompt plus script and prior-job
    context before either reaches the unattended SDK turn. Approved skill
    bodies are no longer copied into this prompt; the immutable published
    plugin and compiled SDK runtime policy own their loading and enforcement.
    """


class CronSkillPolicyBlocked(Exception):
    """An explicitly requested cron skill is absent from the approved wake policy."""


class CronSdkRoutingPolicyBlocked(RuntimeError):
    """A persisted cron routing assertion conflicts with the primary SDK policy."""


_CRON_PRIMARY_SDK_MODEL = "deepseek-v4-pro"
_CRON_PRIMARY_SDK_PROVIDER = "deepseek"
_CRON_SDK_ENABLED_TOOLSETS = (
    "takyon",
    "takyon-authority",
    "web",
    "skills",
    "todo",
)
_CRON_SDK_DISABLED_TOOLSETS = (
    "cronjob",
    "messaging",
    "clarify",
    "memory",
    "session_search",
    "terminal",
    "file",
    "browser",
    "code_execution",
)


def _load_approved_cron_manifest() -> dict:
    """Load the same approved manifest the SDK will enforce for this turn."""

    configured = str(os.getenv("TAKYON_CLAUDE_SKILLS_MANIFEST") or "").strip()
    manifest_path = (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[1] / "skills" / "approved-skills.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CronSkillPolicyBlocked(
            f"approved cron skill manifest is unreadable: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise CronSkillPolicyBlocked("approved cron skill manifest must be an object")
    plugin = manifest.get("plugin")
    policy = manifest.get("mode_tool_policy")
    wake = policy.get("wake") if isinstance(policy, dict) else None
    if (
        not isinstance(plugin, dict)
        or not str(plugin.get("name") or "").strip()
        or not isinstance(manifest.get("skills"), list)
        or not isinstance(wake, dict)
        or not isinstance(wake.get("allowed_skills"), list)
        or not isinstance(wake.get("allowed_tools"), list)
        or not isinstance(wake.get("required_tools"), list)
    ):
        raise CronSkillPolicyBlocked(
            "approved cron skill manifest omits the compiled wake SDK runtime policy"
        )
    return manifest


def _resolve_approved_cron_skills(skills: list[str]) -> list[str]:
    """Resolve legacy identifiers to native published skills allowed on wakes."""

    if not skills:
        return []
    manifest = _load_approved_cron_manifest()
    plugin_name = str((manifest.get("plugin") or {}).get("name") or "").strip()
    wake_policy = (manifest.get("mode_tool_policy") or {}).get("wake") or {}
    allowed = {
        str(value or "").strip()
        for value in wake_policy.get("allowed_skills", [])
        if str(value or "").strip()
    }
    index: dict[str, dict] = {}
    for item in manifest.get("skills", []):
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("name") or "").strip()
        if not canonical:
            continue
        identifiers = {
            canonical,
            Path(str(item.get("source_path") or "")).name,
            *(
                str(value or "").strip()
                for value in item.get("legacy_names", [])
                if str(value or "").strip()
            ),
        }
        for identifier in identifiers:
            index[identifier.lower()] = item

    resolved: list[str] = []
    for requested in skills:
        clean = str(requested or "").strip().lstrip("/")
        if clean.lower().startswith(plugin_name.lower() + ":"):
            clean = clean.split(":", 1)[1]
        item = index.get(clean.lower())
        canonical = str((item or {}).get("name") or "").strip()
        modes = {
            str(value or "").strip().lower()
            for value in (item or {}).get("allowed_modes", [])
        }
        if (
            not canonical
            or canonical not in allowed
            or "wake" not in modes
            or not str((item or {}).get("content_digest") or "").startswith("sha256:")
        ):
            raise CronSkillPolicyBlocked(
                f"cron skill {requested!r} is not approved for wake mode"
            )
        qualified = f"{plugin_name}:{canonical}"
        if qualified not in resolved:
            resolved.append(qualified)
    return resolved


def _resolve_cron_enabled_toolsets(job: dict, cfg: dict) -> list[str] | None:
    """Resolve the toolset list for a cron job.

    Precedence:
    1. Per-job ``enabled_toolsets`` (set via ``cronjob`` tool on create/update).
       Keeps the agent's job-scoped toolset override intact — #6130.
    2. Per-platform ``takyon tools`` config for the ``cron`` platform.
       Mirrors gateway behavior (``_get_platform_tools(cfg, platform_key)``)
       so users can gate cron toolsets globally without recreating every job.
    3. ``None`` on any lookup failure — the compiled wake SDK runtime policy is
       used without an additional legacy toolset restriction.

    _DEFAULT_OFF_TOOLSETS ({moa, homeassistant, rl}) are removed by
    ``_get_platform_tools`` for unconfigured platforms, so fresh installs
    get cron WITHOUT ``moa`` by default (issue reported by Norbert —
    surprise $4.63 run).
    """
    per_job = job.get("enabled_toolsets")
    if per_job:
        return per_job
    try:
        from takyon_cli.tools_config import _get_platform_tools  # lazy: avoid heavy import at cron module load
        return sorted(_get_platform_tools(cfg or {}, "cron"))
    except Exception as exc:
        logger.warning(
            "Cron toolset resolution failed, falling back to full default toolset: %s",
            exc,
        )
        return None


def _cron_sdk_invocation_allowed_tools(job: dict, cfg: dict) -> list[str] | None:
    """Translate a legacy cron toolset restriction under the wake SDK ceiling."""

    requested_toolsets = _resolve_cron_enabled_toolsets(job, cfg)
    if requested_toolsets is None:
        return None
    from plugins.takyon.claude_sdk_runtime import sdk_tool_definitions

    requested_names = {
        str(definition.get("name") or "").strip()
        for definition in sdk_tool_definitions(
            enabled_toolsets=requested_toolsets,
            disabled_toolsets=_CRON_SDK_DISABLED_TOOLSETS,
        )
        if str(definition.get("name") or "").strip()
    }
    manifest = _load_approved_cron_manifest()
    wake = (manifest.get("mode_tool_policy") or {}).get("wake") or {}
    allowed = {
        str(value or "").strip()
        for value in wake.get("allowed_tools", [])
        if str(value or "").strip()
    }
    required = {
        str(value or "").strip()
        for value in wake.get("required_tools", [])
        if str(value or "").strip()
    }
    # Required tools come from the higher-level SDK runtime policy and cannot be
    # removed by a legacy job record. The per-job list may only narrow the
    # optional portion of that policy.
    return sorted(required | (requested_names & allowed))


def _cron_sdk_budget_usd() -> float:
    """Use the explicit primary-agent ceiling; absence is a deployment error."""

    raw = str(os.getenv("TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD") or "").strip()
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0 or value > 100:
        raise RuntimeError(
            "Claude Agent SDK cron turns require "
            "TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD between 0 and 100"
        )
    return value


def _validate_cron_sdk_routing_policy(job: dict) -> None:
    """Treat legacy routing fields as assertions against the fixed SDK lane.

    Empty fields inherit the production policy.  Configured values may only
    restate that policy; they never override it or enable fallback routing.
    """

    conflicts: list[str] = []
    configured_model = str(job.get("model") or "").strip()
    if configured_model and configured_model != _CRON_PRIMARY_SDK_MODEL:
        conflicts.append(
            f"model={configured_model!r} conflicts with required "
            f"{_CRON_PRIMARY_SDK_MODEL!r}"
        )

    configured_provider = str(job.get("provider") or "").strip()
    if configured_provider and configured_provider != _CRON_PRIMARY_SDK_PROVIDER:
        conflicts.append(
            f"provider={configured_provider!r} conflicts with required "
            f"{_CRON_PRIMARY_SDK_PROVIDER!r}"
        )

    configured_base_url = str(job.get("base_url") or "").strip().rstrip("/")
    if configured_base_url:
        from plugins.takyon import safebox

        effective_base_url = str(
            safebox.provider_proxy_base_url() or ""
        ).strip().rstrip("/")
        if not effective_base_url:
            conflicts.append(
                "base_url is configured but the required Safebox provider proxy "
                "is unavailable"
            )
        elif configured_base_url != effective_base_url:
            conflicts.append(
                f"base_url={configured_base_url!r} conflicts with required "
                f"Safebox proxy {effective_base_url!r}"
            )

    if conflicts:
        raise CronSdkRoutingPolicyBlocked(
            "primary SDK routing policy blocked cron job: "
            + "; ".join(conflicts)
            + "; no model/provider fallback is permitted"
        )


def _cron_occurrence_identity(job: dict) -> str:
    """Return one stable identity for this scheduled occurrence/retry group."""

    occurrence = str(job.get("next_run_at") or "").strip()
    if occurrence:
        return occurrence
    occurrence = str(job.get("_sdk_occurrence_id") or "").strip()
    if not occurrence:
        occurrence = str(uuid.uuid4())
        # Direct/manual callers may retry the same in-memory occurrence even
        # when no schedule timestamp exists. Due jobs always carry their
        # persisted pre-advance next_run_at timestamp.
        job["_sdk_occurrence_id"] = occurrence
    return occurrence


def _cron_sdk_epoch(job: dict) -> str:
    """Stable spend envelope for one occurrence, unique across occurrences."""

    occurrence = _cron_occurrence_identity(job)
    return f"cron:{job.get('id') or 'unknown'}:{occurrence}"


def _cron_business_owner_user_id(business: str) -> str:
    """Resolve the authoritative owner from the durable business row."""

    from plugins.takyon.core import TakyonError, TakyonStore

    slug = str(business or "").strip()
    if not slug:
        raise TakyonError(
            "unscoped LLM cron job refused: attach an exact business before running"
        )
    store = TakyonStore()
    with store._connect() as conn:
        record = store._ensure_business(conn, slug)
    owner = str(record.get("owner_user_id") or "").strip()
    if not owner:
        raise TakyonError(f"business:{slug} has no authoritative owner_user_id")
    return owner


def _cron_operator_user_id(job: dict, business: str) -> str:
    if business:
        return _cron_business_owner_user_id(business)
    owner = str(job.get("operator_user_id") or "").strip()
    try:
        return str(uuid.UUID(owner))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(
            "root-scoped LLM cron job has no authenticated operator identity"
        ) from exc

# Valid delivery platforms — used to validate user-supplied platform names
# in cron delivery targets, preventing env var enumeration via crafted names.
_KNOWN_DELIVERY_PLATFORMS = frozenset({
    "telegram", "discord", "slack", "whatsapp", "signal",
    "matrix", "mattermost", "homeassistant", "dingtalk", "feishu",
    "wecom", "wecom_callback", "weixin", "sms", "email", "webhook", "bluebubbles",
    "qqbot", "yuanbao",
})

# Platforms that support a configured cron/notification home target, mapped to
# the environment variable used by gateway setup/runtime config.
_HOME_TARGET_ENV_VARS = {
    "matrix": "MATRIX_HOME_ROOM",
    "telegram": "TELEGRAM_HOME_CHANNEL",
    "discord": "DISCORD_HOME_CHANNEL",
    "slack": "SLACK_HOME_CHANNEL",
    "signal": "SIGNAL_HOME_CHANNEL",
    "mattermost": "MATTERMOST_HOME_CHANNEL",
    "sms": "SMS_HOME_CHANNEL",
    "email": "EMAIL_HOME_ADDRESS",
    "dingtalk": "DINGTALK_HOME_CHANNEL",
    "feishu": "FEISHU_HOME_CHANNEL",
    "wecom": "WECOM_HOME_CHANNEL",
    "weixin": "WEIXIN_HOME_CHANNEL",
    "bluebubbles": "BLUEBUBBLES_HOME_CHANNEL",
    "qqbot": "QQBOT_HOME_CHANNEL",
    "whatsapp": "WHATSAPP_HOME_CHANNEL",
}

# Legacy env var names kept for back-compat.  Each entry is the current
# primary env var → the previous name.  _get_home_target_chat_id falls
# back to the legacy name if the primary is unset, so users who set the
# old name before the rename keep working until they migrate.
_LEGACY_HOME_TARGET_ENV_VARS = {
    "QQBOT_HOME_CHANNEL": "QQ_HOME_CHANNEL",
}

from cron.jobs import get_due_jobs, mark_job_run, save_job_output, advance_next_run

# Sentinel: when a cron agent has nothing new to report, it can start its
# response with this marker to suppress delivery.  Output is still saved
# locally for audit.
SILENT_MARKER = "[SILENT]"

# Backward-compatible module override used by tests and emergency monkeypatches.
_takyon_home: Path | None = None


def _get_takyon_home() -> Path:
    """Resolve Takyon home dynamically while preserving test monkeypatch hooks."""
    return _takyon_home or get_takyon_home()


def _get_lock_paths() -> tuple[Path, Path]:
    """Resolve cron lock paths at call time so profile/env changes are honored."""
    takyon_home = _get_takyon_home()
    lock_dir = takyon_home / "cron"
    return lock_dir, lock_dir / ".tick.lock"


@contextmanager
def _job_profile_context(job_id: str, profile: Optional[str]):
    """Temporarily run a job under a specific Takyon profile.

    Cron jobs are stored and scheduled by the profile running the scheduler, but
    an individual job can opt into a different runtime profile. While active,
    the scheduler's test/override hook and a context-local Takyon home override
    both point at the resolved profile directory so _get_takyon_home(),
    .env/config loading, script resolution, SDK construction, and downstream
    get_takyon_home() callers agree on the same home.

    Some existing provider/config paths still load profile .env values through
    os.environ, so profile jobs also snapshot and restore the process
    environment on exit. tick() runs profile jobs sequentially to keep that
    temporary mutation isolated from other scheduled jobs.
    """
    raw_profile = str(profile or "").strip()
    if not raw_profile:
        yield None
        return

    global _takyon_home
    prior_override = _takyon_home
    env_snapshot = os.environ.copy()

    from takyon_cli.profiles import normalize_profile_name, resolve_profile_env
    from takyon_constants import reset_takyon_home_override, set_takyon_home_override

    normalized_profile = normalize_profile_name(raw_profile)
    try:
        profile_home = Path(resolve_profile_env(normalized_profile)).resolve()
    except (FileNotFoundError, ValueError) as exc:
        logger.warning(
            "Job '%s': configured profile %r no longer valid (%s) — "
            "falling back to scheduler default",
            job_id, raw_profile, exc,
        )
        yield None
        return

    override_token = None
    try:
        override_token = set_takyon_home_override(profile_home)
        _takyon_home = profile_home
        logger.info(
            "Job '%s': using Takyon profile '%s' (%s)",
            job_id,
            normalized_profile,
            profile_home,
        )
        yield normalized_profile
    finally:
        _takyon_home = prior_override
        if override_token is not None:
            reset_takyon_home_override(override_token)
        # Delta-based restore: remove added keys, restore changed keys.
        # Avoids a brief window where other threads see an empty env.
        added = set(os.environ.keys()) - set(env_snapshot.keys())
        for k in added:
            os.environ.pop(k, None)
        for k, v in env_snapshot.items():
            if os.environ.get(k) != v:
                os.environ[k] = v


def _resolve_origin(job: dict) -> Optional[dict]:
    """Extract origin info from a job, preserving any extra routing metadata.

    Treats non-dict origins (free-form provenance strings, ints, lists from
    migration scripts or hand-edited jobs.json) as missing instead of
    crashing with ``AttributeError`` on ``origin.get(...)``. Without this
    guard, a job tagged with e.g. ``"combined-digest-replaces-x-and-y"``
    crashed every fire attempt with
    ``'str' object has no attribute 'get'`` — ``mark_job_run`` recorded the
    failure, but the next tick re-loaded the same poisoned origin and
    crashed identically until the field was patched manually (#18722).
    """
    origin = job.get("origin")
    if not isinstance(origin, dict):
        return None
    platform = origin.get("platform")
    chat_id = origin.get("chat_id")
    if platform and chat_id:
        return origin
    return None


def _plugin_cron_env_var(platform_name: str) -> str:
    """Return the cron home-channel env var registered by a plugin platform.

    Falls through the platform registry so plugins that set
    ``cron_deliver_env_var`` on their ``PlatformEntry`` get cron delivery
    support without editing this module.
    """
    try:
        from takyon_cli.plugins import discover_plugins
        discover_plugins()  # idempotent
        from gateway.platform_registry import platform_registry
        entry = platform_registry.get(platform_name.lower())
        if entry and entry.cron_deliver_env_var:
            return entry.cron_deliver_env_var
    except Exception:
        pass
    return ""


def _is_known_delivery_platform(platform_name: str) -> bool:
    """Whether ``platform_name`` is a valid cron delivery target.

    Hardcoded built-ins in ``_KNOWN_DELIVERY_PLATFORMS`` are checked first;
    plugin platforms registered via ``PlatformEntry`` are accepted if they
    provide a ``cron_deliver_env_var``.
    """
    name = platform_name.lower()
    if name in _KNOWN_DELIVERY_PLATFORMS:
        return True
    return bool(_plugin_cron_env_var(name))


def _resolve_home_env_var(platform_name: str) -> str:
    """Return the env var name for a platform's cron home channel.

    Built-in platforms are in ``_HOME_TARGET_ENV_VARS``; plugin platforms are
    resolved from the platform registry.
    """
    name = platform_name.lower()
    env_var = _HOME_TARGET_ENV_VARS.get(name)
    if env_var:
        return env_var
    return _plugin_cron_env_var(name)


def _get_home_target_chat_id(platform_name: str) -> str:
    """Return the configured home target chat/room ID for a delivery platform."""
    env_var = _resolve_home_env_var(platform_name)
    if not env_var:
        return ""
    value = os.getenv(env_var, "")
    if not value:
        legacy = _LEGACY_HOME_TARGET_ENV_VARS.get(env_var)
        if legacy:
            value = os.getenv(legacy, "")
    return value


def _get_home_target_thread_id(platform_name: str) -> Optional[str]:
    """Return the optional thread/topic ID for a platform home target.

    Telegram-only override: ``TELEGRAM_CRON_THREAD_ID`` takes precedence over
    ``TELEGRAM_HOME_CHANNEL_THREAD_ID`` for cron delivery. When topic mode is
    enabled, deliveries that land in the root DM (thread_id unset) end up in
    the system-only lobby where the user cannot reply — the gateway returns
    the lobby reminder and drops ``reply_to_message_id`` (#24409). Pointing
    cron at a dedicated topic via this env var lets replies work as expected
    without changing the lobby invariant.
    """
    env_var = _resolve_home_env_var(platform_name)
    if not env_var:
        return None
    if platform_name.lower() == "telegram":
        cron_thread = os.getenv("TELEGRAM_CRON_THREAD_ID", "").strip()
        if cron_thread:
            return cron_thread
    value = os.getenv(f"{env_var}_THREAD_ID", "").strip()
    if not value:
        legacy = _LEGACY_HOME_TARGET_ENV_VARS.get(env_var)
        if legacy:
            value = os.getenv(f"{legacy}_THREAD_ID", "").strip()
    return value or None


def _iter_home_target_platforms():
    """Iterate built-in + plugin platform names that expose a home channel.

    Used by the ``deliver=origin`` fallback when the job has no origin.
    """
    for name in _HOME_TARGET_ENV_VARS:
        yield name
    try:
        from takyon_cli.plugins import discover_plugins
        discover_plugins()  # idempotent
        from gateway.platform_registry import platform_registry
        for entry in platform_registry.plugin_entries():
            if entry.cron_deliver_env_var and entry.name not in _HOME_TARGET_ENV_VARS:
                yield entry.name
    except Exception:
        pass


def _resolve_single_delivery_target(job: dict, deliver_value: str) -> Optional[dict]:
    """Resolve one concrete auto-delivery target for a cron job."""

    origin = _resolve_origin(job)

    if deliver_value == "local":
        return None

    if deliver_value == "origin":
        if origin:
            return {
                "platform": origin["platform"],
                "chat_id": str(origin["chat_id"]),
                "thread_id": origin.get("thread_id"),
            }
        # Origin missing (e.g. job created via API/script) — try each
        # platform's home channel as a fallback instead of silently dropping.
        for platform_name in _iter_home_target_platforms():
            chat_id = _get_home_target_chat_id(platform_name)
            if chat_id:
                logger.info(
                    "Job '%s' has deliver=origin but no origin; falling back to %s home channel",
                    job.get("name", job.get("id", "?")),
                    platform_name,
                )
                return {
                    "platform": platform_name,
                    "chat_id": chat_id,
                    "thread_id": _get_home_target_thread_id(platform_name),
                }
        return None

    if ":" in deliver_value:
        platform_name, rest = deliver_value.split(":", 1)
        platform_key = platform_name.lower()

        from tools.send_message_tool import _parse_target_ref

        parsed_chat_id, parsed_thread_id, is_explicit = _parse_target_ref(platform_key, rest)
        if is_explicit:
            chat_id, thread_id = parsed_chat_id, parsed_thread_id
        else:
            chat_id, thread_id = rest, None

        # Resolve human-friendly labels like "Alice (dm)" to real IDs.
        try:
            from gateway.channel_directory import resolve_channel_name
            resolved = resolve_channel_name(platform_key, chat_id)
            if resolved:
                parsed_chat_id, parsed_thread_id, resolved_is_explicit = _parse_target_ref(platform_key, resolved)
                if resolved_is_explicit:
                    chat_id = parsed_chat_id
                    if parsed_thread_id is not None:
                        thread_id = parsed_thread_id
                else:
                    chat_id = resolved
        except Exception:
            pass

        return {
            "platform": platform_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

    platform_name = deliver_value
    if origin and origin.get("platform") == platform_name:
        return {
            "platform": platform_name,
            "chat_id": str(origin["chat_id"]),
            "thread_id": origin.get("thread_id"),
        }

    if not _is_known_delivery_platform(platform_name):
        return None
    chat_id = _get_home_target_chat_id(platform_name)
    if not chat_id:
        return None

    return {
        "platform": platform_name,
        "chat_id": chat_id,
        "thread_id": _get_home_target_thread_id(platform_name),
    }


def _normalize_deliver_value(deliver) -> str:
    """Normalize a stored/submitted ``deliver`` value to its canonical string form.

    The contract is that ``deliver`` is a string (``"local"``, ``"origin"``,
    ``"telegram"``, ``"telegram:-1001:17"``, or comma-separated combinations).
    Historically some callers — MCP clients passing an array, direct edits of
    ``jobs.json``, or stale code paths — have stored a list/tuple like
    ``["telegram"]``.  ``str(["telegram"])`` would serialize to the literal
    string ``"['telegram']"``, which is not a known platform and fails
    resolution silently.  Flatten lists/tuples into a comma-separated string
    so both forms work.  Returns ``"local"`` for anything falsy.
    """
    if deliver is None or deliver == "":
        return "local"
    if isinstance(deliver, (list, tuple)):
        parts = [str(p).strip() for p in deliver if str(p).strip()]
        return ",".join(parts) if parts else "local"
    return str(deliver)


# Routing intent tokens — resolved at fire time, not create time, so a
# job created before Telegram was wired up will pick up Telegram once it
# comes online.  ``all`` expands into the set of connected platforms
# (those with a configured home chat_id) in _expand_routing_tokens.
_ROUTING_TOKENS = frozenset({"all"})


def _expand_routing_tokens(part: str) -> List[str]:
    """Expand a routing-intent token to concrete platform names.

    ``all`` expands to every platform in ``_iter_home_target_platforms()``
    that has a configured home chat_id right now.  Unknown / non-token
    values pass through unchanged as a single-element list, so the caller
    can treat every token uniformly.
    """
    token = part.lower()
    if token not in _ROUTING_TOKENS:
        return [part]
    expanded: List[str] = []
    for platform_name in _iter_home_target_platforms():
        if _get_home_target_chat_id(platform_name):
            expanded.append(platform_name)
    return expanded


def _resolve_delivery_targets(job: dict) -> List[dict]:
    """Resolve all concrete auto-delivery targets for a cron job.

    Accepts the legacy comma-separated ``deliver`` string plus the
    ``all`` routing-intent token, which expands to every platform with
    a configured home channel.  Tokens may be combined with explicit
    targets: ``origin,all`` and ``all,telegram:-100:17`` both work.
    Duplicate (platform, chat_id, thread_id) tuples are collapsed by the
    existing dedup pass.
    """
    deliver = _normalize_deliver_value(job.get("deliver", "local"))
    if deliver == "local":
        return []

    raw_parts = [p.strip() for p in deliver.split(",") if p.strip()]

    # Expand routing intents.
    parts: List[str] = []
    for raw in raw_parts:
        parts.extend(_expand_routing_tokens(raw))

    seen = set()
    targets = []
    for part in parts:
        target = _resolve_single_delivery_target(job, part)
        if target:
            key = (target["platform"].lower(), str(target["chat_id"]), target.get("thread_id"))
            if key not in seen:
                seen.add(key)
                targets.append(target)
    return targets


def _resolve_delivery_target(job: dict) -> Optional[dict]:
    """Resolve the concrete auto-delivery target for a cron job, if any."""
    targets = _resolve_delivery_targets(job)
    return targets[0] if targets else None


# Media extension sets — audio routing is centralized in gateway.platforms.base
# via should_send_media_as_audio() so Telegram-specific rules stay in one place.
_VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'})
_IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})


def _send_media_via_adapter(
    adapter,
    chat_id: str,
    media_files: list,
    metadata: dict | None,
    loop,
    job: dict,
    platform=None,
) -> None:
    """Send extracted MEDIA files as native platform attachments via a live adapter.

    Routes each file to the appropriate adapter method (send_voice, send_image_file,
    send_video, send_document) based on file extension — mirroring the routing logic
    in ``BasePlatformAdapter._process_message_background``.
    """
    from pathlib import Path

    from gateway.platforms.base import should_send_media_as_audio

    for media_path, _is_voice in media_files:
        try:
            ext = Path(media_path).suffix.lower()
            route_platform = platform if platform is not None else getattr(adapter, "platform", None)
            if should_send_media_as_audio(route_platform, ext, is_voice=_is_voice):
                coro = adapter.send_voice(chat_id=chat_id, audio_path=media_path, metadata=metadata)
            elif ext in _VIDEO_EXTS:
                coro = adapter.send_video(chat_id=chat_id, video_path=media_path, metadata=metadata)
            elif ext in _IMAGE_EXTS:
                coro = adapter.send_image_file(chat_id=chat_id, image_path=media_path, metadata=metadata)
            else:
                coro = adapter.send_document(chat_id=chat_id, file_path=media_path, metadata=metadata)

            from agent.async_utils import safe_schedule_threadsafe
            future = safe_schedule_threadsafe(coro, loop)
            if future is None:
                logger.warning(
                    "Job '%s': cannot send media %s, gateway loop unavailable",
                    job.get("id", "?"), media_path,
                )
                return
            try:
                result = future.result(timeout=30)
            except TimeoutError:
                future.cancel()
                raise
            if result and not getattr(result, "success", True):
                logger.warning(
                    "Job '%s': media send failed for %s: %s",
                    job.get("id", "?"), media_path, getattr(result, "error", "unknown"),
                )
        except Exception as e:
            logger.warning("Job '%s': failed to send media %s: %s", job.get("id", "?"), media_path, e)


def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:
    """
    Deliver job output to the configured target(s) (origin chat, specific platform, etc.).

    When ``adapters`` and ``loop`` are provided (gateway is running), tries to
    use the live adapter first — this supports E2EE rooms (e.g. Matrix) where
    the standalone HTTP path cannot encrypt.  Falls back to standalone send if
    the adapter path fails or is unavailable.

    Returns None on success, or an error string on failure.
    """
    targets = _resolve_delivery_targets(job)
    if not targets:
        if job.get("deliver", "local") != "local":
            msg = f"no delivery target resolved for deliver={job.get('deliver', 'local')}"
            logger.warning("Job '%s': %s", job["id"], msg)
            return msg
        return None  # local-only jobs don't deliver — not a failure

    from tools.send_message_tool import _send_to_platform
    from gateway.config import load_gateway_config, Platform

    # Optionally wrap the content with a header/footer so the user knows this
    # is a cron delivery.  Wrapping is on by default; set cron.wrap_response: false
    # in config.yaml for clean output.
    wrap_response = True
    try:
        user_cfg = load_config()
        wrap_response = user_cfg.get("cron", {}).get("wrap_response", True)
    except Exception:
        pass

    if wrap_response:
        task_name = job.get("name", job["id"])
        job_id = job.get("id", "")
        delivery_content = (
            f"Cronjob Response: {task_name}\n"
            f"(job_id: {job_id})\n"
            f"-------------\n\n"
            f"{content}\n\n"
            f"To stop or manage this job, send me a new message (e.g. \"stop reminder {task_name}\")."
        )
    else:
        delivery_content = content

    # Extract MEDIA: tags so attachments are forwarded as files, not raw text
    from gateway.platforms.base import BasePlatformAdapter
    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)

    try:
        config = load_gateway_config()
    except Exception as e:
        msg = f"failed to load gateway config: {e}"
        logger.error("Job '%s': %s", job["id"], msg)
        return msg

    delivery_errors = []

    for target in targets:
        platform_name = target["platform"]
        chat_id = target["chat_id"]
        thread_id = target.get("thread_id")

        # Diagnostic: log thread_id for topic-aware delivery debugging
        origin = _resolve_origin(job) or {}
        origin_thread = origin.get("thread_id")
        if origin_thread and not thread_id:
            logger.warning(
                "Job '%s': origin has thread_id=%s but delivery target lost it "
                "(deliver=%s, target=%s)",
                job["id"], origin_thread, job.get("deliver", "local"), target,
            )
        elif thread_id:
            logger.debug(
                "Job '%s': delivering to %s:%s thread_id=%s",
                job["id"], platform_name, chat_id, thread_id,
            )

        # Built-in names resolve to their enum member; plugin platform names
        # create dynamic members via Platform._missing_().
        try:
            platform = Platform(platform_name.lower())
        except (ValueError, KeyError):
            msg = f"unknown platform '{platform_name}'"
            logger.warning("Job '%s': %s", job["id"], msg)
            delivery_errors.append(msg)
            continue

        pconfig = config.platforms.get(platform)
        if not pconfig or not pconfig.enabled:
            msg = f"platform '{platform_name}' not configured/enabled"
            logger.warning("Job '%s': %s", job["id"], msg)
            delivery_errors.append(msg)
            continue

        # Prefer the live adapter when the gateway is running — this supports E2EE
        # rooms (e.g. Matrix) where the standalone HTTP path cannot encrypt.
        runtime_adapter = (adapters or {}).get(platform)
        delivered = False
        if runtime_adapter is not None and loop is not None and getattr(loop, "is_running", lambda: False)():
            send_metadata = {"thread_id": thread_id} if thread_id else None
            try:
                # Send cleaned text (MEDIA tags stripped) — not the raw content
                text_to_send = cleaned_delivery_content.strip()
                adapter_ok = True
                if text_to_send:
                    from agent.async_utils import safe_schedule_threadsafe
                    future = safe_schedule_threadsafe(
                        runtime_adapter.send(chat_id, text_to_send, metadata=send_metadata),
                        loop,
                    )
                    if future is None:
                        adapter_ok = False
                    else:
                        try:
                            send_result = future.result(timeout=60)
                        except TimeoutError:
                            future.cancel()
                            raise
                        if send_result and not getattr(send_result, "success", True):
                            err = getattr(send_result, "error", "unknown")
                            logger.warning(
                                "Job '%s': live adapter send to %s:%s failed (%s), falling back to standalone",
                                job["id"], platform_name, chat_id, err,
                            )
                            adapter_ok = False  # fall through to standalone path
                        elif (
                            send_result
                            and thread_id
                            and getattr(send_result, "raw_response", None)
                            and send_result.raw_response.get("thread_fallback")
                        ):
                            requested_thread_id = send_result.raw_response.get("requested_thread_id") or thread_id
                            msg = (
                                f"configured thread_id {requested_thread_id} for "
                                f"{platform_name}:{chat_id} was not found; delivered without thread_id"
                            )
                            logger.warning("Job '%s': %s", job["id"], msg)
                            delivery_errors.append(msg)

                # Send extracted media files as native attachments via the live adapter
                if adapter_ok and media_files:
                    _send_media_via_adapter(
                        runtime_adapter,
                        chat_id,
                        media_files,
                        send_metadata,
                        loop,
                        job,
                        platform=platform,
                    )

                if adapter_ok:
                    logger.info("Job '%s': delivered to %s:%s via live adapter", job["id"], platform_name, chat_id)
                    delivered = True
            except Exception as e:
                logger.warning(
                    "Job '%s': live adapter delivery to %s:%s failed (%s), falling back to standalone",
                    job["id"], platform_name, chat_id, e,
                )

        if not delivered:
            # Standalone path: run the async send in a fresh event loop (safe from any thread)
            coro = _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files)
            try:
                result = asyncio.run(coro)
            except RuntimeError:
                # asyncio.run() checks for a running loop before awaiting the coroutine;
                # when it raises, the original coro was never started — close it to
                # prevent "coroutine was never awaited" RuntimeWarning, then retry in a
                # fresh thread that has no running loop.
                coro.close()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files))
                    result = future.result(timeout=30)
            except Exception as e:
                msg = f"delivery to {platform_name}:{chat_id} failed: {e}"
                logger.error("Job '%s': %s", job["id"], msg)
                delivery_errors.append(msg)
                continue

            if result and result.get("error"):
                msg = f"delivery error: {result['error']}"
                logger.error("Job '%s': %s", job["id"], msg)
                delivery_errors.append(msg)
                continue

            logger.info("Job '%s': delivered to %s:%s", job["id"], platform_name, chat_id)

    if delivery_errors:
        return "; ".join(delivery_errors)
    return None


_DEFAULT_SCRIPT_TIMEOUT = 120  # seconds
# Backward-compatible module override used by tests and emergency monkeypatches.
_SCRIPT_TIMEOUT = _DEFAULT_SCRIPT_TIMEOUT


def _get_script_timeout() -> int:
    """Resolve cron pre-run script timeout from module/env/config with a safe default."""
    if _SCRIPT_TIMEOUT != _DEFAULT_SCRIPT_TIMEOUT:
        try:
            timeout = int(float(_SCRIPT_TIMEOUT))
            if timeout > 0:
                return timeout
        except Exception:
            logger.warning("Invalid patched _SCRIPT_TIMEOUT=%r; using env/config/default", _SCRIPT_TIMEOUT)

    env_value = os.getenv("TAKYON_CRON_SCRIPT_TIMEOUT", "").strip()
    if env_value:
        try:
            timeout = int(float(env_value))
            if timeout > 0:
                return timeout
        except Exception:
            logger.warning("Invalid TAKYON_CRON_SCRIPT_TIMEOUT=%r; using config/default", env_value)

    try:
        cfg = load_config() or {}
        cron_cfg = cfg.get("cron", {}) if isinstance(cfg, dict) else {}
        configured = cron_cfg.get("script_timeout_seconds")
        if configured is not None:
            timeout = int(float(configured))
            if timeout > 0:
                return timeout
    except Exception as exc:
        logger.debug("Failed to load cron script timeout from config: %s", exc)

    return _DEFAULT_SCRIPT_TIMEOUT


def _run_job_script(script_path: str) -> tuple[bool, str]:
    """Execute a cron job's data-collection script and capture its output.

    Scripts must reside within TAKYON_HOME/scripts/.  Both relative and
    absolute paths are resolved and validated against this directory to
    prevent arbitrary script execution via path traversal or absolute
    path injection.

    Supported interpreters (chosen by file extension):

    * ``.sh`` / ``.bash`` — run with ``/bin/bash``
    * anything else — run with the current Python interpreter
      (``sys.executable``), preserving the original behaviour for
      Python-based pre-check and data-collection scripts.

    Shell support lets ``no_agent=True`` jobs ship classic bash watchdogs
    (the `memory-watchdog.sh` pattern) without wrapping them in Python.

    Args:
        script_path: Path to the script.  Relative paths are resolved
            against TAKYON_HOME/scripts/.  Absolute and ~-prefixed paths
            are also validated to ensure they stay within the scripts dir.

    Returns:
        (success, output) — on failure *output* contains the error message so the
        LLM can report the problem to the user.
    """
    scripts_dir = _get_takyon_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir_resolved = scripts_dir.resolve()

    raw = Path(script_path).expanduser()
    if raw.is_absolute():
        path = raw.resolve()
    else:
        path = (scripts_dir / raw).resolve()

    # Guard against path traversal, absolute path injection, and symlink
    # escape — scripts MUST reside within TAKYON_HOME/scripts/.
    try:
        path.relative_to(scripts_dir_resolved)
    except ValueError:
        return False, (
            f"Blocked: script path resolves outside the scripts directory "
            f"({scripts_dir_resolved}): {script_path!r}"
        )

    if not path.exists():
        return False, f"Script not found: {path}"
    if not path.is_file():
        return False, f"Script path is not a file: {path}"

    script_timeout = _get_script_timeout()

    # Pick an interpreter by extension.  Bash for .sh/.bash, Python for
    # everything else.  We deliberately do NOT honour the file's own
    # shebang: the scripts dir is trusted, but keeping the interpreter
    # choice explicit here keeps the allowed surface small and auditable.
    suffix = path.suffix.lower()
    if suffix in {".sh", ".bash"}:
        # Resolve bash dynamically so Windows (Git Bash) and Linux/macOS
        # all work.  On native Windows without Git for Windows installed
        # shutil.which returns None — fall back to a clear error rather
        # than a FileNotFoundError with a confusing "[WinError 2]"
        # traceback.
        _bash = shutil.which("bash") or (
            "/bin/bash" if os.path.isfile("/bin/bash") else None
        )
        if _bash is None:
            return False, (
                f"Cannot run .sh/.bash script {path.name!r}: bash not found on PATH. "
                "On Windows, install Git for Windows (which ships Git Bash) "
                "or rewrite the script as Python (.py)."
            )
        argv = [_bash, str(path)]
    else:
        argv = [sys.executable, str(path)]

    run_env = os.environ.copy()
    run_env["TAKYON_HOME"] = str(_get_takyon_home())
    try:
        from takyon_constants import get_subprocess_home

        profile_home = get_subprocess_home()
        if profile_home:
            run_env["HOME"] = profile_home
    except Exception:
        pass

    try:
        popen_kwargs = {"creationflags": windows_hide_flags()} if sys.platform == "win32" else {}
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=script_timeout,
            cwd=str(path.parent),
            env=run_env,
            **popen_kwargs,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        # Redact secrets from both stdout and stderr before any return path.
        try:
            from agent.redact import redact_sensitive_text
            stdout = redact_sensitive_text(stdout)
            stderr = redact_sensitive_text(stderr)
        except Exception:
            pass

        if result.returncode != 0:
            parts = [f"Script exited with code {result.returncode}"]
            if stderr:
                parts.append(f"stderr:\n{stderr}")
            if stdout:
                parts.append(f"stdout:\n{stdout}")
            return False, "\n".join(parts)

        return True, stdout

    except subprocess.TimeoutExpired:
        return False, f"Script timed out after {script_timeout}s: {path}"
    except Exception as exc:
        return False, f"Script execution failed: {exc}"


def _parse_wake_gate(script_output: str) -> bool:
    """Parse the last non-empty stdout line of a cron job's pre-check script
    as a wake gate.

    The convention (ported from nanoclaw #1232): if the last stdout line is
    JSON like ``{"wakeAgent": false}``, the agent is skipped entirely — no
    LLM run, no delivery. Any other output (non-JSON, missing flag, gate
    absent, or ``wakeAgent: true``) means wake the agent normally.

    Returns True if the agent should wake, False to skip.
    """
    if not script_output:
        return True
    stripped_lines = [line for line in script_output.splitlines() if line.strip()]
    if not stripped_lines:
        return True
    last_line = stripped_lines[-1].strip()
    try:
        gate = json.loads(last_line)
    except (json.JSONDecodeError, ValueError):
        return True
    if not isinstance(gate, dict):
        return True
    return gate.get("wakeAgent", True) is not False


def _build_job_prompt(job: dict, prerun_script: Optional[tuple] = None) -> str:
    """Build the effective prompt and native approved-skill invocations for a cron job.

    Args:
        job: The cron job dict.
        prerun_script: Optional ``(success, stdout)`` from a script that has
            already been executed by the caller (e.g. for a wake-gate check).
            When provided, the script is not re-executed and the cached
            result is used for prompt injection. When omitted, the script
            (if any) runs inline as before.
    """
    prompt = str(job.get("prompt") or "")
    skills = job.get("skills")

    # Run data-collection script if configured, inject output as context.
    script_path = job.get("script")
    if script_path:
        if prerun_script is not None:
            success, script_output = prerun_script
        else:
            success, script_output = _run_job_script(script_path)
        if success:
            if script_output:
                prompt = (
                    "## Script Output\n"
                    "The following data was collected by a pre-run script. "
                    "Use it as context for your analysis.\n\n"
                    f"```\n{script_output}\n```\n\n"
                    f"{prompt}"
                )
            else:
                # Script produced no output — nothing to report, skip AI call.
                return None
        else:
            prompt = (
                "## Script Error\n"
                "The data-collection script failed. Report this to the user.\n\n"
                f"```\n{script_output}\n```\n\n"
                f"{prompt}"
            )

    # Inject output from referenced cron jobs as context.
    context_from = job.get("context_from")
    if context_from:
        from cron.jobs import OUTPUT_DIR
        if isinstance(context_from, str):
            context_from = [context_from]
        for source_job_id in context_from:
            # Guard against path traversal — valid job IDs are 12-char hex strings
            if not source_job_id or not all(c in "0123456789abcdef" for c in source_job_id):
                logger.warning("context_from: skipping invalid job_id %r", source_job_id)
                continue
            try:
                job_output_dir = OUTPUT_DIR / source_job_id
                if not job_output_dir.exists():
                    continue  # silent skip — no output yet
                output_files = sorted(
                    job_output_dir.glob("*.md"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if not output_files:
                    continue  # silent skip — no output yet
                latest_output = output_files[0].read_text(encoding="utf-8").strip()
                # Truncate to 8K characters to avoid prompt bloat
                _MAX_CONTEXT_CHARS = 8000
                if len(latest_output) > _MAX_CONTEXT_CHARS:
                    latest_output = latest_output[:_MAX_CONTEXT_CHARS] + "\n\n[... output truncated ...]"
                if latest_output:
                    prompt = (
                        f"## Output from job '{source_job_id}'\n"
                        "The following is the most recent output from a preceding "
                        "cron job. Use it as context for your analysis.\n\n"
                        f"```\n{latest_output}\n```\n\n"
                        f"{prompt}"
                    )
                else:
                    continue  # silent skip — empty output
            except (OSError, PermissionError) as e:
                logger.warning("context_from: failed to read output for job %r: %s", source_job_id, e)
                # silent skip — do not pollute the prompt with error messages

    # Always prepend cron execution guidance so the agent knows how
    # delivery works and can suppress delivery when appropriate.
    cron_hint = (
        "[IMPORTANT: You are running as a scheduled cron job. "
        "DELIVERY: Your final response will be automatically delivered "
        "to the user — do NOT use send_message or try to deliver "
        "the output yourself. Just produce your report/output as your "
        "final response and the system handles the rest. "
        "SILENT: If there is genuinely nothing new to report, respond "
        "with exactly \"[SILENT]\" (nothing else) to suppress delivery. "
        "Never combine [SILENT] with content — either report your "
        "findings normally, or say [SILENT] and nothing more.]\n\n"
    )
    prompt = cron_hint + prompt
    if skills is None:
        legacy = job.get("skill")
        skills = [legacy] if legacy else []
    elif isinstance(skills, str):
        skills = [skills]

    skill_names = [str(name).strip() for name in skills if str(name).strip()]
    approved_skills = _resolve_approved_cron_skills(skill_names)
    if not approved_skills:
        return _scan_assembled_cron_prompt(prompt, job)

    parts = [
        "[IMPORTANT: Invoke each approved native skill below through the Skill tool "
        "before executing the scheduled instruction. Do not emulate, inline, or load "
        "a mutable filesystem copy of a skill.]",
        "",
        *(f"- `{skill_name}`" for skill_name in approved_skills),
    ]
    if prompt:
        parts.extend(
            [
                "",
                "The user provided this scheduled instruction alongside the skill invocation:",
                prompt,
            ]
        )
    return _scan_assembled_cron_prompt("\n".join(parts), job)


def _scan_assembled_cron_prompt(assembled: str, job: dict) -> str:
    """Scan the fully assembled scheduled instruction and collected context for
    injection patterns. Raises ``CronPromptInjectionBlocked`` when a match
    fires so ``run_job`` can surface a clear refusal to the operator.

    Approved skills are selected and read only by the immutable SDK plugin;
    this scanner therefore covers the remaining mutable cron inputs rather
    than copying a skill body into an unattended prompt.
    """
    from tools.cronjob_tools import _scan_cron_prompt

    scan_error = _scan_cron_prompt(assembled)
    if scan_error:
        job_label = job.get("name") or job.get("id") or "<unknown>"
        logger.warning(
            "Cron job '%s': assembled prompt blocked by injection scanner — %s",
            job_label,
            scan_error,
        )
        raise CronPromptInjectionBlocked(scan_error)
    return assembled


def run_job(job: dict) -> tuple[bool, str, str, Optional[str]]:
    """Execute a single cron job, applying any per-job profile override."""
    job_id = job["id"]
    with _job_profile_context(job_id, job.get("profile")):
        return _run_job_impl(job)


def _run_job_impl(job: dict) -> tuple[bool, str, str, Optional[str]]:
    """
    Execute a single cron job.
    
    Returns:
        Tuple of (success, full_output_doc, final_response, error_message)
    """
    job_id = job["id"]
    job_name = str(job.get("name") or job.get("prompt") or job_id or "cron job")

    # ---------------------------------------------------------------
    # no_agent short-circuit — the script IS the job, no LLM involvement.
    # ---------------------------------------------------------------
    # This mirrors the classic "run a bash script on a timer, send its
    # stdout to telegram" watchdog pattern. The model path is skipped
    # entirely: no SDK subprocess, no prompt, no tool loop, no token spend.
    #
    # Keep this block before every SDK/session import so a pure-script tick
    # never pays for model machinery it does not use.
    #
    # Semantics:
    #   - script stdout (trimmed) → delivered verbatim as the final message
    #   - empty stdout            → silent run (no delivery, success=True)
    #   - non-zero exit / timeout → delivered as an error alert, success=False
    #   - wakeAgent=false gate    → treated like empty stdout (silent), since
    #                               the whole point of no_agent is that there
    #                               is no agent to wake
    if job.get("no_agent"):
        script_path = job.get("script")
        if not script_path:
            err = "no_agent=True but no script is set for this job"
            logger.error("Job '%s': %s", job_id, err)
            return False, "", "", err

        # Apply workdir if configured — lets scripts use predictable relative
        # paths. For no_agent jobs this is just the subprocess cwd (not an
        # agent TERMINAL_CWD bridge).
        _job_workdir = (job.get("workdir") or "").strip() or None
        _prior_cwd = None
        if _job_workdir and Path(_job_workdir).is_dir():
            _prior_cwd = os.getcwd()
            try:
                os.chdir(_job_workdir)
            except OSError:
                _prior_cwd = None

        try:
            ok, output = _run_job_script(script_path)
        finally:
            if _prior_cwd is not None:
                try:
                    os.chdir(_prior_cwd)
                except OSError:
                    pass

        now_iso = _takyon_now().strftime("%Y-%m-%d %H:%M:%S")

        if not ok:
            # Script crashed / timed out / exited non-zero.  Deliver the
            # error so the user knows the watchdog itself broke — silent
            # failure for an alerting job is the worst-case outcome.
            alert = (
                f"⚠ Cron watchdog '{job_name}' script failed\n\n"
                f"{output}\n\n"
                f"Time: {now_iso}"
            )
            doc = (
                f"# Cron Job: {job_name}\n\n"
                f"**Job ID:** {job_id}\n"
                f"**Run Time:** {now_iso}\n"
                f"**Mode:** no_agent (script)\n"
                f"**Status:** script failed\n\n"
                f"{output}\n"
            )
            return False, doc, alert, output

        # Honour the wakeAgent gate as a silent signal — `wakeAgent: false`
        # means "nothing to report this tick", same as empty stdout.
        if not _parse_wake_gate(output):
            logger.info(
                "Job '%s' (no_agent): wakeAgent=false gate — silent run", job_id
            )
            silent_doc = (
                f"# Cron Job: {job_name}\n\n"
                f"**Job ID:** {job_id}\n"
                f"**Run Time:** {now_iso}\n"
                f"**Mode:** no_agent (script)\n"
                f"**Status:** silent (wakeAgent=false)\n"
            )
            return True, silent_doc, SILENT_MARKER, None

        if not output.strip():
            logger.info("Job '%s' (no_agent): empty stdout — silent run", job_id)
            silent_doc = (
                f"# Cron Job: {job_name}\n\n"
                f"**Job ID:** {job_id}\n"
                f"**Run Time:** {now_iso}\n"
                f"**Mode:** no_agent (script)\n"
                f"**Status:** silent (empty output)\n"
            )
            return True, silent_doc, SILENT_MARKER, None

        doc = (
            f"# Cron Job: {job_name}\n\n"
            f"**Job ID:** {job_id}\n"
            f"**Run Time:** {now_iso}\n"
            f"**Mode:** no_agent (script)\n\n"
            f"---\n\n"
            f"{output}\n"
        )
        return True, doc, output, None

    # The model path is the scoped primary Claude Agent SDK. Script-only
    # no_agent jobs returned above and remain independent of the SDK runtime.

    # Wake-gate: if this job has a pre-check script, run it BEFORE building
    # the prompt so a ``{"wakeAgent": false}`` response can short-circuit
    # the whole agent run. We pass the result into _build_job_prompt so
    # the script is only executed once.
    prerun_script = None
    script_path = job.get("script")
    if script_path:
        prerun_script = _run_job_script(script_path)
        _ran_ok, _script_output = prerun_script
        if _ran_ok and not _parse_wake_gate(_script_output):
            logger.info(
                "Job '%s' (ID: %s): wakeAgent=false, skipping agent run",
                job_name, job_id,
            )
            silent_doc = (
                f"# Cron Job: {job_name}\n\n"
                f"**Job ID:** {job_id}\n"
                f"**Run Time:** {_takyon_now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "Script gate returned `wakeAgent=false` — agent skipped.\n"
            )
            return True, silent_doc, SILENT_MARKER, None

    try:
        prompt = _build_job_prompt(job, prerun_script=prerun_script)
    except (CronPromptInjectionBlocked, CronSkillPolicyBlocked) as block_exc:
        # Mutable prompt/context or an unapproved explicit skill failed the
        # unattended-run policy before any model process or spend authority.
        logger.warning(
            "Job '%s' (ID: %s): blocked by prompt-injection scanner — %s",
            job_name, job_id, block_exc,
        )
        blocked_doc = (
            f"# Cron Job: {job_name}\n\n"
            f"**Job ID:** {job_id}\n"
            f"**Run Time:** {_takyon_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**Status:** BLOCKED\n\n"
            "The scheduled instruction or its explicit skill selection failed "
            "the cron policy and the model was NOT run.\n\n"
            f"**Policy result:** {block_exc}\n"
        )
        return False, blocked_doc, "", str(block_exc)
    if prompt is None:
        logger.info("Job '%s': script produced no output, skipping AI call.", job_name)
        return True, "", SILENT_MARKER, None
    _cron_session_id = ""

    logger.info("Running job '%s' (ID: %s)", job_name, job_id)
    logger.info("Prompt: %s", prompt[:100])

    # Preserve the legacy marker for non-model cron delivery/tool consumers.
    os.environ["TAKYON_CRON_SESSION"] = "1"

    # Delivery routing is context-local. Model/tool identity is bound only
    # after the business owner and canonical workspace have been resolved.
    from gateway.session_context import set_session_vars, clear_session_vars, _VAR_MAP
    _ctx_tokens = []
    _cron_delivery_vars = (
        "TAKYON_CRON_AUTO_DELIVER_PLATFORM",
        "TAKYON_CRON_AUTO_DELIVER_CHAT_ID",
        "TAKYON_CRON_AUTO_DELIVER_THREAD_ID",
    )
    for _var_name in _cron_delivery_vars:
        _VAR_MAP[_var_name].set("")

    # LLM cron may never escape its business workspace. A legacy workdir is
    # accepted only when it resolves to that exact canonical root below.
    _job_workdir = (job.get("workdir") or "").strip() or None

    try:
        # Re-read .env and config.yaml fresh every run so provider/key
        # changes take effect without a gateway restart.
        from dotenv import load_dotenv
        try:
            load_dotenv(str(_get_takyon_home() / ".env"), override=True, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(str(_get_takyon_home() / ".env"), override=True, encoding="latin-1")

        delivery_target = _resolve_delivery_target(job)
        if delivery_target:
            _VAR_MAP["TAKYON_CRON_AUTO_DELIVER_PLATFORM"].set(delivery_target["platform"])
            _VAR_MAP["TAKYON_CRON_AUTO_DELIVER_CHAT_ID"].set(str(delivery_target["chat_id"]))
            _VAR_MAP["TAKYON_CRON_AUTO_DELIVER_THREAD_ID"].set(
                ""
                if delivery_target.get("thread_id") is None
                else str(delivery_target["thread_id"])
            )

        # Load only bounded turn/tool policy from config. Provider/model/base
        # overrides are retired on the primary SDK path.
        _cfg = {}
        try:
            import yaml
            _cfg_path = str(_get_takyon_home() / "config.yaml")
            if os.path.exists(_cfg_path):
                with open(_cfg_path, encoding="utf-8") as _f:
                    _cfg = yaml.safe_load(_f) or {}
                _cfg = _expand_env_vars(_cfg)
        except Exception as e:
            logger.warning("Job '%s': failed to load config.yaml, using defaults: %s", job_id, e)

        # Apply IPv4 preference if configured.
        try:
            from takyon_constants import apply_ipv4_preference
            _net_cfg = _cfg.get("network", {})
            if isinstance(_net_cfg, dict) and _net_cfg.get("force_ipv4"):
                apply_ipv4_preference(force=True)
        except Exception:
            pass

        effort = str(_cfg.get("agent", {}).get("reasoning_effort", "high")).lower()
        if effort not in {"low", "medium", "high"}:
            effort = "high"
        max_iterations = int(
            _cfg.get("agent", {}).get("max_turns")
            or _cfg.get("max_turns")
            or 90
        )

        # Persisted routing fields are assertions only. Validate them before
        # tenant/session lookup or any SDK capability/spend work.
        _validate_cron_sdk_routing_policy(job)
        if any(job.get(field) for field in ("model", "provider", "base_url")):
            logger.info(
                "Job '%s': configured routing matches the fixed primary SDK policy",
                job_id,
            )

        business = str(job.get("business") or "").strip()
        owner_user_id = _cron_operator_user_id(job, business)
        from plugins.takyon.claude_sdk_runtime import (
            SDK_GLOBAL_OPERATOR_TOOLS,
            primary_sdk_session_project_key,
            run_primary_sdk_subprocess,
            stable_sdk_session_id,
        )
        from plugins.takyon.claude_sdk_sessions import PostgresClaudeSdkSessionStore
        from plugins.takyon.operator_gateway import compose_primary_agent_system_prompt
        from plugins.takyon.turn_runtime import _business_workspace_execution_context

        occurrence_identity = _cron_occurrence_identity(job)
        _cron_session_id = stable_sdk_session_id(
            f"cron:{business}:{job_id}:{occurrence_identity}"
        )
        session_store = PostgresClaudeSdkSessionStore(
            operator_user_id=owner_user_id,
            business_slug=business,
        )
        session_key = {
            "projectKey": primary_sdk_session_project_key(
                operator_user_id=owner_user_id,
                business=business,
            ),
            "sessionId": _cron_session_id,
        }
        resume_session = session_store.load(session_key) is not None
        invocation_allowed_tools = (
            _cron_sdk_invocation_allowed_tools(job, _cfg)
            if business
            else sorted(SDK_GLOBAL_OPERATOR_TOOLS)
        )

        # Run the SDK with an inactivity-based timeout: the job can run
        # for hours if it's actively calling tools / receiving stream tokens,
        # but a hung API call or stuck tool with no activity for the configured
        # duration is caught and killed.  Default 600s (10 min inactivity);
        # override via TAKYON_CRON_TIMEOUT env var.  0 = unlimited.
        #
        # Uses the agent's built-in activity tracker (updated by
        # _touch_activity() on every tool call, API call, and stream delta).
        _raw_cron_timeout = os.getenv("TAKYON_CRON_TIMEOUT", "").strip()
        if _raw_cron_timeout:
            try:
                _cron_timeout = float(_raw_cron_timeout)
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid TAKYON_CRON_TIMEOUT=%r; using default 600s",
                    _raw_cron_timeout,
                )
                _cron_timeout = 600.0
        else:
            _cron_timeout = 600.0
        def _sdk_progress(event: dict) -> None:
            if not isinstance(event, dict):
                return
            kind = str(event.get("kind") or "runtime")
            status = str(event.get("status") or "running")
            detail = str(event.get("detail") or "").strip()
            if kind in {"skill", "session", "provider", "turn"}:
                logger.info(
                    "Job '%s' SDK %s.%s%s",
                    job_id,
                    kind,
                    status,
                    f": {detail}" if detail else "",
                )

        invocation_epoch = _cron_sdk_epoch(job)
        global_workspace = (
            _get_takyon_home()
            / "runtime"
            / "operator-workspaces"
            / owner_user_id
        )
        if not business:
            global_workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            global_workspace.chmod(0o700)
        workspace_context = (
            _business_workspace_execution_context(
                business,
                operator_user_id=owner_user_id,
                sync_on_exception=True,
            )
            if business
            else nullcontext(global_workspace)
        )
        with workspace_context as workspace_home:
            workspace = Path(workspace_home).resolve()
            if business and _job_workdir and Path(_job_workdir).expanduser().resolve() != workspace:
                raise RuntimeError(
                    "scoped SDK cron workdir must equal the canonical business workspace"
                )
            if not business and _job_workdir:
                requested_workdir = Path(_job_workdir).expanduser().resolve()
                if not requested_workdir.is_dir():
                    raise RuntimeError("root-scoped SDK cron workdir does not exist")
                workspace = requested_workdir
            _ctx_tokens = set_session_vars(
                platform="cron",
                chat_id="",
                chat_name="",
                user_id=owner_user_id,
                session_key=f"cron:{job_id}",
                workspace_root=str(workspace),
                business_slug=business,
                task_kind="ceo_wake" if business else "cron",
            )
            result = run_primary_sdk_subprocess(
                business=business,
                operator_user_id=owner_user_id,
                system_prompt=compose_primary_agent_system_prompt(
                    (
                        "Execute this unattended scheduled business wake inside the exact "
                        "bound business. Return only the final report for scheduler delivery; "
                        "never use messaging tools or attempt delivery yourself."
                        if business
                        else "Execute this root-scoped operator schedule with only the exposed "
                        "read, research, and planning capabilities. Do not mutate a business, "
                        "use messaging tools, or attempt delivery yourself."
                    )
                ),
                user_prompt=prompt,
                enabled_toolsets=_CRON_SDK_ENABLED_TOOLSETS,
                disabled_toolsets=_CRON_SDK_DISABLED_TOOLSETS,
                invocation_allowed_tools=invocation_allowed_tools,
                workspace_root=str(workspace),
                session_id=_cron_session_id,
                resume_session=resume_session,
                session_store=session_store,
                task_id=invocation_epoch,
                mode="wake" if business else "interactive",
                epoch=invocation_epoch,
                max_turns=max_iterations,
                max_budget_usd=_cron_sdk_budget_usd(),
                effort=effort,
                inactivity_limit=max(0.0, _cron_timeout),
                progress_callback=_sdk_progress,
            )

        if not isinstance(result, dict):
            raise RuntimeError(
                f"primary SDK returned {type(result).__name__} instead of dict"
            )
        final_response = str(result.get("summary") or "").strip()
        if final_response == "(No response generated)":
            final_response = ""
        logged_response = final_response or "(No response generated)"
        
        output = f"""# Cron Job: {job_name}

**Job ID:** {job_id}
**Run Time:** {_takyon_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Response

{logged_response}
"""
        
        logger.info("Job '%s' completed successfully", job_name)
        return True, output, final_response, None
        
    except CronSdkRoutingPolicyBlocked as block_exc:
        error_msg = str(block_exc)
        logger.warning("Job '%s' blocked: %s", job_name, error_msg)
        output = f"""# Cron Job: {job_name} (BLOCKED)

**Job ID:** {job_id}
**Run Time:** {_takyon_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}
**Status:** BLOCKED

## Prompt

{prompt}

## Policy Result

{error_msg}
"""
        return False, output, "", error_msg

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.exception("Job '%s' failed: %s", job_name, error_msg)
        
        output = f"""# Cron Job: {job_name} (FAILED)

**Job ID:** {job_id}
**Run Time:** {_takyon_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Error

```
{error_msg}
```
"""
        return False, output, "", error_msg

    finally:
        clear_session_vars(_ctx_tokens)
        for _var_name in _cron_delivery_vars:
            _VAR_MAP[_var_name].set("")


def tick(verbose: bool = True, adapters=None, loop=None) -> int:
    """
    Check and run all due jobs.
    
    Uses a file lock so only one tick runs at a time, even if the gateway's
    in-process ticker and a standalone daemon or manual tick overlap.
    
    Args:
        verbose: Whether to print status messages
        adapters: Optional dict mapping Platform → live adapter (from gateway)
        loop: Optional asyncio event loop (from gateway) for live adapter sends
    
    Returns:
        Number of jobs executed (0 if another tick is already running)
    """
    lock_dir, lock_file = _get_lock_paths()
    lock_dir.mkdir(parents=True, exist_ok=True)

    # Cross-platform file locking: fcntl on Unix, msvcrt on Windows
    lock_fd = None
    try:
        lock_fd = open(lock_file, "w", encoding="utf-8")
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        logger.debug("Tick skipped — another instance holds the lock")
        if lock_fd is not None:
            lock_fd.close()
        return 0

    try:
        due_jobs = get_due_jobs()
        action_runs = _run_due_action_schedules_inline(verbose=verbose)

        if verbose and not due_jobs and not action_runs:
            logger.info("%s - No jobs due", _takyon_now().strftime('%H:%M:%S'))
            return 0

        if verbose:
            logger.info("%s - %s job(s) due", _takyon_now().strftime('%H:%M:%S'), len(due_jobs))

        # Advance next_run_at for all recurring jobs FIRST, under the file lock,
        # before any execution begins.  This preserves at-most-once semantics.
        for job in due_jobs:
            advance_next_run(job["id"])

        # Resolve max parallel workers: env var > config.yaml > unbounded.
        # Set TAKYON_CRON_MAX_PARALLEL=1 to restore old serial behaviour.
        _max_workers: Optional[int] = None
        try:
            _env_par = os.getenv("TAKYON_CRON_MAX_PARALLEL", "").strip()
            if _env_par:
                _max_workers = int(_env_par) or None
        except (ValueError, TypeError):
            logger.warning("Invalid TAKYON_CRON_MAX_PARALLEL value; defaulting to unbounded")
        if _max_workers is None:
            try:
                _ucfg = load_config() or {}
                _cfg_par = (
                    _ucfg.get("cron", {}) if isinstance(_ucfg, dict) else {}
                ).get("max_parallel_jobs")
                if _cfg_par is not None:
                    _max_workers = int(_cfg_par) or None
            except Exception:
                pass

        if verbose:
            logger.info(
                "Running %d job(s) in parallel (max_workers=%s)",
                len(due_jobs),
                _max_workers if _max_workers else "unbounded",
            )

        def _process_job(job: dict) -> bool:
            """Run one due job end-to-end: execute, save, deliver, mark."""
            try:
                success, output, final_response, error = run_job(job)

                output_file = save_job_output(job["id"], output)
                if verbose:
                    logger.info("Output saved to: %s", output_file)

                # Deliver the final response to the origin/target chat.
                # If the agent responded with [SILENT], skip delivery (but
                # output is already saved above).  Failed jobs always deliver.
                deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
                # Treat whitespace-only final responses the same as empty
                # responses: do not deliver a blank message, and let the
                # empty-response guard below mark the run as a soft failure.
                should_deliver = bool(deliver_content.strip())
                if should_deliver and success and SILENT_MARKER in deliver_content.strip().upper():
                    logger.info("Job '%s': agent returned %s — skipping delivery", job["id"], SILENT_MARKER)
                    should_deliver = False

                delivery_error = None
                if should_deliver:
                    try:
                        delivery_error = _deliver_result(job, deliver_content, adapters=adapters, loop=loop)
                    except Exception as de:
                        delivery_error = str(de)
                        logger.error("Delivery failed for job %s: %s", job["id"], de)

                # Treat empty final_response as a soft failure so last_status
                # is not "ok" — the agent ran but produced nothing useful.
                # (issue #8585)
                if success and not final_response.strip():
                    success = False
                    error = "Agent completed but produced empty response (model error, timeout, or misconfiguration)"

                mark_job_run(job["id"], success, error, delivery_error=delivery_error)
                return True

            except Exception as e:
                logger.error("Error processing job %s: %s", job['id'], e)
                mark_job_run(job["id"], False, str(e))
                return False

        # Partition due jobs: jobs with a per-job workdir and/or profile touch
        # process-global runtime state inside run_job. Workdir jobs temporarily
        # set os.environ["TERMINAL_CWD"]; profile jobs use a context-local
        # Takyon home override, scheduler _takyon_home hook, and temporary
        # profile .env load into os.environ with snapshot/restore. They MUST run
        # sequentially to avoid corrupting each other. Jobs without either field
        # stay parallel-safe.
        sequential_jobs = [
            j for j in due_jobs
            if (j.get("workdir") or "").strip() or (j.get("profile") or "").strip()
        ]
        parallel_jobs = [
            j for j in due_jobs
            if not ((j.get("workdir") or "").strip() or (j.get("profile") or "").strip())
        ]

        _results: list = []

        # Sequential pass for env/context-mutating jobs.
        for job in sequential_jobs:
            _ctx = contextvars.copy_context()
            _results.append(_ctx.run(_process_job, job))

        # Parallel pass for the rest — same behaviour as before.
        if parallel_jobs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=_max_workers) as _tick_pool:
                _futures = []
                for job in parallel_jobs:
                    _ctx = contextvars.copy_context()
                    _futures.append(_tick_pool.submit(_ctx.run, _process_job, job))
                for f in concurrent.futures.as_completed(_futures, timeout=600):
                    try:
                        _results.append(f.result())
                    except Exception as exc:
                        logger.error("Parallel cron job future failed: %s", exc)
                        _results.append(False)

        # Best-effort sweep of MCP stdio subprocesses that survived their
        # session teardown during this tick.  Runs AFTER every job has
        # finished so active sessions (including live user chats) are
        # never touched — only PIDs explicitly detected as orphans in
        # tools.mcp_tool._run_stdio's finally block are reaped.
        try:
            from tools.mcp_tool import _kill_orphaned_mcp_children
            _kill_orphaned_mcp_children()
        except Exception as _e:
            logger.debug("Post-tick MCP orphan cleanup failed: %s", _e)

        return sum(_results) + action_runs
    finally:
        if fcntl:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
        elif msvcrt:
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, IOError):
                pass
        lock_fd.close()


def _run_due_action_schedules_inline(*, verbose: bool) -> int:
    try:
        from plugins.takyon import app_actions as takyon_app_actions
        from plugins.takyon.core import _PGConn, _store
    except Exception:
        return 0

    store = _store()
    try:
        with store._connect() as conn:
            if isinstance(conn, _PGConn):
                return 0
    except Exception:
        return 0

    executed = 0

    def _enqueue(item: dict[str, str]) -> None:
        nonlocal executed
        takyon_app_actions.execute_scheduled_action(
            store,
            business_slug=str(item.get("business_slug") or ""),
            action_name=str(item.get("action_name") or ""),
            window_key=str(item.get("window_key") or ""),
        )
        executed += 1

    count = takyon_app_actions.dispatch_due_action_schedules(
        store,
        datetime.now(timezone.utc),
        _enqueue,
    )
    if verbose and count:
        logger.info("Ran %d due app action schedule(s)", count)
    return executed


if __name__ == "__main__":
    tick(verbose=True)
