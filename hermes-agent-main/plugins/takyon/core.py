"""Core storage and guardrails for the Takyon business plugin."""

from __future__ import annotations

import hashlib
import hmac
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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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

from agent.skill_utils import get_all_skills_dirs, parse_frontmatter
from takyon_constants import get_takyon_home
from tools.registry import tool_error, tool_result


TAKYON_TOOLSET = "takyon"
DEFAULT_TAKYON_DIRNAME = "takyon"
DEFAULT_CLAUDE_AGENT_MODEL = "claude-opus-4-7"
MAX_READ_CHARS = 64_000
MAX_WRITE_CHARS = 1_000_000
CURRENT_BUSINESS_SCHEMA_VERSION = 1
CURRENT_BUSINESS_CAPABILITY_VERSION = 1
BUSINESS_UPGRADE_RECEIPT = "metrics/receipts/upgrades/takyon-business-upgrade-v1.json"
TAKYON_BUSINESS_ROOTS = ("product", "distribution", "research", "metrics")
NO_PRETEND_PRODUCT_CONTRACT = """Hermes no-pretend product contract:
- You are not allowed to invent backend behavior.
- Never fake auth, sessions, users, entitlements, checkout, subscriptions, outreach sends, deploys, provider calls, metrics, or business outcomes.
- Use canonical Hermes/Takyon runtime tools or endpoints for auth, billing, entitlements, usage, outreach, and receipts.
- If no browser endpoint exists for auth, billing, entitlements, usage, or outreach, build the screen as unavailable/blocking, not fake.
- If a runtime endpoint or provider path is unavailable in this workspace, show a visible DEBUG/blocked state that says the feature is not wired yet.
- Do not use localStorage, demo query parameters, hardcoded test users, or fake checkout URLs to simulate business reality in product source.
- In customer-facing product copy, describe capabilities instead of naming upstream foundation model vendors or snapshot ids unless the operator explicitly wants model-led positioning.
"""
CUSTOMER_FACING_AI_COPY_CONTRACT = """Customer-facing AI product copy contract:
- This work may ship to customers or prospects.
- Default to capability-first language, not vendor/model-name-first language.
- Do not mention OpenAI, GPT-* names, Anthropic, Claude family names, model snapshots, or API internals in customer-facing UI/copy unless the operator explicitly asks for provider/model-led positioning or comparison.
- If named model context is truly required, use current Anthropic family names accurately: Claude Opus 4.7, Claude Sonnet 4.6, and Claude Haiku 4.5.
- Never mix vendors accidentally. Do not describe Claude-backed behavior with GPT names or stale model labels like GPT-4o-mini.
- Prefer customer-visible claims like analyze feedback, cluster themes, rank opportunities, explain why, and export insights.
"""
RUNTIME_UI_CONTRACT_INTRO = """Hermes runtime UI contract:
- Build runtime-backed product UI to the declared Takyon app-runtime contract, not browser-only state.
- Call ONLY the exact runtime endpoints listed below. Use each path verbatim, including the full Runtime API base prefix. Do not shorten, rename, or invent rail paths.
- Do not invent local-only auth, sessions, entitlements, checkout, billing, or usage state.
- If a declared runtime feature is not wired yet, keep the blocked state visible and name the missing runtime step.
- Do not claim undeclared runtime-backed features without first updating the app surface contract.
"""
WORKSPACE_PATH_CONTRACT = """Hermes workspace path contract:
- The current working directory is already the requested business workspace: {workspace}.
- Write files relative to the current working directory.
- Do not recreate the workspace path inside itself. If the workspace is `product/site`, write `index.html`, `app/page.tsx`, or `package.json`, not `product/site/index.html` or `product/site/app/page.tsx`.
- If an instruction mentions the workspace path, interpret it as the current working directory unless it explicitly asks for a different business-relative path.
"""
_WORKER_GUIDANCE_SKILL_SECTIONS: dict[str, tuple[str, ...]] = {
    "claude-design": (
        "When To Use",
        "Shared Style Selection",
        "Workflow",
        "Marketing Surfaces",
        "Product Surfaces",
        "Self Review Loop",
        "Hard Rules",
    ),
    "claude-design-openai": ("When To Use", "Visual Direction", "Typography", "Color and Tokens", "Components", "Hard Rules"),
    "claude-design-stripe": ("When To Use", "Visual Direction", "Typography", "Color and Tokens", "Components", "Hard Rules"),
    "claude-design-superhuman": ("When To Use", "Visual Direction", "Typography", "Color and Tokens", "Components", "Hard Rules"),
    "claude-design-vibrant": ("When To Use", "Visual Direction", "Typography", "Color and Tokens", "Components", "Hard Rules"),
    "claude-design-doodle": ("When To Use", "Visual Direction", "Typography", "Color and Tokens", "Components", "Hard Rules"),
}
PRODUCT_RUNTIME_RAILS: dict[str, dict[str, Any]] = {
    "auth": {
        "owner_skill": "takyon-app-runtime",
        "tools": ["business_request_app_magic_link", "business_verify_app_magic_link", "business_read_app_account"],
        "endpoints": [
            ("POST", "auth/request"),
            ("GET", "auth/verify"),
            ("GET", "session"),
        ],
        "worker_contract": [
            "Use Takyon magic-link and session rails instead of browser-only auth state.",
            "If auth is not wired yet, keep sign-in blocked and name the missing runtime step.",
        ],
    },
    "account": {
        "owner_skill": "takyon-app-runtime",
        "tools": ["business_read_app_account"],
        "endpoints": [("GET", "account")],
        "worker_contract": [
            "Read account/session state from the shared Takyon account runtime route.",
            "Do not invent a local current-user object.",
        ],
    },
    "checkout": {
        "owner_skill": "takyon-app-runtime",
        "tools": ["business_create_app_checkout", "business_record_stripe_webhook"],
        "endpoints": [("POST", "checkout")],
        "worker_contract": [
            "Use Takyon checkout rails instead of fake payment links or browser-only purchase state.",
            "If checkout is not wired yet, keep upgrade or pay actions visibly blocked.",
        ],
    },
    "billing": {
        "owner_skill": "takyon-app-runtime",
        "tools": ["business_upsert_app_plan", "business_create_app_checkout", "business_record_stripe_webhook"],
        "endpoints": [("POST", "checkout"), ("GET", "account")],
        "worker_contract": [
            "Plan, billing, and paid-state UI must reflect Takyon billing rails, not local assumptions.",
            "Do not claim a paid tier without real runtime entitlement or checkout truth.",
        ],
    },
    "entitlements": {
        "owner_skill": "takyon-app-runtime",
        "tools": ["business_upsert_app_customer", "business_grant_app_entitlement", "business_record_stripe_webhook"],
        "endpoints": [("GET", "account")],
        "worker_contract": [
            "Feature gating must come from Takyon entitlements, not hardcoded client flags.",
            "If entitlements are not wired yet, keep gated actions blocked.",
        ],
    },
    "usage": {
        "owner_skill": "takyon-app-runtime",
        "tools": ["business_configure_app_budget", "business_record_app_usage"],
        "endpoints": [("GET", "usage")],
        "worker_contract": [
            "Usage meters and budget warnings should reflect Takyon usage rails, not fake counters.",
            "If usage tracking is not wired yet, say so instead of simulating quotas.",
        ],
    },
    "generate": {
        "owner_skill": "takyon-app-runtime",
        "tools": [],
        "endpoints": [("POST", "generate")],
        "worker_contract": [
            "AI generation goes through the shared Takyon AI gateway: POST /internal/ai-gateway/messages on the runtime host, body {messages|prompt, model?, system?, max_tokens?, temperature?}.",
            "Authenticate with the business's own tkg_ gateway key via `Authorization: Bearer tkg_...`. The generated app holds ONLY that gateway key, never the platform provider key (e.g. ANTHROPIC_API_KEY) — the gateway calls the shared provider key server-side and never returns it.",
            "The gateway meters each call against the business product budget: treat 402 as out-of-credit (surface it, do not retry as if free) and 503 as generation-not-configured (keep the action visible but clearly blocked; never fake a completion).",
            "Use the returned {text, content, model, usage} as the only source of truth for output and spend; do not invent token counts or cost.",
        ],
    },
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_CONTROL_STATES = {"active", "paused", "killed"}
_BUSINESS_MODES = {"live", "test"}
_BUSINESS_WORK_FOCUS_MODES = {"all", "marketing", "product"}
_DEFAULT_COMPANY_BASE_DOMAIN = "fourmanifold.com"
_DEFAULT_PRODUCT_PUBLISH_POLICY = "publish_after_verify"
_DEFAULT_PRODUCT_MODE_BEHAVIOR = "test_mode_publishes_product_surface"
_DEFAULT_PRODUCT_DONE_GATE = "business_verify_product_surface:verified_and_published_or_exact_blocker"
_SHARED_RENDERER_PUBLISH_POLICIES = {"shared_renderer", "shared_product_renderer", "shared_page_renderer"}
_PRODUCT_SERVICE_PORT_MIN = 9200
_PRODUCT_SERVICE_PORT_MAX = 9799
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


def _normalize_guidance_skills(raw: Any) -> list[str]:
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, (list, tuple, set)) else []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _normalize_runtime_features(raw: Any, *, strict: bool = False) -> list[str]:
    if raw is None:
        return []
    values: list[Any]
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, dict):
        values = [key for key, enabled in raw.items() if enabled]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
        if not text or not re.match(r"^[a-z0-9][a-z0-9_]{0,63}$", text):
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    unknown = [item for item in normalized if item not in PRODUCT_RUNTIME_RAILS]
    if strict and unknown:
        raise TakyonError(
            "unknown runtime_features: "
            + ", ".join(unknown)
            + f". Known rails: {', '.join(sorted(PRODUCT_RUNTIME_RAILS))}"
        )
    return [item for item in normalized if item in PRODUCT_RUNTIME_RAILS]


def _surface_runtime_features(surface: dict[str, Any] | None) -> list[str]:
    if not isinstance(surface, dict):
        return []
    direct = _normalize_runtime_features(surface.get("runtime_features"))
    if direct:
        return direct
    metadata = surface.get("metadata") if isinstance(surface.get("metadata"), dict) else {}
    return _normalize_runtime_features(metadata.get("runtime_features"))


def _runtime_rails_for_owner(surface: dict[str, Any] | None, owner_skill: str) -> list[tuple[str, dict[str, Any]]]:
    owner = str(owner_skill or "").strip().lower()
    selected = _surface_runtime_features(surface)
    rails: list[tuple[str, dict[str, Any]]] = []
    for key in selected:
        spec = PRODUCT_RUNTIME_RAILS.get(key)
        if not spec:
            continue
        if str(spec.get("owner_skill") or "").strip().lower() == owner:
            rails.append((key, spec))
    return rails


def _workspace_needs_runtime_ui_contract(workspace_raw: str) -> bool:
    normalized = workspace_raw.strip("/").lower()
    return normalized == "product/site" or normalized.startswith("product/site/")


def _runtime_ui_contract_block(surface: dict[str, Any] | None) -> str:
    runtime_features = _surface_runtime_features(surface)
    if not runtime_features:
        return ""
    runtime_api_base = ""
    if isinstance(surface, dict):
        runtime_api_base = str(surface.get("runtime_api_base") or "").strip()
    base = runtime_api_base.rstrip("/")
    lines = [RUNTIME_UI_CONTRACT_INTRO.rstrip(), ""]
    lines.append(f"- Declared runtime-backed features: {', '.join(runtime_features)}")
    if runtime_api_base:
        lines.append(f"- Runtime API base: {runtime_api_base}")
    lines.extend(["", "Selected runtime rails:"])
    for rail in runtime_features:
        spec = PRODUCT_RUNTIME_RAILS.get(rail, {})
        owner = str(spec.get("owner_skill") or "unknown")
        lines.append(f"- {rail} (owner: {owner})")
        endpoints = spec.get("endpoints") or []
        if endpoints and base:
            rendered = ", ".join(f"{method} {base}/{route}" for method, route in endpoints)
            lines.append(f"  - Exact runtime endpoints: {rendered}")
        elif endpoints:
            rendered = ", ".join(f"{method} <runtime_api_base>/{route}" for method, route in endpoints)
            lines.append(f"  - Exact runtime endpoints: {rendered}")
        tools = [str(tool).strip() for tool in spec.get("tools") or [] if str(tool).strip()]
        if tools:
            lines.append(f"  - Canonical tools: {', '.join(tools)}")
        for item in spec.get("worker_contract") or []:
            lines.append(f"  - {str(item).strip()}")
    return "\n".join(lines).strip()


def _find_guidance_skill_file(identifier: str) -> Path | None:
    raw_identifier = str(identifier or "").strip()
    if not raw_identifier:
        return None
    identifier = raw_identifier.lstrip("/")
    identifier_path = Path(raw_identifier).expanduser()
    scan_dirs = get_all_skills_dirs()

    if identifier_path.is_absolute():
        if identifier_path.is_dir():
            candidate = identifier_path / "SKILL.md"
            if candidate.exists():
                return candidate
        elif identifier_path.name == "SKILL.md" and identifier_path.exists():
            return identifier_path

    for skills_dir in scan_dirs:
        direct = skills_dir / identifier / "SKILL.md"
        if direct.exists():
            return direct

    for skills_dir in scan_dirs:
        for candidate in skills_dir.rglob("SKILL.md"):
            try:
                raw = candidate.read_text(encoding="utf-8")
                frontmatter, _ = parse_frontmatter(raw)
            except Exception:
                continue
            name = str(frontmatter.get("name") or candidate.parent.name).strip()
            try:
                rel_dir = str(candidate.parent.relative_to(skills_dir)).replace("\\", "/")
            except ValueError:
                rel_dir = candidate.parent.name
            if identifier in {name, candidate.parent.name, rel_dir}:
                return candidate
    return None


def _normalize_heading_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("`", "").strip()).lower()


def _excerpt_guidance_skill(content: str, *, section_titles: tuple[str, ...], max_chars: int = 12_000) -> str:
    body = str(content or "").strip()
    if not body:
        return ""

    lines = body.splitlines()
    heading_re = re.compile(r"^(#{1,6})\s+(.*)$")
    intro_end = len(lines)
    title_lines: list[str] = []
    intro_lines: list[str] = []
    if lines:
        title_lines.append(lines[0])
        for idx in range(1, len(lines)):
            if heading_re.match(lines[idx]):
                intro_end = idx
                break
            intro_lines.append(lines[idx])

    wanted = {_normalize_heading_text(title) for title in section_titles}
    sections: list[str] = []
    idx = intro_end
    while idx < len(lines):
        match = heading_re.match(lines[idx])
        if not match:
            idx += 1
            continue
        level = len(match.group(1))
        title = match.group(2)
        start = idx
        idx += 1
        while idx < len(lines):
            next_match = heading_re.match(lines[idx])
            if next_match and len(next_match.group(1)) <= level:
                break
            idx += 1
        if _normalize_heading_text(title) in wanted:
            sections.extend(lines[start:idx])
            sections.append("")

    excerpt_parts: list[str] = []
    if title_lines:
        excerpt_parts.extend(title_lines)
    if intro_lines:
        excerpt_parts.append("")
        excerpt_parts.extend(intro_lines)
    if sections:
        excerpt_parts.append("")
        excerpt_parts.extend(sections)
    excerpt = "\n".join(excerpt_parts).strip()
    if not excerpt:
        excerpt = body
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip() + "\n...[truncated]"
    return excerpt


def _compose_worker_guidance_block(skill_identifiers: list[str]) -> tuple[list[str], str]:
    resolved_names: list[str] = []
    blocks: list[str] = []
    for identifier in skill_identifiers:
        skill_file = _find_guidance_skill_file(identifier)
        if skill_file is None:
            raise TakyonError(
                f"guidance skill '{identifier}' was requested for business_claude_agent_task but is not installed"
            )
        raw = skill_file.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(raw)
        skill_name = str(frontmatter.get("name") or skill_file.parent.name).strip() or skill_file.parent.name
        resolved_names.append(skill_name)
        section_titles = _WORKER_GUIDANCE_SKILL_SECTIONS.get(skill_name.lower(), ())
        excerpt = _excerpt_guidance_skill(body, section_titles=section_titles)
        block = (
            f"[Hermes guidance skill: {skill_name}]\n"
            "Follow this guidance when it improves the artifact quality or UX. "
            "Business state, workspace boundaries, runtime truth, and the Hermes no-pretend contract override this guidance if they conflict.\n\n"
            f"{excerpt}"
        )
        blocks.append(block.strip())
    return resolved_names, "\n\n".join(blocks).strip()


def _microusd_to_cents(value: int | float | None) -> int:
    return int(round(float(value or 0) / 10_000))


def _json_default(value: Any) -> Any:
    """Serialize the non-JSON-native scalars the Postgres backend returns. On SQLite every column the
    store reads is TEXT/INTEGER/REAL, so this never fires; on Postgres the operator store reads through
    leaf tables whose ``timestamptz`` columns deserialize to ``datetime`` and whose numeric/aggregate
    columns can deserialize to ``Decimal``. ``datetime`` → ISO-8601 string (matching the string form the
    SQLite trunk stored), ``Decimal`` → ``int`` when integral else ``float`` (the store treats every
    money/usage figure as integer microUSD/cents), ``UUID`` → canonical string, other ``date``/``time``
    objects → ``isoformat``.
    Anything else still raises ``TypeError`` so a genuinely unserializable value fails loud (invariant
    #8: never silently coerce an unexpected type into a fake string)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _normalize_outreach_body(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "\\n" in text or "\\r" in text:
        text = (
            text
            .replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\r", "\n")
        )
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _normalize_destination_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        return ""
    candidate = raw if re.match(r"^https?://", raw, re.IGNORECASE) else f"https://{raw}"
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.netloc.lower()
    if "." not in host:
        return ""
    path = parsed.path or "/"
    return urllib.parse.urlunparse((parsed.scheme, host, path, "", parsed.query, ""))


def _outreach_destination_url(
    *,
    channel: Any,
    provider: Any,
    target: Any,
    destination_url: Any = None,
    metadata: Any = None,
) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    explicit = (
        destination_url
        or metadata.get("destination_url")
        or metadata.get("intended_destination_url")
        or metadata.get("publish_url")
        or metadata.get("submit_url")
    )
    explicit_url = _normalize_destination_url(explicit)
    if explicit_url:
        return explicit_url

    channel_slug = _file_slug(str(channel or ""), "")
    provider_slug = _file_slug(str(provider or ""), "")
    target_text = str(target or "").strip()
    target_url = _normalize_destination_url(target_text)
    if channel_slug in {"show_hn", "hacker_news"} or provider_slug in {"hacker_news", "news_ycombinator"}:
        return "https://news.ycombinator.com/submit"
    if channel_slug == "reddit" or provider_slug == "reddit":
        match = re.match(r"^/?r/([A-Za-z0-9_][A-Za-z0-9_]{1,20})/?$", target_text)
        if match:
            return f"https://www.reddit.com/r/{match.group(1)}/submit/"
        return target_url
    return target_url


def _outreach_artifact_markdown(
    subject: str,
    body: str,
    *,
    destination_url: str = "",
    destination_label: str = "",
) -> str:
    title = subject.strip() or "Outreach"
    cleaned_body = _normalize_outreach_body(body)
    lines = cleaned_body.splitlines()
    if lines and lines[0].strip().casefold() == title.casefold():
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
        cleaned_body = "\n".join(lines).strip()
    destination = destination_label.strip()
    if destination_url:
        destination = f"{destination} — {destination_url}" if destination else destination_url
    if destination:
        return f"# {title}\n\nDestination: {destination}\n\n{cleaned_body}\n"
    return f"# {title}\n\n{cleaned_body}\n"


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


def _node_package_dir(root: Path, package: str) -> Path:
    return root / "node_modules" / Path(*package.split("/"))


def _missing_repo_node_packages(root: Path, packages: Iterable[str]) -> list[str]:
    return [package for package in packages if not _node_package_dir(root, package).exists()]


def _ensure_repo_node_dependencies(packages: Iterable[str]) -> dict[str, Any]:
    """Ensure repo-level Node packages needed by Takyon runtime helpers exist."""
    root = _repo_root()
    package_list = [str(package).strip() for package in packages if str(package).strip()]
    package_json = root / "package.json"
    if not package_json.exists():
        return {
            "success": False,
            "installed": False,
            "root": str(root),
            "missing_packages": package_list,
            "error": f"repo package.json missing: {package_json}",
        }

    before_missing = _missing_repo_node_packages(root, package_list)
    if not before_missing:
        return {
            "success": True,
            "installed": False,
            "root": str(root),
            "missing_packages": [],
            "capabilities": _runtime_capabilities(("node", "npm", "npx")),
        }

    if not _allow_runtime_installs():
        return {
            "success": False,
            "installed": False,
            "root": str(root),
            "missing_packages": before_missing,
            "capabilities": _runtime_capabilities(("node", "npm", "npx")),
            "error": "runtime installs are disabled by config",
        }

    npm = _resolve_runtime_executable("npm")
    ensure_runtime: dict[str, Any] | None = None
    if not npm:
        ensure_runtime = _ensure_javascript_runtime(package_manager=True)
        npm = _resolve_runtime_executable("npm")
    if not npm:
        return {
            "success": False,
            "installed": False,
            "root": str(root),
            "missing_packages": before_missing,
            "ensure_runtime": ensure_runtime,
            "capabilities": _runtime_capabilities(("node", "npm", "npx")),
            "error": "npm is unavailable, so repo Node dependencies cannot be installed",
        }

    lockfile = root / "package-lock.json"
    commands: list[list[str]] = []
    if lockfile.exists():
        commands.append([npm, "ci", "--prefer-offline", "--no-audit"])
    commands.append([npm, "install", "--prefer-offline", "--no-audit"])

    started = _now()
    attempts: list[dict[str, Any]] = []
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                cwd=str(root),
                text=True,
                capture_output=True,
                timeout=420,
                env=_runtime_env(),
            )
            attempts.append({
                "command": " ".join(shlex.quote(part) for part in command),
                "returncode": proc.returncode,
                "stdout": _truncate_text(proc.stdout or "", 4000),
                "stderr": _truncate_text(proc.stderr or "", 4000),
            })
            if proc.returncode == 0:
                break
        except subprocess.TimeoutExpired as exc:
            attempts.append({
                "command": " ".join(shlex.quote(part) for part in command),
                "returncode": None,
                "stdout": _truncate_text(exc.stdout or "", 4000),
                "stderr": _truncate_text(exc.stderr or "", 4000),
                "error": "npm dependency install timed out",
            })
            break

    after_missing = _missing_repo_node_packages(root, package_list)
    return {
        "success": not after_missing,
        "installed": bool(before_missing and not after_missing),
        "root": str(root),
        "missing_packages": after_missing,
        "started_at": started,
        "completed_at": _now(),
        "attempts": attempts,
        "ensure_runtime": ensure_runtime,
        "capabilities": _runtime_capabilities(("node", "npm", "npx")),
        "error": None if not after_missing else "repo Node dependency install did not provide required packages",
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
_PRODUCT_INVENTORY_TEXT_EXTENSIONS = _PRODUCT_SOURCE_EXTENSIONS | {".css", ".json", ".md", ".txt", ".yml", ".yaml"}
_PRODUCT_INVENTORY_MAX_FILES = 120
_PRODUCT_INVENTORY_MAX_BYTES = 96 * 1024
_PRODUCT_INVENTORY_MAX_MARKERS = 40
_PRODUCT_INVENTORY_MAX_SNIPPETS = 24
_PRODUCT_INVENTORY_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("todo", re.compile(r"\bTODO\b|\bFIXME\b", re.IGNORECASE)),
    ("stub_or_mock", re.compile(r"\b(?:stub|mock|fixture|placeholder)\b", re.IGNORECASE)),
    ("demo_or_test_state", re.compile(r"\b(?:demo|test mode|fake|sample data)\b", re.IGNORECASE)),
    ("browser_storage", re.compile(r"\b(?:localStorage|sessionStorage)\b", re.IGNORECASE)),
    ("blocked_or_unwired", re.compile(r"\b(?:not yet wired|not wired|blocked|unavailable|coming soon)\b", re.IGNORECASE)),
    ("auth_or_session", re.compile(r"\b(?:auth|login|sign in|magic[- ]?link|session)\b", re.IGNORECASE)),
    ("billing_or_checkout", re.compile(r"\b(?:stripe|checkout|billing|subscription|pricing)\b", re.IGNORECASE)),
    ("provider_or_compile", re.compile(r"\b(?:compile|pdf|git sync|provider|api key|env var)\b", re.IGNORECASE)),
)
_PRODUCT_CLAIM_SNIPPET_PATTERN = re.compile(
    r"(\$[0-9]|/month|/mo\b|\b(?:free|pro|team|unlimited|included|live|available|yes|pricing|checkout|auth|login|magic[- ]?link|compile|pdf|git sync)\b)",
    re.IGNORECASE,
)
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
# Rail integrations count only when the call targets the canonical, reachable
# runtime base (/api/takyon/apps/<slug>/ or /api/generated-apps/<slug>/). A bare
# rail path such as /auth/request is NOT proxied to the shared runtime, so it is
# unreachable; requiring the canonical prefix here makes the verification gate
# reject drifted/unreachable front-ends and route them back through the normal
# worker retry loop instead of shipping a broken "rail not wired" surface.
_RUNTIME_BASE_PREFIX = r"/api/(?:takyon/apps|generated-apps)/[^'\"`\s)]+/"
_PRODUCT_RUNTIME_INTEGRATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("runtime_api", re.compile(r"/api/takyon/apps/|/api/generated-apps/", re.IGNORECASE)),
    ("auth", re.compile(_RUNTIME_BASE_PREFIX + r"auth/(?:request|verify)\b", re.IGNORECASE)),
    ("session", re.compile(_RUNTIME_BASE_PREFIX + r"session\b", re.IGNORECASE)),
    ("account", re.compile(_RUNTIME_BASE_PREFIX + r"account\b", re.IGNORECASE)),
    ("checkout", re.compile(_RUNTIME_BASE_PREFIX + r"checkout\b", re.IGNORECASE)),
    ("usage", re.compile(_RUNTIME_BASE_PREFIX + r"usage\b", re.IGNORECASE)),
    ("generate", re.compile(_RUNTIME_BASE_PREFIX + r"generate\b", re.IGNORECASE)),
)
_PRODUCT_WORKFLOW_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("form", re.compile(r"<form\b", re.IGNORECASE)),
    ("input", re.compile(r"<(?:input|textarea|select)\b", re.IGNORECASE)),
    ("file_upload", re.compile(r"type\s*=\s*['\"]file['\"]", re.IGNORECASE)),
    ("runtime_fetch", re.compile(r"\bfetch\s*\(", re.IGNORECASE)),
    ("event_handler", re.compile(r"\baddEventListener\s*\(", re.IGNORECASE)),
)
_PRODUCT_DECLARED_ROUTE_PATTERN = re.compile(
    r"""(?:href|to)\s*=\s*["'](?P<html>/[A-Za-z0-9][A-Za-z0-9_/\-.]*)["']|"""
    r"""(?P<quoted>['"]/(?:app|dashboard|workspace|editor|scan|upload|generate|coach|chat|tracker|settings|account|login|checkout|intake|wizard|builder|tool|pricing)(?:/[A-Za-z0-9_/\-.]*)?['"])""",
    re.IGNORECASE,
)
_PRODUCT_LANDING_ONLY_FLAGS = {
    "allow_landing_only",
    "landing_page_only",
    "offer_page_only",
    "validation_landing_page_only",
}
_PRODUCT_AI_SURFACE_PATTERN = re.compile(
    r"\b(?:ai|llm|generate|summari[sz]e|classif(?:y|ier|ication)|analy[sz]e|analysis|assistant|coach|scan|chat)\b",
    re.IGNORECASE,
)
_PRODUCT_AUTH_SURFACE_PATTERN = re.compile(
    r"\b(?:auth|login|log in|sign[- ]?in|magic[- ]?link|session|account|customer|entitlement)\b",
    re.IGNORECASE,
)
_PRODUCT_CHECKOUT_SURFACE_PATTERN = re.compile(
    r"\b(?:checkout|billing|subscription|stripe|upgrade|paid|pro plan)\b",
    re.IGNORECASE,
)
_PRODUCT_WORKFLOW_ROUTE_PATTERN = re.compile(
    r"\b(?:app|dashboard|workspace|editor|scan|upload|generate|coach|chat|tracker|settings|account|login|checkout|intake|wizard|builder|tool)\b",
    re.IGNORECASE,
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


def _route_from_source_path(rel: str) -> tuple[str, str] | None:
    parts = rel.split("/")
    name = parts[-1] if parts else rel
    suffix = Path(name).suffix.lower()
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "api" and name.startswith("route."):
        route = "/" + "/".join(parts[:-1])
        return "api_route", route
    if len(parts) >= 2 and parts[0] == "pages" and parts[1] == "api":
        stem_parts = parts[2:]
        if stem_parts:
            stem_parts[-1] = Path(stem_parts[-1]).stem
        return "api_route", "/" + "/".join(["api", *[part for part in stem_parts if part != "index"]])
    if len(parts) >= 2 and parts[0] == "app" and name.startswith("page."):
        route_parts = [part for part in parts[1:-1] if not part.startswith("(")]
        return "route", "/" + "/".join(route_parts) if route_parts else "/"
    if len(parts) >= 2 and parts[0] == "pages" and suffix in {".js", ".jsx", ".ts", ".tsx"}:
        route_parts = parts[1:]
        route_parts[-1] = Path(route_parts[-1]).stem
        route_parts = [part for part in route_parts if part != "index"]
        return "route", "/" + "/".join(route_parts) if route_parts else "/"
    if suffix == ".html":
        route_parts = parts[:]
        route_parts[-1] = Path(route_parts[-1]).stem
        route_parts = [part for part in route_parts if part != "index"]
        return "route", "/" + "/".join(route_parts) if route_parts else "/"
    return None


def _bounded_product_inventory(business_root: Path, source_path: str, *, surface: dict[str, Any] | None = None) -> dict[str, Any]:
    source_rel = _safe_relpath(source_path or "product/site", field="source_path").as_posix()
    root = (business_root / source_rel).resolve()
    inventory: dict[str, Any] = {
        "status": "missing",
        "source_path": source_rel,
        "generated_at": _now(),
        "routes": [],
        "declared_routes": [],
        "api_routes": [],
        "package": {},
        "runtime_integrations": [],
        "workflow_markers": [],
        "risk_markers": [],
        "claim_snippets": [],
        "pretend_findings": [],
        "files_scanned": 0,
        "files_skipped": 0,
        "public_url": "",
        "publish_receipt_path": "",
        "scanner_error": "",
    }
    if isinstance(surface, dict):
        inventory["public_url"] = str(surface.get("public_url") or "")
        inventory["publish_receipt_path"] = str(surface.get("publish_receipt_path") or "")
    if business_root.resolve() not in (root, *root.parents):
        inventory.update({"status": "unavailable", "scanner_error": "source path escaped business root"})
        return inventory
    if not root.exists() or not root.is_dir():
        return inventory

    routes: set[str] = set()
    declared_routes: set[str] = set()
    api_routes: set[str] = set()
    runtime_integrations: set[str] = set()
    workflow_markers: set[str] = set()
    risk_markers: list[dict[str, Any]] = []
    claim_snippets: list[dict[str, Any]] = []
    files_scanned = 0
    files_skipped = 0
    partial = False

    for path in sorted(root.rglob("*")):
        if not path.is_file() or _product_source_is_skipped(path):
            continue
        if files_scanned >= _PRODUCT_INVENTORY_MAX_FILES:
            partial = True
            files_skipped += 1
            continue
        rel = path.relative_to(root).as_posix()
        route = _route_from_source_path(rel)
        if route:
            kind, value = route
            if kind == "api_route":
                api_routes.add(value)
            else:
                routes.add(value)
        if path.name == "package.json":
            try:
                package_data = json.loads(path.read_text(encoding="utf-8")[:_PRODUCT_INVENTORY_MAX_BYTES])
                scripts = package_data.get("scripts") if isinstance(package_data.get("scripts"), dict) else {}
                deps = {
                    **(package_data.get("dependencies") if isinstance(package_data.get("dependencies"), dict) else {}),
                    **(package_data.get("devDependencies") if isinstance(package_data.get("devDependencies"), dict) else {}),
                }
                inventory["package"] = {
                    "name": str(package_data.get("name") or ""),
                    "package_manager": str(package_data.get("packageManager") or ""),
                    "scripts": sorted(str(key) for key in scripts.keys())[:20],
                    "frameworks": [name for name in ("next", "react", "vite", "vue", "svelte") if name in deps],
                }
            except Exception as exc:
                risk_markers.append({"path": rel, "issue": "package_unreadable", "snippet": _truncate_text(str(exc), 160)})
            files_scanned += 1
            continue
        if path.suffix.lower() not in _PRODUCT_INVENTORY_TEXT_EXTENSIONS:
            files_skipped += 1
            continue
        try:
            if path.stat().st_size > _PRODUCT_INVENTORY_MAX_BYTES:
                files_skipped += 1
                partial = True
                continue
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            files_skipped += 1
            continue
        except Exception as exc:
            risk_markers.append({"path": rel, "issue": "file_unreadable", "snippet": _truncate_text(str(exc), 160)})
            files_skipped += 1
            continue
        files_scanned += 1
        for number, line in enumerate(text.splitlines(), start=1):
            for route_match in _PRODUCT_DECLARED_ROUTE_PATTERN.finditer(line):
                route_value = (route_match.group("html") or route_match.group("quoted") or "").strip("'\"")
                if route_value and not route_value.startswith("/api/") and route_value != "/":
                    declared_routes.add(route_value.rstrip("/") or "/")
            for label, pattern in _PRODUCT_RUNTIME_INTEGRATION_PATTERNS:
                if pattern.search(line):
                    runtime_integrations.add(label)
            for label, pattern in _PRODUCT_WORKFLOW_MARKERS:
                if pattern.search(line):
                    workflow_markers.add(label)
            if len(risk_markers) < _PRODUCT_INVENTORY_MAX_MARKERS:
                for label, pattern in _PRODUCT_INVENTORY_MARKERS:
                    if pattern.search(line):
                        risk_markers.append({
                            "path": rel,
                            "line": number,
                            "issue": label,
                            "snippet": line.strip()[:220],
                        })
                        break
            if len(claim_snippets) < _PRODUCT_INVENTORY_MAX_SNIPPETS and _PRODUCT_CLAIM_SNIPPET_PATTERN.search(line):
                clean = line.strip()
                if clean:
                    claim_snippets.append({"path": rel, "line": number, "snippet": clean[:220]})

    try:
        inventory["pretend_findings"] = _scan_for_pretend_product_state(root, limit=12)
    except Exception as exc:
        risk_markers.append({"path": source_rel, "issue": "pretend_scan_unavailable", "snippet": _truncate_text(str(exc), 160)})

    inventory.update({
        "status": "partial" if partial else "collected",
        "routes": sorted(routes),
        "declared_routes": sorted(declared_routes),
        "api_routes": sorted(api_routes),
        "runtime_integrations": sorted(runtime_integrations),
        "workflow_markers": sorted(workflow_markers),
        "risk_markers": risk_markers,
        "claim_snippets": claim_snippets,
        "files_scanned": files_scanned,
        "files_skipped": files_skipped,
    })
    return inventory


def _product_inventory(business_root: Path, source_path: str, *, surface: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return _bounded_product_inventory(business_root, source_path, surface=surface)
    except Exception as exc:
        source_text = str(source_path or "product/site")
        return {
            "status": "unavailable",
            "source_path": source_text,
            "generated_at": _now(),
            "routes": [],
            "declared_routes": [],
            "api_routes": [],
            "package": {},
            "runtime_integrations": [],
            "workflow_markers": [],
            "risk_markers": [],
            "claim_snippets": [],
            "pretend_findings": [],
            "files_scanned": 0,
            "files_skipped": 0,
            "public_url": str((surface or {}).get("public_url") or "") if isinstance(surface, dict) else "",
            "publish_receipt_path": str((surface or {}).get("publish_receipt_path") or "") if isinstance(surface, dict) else "",
            "scanner_error": _truncate_text(str(exc), 500),
        }


def _surface_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _surface_allows_landing_only(surface: dict[str, Any] | None) -> bool:
    if not isinstance(surface, dict):
        return False
    for container_name in ("metadata", "constraints", "theme"):
        container = surface.get(container_name)
        if isinstance(container, dict):
            for key in _PRODUCT_LANDING_ONLY_FLAGS:
                if key in container and _surface_bool(container.get(key)):
                    return True
    for key in _PRODUCT_LANDING_ONLY_FLAGS:
        if key in surface and _surface_bool(surface.get(key)):
            return True
    return False


def _surface_routes(surface: dict[str, Any] | None) -> list[str]:
    if not isinstance(surface, dict):
        return []
    values = surface.get("routes")
    routes: list[str] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str):
                path = item
            elif isinstance(item, dict):
                path = str(item.get("path") or "")
            else:
                path = ""
            path = path.strip()
            if path:
                routes.append(path.rstrip("/") or "/")
    return routes


def _surface_text(surface: dict[str, Any] | None) -> str:
    if not isinstance(surface, dict):
        return ""
    parts: list[str] = []
    for key in ("status", "runtime_api_base", "notes", "publish_policy", "done_gate"):
        value = surface.get(key)
        if value:
            parts.append(str(value))
    for item in surface.get("routes") or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for key in ("path", "label", "description", "purpose", "type"):
                if item.get(key):
                    parts.append(str(item.get(key)))
    for key in ("constraints", "metadata"):
        value = surface.get(key)
        if isinstance(value, dict):
            parts.extend(f"{nested_key}={nested_value}" for nested_key, nested_value in value.items())
    return "\n".join(parts)


def _surface_contract_kind(surface: dict[str, Any] | None) -> dict[str, bool]:
    text = _surface_text(surface)
    routes = _surface_routes(surface)
    route_text = "\n".join(route for route in routes if route != "/")
    has_workflow_route = any(_PRODUCT_WORKFLOW_ROUTE_PATTERN.search(route) for route in routes if route != "/")
    return {
        "landing_only": _surface_allows_landing_only(surface),
        "app_like": has_workflow_route
        or _PRODUCT_AI_SURFACE_PATTERN.search(text) is not None
        or _PRODUCT_AUTH_SURFACE_PATTERN.search(text) is not None
        or _PRODUCT_CHECKOUT_SURFACE_PATTERN.search(text) is not None
        or _PRODUCT_WORKFLOW_ROUTE_PATTERN.search(route_text) is not None,
        "ai": _PRODUCT_AI_SURFACE_PATTERN.search(text) is not None,
        "auth": _PRODUCT_AUTH_SURFACE_PATTERN.search(text) is not None,
        "checkout": _PRODUCT_CHECKOUT_SURFACE_PATTERN.search(text) is not None,
    }


def _validate_product_surface_contract(
    inventory: dict[str, Any],
    surface: dict[str, Any] | None,
) -> tuple[bool, str]:
    kind = _surface_contract_kind(surface)
    if not kind["app_like"] or kind["landing_only"]:
        return True, ""

    source_routes = set(str(route) for route in inventory.get("routes") or [])
    declared_routes = set(str(route) for route in inventory.get("declared_routes") or [])
    contract_routes = set(_surface_routes(surface))
    source_app_routes = {
        route
        for route in source_routes
        if route != "/" and _PRODUCT_WORKFLOW_ROUTE_PATTERN.search(route)
    }
    declared_app_routes = {
        route
        for route in (declared_routes | contract_routes)
        if route != "/" and _PRODUCT_WORKFLOW_ROUTE_PATTERN.search(route)
    }
    if "/" not in source_routes:
        return False, "app-like product surface must include a homepage route at /"
    if not source_app_routes:
        if declared_app_routes:
            claimed = ", ".join(sorted(declared_app_routes)[:3])
            return False, f"app-like product surface claims {claimed} but generated source does not include a working app subroute; add the route or mark the surface landing_page_only"
        return False, "app-like product surface must include a working app subroute such as /app, or mark the surface landing_page_only"

    workflow_markers = set(str(marker) for marker in inventory.get("workflow_markers") or [])
    if not ({"form", "input"} & workflow_markers) and "runtime_fetch" not in workflow_markers:
        return False, "app-like product surface must contain a real product workflow, not only marketing sections"

    integrations = set(str(item) for item in inventory.get("runtime_integrations") or [])
    base_hint = str((surface or {}).get("runtime_api_base") or "/api/takyon/apps/<business>").rstrip("/") if isinstance(surface, dict) else "/api/takyon/apps/<business>"
    if kind["ai"]:
        if "generate" not in integrations:
            return False, f"AI-backed product surface must call the canonical runtime route {base_hint}/generate verbatim (a bare /generate path is not proxied to the runtime and will 404)"
        if not ({"auth", "session", "account"} & integrations):
            return False, f"AI-backed product surface must call the canonical app-session route {base_hint}/session or {base_hint}/auth/request used by generate (use the full runtime base prefix, not a bare path)"
    if kind["auth"] and not ({"auth", "session", "account"} & integrations):
        return False, f"auth/session product surface must call the canonical runtime routes {base_hint}/auth/request and {base_hint}/session verbatim (a bare /auth or /session path is not proxied to the runtime and will 404)"
    if kind["checkout"] and "checkout" not in integrations:
        return False, f"checkout/billing product surface must call the canonical runtime route {base_hint}/checkout verbatim or mark checkout unavailable (a bare /checkout path is not proxied to the runtime and will 404)"
    return True, ""


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


def _repair_nested_workspace_prefix(workspace_path: Path, workspace: str) -> dict[str, Any]:
    """Move files up when a worker writes product/site inside product/site."""
    rel = _safe_relpath(workspace or ".", field="workspace")
    rel_text = rel.as_posix()
    if rel_text in {".", ""}:
        return {"repaired": False}
    nested = workspace_path / rel
    if not nested.is_dir() or not _product_source_files(nested, limit=1):
        return {"repaired": False}
    root_files = [
        item for item in _product_source_files(workspace_path, limit=20)
        if item != rel_text and not item.startswith(f"{rel_text}/")
    ]
    if root_files:
        return {
            "repaired": False,
            "blocked": True,
            "reason": "workspace root already contains source files outside duplicate prefix",
            "root_files": root_files[:10],
            "nested_source_path": f"{rel_text}/{rel_text}",
        }
    moved: list[str] = []
    for child in sorted(nested.iterdir(), key=lambda item: item.name):
        destination = workspace_path / child.name
        if destination.exists():
            return {
                "repaired": False,
                "blocked": True,
                "reason": f"destination already exists: {child.name}",
                "moved": moved,
                "nested_source_path": f"{rel_text}/{rel_text}",
            }
        shutil.move(str(child), str(destination))
        moved.append(child.name)
    cursor = nested
    while cursor != workspace_path and cursor.is_dir():
        try:
            cursor.rmdir()
        except OSError:
            break
        cursor = cursor.parent
    return {
        "repaired": True,
        "moved": moved,
        "nested_source_path": f"{rel_text}/{rel_text}",
        "source_path": rel_text,
    }


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


def _javascript_install_env() -> dict[str, str]:
    env = _runtime_env({"NODE_ENV": "development", "NPM_CONFIG_PRODUCTION": "false"})
    env.pop("npm_config_production", None)
    return env


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
    surface: dict[str, Any] | None = None,
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
        "inventory": _product_inventory(business_root, source_rel, surface=surface),
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
        static_publish_source, _static_publish_label = _product_static_publish_source(root)
        if static_publish_source is not None:
            result.update({"status": "passed", "kind": "static_source_present"})
            return result
        result.update({"status": "passed", "kind": "source_present"})
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
    looks_next = (
        "next" in deps
        or any((root / name).exists() for name in ("next.config.js", "next.config.mjs", "next.config.ts"))
        or (root / ".next").is_dir()
    )
    static_publish_source, _static_publish_label = _product_static_publish_source(root)
    if static_publish_source is not None and not looks_next:
        result.update({"status": "passed", "kind": "static_source_present"})
        return result
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
            install_check = _run_verification_command(
                _javascript_install_command(package_manager),
                cwd=root,
                timeout_seconds=timeout_seconds,
                env=_javascript_install_env(),
            )
            result["checks"].append(install_check)
            if install_check["status"] != "passed":
                result.update({"status": "failed", "error": "dependency install failed"})
                return result
        else:
            result["warnings"].append("dependency install skipped because no package manager is available; using existing node_modules")
    if "build" not in scripts:
        result.update({"status": "passed", "kind": "source_present"})
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
    ("audit/test record", ("audit record", "audit", "receipt", "test record", "test_record", "job id", "agent record")),
    ("remaining blocker", ("remaining blocker", "blocker", "blocked", "not wired")),
)

_TAKYON_PATH_EXACT_ALIASES = {
    "brain/index.md": "research/index.md",
    "brain/business-model.md": "research/strategy.md",
    "brain/pulse.md": "metrics/summary.md",
    "brain/wake_journal.md": "metrics/wake-history.md",
    "app/index.md": "product/runtime.md",
    "app/surface.md": "product/surface.md",
    "app/plans.md": "product/plans.md",
    "app/customers.md": "product/customers.md",
    "app/billing.md": "product/billing.md",
    "app/usage.md": "product/usage.md",
    "conversations/index.md": "metrics/conversations/index.md",
    "conversations/corpus/messages.jsonl": "metrics/conversations/corpus/messages.jsonl",
    "conversations/corpus/events.jsonl": "metrics/conversations/corpus/events.jsonl",
}
_TAKYON_PATH_PREFIX_ALIASES = (
    ("brain/", "research/"),
    ("app/", "product/"),
    ("conversations/", "metrics/conversations/"),
    ("receipts/", "metrics/receipts/"),
    ("sales/", "distribution/"),
    ("campaigns/", "distribution/campaigns/"),
    ("outreach/", "distribution/outreach/"),
)


def _canonical_business_relpath(rel: str) -> str:
    normalized = _safe_relpath(rel, field="business path").as_posix()
    exact = _TAKYON_PATH_EXACT_ALIASES.get(normalized)
    if exact:
        return exact
    for old_prefix, new_prefix in _TAKYON_PATH_PREFIX_ALIASES:
        if normalized.startswith(old_prefix):
            return new_prefix + normalized[len(old_prefix):]
    return normalized


def _validate_brain_index_completion_gate(rel: str, content: str) -> None:
    if rel != "research/index.md":
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
            "research/index.md cannot claim complete/built/done work without a feature evidence ledger. "
            "For each feature list source files, runtime/tool endpoint used, audit/test record, "
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


def _product_publish_target(slug: str, explicit: Any = None) -> str:
    raw = str(explicit or "").strip()
    if not raw:
        return f"https://{_business_domain_candidates(slug)[0]}/"
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TakyonError(f"publish_target must be an http(s) URL or domain: {explicit!r}")
    host = _normalize_domain_name(parsed.netloc, field="publish_target")
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return urllib.parse.urlunparse((parsed.scheme, host, path, "", "", ""))


def _is_shared_renderer_publish_policy(value: Any) -> bool:
    return str(value or "").strip().lower() in _SHARED_RENDERER_PUBLISH_POLICIES


def _product_publish_root() -> Path | None:
    load_takyon_env()
    raw = (
        os.getenv("TAKYON_PRODUCT_SITE_ROOT", "").strip()
        or os.getenv("PUBLIC_COMPANY_SITE_ROOT", "").strip()
        or os.getenv("TAKYON_STATIC_SITE_ROOT", "").strip()
    )
    return Path(raw).expanduser().resolve() if raw else get_takyon_home() / "product-sites"


def _product_local_public_url(slug: str) -> str:
    load_takyon_env()
    raw = os.getenv("TAKYON_PRODUCT_LOCAL_BASE_URL", "").strip()
    if not raw:
        return ""
    return f"{raw.rstrip('/')}/{_slugify(slug)}/"


def _product_static_publish_source(source_root: Path) -> tuple[Path | None, str]:
    candidates = [
        ("source", source_root),
        ("dist", source_root / "dist"),
        ("out", source_root / "out"),
        ("build", source_root / "build"),
        ("public", source_root / "public"),
    ]
    for label, candidate in candidates:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate.resolve(), label
    return None, ""


def _product_service_name(slug: str) -> str:
    return f"takyon-product-{_slugify(slug)}"


def _product_service_systemd_dir() -> Path:
    raw = os.getenv("TAKYON_PRODUCT_SYSTEMD_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path("/etc/systemd/system")


def _product_service_caddyfile() -> Path:
    raw = os.getenv("TAKYON_PRODUCT_CADDYFILE", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path("/etc/caddy/Caddyfile")


def _product_deploy_dry_run() -> bool:
    return str(os.getenv("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "")).strip().lower() in {"1", "true", "yes", "on"}


def _product_public_probe_enabled() -> bool:
    return str(os.getenv("TAKYON_PRODUCT_SKIP_PUBLIC_PROBE", "")).strip().lower() not in {"1", "true", "yes", "on"}


def _read_product_package(source_root: Path) -> tuple[dict[str, Any] | None, str]:
    package_json = source_root / "package.json"
    if not package_json.exists():
        return None, "package.json is missing"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"package.json is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "package.json must be an object"
    return data, ""


def _product_next_service_metadata(source_root: Path) -> tuple[dict[str, Any] | None, str]:
    package_data, error = _read_product_package(source_root)
    if package_data is None:
        return None, error
    scripts = package_data.get("scripts") if isinstance(package_data.get("scripts"), dict) else {}
    dependencies = package_data.get("dependencies") if isinstance(package_data.get("dependencies"), dict) else {}
    dev_dependencies = package_data.get("devDependencies") if isinstance(package_data.get("devDependencies"), dict) else {}
    deps = {**dependencies, **dev_dependencies}
    looks_next = (
        "next" in deps
        or (source_root / ".next").is_dir()
        or any((source_root / name).exists() for name in ("next.config.js", "next.config.mjs", "next.config.ts"))
    )
    if not looks_next:
        return None, "product source is not a Next.js app and no static publish directory exists"
    if "start" not in scripts:
        return None, "Next.js product source has no package.json start script for production serving"
    if not (source_root / ".next").exists():
        return None, "Next.js build output .next is missing after verification"
    manager_name = _javascript_package_manager_name(source_root, package_data)
    manager = _javascript_package_manager_command(manager_name)
    start_command = _javascript_run_script_command(manager, "start", root=source_root)
    if not start_command:
        return None, f"no available runtime command for {manager_name} start script"
    return {
        "kind": "next_systemd_caddy",
        "package_manager": manager_name,
        "start_command": [*start_command, "--", "-H", "127.0.0.1", "-p"],
    }, ""


def _extract_reverse_proxy_ports(text: str) -> set[int]:
    ports: set[int] = set()
    for match in re.finditer(r"\b127\.0\.0\.1:(\d{2,5})\b", text):
        try:
            ports.add(int(match.group(1)))
        except ValueError:
            continue
    return ports


def _existing_product_service_port(slug: str, *, systemd_dir: Path | None = None, caddyfile: Path | None = None) -> int | None:
    service_file = (systemd_dir or _product_service_systemd_dir()) / f"{_product_service_name(slug)}.service"
    if service_file.exists():
        try:
            text = service_file.read_text(encoding="utf-8")
        except OSError:
            text = ""
        match = re.search(r"\bPORT=(\d{2,5})\b|\s-p\s+(\d{2,5})\b", text)
        if match:
            return int(next(group for group in match.groups() if group))
    host = urllib.parse.urlparse(_product_publish_target(slug)).netloc
    caddy_path = caddyfile or _product_service_caddyfile()
    if caddy_path.exists():
        try:
            text = caddy_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        pattern = re.compile(rf"(?ms)^{re.escape(host)}\s*\{{(?P<body>.*?)^\}}\s*")
        match = pattern.search(text)
        if match:
            ports = _extract_reverse_proxy_ports(match.group("body"))
            if ports:
                return sorted(ports)[0]
    return None


def _product_service_port(slug: str, *, systemd_dir: Path | None = None, caddyfile: Path | None = None) -> int:
    business = _slugify(slug)
    env_key = f"TAKYON_PRODUCT_PORT_{re.sub(r'[^A-Z0-9]', '_', business.upper())}"
    configured = os.getenv(env_key, "").strip() or os.getenv("TAKYON_PRODUCT_PORT", "").strip()
    if configured:
        port = int(configured)
        if not (_PRODUCT_SERVICE_PORT_MIN <= port <= 65535):
            raise TakyonError(f"{env_key} must be a TCP port >= {_PRODUCT_SERVICE_PORT_MIN}")
        return port
    existing = _existing_product_service_port(business, systemd_dir=systemd_dir, caddyfile=caddyfile)
    if existing:
        return existing
    caddy_path = caddyfile or _product_service_caddyfile()
    used: set[int] = set()
    if caddy_path.exists():
        try:
            used.update(_extract_reverse_proxy_ports(caddy_path.read_text(encoding="utf-8")))
        except OSError:
            pass
    service_dir = systemd_dir or _product_service_systemd_dir()
    if service_dir.exists():
        for service_file in service_dir.glob("takyon-product-*.service"):
            if service_file.name == f"{_product_service_name(business)}.service":
                continue
            try:
                used.update(_extract_reverse_proxy_ports(service_file.read_text(encoding="utf-8")))
            except OSError:
                continue
    spread = _PRODUCT_SERVICE_PORT_MAX - _PRODUCT_SERVICE_PORT_MIN + 1
    start = _PRODUCT_SERVICE_PORT_MIN + (int(hashlib.sha256(business.encode("utf-8")).hexdigest()[:8], 16) % spread)
    for offset in range(spread):
        port = _PRODUCT_SERVICE_PORT_MIN + ((start - _PRODUCT_SERVICE_PORT_MIN + offset) % spread)
        if port not in used:
            return port
    raise TakyonError("no free Takyon product service port is available")


def _run_product_admin_command(command: list[str], *, timeout_seconds: int = 30) -> tuple[bool, str]:
    if _product_deploy_dry_run():
        return True, ""
    try:
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=_runtime_env(),
        )
    except Exception as exc:
        return False, str(exc)
    output = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
    return proc.returncode == 0, _truncate_text(output, 4000)


def _write_product_service_file(*, slug: str, source_root: Path, port: int, metadata: dict[str, Any]) -> tuple[Path | None, str]:
    systemd_dir = _product_service_systemd_dir()
    service_name = _product_service_name(slug)
    if not _product_deploy_dry_run():
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return None, "systemctl is unavailable; cannot install product service"
        if not systemd_dir.exists():
            return None, f"systemd unit directory does not exist: {systemd_dir}"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    start_command = [*list(metadata.get("start_command") or []), str(port)]
    if not start_command:
        return None, "missing product service start command"
    path_value = _runtime_env().get("PATH", os.getenv("PATH", ""))
    command_line = shlex.join(start_command)
    unit = "\n".join(
        [
            "[Unit]",
            f"Description=Takyon Product - {_slugify(slug)}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={source_root}",
            "Environment=NODE_ENV=production",
            f"Environment=PORT={port}",
            "Environment=HOSTNAME=127.0.0.1",
            f"Environment=PATH={path_value}",
            f"ExecStart=/bin/bash -lc {shlex.quote('exec ' + command_line)}",
            "Restart=always",
            "RestartSec=3",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    service_file = systemd_dir / f"{service_name}.service"
    service_file.write_text(unit, encoding="utf-8")
    if _product_deploy_dry_run():
        return service_file, ""
    systemctl = shutil.which("systemctl") or "systemctl"
    for command in (
        [systemctl, "daemon-reload"],
        [systemctl, "enable", "--now", service_name],
        [systemctl, "restart", service_name],
    ):
        ok, output = _run_product_admin_command(command, timeout_seconds=60)
        if not ok:
            return service_file, f"{' '.join(command)} failed: {output}"
    ok, output = _run_product_admin_command([systemctl, "is-active", service_name], timeout_seconds=20)
    if not ok:
        return service_file, f"{service_name} is not active: {output}"
    return service_file, ""


def _ensure_product_caddy_route(*, slug: str, publish_target: str, port: int) -> tuple[Path | None, str]:
    caddyfile = _product_service_caddyfile()
    host = urllib.parse.urlparse(publish_target).netloc
    if not host:
        return None, "publish target has no host"
    if not _product_deploy_dry_run():
        if not caddyfile.exists():
            return None, f"Caddyfile does not exist: {caddyfile}"
        if not shutil.which("caddy"):
            return None, "caddy is unavailable; cannot validate product route"
    caddyfile.parent.mkdir(parents=True, exist_ok=True)
    existing = caddyfile.read_text(encoding="utf-8") if caddyfile.exists() else ""
    # Reserve the whole /api/* namespace on the product host for the shared
    # Hermes app runtime. The hostname identifies the business, so the runtime
    # resolves any rail call (auth/session/account/checkout/usage/generate) to
    # this host's business regardless of the exact path the generated
    # front-end used. This removes the recurring "rail not wired" 404 when a
    # site calls /api/auth/request instead of /api/takyon/apps/<slug>/...; a
    # static product export never serves real pages under /api/.
    block = (
        f"{host} {{\n"
        "    @takyon_app_runtime path /api/*\n"
        "    handle @takyon_app_runtime {\n"
        "        reverse_proxy 127.0.0.1:9119 {\n"
        "            header_up Host {host}\n"
        "            header_up X-Forwarded-Proto https\n"
        "        }\n"
        "    }\n"
        "    handle {\n"
        f"        reverse_proxy 127.0.0.1:{port}\n"
        "    }\n"
        "}\n"
    )
    pattern = re.compile(rf"(?ms)^{re.escape(host)}\s*\{{.*?^\}}\s*")
    if pattern.search(existing):
        updated = pattern.sub(block + "\n", existing).rstrip() + "\n"
    else:
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    if updated != existing:
        if caddyfile.exists():
            backup = caddyfile.with_name(f"{caddyfile.name}.takyon-backup")
            backup.write_text(existing, encoding="utf-8")
        caddyfile.write_text(updated, encoding="utf-8")
    if _product_deploy_dry_run():
        return caddyfile, ""
    caddy = shutil.which("caddy") or "caddy"
    ok, output = _run_product_admin_command([caddy, "validate", "--config", str(caddyfile)], timeout_seconds=30)
    if not ok:
        return caddyfile, f"caddy validate failed: {output}"
    systemctl = shutil.which("systemctl") or "systemctl"
    ok, output = _run_product_admin_command([systemctl, "reload", "caddy"], timeout_seconds=30)
    if not ok:
        return caddyfile, f"systemctl reload caddy failed: {output}"
    return caddyfile, ""


def _ensure_product_static_caddy_route(*, slug: str, publish_target: str, static_root: Path) -> tuple[Path | None, str]:
    if not os.getenv("TAKYON_PRODUCT_CADDYFILE", "").strip() and not _product_deploy_dry_run():
        return None, ""
    caddyfile = _product_service_caddyfile()
    host = urllib.parse.urlparse(publish_target).netloc
    if not host:
        return None, "publish target has no host"
    if not _product_deploy_dry_run():
        if not caddyfile.exists():
            return None, ""
        if not shutil.which("caddy"):
            return None, "caddy is unavailable; cannot validate product static route"
    caddyfile.parent.mkdir(parents=True, exist_ok=True)
    existing = caddyfile.read_text(encoding="utf-8") if caddyfile.exists() else ""
    block = (
        f"{host} {{\n"
        "    @takyon_app_runtime path /api/*\n"
        "    handle @takyon_app_runtime {\n"
        "        reverse_proxy 127.0.0.1:9119 {\n"
        "            header_up Host {host}\n"
        "            header_up X-Forwarded-Proto https\n"
        "        }\n"
        "    }\n"
        "    handle {\n"
        f"        root * {static_root}\n"
        "        try_files {path} {path}/ /index.html\n"
        "        file_server\n"
        "    }\n"
        "}\n"
    )
    pattern = re.compile(rf"(?ms)^{re.escape(host)}\s*\{{.*?^\}}\s*")
    if pattern.search(existing):
        updated = pattern.sub(block + "\n", existing).rstrip() + "\n"
    else:
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    if updated != existing:
        if caddyfile.exists():
            backup = caddyfile.with_name(f"{caddyfile.name}.takyon-backup")
            backup.write_text(existing, encoding="utf-8")
        caddyfile.write_text(updated, encoding="utf-8")
    if _product_deploy_dry_run():
        return caddyfile, ""
    caddy = shutil.which("caddy") or "caddy"
    ok, output = _run_product_admin_command([caddy, "validate", "--config", str(caddyfile)], timeout_seconds=30)
    if not ok:
        return caddyfile, f"caddy validate failed: {output}"
    systemctl = shutil.which("systemctl") or "systemctl"
    ok, output = _run_product_admin_command([systemctl, "reload", "caddy"], timeout_seconds=30)
    if not ok:
        return caddyfile, f"systemctl reload caddy failed: {output}"
    return caddyfile, ""


def _make_static_publish_tree_readable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            if path.is_dir():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(0o644)
        except OSError:
            continue


def _probe_product_public_url(url: str) -> tuple[bool, str]:
    if _product_deploy_dry_run() or not _product_public_probe_enabled():
        return True, ""
    last_error = ""
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Takyon product publish verifier"})
            with urllib.request.urlopen(request, timeout=12) as response:
                status = int(getattr(response, "status", 0) or 0)
                if 200 <= status < 500:
                    return True, ""
                last_error = f"HTTP {status}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < 2:
            time.sleep(2)
    return False, f"public URL probe failed for {url}: {last_error}"


def _publish_next_product_service(*, source_root: Path, slug: str, publish_target: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "blocked",
        "publish_target": publish_target,
        "public_url": "",
        "published_at": "",
        "publish_root": "",
        "publish_source_path": "",
        "blocker": "",
        "deploy_kind": "next_systemd_caddy",
        "service_name": _product_service_name(slug),
    }
    metadata, blocker = _product_next_service_metadata(source_root)
    if metadata is None:
        result["blocker"] = (
            "product surface source exists, but no static publish directory with index.html exists; "
            f"{blocker}; provide source/index.html, dist/index.html, out/index.html, or a supported Next.js service app"
        )
        return result
    try:
        port = _product_service_port(slug)
    except Exception as exc:
        result["blocker"] = str(exc)
        return result
    service_file, blocker = _write_product_service_file(slug=slug, source_root=source_root, port=port, metadata=metadata)
    result.update({"port": port, "service_file": str(service_file or "")})
    if blocker:
        result["blocker"] = blocker
        return result
    caddyfile, blocker = _ensure_product_caddy_route(slug=slug, publish_target=publish_target, port=port)
    result["caddyfile"] = str(caddyfile or "")
    if blocker:
        result["blocker"] = blocker
        return result
    ok, blocker = _probe_product_public_url(publish_target)
    if not ok:
        result["blocker"] = blocker
        return result
    result.update(
        {
            "status": "published",
            "public_url": publish_target,
            "published_at": _now(),
            "publish_root": str(source_root),
            "publish_source_path": str(source_root.name),
            "blocker": "",
        }
    )
    if _product_deploy_dry_run():
        result["dry_run"] = True
    return result


def _canonical_product_url(store: "TakyonStore", conn: sqlite3.Connection, business: str) -> str:
    surface = store._app_surface_contract(conn, business)
    return str(surface.get("public_url") or surface.get("publish_target") or _product_publish_target(business)).strip()


def _canonicalize_business_product_links(body: str, *, business: str, canonical_url: str) -> tuple[str, list[dict[str, str]]]:
    canonical = canonical_url.strip() or _product_publish_target(business)
    canonical_base = canonical.rstrip("/")
    replacements: list[dict[str, str]] = []
    business_re = re.escape(_slugify(business))
    pattern = re.compile(
        rf"(?<![\w.-])(?P<url>(?:https?://)?(?:www\.)?{business_re}\.(?:io|com|co|app|dev)(?P<path>/[^\s)\]]*)?)",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        old = match.group("url")
        path = match.group("path") or ""
        new = canonical if not path or canonical.endswith(path) else f"{canonical_base}{path}"
        if old != new:
            replacements.append({"from": old, "to": new})
        return new

    return pattern.sub(replace, body), replacements


def _publish_product_surface_path(
    *,
    business_root: Path,
    slug: str,
    source_path: str,
    publish_target: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "blocked",
        "publish_target": publish_target,
        "public_url": "",
        "published_at": "",
        "publish_root": "",
        "publish_source_path": "",
        "blocker": "",
    }
    rel = _safe_relpath(source_path or "product/site", field="source_path").as_posix()
    source_root = (business_root / rel).resolve()
    if business_root.resolve() not in (source_root, *source_root.parents):
        result["blocker"] = "source path escaped business root"
        return result
    publish_source, publish_source_label = _product_static_publish_source(source_root)
    if publish_source is None:
        service_result = _publish_next_product_service(
            source_root=source_root,
            slug=slug,
            publish_target=publish_target,
        )
        if service_result.get("publish_source_path") == source_root.name:
            service_result["publish_source_path"] = rel
        return service_result

    next_service_metadata, _next_service_blocker = _product_next_service_metadata(source_root)
    if next_service_metadata is not None:
        service_result = _publish_next_product_service(
            source_root=source_root,
            slug=slug,
            publish_target=publish_target,
        )
        if service_result.get("publish_source_path") == source_root.name:
            service_result["publish_source_path"] = rel
        if service_result.get("status") == "published":
            return service_result

    publish_root = _product_publish_root()
    if publish_root is None:
        service_result = _publish_next_product_service(
            source_root=source_root,
            slug=slug,
            publish_target=publish_target,
        )
        if service_result.get("publish_source_path") == source_root.name:
            service_result["publish_source_path"] = rel
        if service_result.get("status") == "published":
            return service_result
        result["blocker"] = (
            "product surface has static output, but no static hosting root is configured; "
            "set TAKYON_PRODUCT_SITE_ROOT to the directory served for business subdomains, "
            f"or fix the service deploy rail: {service_result.get('blocker') or 'unknown service deploy blocker'}"
        )
        return result

    target_dir = (publish_root / _slugify(slug)).resolve()
    if publish_root not in (target_dir, *target_dir.parents):
        result["blocker"] = "publish target escaped TAKYON_PRODUCT_SITE_ROOT"
        return result
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        shutil.rmtree(target_dir)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {"node_modules", ".git", ".next", ".cache", "__pycache__"}
            or name.endswith(".pyc")
        }

    shutil.copytree(publish_source, target_dir, ignore=ignore)
    _make_static_publish_tree_readable(target_dir)
    caddyfile, blocker = _ensure_product_static_caddy_route(slug=slug, publish_target=publish_target, static_root=target_dir)
    result["caddyfile"] = str(caddyfile or "")
    if blocker:
        result["blocker"] = blocker
        return result
    if caddyfile:
        ok, blocker = _probe_product_public_url(publish_target)
        if not ok:
            result["blocker"] = blocker
            return result
    local_url = _product_local_public_url(slug)
    result.update(
        {
            "status": "published",
            "public_url": local_url or publish_target,
            "local_url": local_url,
            "published_at": _now(),
            "publish_root": str(target_dir),
            "publish_source_path": f"{rel}/{publish_source_label}" if publish_source_label != "source" else rel,
            "publish_mode": "local_static" if local_url else "public_static",
            "blocker": "",
        }
    )
    return result


def _status_rank(status: str) -> int:
    return {"active": 0, "trialing": 0, "past_due": 1, "cancelled": 2, "canceled": 2, "revoked": 3}.get(status, 9)


def _tier_rank(tier: str) -> int:
    return {"owner": 0, "paid": 1, "pro": 1, "free": 2}.get(tier, 5)


def _hash_operation(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _normalize_budget_spec(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    aliases = (
        ("amount", None),
        ("cap", None),
        ("limit", None),
        ("monthly_cap", None),
        ("cap_usd", "USD"),
        ("amount_usd", "USD"),
        ("budget_usd", "USD"),
    )
    if "amount" not in normalized:
        for key, implied_currency in aliases:
            if key not in normalized:
                continue
            try:
                normalized["amount"] = float(normalized[key])
            except (TypeError, ValueError):
                normalized["amount"] = normalized[key]
            if implied_currency and not normalized.get("currency"):
                normalized["currency"] = implied_currency
            break
    currency = normalized.get("currency")
    if isinstance(currency, str) and currency.strip():
        normalized["currency"] = currency.strip().upper()
    return normalized


def _budget_amount(value: Any) -> float | None:
    normalized = _normalize_budget_spec(value)
    if isinstance(normalized, dict):
        for key in ("amount", "cap", "limit", "monthly_cap", "cap_usd", "amount_usd", "budget_usd"):
            if key in normalized:
                try:
                    return float(normalized[key])
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


def _workspace_needs_customer_ai_copy_contract(workspace_raw: str) -> bool:
    normalized = str(workspace_raw or "").strip().strip("/").lower()
    if normalized in {"product", "site", "website"}:
        return True
    return normalized.startswith("product/")


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
        "app.surface.publish_result",
        "app.surface.upsert",
        "app.usage.record",
    }
    product_paths = ("product/", "website/")
    marketing_paths = ("distribution/", "research/", "metrics/conversations/")

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


def _db_backend() -> str:
    """Which engine the operator store opens. Default ``sqlite`` keeps every existing call site and
    the whole test suite on the historical engine; an explicit ``TAKYON_DB_BACKEND=postgres`` opts a
    process onto the Postgres-backed seam (the operator tables ported by migration 0011). Deliberately
    a separate, explicit flag — NOT auto-detected from DATABASE_URL — so a developer or CI run that
    merely *has* DATABASE_URL set for the leaf modules does not silently flip the operator store onto
    Postgres. Parallels storage.py's ``TAKYON_STORAGE_BACKEND``."""
    return (os.getenv("TAKYON_DB_BACKEND") or "sqlite").strip().lower()


class _PGConn:
    """Thin psycopg adapter that lets the SQLite-shaped ``TakyonStore`` SQL run unchanged on Postgres.

    The store issues sqlite3-style ``conn.execute(sql, params)`` with ``?`` placeholders and reads
    every row by column name. psycopg3 wants ``%s`` placeholders and (here) ``dict_row`` rows, so this
    wrapper translates ``?`` → ``%s`` (escaping any literal ``%`` → ``%%`` first, and ONLY when params
    are bound — psycopg performs no %-substitution on a paramless query) and otherwise delegates
    verbatim. Verified faithful to the exact sqlite3 surface the store uses: only ``execute`` plus a
    single ``executescript``/``row_factory`` that live on the SQLite bootstrap path this backend skips;
    zero positional row reads (so ``dict_row`` is a true drop-in for ``sqlite3.Row``); no
    ``cursor``/``commit``/``rollback``/``executemany``/``create_function``; the only ``%`` anywhere in
    store SQL is a LIKE wildcard that rides inside a bound parameter, which psycopg leaves untouched.

    Used as a context manager exactly like ``sqlite3``: the underlying connection is opened
    ``autocommit=False`` so one ``with self._connect() as conn:`` block is exactly one atomic
    transaction — psycopg's own ``__exit__`` commits on success, rolls back on exception, and closes
    the per-block connection (no leak)."""

    def __init__(self, conn: Any) -> None:
        self._pg = conn
        self._depth = 0

    @staticmethod
    def _translate(sql: str) -> str:
        # Escape literal % BEFORE turning ? into %s so any literal % in the SQL text (none today, but
        # future-proof) survives psycopg's %-substitution. Only ever applied on the params path.
        return sql.replace("%", "%%").replace("?", "%s")

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> Any:
        if params is None:
            # Paramless: psycopg does no %-substitution, so send the SQL verbatim.
            return self._pg.execute(sql)
        return self._pg.execute(self._translate(sql), tuple(params))

    def executescript(self, sql: str) -> Any:
        # Only _init_db calls this, and the Postgres backend skips _init_db (the migration runner owns
        # all DDL). Fail loud rather than bootstrap a divergent schema (invariant #8: no fake success).
        raise RuntimeError(
            "TakyonStore schema bootstrap must not run on the Postgres backend; "
            "plugins/takyon/db/runner.py owns DDL"
        )

    def __enter__(self) -> "_PGConn":
        # Re-entrancy matters: the store nests a transaction block (``with conn:``) INSIDE the
        # connection block (``with self._connect() as conn:`` — commit() at core.py:4907,
        # upgrade_businesses at 8158). sqlite3's context manager only commits/rolls back and never
        # closes, so that nesting is harmless on SQLite. psycopg3's ``__exit__`` commits/rolls back AND
        # CLOSES, so only the OUTERMOST block may drive the real transaction+close; inner blocks must be
        # no-ops. No code reads the DB after an inner block exits, so collapsing both levels into one
        # outer-managed transaction preserves the original atomicity (all writes commit together on
        # success, roll back together on error).
        if self._depth == 0:
            self._pg.__enter__()
        self._depth += 1
        return self

    def __exit__(self, *exc_info: Any) -> Any:
        self._depth -= 1
        if self._depth == 0:
            return self._pg.__exit__(*exc_info)
        # Inner block: do not commit, do not close, and do not suppress a propagating exception
        # (return falsy) so the outermost block still sees it and rolls back.
        return False

    def close(self) -> None:
        self._pg.close()


class TakyonStore:
    """File + SQLite store for isolated business state and scoped workspaces."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        database_url: str | None = None,
        operator_user_id: str | None = None,
    ):
        base = Path(root).expanduser() if root else Path(os.getenv("TAKYON_HOME") or get_takyon_home() / DEFAULT_TAKYON_DIRNAME)
        self.root = base.resolve()
        self.db_path = self.root / "state.sqlite3"
        # Explicit Postgres DSN for the postgres backend (tests point this at a throwaway DB). When
        # None, the postgres path resolves DATABASE_URL/POSTGRES_URL via runtime_app. Unused on SQLite.
        self._database_url = database_url
        session_user_id = ""
        session_workspace_root = ""
        if operator_user_id is None:
            try:
                from gateway.session_context import get_session_env

                session_user_id = str(
                    get_session_env("TAKYON_SESSION_USER_ID", "") or ""
                ).strip()
                session_workspace_root = str(
                    get_session_env("TAKYON_SESSION_WORKSPACE_ROOT", "") or ""
                ).strip()
            except Exception:
                session_user_id = ""
                session_workspace_root = ""
        self._operator_user_id = str(
            operator_user_id
            or session_user_id
            or os.getenv("TAKYON_OPERATOR_USER_ID")
            or ""
        ).strip()
        self._workspace_root_override = (
            Path(session_workspace_root).expanduser().resolve()
            if session_workspace_root
            else None
        )
        self._workspace_sync_cache: set[str] = set()

    def _connect(self) -> "sqlite3.Connection | _PGConn":
        # The per-business filesystem half of the store is used on both backends, so make root first.
        self.root.mkdir(parents=True, exist_ok=True)
        if _db_backend() == "postgres":
            return self._connect_postgres()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        self._init_db(conn)
        return conn

    def _connect_postgres(self) -> "_PGConn":
        """Open the Postgres-backed connection seam. Lazy-imports psycopg and the canonical URL factory
        so the default SQLite path stays dependency-free. No schema bootstrap here: migration runner
        ``plugins/takyon/db/runner.py`` owns all DDL, so ``_init_db``/``_migrate_db`` are intentionally
        NOT called (their SQLite-only PRAGMA/ALTER logic would not even parse on Postgres)."""
        import psycopg
        from psycopg.rows import dict_row

        try:
            from .runtime_app import resolve_database_url
        except ImportError:  # pragma: no cover - import-style robustness for alternate load paths
            from plugins.takyon.runtime_app import resolve_database_url

        conn = psycopg.connect(
            resolve_database_url(self._database_url),
            row_factory=dict_row,
            autocommit=False,
            # See runtime_app.build_runtime_app: the live DATABASE_URL is Supabase's pgbouncer
            # endpoint (6543). prepare_threshold=None disables auto server-side prepared statements
            # so a PREPARE/EXECUTE can never split across pooler-reassigned backends. Store
            # connections are also short-lived (one per `with self._connect()` block), so nothing
            # relies on cross-transaction prepared-statement reuse.
            prepare_threshold=None,
        )
        return _PGConn(conn)

    def seed_platform_owner(self) -> tuple[str | None, str | None]:
        """Idempotently provision the single platform/operator owner — the Phase-8 serving-flip
        startup seed (mediationplan.md owner-wiring finding, step 3→4). No-op off Postgres (returns
        ``(None, None)``): the SQLite era has no ``users`` table to seed and never needed an owner.

        On Postgres this is the ONE place a key is minted as a side effect of *serving*: it opens one
        store transaction and delegates to ``control_plane.ensure_platform_owner`` over the RAW psycopg
        connection lent by ``_leaf_conn`` (control_plane speaks native ``%s`` + positional rows, so it
        must bypass the ``?``-translating ``_PGConn`` wrapper, exactly like the app-leaf delegations).
        Returns ``(user_id, raw_key)``: ``raw_key`` is the one-time API key minted on the very first
        call ONLY (the caller surfaces it once — never stored in clear), ``None`` on every later call.
        Deliberately separate from ``business.upsert`` (which resolves the owner *read-only* and blocks
        if unprovisioned, invariant #8) so no secret ever rides through a business commit, event
        payload, or file mirror. Idempotent and race-safe via ``provision_user_on_first_login``."""
        if _db_backend() != "postgres":
            return (None, None)
        try:
            from . import control_plane
        except ImportError:  # pragma: no cover - alternate load path when run as a top-level package
            from plugins.takyon import control_plane
        with self._connect() as conn:
            with self._leaf_conn(conn) as raw:
                return control_plane.ensure_platform_owner(raw)

    def _active_operator_user_id(self) -> str:
        return self._operator_user_id if _db_backend() == "postgres" else ""

    def _enforce_operator_business_access(
        self,
        conn: sqlite3.Connection,
        business_slug: str,
    ) -> None:
        operator_user_id = self._active_operator_user_id()
        if not operator_user_id:
            return
        row = conn.execute(
            "SELECT owner_user_id FROM businesses WHERE slug = ?",
            (business_slug,),
        ).fetchone()
        if row is None:
            raise TakyonError(f"business:{business_slug} does not exist")
        owner_user_id = str(row["owner_user_id"] or "").strip()
        if owner_user_id != operator_user_id:
            raise TakyonError(f"access denied for business:{business_slug}")

    def _work_requests_table(self) -> str:
        """Physical table name for the operator's *work-request record* store. On SQLite that is the
        historical ``jobs`` table (bootstrapped by ``_init_db``). On Postgres it is ``business_work_requests``
        — migration 0011's exact 1:1 column port of the SQLite operator ``jobs`` — which ISOLATES it from
        the 0010 ``jobs`` worker-plane *execution queue* (a different table with uuid/jsonb/SKIP-LOCKED
        shape). The store only enqueues/counts/lists/GCs this record; it never drains it, so this is a pure
        storage retarget, not the deferred worker-plane consolidation. Interpolated into the operator-jobs
        SQL so every existing ``conn.execute`` stays otherwise unchanged."""
        return "business_work_requests" if _db_backend() == "postgres" else "jobs"

    def _app_user_metadata_select(self) -> str:
        """Column expression for the sub-user metadata blob in the two operator reads that list it
        explicitly (``_rewrite_app_files``/``_app_summary``). On SQLite the column is TEXT ``metadata_json``,
        which ``_row_to_dict`` decodes back to a ``metadata`` dict; on Postgres (leaf migration 0005) the
        column is ``jsonb metadata`` that already deserializes to a dict and has no ``_json`` suffix. Both
        forms therefore surface as ``row['metadata']`` after ``_row_to_dict`` — identical output shape — so
        only the SELECT text differs by backend. Every other operator app read uses ``SELECT *`` and needs
        no such switch."""
        return "metadata" if _db_backend() == "postgres" else "metadata_json"

    @contextmanager
    def _leaf_conn(self, conn: "_PGConn"):
        """Lend the raw psycopg connection (unwrapped from the SQLite-shaped ``_PGConn`` adapter) to a
        Phase-5/6 app leaf module for the duration of one delegated write. The leaves speak native psycopg:
        ``%s`` placeholders (so the adapter's ``?``→``%s`` translation must be bypassed) and positional row
        reads (``row[0]``…), so the row factory is swapped ``dict_row``→``tuple_row`` here and restored on
        exit. The leaf opens a SAVEPOINT (``with conn.transaction()``) inside the store's already-open outer
        transaction, so its writes commit or roll back atomically with the operator idempotency row and the
        event/file mirror that the shared op tail writes. Postgres-only; never entered on the SQLite path."""
        from psycopg.rows import dict_row, tuple_row

        raw = conn._pg
        raw.row_factory = tuple_row
        try:
            yield raw
        finally:
            raw.row_factory = dict_row

    @staticmethod
    def _app_leaves() -> dict[str, Any]:
        """Lazy-import the canonical Postgres app leaf modules that own the ``app_*`` writes the operator
        store delegates to on the Postgres backend (identity/entitlements/payments/usage). Imported lazily and only
        on the Postgres branch so the default SQLite path stays dependency-free and pays no import cost."""
        try:
            from . import app_entitlements, app_identity, app_payments, app_usage
        except ImportError:  # pragma: no cover - alternate load path when run as a top-level package
            from plugins.takyon import app_entitlements, app_identity, app_payments, app_usage
        return {"identity": app_identity, "entitlements": app_entitlements, "payments": app_payments, "usage": app_usage}

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
              runtime_features_json TEXT,
              routes_json TEXT,
              theme_json TEXT,
              constraints_json TEXT,
              publish_target TEXT,
              publish_policy TEXT NOT NULL DEFAULT 'publish_after_verify',
              mode_behavior TEXT NOT NULL DEFAULT 'test_mode_publishes_product_surface',
              done_gate TEXT NOT NULL DEFAULT 'business_verify_product_surface:verified_and_published_or_exact_blocker',
              public_url TEXT,
              publish_status TEXT NOT NULL DEFAULT 'not_published',
              published_at TEXT,
              publish_receipt_path TEXT,
              publish_blocker TEXT,
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
        surface_columns = {row["name"] for row in conn.execute("PRAGMA table_info(app_surface_contracts)").fetchall()}
        surface_additions = {
            "runtime_features_json": "TEXT",
            "publish_target": "TEXT",
            "publish_policy": "TEXT NOT NULL DEFAULT 'publish_after_verify'",
            "mode_behavior": "TEXT NOT NULL DEFAULT 'test_mode_publishes_product_surface'",
            "done_gate": "TEXT NOT NULL DEFAULT 'business_verify_product_surface:verified_and_published_or_exact_blocker'",
            "public_url": "TEXT",
            "publish_status": "TEXT NOT NULL DEFAULT 'not_published'",
            "published_at": "TEXT",
            "publish_receipt_path": "TEXT",
            "publish_blocker": "TEXT",
        }
        for column, ddl in surface_additions.items():
            if column not in surface_columns:
                conn.execute(f"ALTER TABLE app_surface_contracts ADD COLUMN {column} {ddl}")
        for row in conn.execute("SELECT business_slug, publish_target FROM app_surface_contracts").fetchall():
            slug = str(row["business_slug"] or "")
            if slug and not str(row["publish_target"] or "").strip():
                conn.execute(
                    "UPDATE app_surface_contracts SET publish_target = ?, updated_at = COALESCE(updated_at, ?) WHERE business_slug = ?",
                    (_product_publish_target(slug), _now(), slug),
                )
        needs_surface_defaults = conn.execute(
            """
            SELECT 1 FROM app_surface_contracts
            WHERE
              publish_policy IS NULL OR publish_policy = ''
              OR mode_behavior IS NULL OR mode_behavior = ''
              OR done_gate IS NULL OR done_gate = ''
              OR publish_status IS NULL OR publish_status = ''
            LIMIT 1
            """
        ).fetchone()
        if needs_surface_defaults:
            conn.execute(
                """
                UPDATE app_surface_contracts
                SET
                  publish_policy = COALESCE(NULLIF(publish_policy, ''), ?),
                  mode_behavior = COALESCE(NULLIF(mode_behavior, ''), ?),
                  done_gate = COALESCE(NULLIF(done_gate, ''), ?),
                  publish_status = COALESCE(NULLIF(publish_status, ''), 'not_published')
                WHERE
                  publish_policy IS NULL OR publish_policy = ''
                  OR mode_behavior IS NULL OR mode_behavior = ''
                  OR done_gate IS NULL OR done_gate = ''
                  OR publish_status IS NULL OR publish_status = ''
                """,
                (_DEFAULT_PRODUCT_PUBLISH_POLICY, _DEFAULT_PRODUCT_MODE_BEHAVIOR, _DEFAULT_PRODUCT_DONE_GATE),
            )

    def _sync_business_workspace_cache(self, slug: str, root: Path) -> None:
        if self._workspace_root_override is not None:
            return
        normalized = _slugify(slug)
        if normalized in self._workspace_sync_cache:
            return
        from . import storage

        load_takyon_env()
        backend = storage.get_storage_backend()
        backend_name = str(getattr(backend, "name", "") or "").strip().lower()
        local_bucket = str(os.getenv("TAKYON_STORAGE_LOCAL_DIR") or "").strip()
        if backend_name not in {"supabase_s3", "local"}:
            return
        if backend_name == "local" and not local_bucket:
            return
        # Refresh from the durable backend once per store instance. A scope read fans out through
        # multiple helpers (`summary`, `list_files`, pulse, product surface reads), and each one may
        # resolve the business root repeatedly. Re-syncing for every nested helper call turns one
        # dashboard hydrate into many full storage downloads and regularly times out the UI.
        storage.sync_down(backend, normalized, root, delete_local=True)
        self._workspace_sync_cache.add(normalized)

    def _sync_business_workspace_remote(self, slug: str) -> None:
        if self._workspace_root_override is None:
            return
        from . import storage

        load_takyon_env()
        backend = storage.get_storage_backend()
        backend_name = str(getattr(backend, "name", "") or "").strip().lower()
        local_bucket = str(os.getenv("TAKYON_STORAGE_LOCAL_DIR") or "").strip()
        if backend_name not in {"supabase_s3", "local"}:
            return
        if backend_name == "local" and not local_bucket:
            return
        workspace = self._workspace_root_override / "businesses" / _slugify(slug)
        if not workspace.exists():
            return
        storage.sync_up(backend, _slugify(slug), workspace, delete_remote=True)

    def _business_root(self, slug: str) -> Path:
        base = self._workspace_root_override or self.root
        root = base / "businesses" / _slugify(slug)
        self._sync_business_workspace_cache(slug, root)
        return root

    def _resolve_business_file(self, slug: str, rel: str) -> Path:
        root = self._business_root(slug)
        path = (root / _canonical_business_relpath(rel)).resolve()
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
        business = self._row_to_dict(row)
        if business and "budget" in business:
            business["budget"] = _normalize_budget_spec(business.get("budget"))
        return business

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
        return f"metrics/conversations/{source}/{_file_slug(label, 'thread')}.md"

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
        rel = "metrics/conversations/corpus/messages.jsonl"
        _append_jsonl(self._business_root(slug) / rel, self._conversation_corpus_message(thread, message))
        return rel

    def _append_conversation_event_corpus(self, slug: str, event_type: str, payload: Any) -> str:
        rel = "metrics/conversations/corpus/events.jsonl"
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
            "- metrics/conversations/corpus/messages.jsonl",
            "- metrics/conversations/corpus/events.jsonl",
        ])
        _atomic_write_text(self._business_root(slug) / "metrics" / "conversations" / "index.md", "\n".join(lines) + "\n")

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
            "filesystem_index": "metrics/conversations/index.md",
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
                "INSERT INTO app_budgets (business_slug, status, hard_limit_microusd, current_period_start, current_period_end, created_at, updated_at) VALUES (?, 'active', ?, ?, ?, ?, ?) ON CONFLICT(business_slug) DO NOTHING",
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
            "runtime_features": [],
            "routes": [],
            "theme": {"source": "business design brief"},
            "constraints": {
                "no_hardcoded_product_ui": True,
                "backend_runtime_only": True,
            },
            "publish_target": _product_publish_target(slug),
            "publish_policy": _DEFAULT_PRODUCT_PUBLISH_POLICY,
            "mode_behavior": _DEFAULT_PRODUCT_MODE_BEHAVIOR,
            "done_gate": _DEFAULT_PRODUCT_DONE_GATE,
            "public_url": "",
            "publish_status": "not_published",
            "published_at": "",
            "publish_receipt_path": "",
            "publish_blocker": "",
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

    def _product_surface_evidence(self, conn: sqlite3.Connection, slug: str, surface: dict[str, Any] | None = None) -> dict[str, Any]:
        surface = surface if isinstance(surface, dict) else self._app_surface_contract(conn, slug)
        source_path = str(surface.get("source_path") or "").strip()
        latest = self._latest_surface_verification(conn, slug, source_path) if source_path else None
        inventory = latest.get("inventory") if isinstance((latest or {}).get("inventory"), dict) else {}
        if not inventory and source_path:
            inventory = _product_inventory(self._business_root(slug), source_path, surface=surface)
        elif isinstance(inventory, dict):
            inventory = {
                **inventory,
                "public_url": str(surface.get("public_url") or inventory.get("public_url") or ""),
                "publish_receipt_path": str(surface.get("publish_receipt_path") or inventory.get("publish_receipt_path") or ""),
            }
        root = self._business_root(slug) / source_path if source_path else None
        has_source_files = bool(root and root.exists() and root.is_dir() and _product_source_files(root, limit=1))
        local_work: list[str] = []
        if not source_path:
            local_work.append("missing product source path")
        elif not has_source_files:
            local_work.append("missing product source files")
        elif not latest:
            local_work.append("product source has not been published")
        else:
            if latest.get("status") not in {"passed"}:
                local_work.append(f"product publish check is {latest.get('status') or 'unknown'}")
            if latest.get("done_gate_status") not in {"passed"}:
                local_work.append(latest.get("blocker") or "product is complete only when published or an exact blocker is recorded")
        risk_issues = {
            str(item.get("issue") or "")
            for item in (inventory.get("risk_markers") or [])
            if isinstance(item, dict)
        }
        pretend_count = len(inventory.get("pretend_findings") or []) if isinstance(inventory, dict) else 0
        if pretend_count:
            local_work.append("product source has pretend-state findings")
        elif risk_issues.intersection({"stub_or_mock", "demo_or_test_state", "browser_storage", "blocked_or_unwired"}):
            local_work.append("product source has advisory stub/demo/unwired markers")
        return {
            "surface_status": str(surface.get("status") or "missing"),
            "publish_status": str(surface.get("publish_status") or ""),
            "public_url": str(surface.get("public_url") or ""),
            "source_path": source_path,
            "has_source_files": has_source_files,
            "latest_verification": latest or {},
            "inventory": inventory or {},
            "local_continuable_work": local_work[:8],
        }

    def _surface_status_for_upsert(
        self,
        conn: sqlite3.Connection,
        slug: str,
        requested_status: str,
        source_path: str | None,
        publish_policy: str,
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
        if not latest:
            validation.update({
                "status": "unverified",
                "reason": "no product.surface.publish receipt for source_path",
                "source_path": source_path,
            })
            return "unverified", {**metadata, "takyon_surface_validation": validation}
        publish = latest.get("publish") if isinstance(latest.get("publish"), dict) else {}
        if publish.get("status") == "published":
            if latest.get("status") == "passed" and latest.get("done_gate_status") == "passed":
                return status, {**metadata, "takyon_surface_validation": {"status": "passed", "receipt": latest.get("receipt_path")}}
            validation.update(
                {
                    "status": "warning",
                    "reason": "published with advisory verification blockers",
                    "source_path": source_path,
                    "latest_verification": latest,
                }
            )
            return status, {**metadata, "takyon_surface_validation": validation}
        if latest.get("status") != "passed":
            validation.update({
                "status": "unverified",
                "reason": "no passing product.surface.publish receipt for source_path",
                "source_path": source_path,
                "latest_verification": latest,
            })
            return "unverified", {**metadata, "takyon_surface_validation": validation}
        if latest.get("done_gate_status") != "passed" or publish.get("status") != "published":
            validation.update({
                "status": "publish_blocked",
                "reason": "no published product.surface receipt for source_path",
                "source_path": source_path,
                "latest_verification": latest,
            })
            return "publish_blocked", {**metadata, "takyon_surface_validation": validation}
        return status, {**metadata, "takyon_surface_validation": {"status": "passed", "receipt": latest.get("receipt_path")}}

    def _rewrite_app_files(self, conn: sqlite3.Connection, slug: str) -> None:
        root = self._business_root(slug) / "product"
        budget = self._ensure_app_budget(conn, slug)
        surface = self._app_surface_contract(conn, slug)
        surface_evidence = self._product_surface_evidence(conn, slug, surface)
        inventory = surface_evidence.get("inventory") if isinstance(surface_evidence.get("inventory"), dict) else {}
        plans = [
            self._row_to_dict(row)
            for row in conn.execute("SELECT * FROM app_plan_policies WHERE business_slug = ? ORDER BY price_cents ASC, plan_key ASC", (slug,)).fetchall()
        ]
        users = [
            self._row_to_dict(row)
            for row in conn.execute(f"SELECT id, business_slug, email, name, status, tier, {self._app_user_metadata_select()}, created_at, updated_at FROM app_users WHERE business_slug = ? ORDER BY updated_at DESC LIMIT 200", (slug,)).fetchall()
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
        has_real_surface = bool(surface_evidence.get("source_path")) and bool(surface_evidence.get("has_source_files"))
        has_runtime_state = bool(
            plans
            or users
            or int(revenue["count"] or 0)
            or int(checkout_count["count"] or 0)
            or int(usage["count"] or 0)
        )

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
            f"- Runtime features: {', '.join(_surface_runtime_features(surface)) or 'none declared'}",
            f"- Publish target: {surface.get('publish_target') or _product_publish_target(slug)}",
            f"- Publish policy: {surface.get('publish_policy') or _DEFAULT_PRODUCT_PUBLISH_POLICY}",
            f"- Mode behavior: {surface.get('mode_behavior') or _DEFAULT_PRODUCT_MODE_BEHAVIOR}",
            f"- Done gate: {surface.get('done_gate') or _DEFAULT_PRODUCT_DONE_GATE}",
            f"- Publish status: {surface.get('publish_status') or 'not_published'}",
            f"- Public URL: {surface.get('public_url') or 'not published'}",
            f"- Published at: {surface.get('published_at') or 'not published'}",
            f"- Publish receipt: {surface.get('publish_receipt_path') or 'not set'}",
            f"- Publish blocker: {surface.get('publish_blocker') or 'none'}",
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
        if inventory:
            surface_lines.extend(["", "## Product Inventory", ""])
            surface_lines.extend([
                f"- Status: {inventory.get('status') or 'unknown'}",
                f"- Routes: {', '.join(inventory.get('routes') or []) or 'none found'}",
                f"- API routes: {', '.join(inventory.get('api_routes') or []) or 'none found'}",
                f"- Risk markers: {len(inventory.get('risk_markers') or [])}",
                f"- Claim snippets: {len(inventory.get('claim_snippets') or [])}",
                f"- Pretend-state findings: {len(inventory.get('pretend_findings') or [])}",
            ])
            local_work = surface_evidence.get("local_continuable_work") or []
            if local_work:
                surface_lines.extend(["", "### Local Continuable Work", ""])
                surface_lines.extend(f"- {_markdown_scalar(item)}" for item in local_work[:8])
            risk_markers = [item for item in (inventory.get("risk_markers") or []) if isinstance(item, dict)]
            if risk_markers:
                surface_lines.extend(["", "### Advisory Markers", ""])
                for item in risk_markers[:6]:
                    surface_lines.append(
                        f"- {item.get('path')}:{item.get('line') or '?'} {item.get('issue')}: {_markdown_scalar(item.get('snippet'))}"
                    )
            claim_snippets = [item for item in (inventory.get("claim_snippets") or []) if isinstance(item, dict)]
            if claim_snippets:
                surface_lines.extend(["", "### Public Claim Snippets", ""])
                for item in claim_snippets[:6]:
                    surface_lines.append(
                        f"- {item.get('path')}:{item.get('line') or '?'} {_markdown_scalar(item.get('snippet'))}"
                    )
        _atomic_write_text(root / "surface.md", "\n".join(surface_lines).rstrip() + "\n")

        runtime_files = [
            root / "runtime.md",
            root / "plans.md",
            root / "customers.md",
            root / "billing.md",
            root / "usage.md",
        ]
        if not has_real_surface and not has_runtime_state:
            for path in runtime_files:
                if path.exists():
                    path.unlink()
            return

        selected_runtime_rails = _surface_runtime_features(surface)
        rails_by_owner: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for rail in selected_runtime_rails:
            spec = PRODUCT_RUNTIME_RAILS.get(rail)
            if not spec:
                continue
            owner = str(spec.get("owner_skill") or "unknown").strip() or "unknown"
            rails_by_owner.setdefault(owner, []).append((rail, spec))

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
        index.extend(["", "## Selected Runtime Rails", ""])
        if not selected_runtime_rails:
            index.append("- No runtime rails declared.")
        else:
            for rail in selected_runtime_rails:
                spec = PRODUCT_RUNTIME_RAILS.get(rail, {})
                owner = str(spec.get("owner_skill") or "unknown")
                tools = [str(tool).strip() for tool in spec.get("tools") or [] if str(tool).strip()]
                index.append(f"- {rail} — owner: {owner}")
                if tools:
                    index.append(f"  - Tools: {', '.join(tools)}")
        index.extend(["", "## Rails By Owner", ""])
        if not rails_by_owner:
            index.append("- No runtime rail ownership is currently declared.")
        else:
            for owner, items in rails_by_owner.items():
                index.extend(["", f"### {owner}", ""])
                for rail, spec in items:
                    tools = [str(tool).strip() for tool in spec.get("tools") or [] if str(tool).strip()]
                    index.append(f"- {rail}")
                    if tools:
                        index.append(f"  - Tools: {', '.join(tools)}")
                    for item in spec.get("worker_contract") or []:
                        index.append(f"  - UI contract: {str(item).strip()}")
        _atomic_write_text(root / "runtime.md", "\n".join(index) + "\n")

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
        surface = self._app_surface_contract(conn, slug)
        surface_evidence = self._product_surface_evidence(conn, slug, surface)
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
            "surface_contract": surface,
            "product_surface": surface_evidence,
            "product_inventory": surface_evidence.get("inventory") or {},
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
                for row in conn.execute(f"SELECT id, business_slug, email, name, status, tier, {self._app_user_metadata_select()}, created_at, updated_at FROM app_users WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?", (slug, limit)).fetchall()
            ],
            "entitlements": [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM app_entitlements WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?", (slug, limit)).fetchall()
            ],
            "recent_checkouts": [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM app_checkout_intents WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?", (slug, limit)).fetchall()
            ],
            "filesystem_index": "product/runtime.md",
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
                    f"""
                    SELECT COUNT(*) AS jobs,
                           SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                           SUM(CASE WHEN status IN ('failed', 'error', 'blocked') THEN 1 ELSE 0 END) AS blocked_or_failed,
                           SUM(CASE WHEN status IN ('done', 'completed', 'succeeded') THEN 1 ELSE 0 END) AS completed
                    FROM {self._work_requests_table()}
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
            product_evidence = self._product_surface_evidence(conn, slug)
            current_jobs = one(
                f"SELECT SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued FROM {self._work_requests_table()} WHERE business_slug = ?",
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
                "local_continuable_product_work": len(product_evidence.get("local_continuable_work") or []),
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
                    "product_surface": {
                        "status": product_evidence.get("surface_status"),
                        "publish_status": product_evidence.get("publish_status"),
                        "public_url": product_evidence.get("public_url"),
                        "source_path": product_evidence.get("source_path"),
                        "has_source_files": product_evidence.get("has_source_files"),
                        "latest_verification_status": (product_evidence.get("latest_verification") or {}).get("status"),
                        "done_gate_status": (product_evidence.get("latest_verification") or {}).get("done_gate_status"),
                        "inventory_status": (product_evidence.get("inventory") or {}).get("status"),
                        "risk_marker_count": len((product_evidence.get("inventory") or {}).get("risk_markers") or []),
                        "claim_snippet_count": len((product_evidence.get("inventory") or {}).get("claim_snippets") or []),
                        "pretend_finding_count": len((product_evidence.get("inventory") or {}).get("pretend_findings") or []),
                        "local_continuable_work": product_evidence.get("local_continuable_work") or [],
                    },
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
                    "human_summary_path": "metrics/summary.md",
                    "business_model_path": "research/strategy.md",
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
            self._work_requests_table(),
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
        for table in (self._work_requests_table(), "ledger_entries", "events"):
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

        # Preserve historical money-ledger rows, but detach them from the business before the
        # business row itself is removed. Postgres keeps `billing_entries.business_slug` and
        # `custody_entries.business_slug` as nullable FKs for auditability; deleting the business
        # first would fail the FK and strand the operator-facing delete rail.
        for table in ("billing_entries", "custody_entries"):
            cursor = conn.execute(
                f"UPDATE {table} SET business_slug = NULL WHERE business_slug = ?",
                (business,),
            )
            deleted[f"{table}_detached"] = int(cursor.rowcount or 0)

        for table in ("agent_runs", "control_states"):
            cursor = conn.execute(f"DELETE FROM {table} WHERE scope = ? OR scope LIKE ?", (scope, scope_like))
            deleted[table] = int(cursor.rowcount or 0)
        for table in (self._work_requests_table(), "ledger_entries", "events"):
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
        published_root = _product_publish_root()
        published_site = (published_root / slug).resolve() if published_root else None
        published_site_summary: dict[str, Any] = {"path": str(published_site or ""), "exists": False}
        if published_site is not None:
            publish_root_resolved = published_root.resolve()
            if publish_root_resolved not in (published_site, *published_site.parents):
                raise TakyonError("refusing to delete published site outside product site root")
            published_site_summary = self._filesystem_summary(published_site)
        cron_preview = self._delete_business_crons(slug, confirm=False) if delete_cron else {"matched": [], "removed": []}
        db_counts = self._business_delete_db_counts(conn, slug)

        result: dict[str, Any] = {
            "action": "business.delete",
            "business": slug,
            "dry_run": not confirm,
            "business_record": business,
            "filesystem": filesystem,
            "published_site": published_site_summary,
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
        if delete_files and published_site is not None and published_site.exists():
            shutil.rmtree(published_site)
            result["published_site"] = {**published_site_summary, "removed": True}
        elif delete_files and published_site is not None:
            result["published_site"] = {**published_site_summary, "removed": False}
        else:
            result["published_site"] = {**published_site_summary, "removed": False, "skipped": True}

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
                operator_user_id = self._active_operator_user_id()
                if operator_user_id:
                    business_rows = conn.execute(
                        "SELECT * FROM businesses WHERE owner_user_id = ? ORDER BY updated_at DESC LIMIT ?",
                        (operator_user_id, limit),
                    ).fetchall()
                else:
                    business_rows = conn.execute(
                        "SELECT * FROM businesses ORDER BY updated_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                businesses = [self._row_to_dict(row) for row in business_rows]
                if operator_user_id:
                    owned_scopes = {
                        f"business:{str(item.get('slug') or '').strip()}"
                        for item in businesses
                        if isinstance(item, dict) and str(item.get("slug") or "").strip()
                    }
                    controls = [
                        self._row_to_dict(row)
                        for row in conn.execute(
                            "SELECT * FROM control_states ORDER BY updated_at DESC LIMIT ?",
                            (limit,),
                        ).fetchall()
                        if str(row["scope"] or "") in owned_scopes
                        or any(
                            str(row["scope"] or "").startswith(f"{scope}/")
                            for scope in owned_scopes
                        )
                    ]
                else:
                    controls = [
                        self._row_to_dict(row)
                        for row in conn.execute("SELECT * FROM control_states ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
                    ]
                return {"success": True, "scope": "global", "businesses": businesses, "controls": controls}

            slug = str(parsed["business"])
            self._enforce_operator_business_access(conn, slug)
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
                for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if rel in {"", "."} and child.name not in TAKYON_BUSINESS_ROOTS:
                        continue
                    if len(files) >= limit:
                        break
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
                    f"SELECT * FROM {self._work_requests_table()} WHERE business_slug = ? ORDER BY updated_at DESC LIMIT ?",
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
            brain_root = self._business_root(slug) / "research"
            if brain_root.exists():
                for child in sorted(brain_root.rglob("*")):
                    if child.is_file():
                        brain_index.append({"path": str(child.relative_to(self._business_root(slug)))})
                        if len(brain_index) >= limit:
                            break

            research_index: list[dict[str, Any]] = []
            research_seen: set[str] = set()
            business_root = self._business_root(slug)
            for rel_root in ("research", "metrics"):
                root = business_root / rel_root
                if not root.exists() or not root.is_dir():
                    continue
                for child in sorted(root.rglob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                    if not child.is_file() or child.name.startswith("."):
                        continue
                    rel = str(child.relative_to(business_root))
                    if rel in {"research/index.md", "research/strategy.md", "metrics/summary.md", "metrics/wake-history.md"}:
                        continue
                    if rel in research_seen:
                        continue
                    research_seen.add(rel)
                    try:
                        stat = child.stat()
                    except OSError:
                        continue
                    research_index.append({
                        "path": rel,
                        "updated_at": int(stat.st_mtime * 1000),
                        "size": int(stat.st_size),
                        "source": rel_root,
                    })
                    if len(research_index) >= limit:
                        break
                if len(research_index) >= limit:
                    break

            response: dict[str, Any] = {
                "success": True,
                "scope": scope,
                "business": business,
                "workspaces": workspaces,
                "controls": controls,
                "brain_index": brain_index,
                "research_index": research_index,
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
            "app.surface.publish_result",
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
            self._enforce_operator_business_access(conn, business_slug)
            self._ensure_business(conn, business_slug)
        business_mode = "live"
        if business_slug and action != "business.upsert":
            business = self._ensure_business(conn, business_slug)
            business_mode = str(business.get("mode") or "live")
            _enforce_business_work_focus(op, str(business.get("work_focus") or "all"))
        if action == "business.upsert" and business_slug:
            existing = self._business(conn, business_slug)
            if existing:
                self._enforce_operator_business_access(conn, business_slug)
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
            budget = _normalize_budget_spec(op.get("budget"))
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
            elif _db_backend() == "postgres":
                # PG businesses.owner_user_id is NOT NULL (0001 spine; 0011 enrich). The operator store
                # has no Auth0/login context, so a single platform owner (control_plane, keyed by
                # TAKYON_PLATFORM_OWNER_SUB) owns every shell/CEO-created business. Resolve it READ-ONLY
                # here — creating a business must never mint/surface an API key as a side effect (the
                # one-time key is surfaced only by the explicit ensure_platform_owner startup bootstrap).
                # Unprovisioned → block with a reason (invariant #8), never a NULL/fake owner.
                try:
                    from . import control_plane
                except ImportError:  # pragma: no cover - alternate load path as a top-level package
                    from plugins.takyon import control_plane
                owner_user_id = self._active_operator_user_id()
                with self._leaf_conn(conn) as raw:
                    if not owner_user_id:
                        owner_user_id = control_plane.resolve_platform_owner_id(raw)
                    if not owner_user_id:
                        raise TakyonError(
                            "cannot create business on Postgres: platform owner is not provisioned. "
                            "Seed it once at startup (control_plane.ensure_platform_owner) or set "
                            "TAKYON_PLATFORM_OWNER_SUB to a provisioned user's Auth0 sub."
                        )
                conn.execute(
                    "INSERT INTO businesses (slug, name, goal, status, mode, work_focus, budget_json, metadata_json, owner_user_id, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
                    (slug, name, goal, mode or "live", work_focus or "all", _json_dumps(budget or {}), _json_dumps(metadata), owner_user_id, now, now),
                )
            else:
                conn.execute(
                    "INSERT INTO businesses (slug, name, goal, status, mode, work_focus, budget_json, metadata_json, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                    (slug, name, goal, mode or "live", work_focus or "all", _json_dumps(budget or {}), _json_dumps(metadata), now, now),
                )
            root = self._business_root(slug)
            root.mkdir(parents=True, exist_ok=True)
            for dirname in TAKYON_BUSINESS_ROOTS:
                (root / dirname).mkdir(parents=True, exist_ok=True)
            strategy = root / "research" / "strategy.md"
            if not strategy.exists():
                _atomic_write_text(strategy, f"# {name}\n\nGoal: {goal or 'Unspecified'}\n")
            self._sync_business_workspace_remote(slug)
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
            status = str(op.get("status") or current.get("status") or "active")
            if _db_backend() == "postgres":
                # Canonical Postgres budget write: app_usage.set_app_budget owns the app_budgets cap (it
                # row-locks then upserts), so the operator store delegates rather than carry a second
                # writer. Prior status is preserved when the op omits it (the `status` var above).
                leaves = self._app_leaves()
                try:
                    with self._leaf_conn(conn) as raw:
                        leaves["usage"].set_app_budget(raw, slug, hard_limit_microusd=amount, status=status)
                except leaves["usage"].AppUsageError as exc:
                    raise TakyonError(str(exc)) from exc
            else:
                conn.execute(
                    "UPDATE app_budgets SET hard_limit_microusd = ?, status = ?, updated_at = ? WHERE business_slug = ?",
                    (amount, status, now, slug),
                )
            self._rewrite_app_files(conn, slug)
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"hard_limit_microusd": amount, "reason": reason, "actor": actor})
            return {"action": action, "business": slug, "hard_limit_microusd": amount}

        if action == "app.surface.upsert":
            status = str(op.get("status") or "draft").strip().lower()
            if not status:
                raise TakyonError("surface status is required")
            existing = self._app_surface_contract(conn, slug)
            design_brief_path = _safe_relpath(str(op.get("design_brief_path") or "product/design-brief.md"), field="design_brief_path").as_posix()
            source_path = None
            if op.get("source_path"):
                source_path = _safe_relpath(str(op.get("source_path")), field="source_path").as_posix()
            runtime_api_base = str(op.get("runtime_api_base") or f"/api/takyon/apps/{slug}").strip()
            runtime_features_raw = op.get("runtime_features")
            runtime_features = (
                _normalize_runtime_features(runtime_features_raw, strict=True)
                if runtime_features_raw is not None
                else _surface_runtime_features(existing)
            )
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
            publish_target = _product_publish_target(slug, op.get("publish_target"))
            publish_policy = str(op.get("publish_policy") or _DEFAULT_PRODUCT_PUBLISH_POLICY).strip() or _DEFAULT_PRODUCT_PUBLISH_POLICY
            mode_behavior = str(op.get("mode_behavior") or _DEFAULT_PRODUCT_MODE_BEHAVIOR).strip() or _DEFAULT_PRODUCT_MODE_BEHAVIOR
            done_gate = str(op.get("done_gate") or _DEFAULT_PRODUCT_DONE_GATE).strip() or _DEFAULT_PRODUCT_DONE_GATE
            metadata = op.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {"value": metadata}
            status, metadata = self._surface_status_for_upsert(conn, slug, status, source_path, publish_policy, metadata)
            now = _now()
            conn.execute(
                """
                INSERT INTO app_surface_contracts (
                  business_slug, status, design_brief_path, source_path, runtime_api_base,
                  runtime_features_json, routes_json, theme_json, constraints_json, publish_target, publish_policy,
                  mode_behavior, done_gate, notes, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_slug) DO UPDATE SET
                  status = excluded.status,
                  design_brief_path = excluded.design_brief_path,
                  source_path = excluded.source_path,
                  runtime_api_base = excluded.runtime_api_base,
                  runtime_features_json = excluded.runtime_features_json,
                  routes_json = excluded.routes_json,
                  theme_json = excluded.theme_json,
                  constraints_json = excluded.constraints_json,
                  publish_target = excluded.publish_target,
                  publish_policy = excluded.publish_policy,
                  mode_behavior = excluded.mode_behavior,
                  done_gate = excluded.done_gate,
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
                    _json_dumps(runtime_features),
                    _json_dumps(routes),
                    _json_dumps(theme),
                    _json_dumps(constraints),
                    publish_target,
                    publish_policy,
                    mode_behavior,
                    done_gate,
                    str(op.get("notes") or ""),
                    _json_dumps(metadata),
                    now,
                    now,
                ),
            )
            self._rewrite_app_files(conn, slug)
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"status": status, "design_brief_path": design_brief_path, "source_path": source_path, "runtime_features": runtime_features, "publish_target": publish_target, "publish_policy": publish_policy, "done_gate": done_gate, "metadata": metadata})
            return {"action": action, "business": slug, "status": status, "surface_contract": "product/surface.md", "publish_target": publish_target, "publish_policy": publish_policy}

        if action == "app.surface.publish_result":
            publish_status = str(op.get("publish_status") or op.get("status") or "").strip().lower()
            if publish_status not in {"not_published", "published", "blocked"}:
                raise TakyonError("publish_status must be one of: not_published, published, blocked")
            public_url = str(op.get("public_url") or "").strip()
            publish_target = _product_publish_target(slug, op.get("publish_target"))
            published_at = str(op.get("published_at") or "").strip()
            receipt_path = None
            if op.get("receipt_path"):
                receipt_path = _safe_relpath(str(op.get("receipt_path")), field="receipt_path").as_posix()
            publish_source_path = None
            if op.get("publish_source_path"):
                publish_source_path = _safe_relpath(str(op.get("publish_source_path")), field="publish_source_path").as_posix()
            blocker = str(op.get("blocker") or "")
            existing = self._app_surface_contract(conn, slug)
            metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
            metadata = {
                **metadata,
                "takyon_publish": {
                    "status": publish_status,
                    "public_url": public_url,
                    "publish_target": publish_target,
                    "published_at": published_at,
                    "receipt_path": receipt_path,
                    "publish_source_path": publish_source_path,
                    "blocker": blocker,
                },
            }
            conn.execute(
                """
                UPDATE app_surface_contracts
                SET publish_target = ?, public_url = NULLIF(?, ''), publish_status = ?,
                    published_at = NULLIF(?, ''), publish_receipt_path = ?, publish_blocker = NULLIF(?, ''),
                    metadata_json = ?, updated_at = ?
                WHERE business_slug = ?
                """,
                (
                    publish_target,
                    public_url,
                    publish_status,
                    published_at,
                    receipt_path,
                    blocker,
                    _json_dumps(metadata),
                    _now(),
                    slug,
                ),
            )
            self._rewrite_app_files(conn, slug)
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload=metadata["takyon_publish"])
            return {
                "action": action,
                "business": slug,
                "publish_status": publish_status,
                "public_url": public_url,
                "publish_target": publish_target,
                "receipt_path": receipt_path,
                "blocker": blocker,
            }

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
            if _db_backend() == "postgres":
                # Canonical Postgres plan write: app_entitlements.upsert_plan_policy owns app_plan_policies
                # (migration 0006 dropped the dead stripe_payment_link_* columns) and folds plan-validation
                # warnings into metadata itself, so pass the RAW metadata dict — folding here too would
                # double the warnings. plan_key is re-read from the persisted policy for receipt fidelity.
                leaves = self._app_leaves()
                try:
                    with self._leaf_conn(conn) as raw:
                        policy = leaves["entitlements"].upsert_plan_policy(
                            raw,
                            slug,
                            plan_key,
                            tier=tier,
                            price_cents=price_cents,
                            currency=str(op.get("currency") or "usd").lower(),
                            billing_interval=interval,
                            included_ai_budget_microusd=int(float(op.get("included_ai_budget_microusd") or 0)),
                            included_action_quota=included_action_quota,
                            allow_overage=allow_overage,
                            stripe_product_id=op.get("stripe_product_id"),
                            stripe_price_id=op.get("stripe_price_id"),
                            source=str(op.get("source") or "takyon"),
                            notes=str(op.get("notes") or ""),
                            metadata=metadata,
                        )
                except leaves["entitlements"].EntitlementError as exc:
                    raise TakyonError(str(exc)) from exc
                plan_key = policy.plan_key
            else:
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
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"plan_key": plan_key, "price_cents": price_cents})
            return {"action": action, "business": slug, "plan_key": plan_key}

        if action == "app.customer.upsert":
            email = _normalize_email(str(op.get("email") or ""))
            now = _now()
            user_id = op.get("id") or uuid.uuid4().hex
            if _db_backend() == "postgres":
                # Canonical Postgres sub-user write: app_identity.upsert_app_user owns app_users. It is
                # deliberately narrower than the SQLite op — it forces status='active' and does not persist
                # a caller tier/metadata/custom-id — which is acceptable here: effective tier is governed by
                # entitlements (_sync_user_tier), 'active' is already this op's default status, and the
                # metadata blob on app_users is not read by any downstream operator path.
                leaves = self._app_leaves()
                try:
                    with self._leaf_conn(conn) as raw:
                        user = leaves["identity"].upsert_app_user(raw, slug, email, name=op.get("name"))
                except leaves["identity"].AppIdentityError as exc:
                    raise TakyonError(str(exc)) from exc
                app_user_id = user.id
            else:
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
                app_user_id = row["id"]
            self._rewrite_app_files(conn, slug)
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"app_user_id": app_user_id, "email": email})
            return {"action": action, "business": slug, "app_user_id": app_user_id, "email": email}

        if action == "app.entitlement.upsert":
            if _db_backend() == "postgres":
                # Canonical Postgres entitlement write: app_entitlements.grant_entitlement owns the grant.
                # It auto-provisions the sub-user from email (so no recursive customer.upsert is needed),
                # enforces the SAME anti-fake-billing rule, and resyncs app_users.tier atomically — i.e. the
                # whole semantic of the SQLite block below, in the leaf. Pre-check the id/email requirement
                # here so the operator surfaces its own message before the leaf is touched.
                if not op.get("app_user_id") and not op.get("email"):
                    raise TakyonError("app entitlement requires app_user_id or email")
                source_value = str(op.get("source") or "manual")
                metadata = op.get("metadata") or {}
                if not isinstance(metadata, dict):
                    metadata = {"value": metadata}
                leaves = self._app_leaves()
                try:
                    with self._leaf_conn(conn) as raw:
                        ent, tier = leaves["entitlements"].grant_entitlement(
                            raw,
                            slug,
                            app_user_id=(str(op.get("app_user_id")) if op.get("app_user_id") else None),
                            email=(_normalize_email(str(op.get("email"))) if op.get("email") else None),
                            tier=str(op.get("tier") or "free"),
                            status=str(op.get("status") or "active"),
                            source=source_value,
                            stripe_customer_id=op.get("stripe_customer_id"),
                            stripe_subscription_id=op.get("stripe_subscription_id"),
                            stripe_checkout_session_id=op.get("stripe_checkout_session_id"),
                            plan_key=op.get("plan_key"),
                            current_period_end=op.get("current_period_end"),
                            metadata=metadata,
                        )
                except (leaves["entitlements"].EntitlementError, leaves["identity"].AppIdentityError) as exc:
                    raise TakyonError(str(exc)) from exc
                user_id = ent.app_user_id
                entitlement_id = ent.id
            else:
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
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"app_user_id": user_id, "tier": tier, "source": source_value})
            return {"action": action, "business": slug, "app_user_id": user_id, "entitlement": entitlement_id, "tier": tier}

        if action == "app.usage.record":
            app_user_id = op.get("app_user_id")
            actual = int(float(op.get("actual_cost_microusd") or 0))
            estimated = int(float(op.get("estimated_cost_microusd") or actual or 0))
            if actual < 0 or estimated < 0:
                raise TakyonError("usage costs must be non-negative")
            event_id = op.get("id") or uuid.uuid4().hex
            if _db_backend() == "postgres":
                # Canonical Postgres usage write: app_usage.record_completed_usage owns app_usage_events and
                # is REQUIRED here — the Postgres table mandates a NOT NULL reservation_key the SQLite INSERT
                # never set. It row-locks the budget, re-checks the cap atomically against committed spend,
                # and writes the completed row in one transaction (invariant #8: the cap is enforced, not
                # raced), so the store-side non-atomic SUM pre-check below is skipped on Postgres. The op id
                # is the idempotent reservation_key; the real persisted event id is read back for the receipt.
                leaves = self._app_leaves()
                try:
                    with self._leaf_conn(conn) as raw:
                        event = leaves["usage"].record_completed_usage(
                            raw,
                            slug,
                            actual_cost_microusd=actual,
                            reservation_key=event_id,
                            estimated_cost_microusd=estimated,
                            app_user_id=app_user_id,
                            app_user_tier=op.get("app_user_tier"),
                            purpose=str(op.get("purpose") or "product_usage"),
                            route=str(op.get("route") or "app"),
                            input_tokens=op.get("input_tokens"),
                            output_tokens=op.get("output_tokens"),
                            provider_request_id=op.get("provider_request_id"),
                            provider=op.get("provider"),
                            model=op.get("model"),
                            metadata=op.get("metadata") or {},
                        )
                except leaves["usage"].AppUsageError as exc:
                    raise TakyonError(str(exc)) from exc
                event_id = event.id
            else:
                if app_user_id and not conn.execute("SELECT 1 FROM app_users WHERE business_slug = ? AND id = ?", (slug, app_user_id)).fetchone():
                    raise TakyonError(f"app user not found: {app_user_id}")
                budget = self._ensure_app_budget(conn, slug)
                used = conn.execute(
                    "SELECT COALESCE(SUM(actual_cost_microusd), 0) AS total FROM app_usage_events WHERE business_slug = ? AND created_at >= ?",
                    (slug, budget["current_period_start"]),
                ).fetchone()["total"]
                if int(used or 0) + actual > int(budget["hard_limit_microusd"] or 0):
                    raise TakyonError(f"app usage would exceed budget cap {budget['hard_limit_microusd']} microusd")
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
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/app", business_slug=slug, event_type=action, payload={"usage_event": event_id, "actual_cost_microusd": actual})
            return {"action": action, "business": slug, "usage_event": event_id, "actual_cost_microusd": actual}

        if action == "workspace.upsert":
            path_text = _canonical_business_relpath(str(op.get("path") or op.get("workspace") or ""))
            rel = Path(path_text)
            kind = str(op.get("kind") or "workspace")
            status = str(op.get("status") or "active")
            budget = _normalize_budget_spec(op.get("budget"))
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
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=f"business:{slug}/workspace:{path_text}", business_slug=slug, event_type=action, payload={"reason": reason, "actor": actor, "metadata": metadata})
            return {"action": action, "business": slug, "workspace": path_text}

        if action in {"artifact.write", "memory.write"}:
            raw_path = str(op.get("path") or "")
            if action == "memory.write" and not raw_path.startswith("research/"):
                raw_path = f"research/{raw_path}"
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
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"path": rel, "reason": reason, "actor": actor})
            if rel == "metrics/summary.md":
                self._record_event(
                    conn,
                    scope=f"business:{slug}",
                    business_slug=slug,
                    event_type="business.pulse.snapshot",
                    payload={
                        "generated_at": _now(),
                        "pulse_path": rel,
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "source": "metrics/summary.md write",
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
            self._sync_business_workspace_remote(slug)
            self._record_event(conn, scope=target_scope, business_slug=slug, event_type=action, payload={"path": rel, "reason": reason, "actor": actor})
            if rel == "metrics/summary.md":
                self._record_event(
                    conn,
                    scope=f"business:{slug}",
                    business_slug=slug,
                    event_type="business.pulse.snapshot",
                    payload={
                        "generated_at": _now(),
                        "pulse_path": rel,
                        "content_sha256": hashlib.sha256(updated_content.encode("utf-8")).hexdigest(),
                        "source": "metrics/summary.md patch",
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
                f"INSERT INTO {self._work_requests_table()} (id, scope, business_slug, kind, status, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
            body = _normalize_outreach_body(op.get("body"))
            if not body:
                raise TakyonError("outreach.local_publish body is required")
            channel = _file_slug(str(op.get("channel") or op.get("provider") or "outreach"), "outreach")
            target = str(op.get("target") or op.get("recipient") or "local-target").strip() or "local-target"
            subject = str(op.get("subject") or op.get("title") or f"Test outreach to {target}").strip()
            provider = str(op.get("provider") or channel).strip()
            metadata = op.get("metadata") if isinstance(op.get("metadata"), dict) else {}
            destination_url = _outreach_destination_url(
                channel=channel,
                provider=provider,
                target=target,
                destination_url=op.get("destination_url"),
                metadata=metadata,
            )
            destination_label = str(op.get("destination_label") or metadata.get("destination_label") or "").strip()
            publish_id = str(op.get("id") or uuid.uuid4().hex)
            created_at = _now()
            file_stem = f"{created_at[:10]}-{_file_slug(target, 'target')}-{publish_id[:8]}"
            rel = f"distribution/local-published/{channel}/{file_stem}.md"
            receipt_rel = f"metrics/receipts/outreach/{publish_id}.json"
            _atomic_write_text(
                self._business_root(slug) / rel,
                _outreach_artifact_markdown(
                    subject,
                    body,
                    destination_url=destination_url,
                    destination_label=destination_label,
                ),
            )
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
            if destination_url:
                receipt["destination_url"] = destination_url
            if destination_label:
                receipt["destination_label"] = destination_label
            _atomic_write_text(self._business_root(slug) / receipt_rel, _json_dumps(receipt) + "\n")
            self._sync_business_workspace_remote(slug)

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
            result = {
                "action": action,
                "business": slug,
                "mode": "test",
                "local_publish_id": publish_id,
                "artifact": rel,
                "receipt": receipt_rel,
                "thread": thread["id"],
                "message": row["id"],
                "conversation_file": mirror,
                "conversation_corpus": corpus or "metrics/conversations/corpus/messages.jsonl",
                "external_side_effects": "suppressed",
                "sent": False,
            }
            if destination_url:
                result["destination_url"] = destination_url
            if destination_label:
                result["destination_label"] = destination_label
            return result

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
            return {"action": action, "business": slug, "thread": thread["id"], "message": row["id"], "file": file_path, "status": row["status"], "conversation_corpus": corpus or "metrics/conversations/corpus/messages.jsonl"}

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
            self._work_requests_table(): (
                f"SELECT id FROM {self._work_requests_table()} WHERE created_at < ? AND status IN "
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
            "This is a scheduled or manually triggered CEO wake, not the initial /create bootstrap turn.\n"
            "Start with business_calculate_pulse, then use takyon-business-metrics to write metrics/summary.md and record "
            "a business.pulse.snapshot event. Use concrete business_* tools to read state, update research and metrics files, "
            "create workspaces, enqueue jobs, allocate budget, and adjust the next wakeup if useful. Decide the highest "
            "expected-impact move under the business goal, budget, evidence, active campaigns, failures, and kill switches. Keep all business "
            "memory inside this business scope. Read prior wake notes from metrics/wake-history.md and compare "
            "this state to those notes, including business "
            "age, app/customer/revenue/usage signals, conversations, job progress, blockers, and stale assumptions. "
            "After reading business state, honor the business work_focus field as an operator constraint: "
            "marketing means choose only marketing, demand, research, outreach, pricing, conversion, campaign, or sales work; "
            "product means choose only product, offer, app runtime, checkout, surface, build, publication, or product-support work; "
            "all means no focus restriction. Safety/control reads, pulse, blocker recording, and changing the focus are always allowed. "
            "Use first-class business tools for requested videos/images, local outreach publication, websites, deploys, checkout, provider calls, and other concrete artifacts; if a gate is missing, report the gate instead of substituting a Markdown brief. "
            "Advance the outreach lifecycle: if no distribution campaign exists, start distribution/campaign/; if the current distribution campaign is incomplete, continue missing lanes, touches, or files; if complete but unreviewed, review distribution files, conversation mirrors, blockers, replies, elapsed time, and audit receipts only as needed; if replies exist, inspect them directly with takyon-x or takyon-reddit when the channel is clear, or load takyon-conversation-followup to compress them into follow-up decisions; if no replies after review, choose the next campaign, angle, lane, or offer change. "
            "Do not narrate private setup with phrases like 'Good, I have the full business context' or 'Now I will'. "
            "Think holistically about whether the business or current strategy has gotten stale from wake cadence, "
            "elapsed time, and traction movement; if stale, make a drastic strategic change instead of continuing "
            "the same motion. "
            "Append a compact wake snapshot to metrics/wake-history.md for future comparison. Never delete prior metrics, "
            "metric, event, conversation, ledger, job, or wake data during a wake. "
            "Honor business mode: in test mode, keep product/website build and "
            "publication, app rails, distribution files, hidden audit receipts, conversations, and follow-up review active. Suppress external outreach, "
            "acquisition, paid spend, customer charging, and outreach/marketing email delivery."
        )

    def _ceo_cron_toolsets(self) -> list[str]:
        return ["takyon", "web", "skills", "todo"]

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
                    "skills": [],
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

        if _db_backend() == "postgres":
            from cron.jobs import parse_schedule

            try:
                from . import wakes
                from .policy import expensive_threshold_cents
            except ImportError:  # pragma: no cover - alternate load path as a top-level package
                from plugins.takyon import wakes
                from plugins.takyon.policy import expensive_threshold_cents

            parsed = parse_schedule(schedule)
            if str(parsed.get("kind") or "") != "interval":
                raise TakyonError(
                    "Postgres CEO wake schedules currently require an interval cadence like "
                    "'every 6h'."
                )
            interval_seconds = max(60, int(parsed.get("minutes") or 0) * 60)
            with self._connect() as conn:
                with self._leaf_conn(conn) as raw:
                    existing = wakes.get_wake_schedule(raw, slug)
                    wakes.upsert_wake_schedule(
                        raw,
                        slug,
                        interval_seconds=interval_seconds,
                        kind="ceo_wake",
                        enabled=True,
                        payload={"estimate_cents": expensive_threshold_cents()},
                    )
            return {
                "wake_schedule": slug,
                "schedule": str(parsed.get("display") or schedule),
                "updated": existing is not None,
                "interval_seconds": interval_seconds,
                "reason": reason,
            }

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
                    "skills": [],
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
            skills=[],
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


def _ugc_ad_record(args: dict[str, Any]) -> dict[str, Any]:
    value = args.get("value")
    if not isinstance(value, dict):
        raise TakyonError("value is required")

    record = dict(value)
    slug = _file_slug(str(record.get("slug") or args.get("slug") or ""), "ugc-ad")
    path = _safe_relpath(str(record.get("path") or args.get("path") or ""), field="value.path").as_posix()
    expected_prefix = f"product/ugc-ads/{slug}/"
    if not path.startswith(expected_prefix):
        raise TakyonError(f"value.path must stay under {expected_prefix}")
    if Path(path).name != "ad.mp4":
        raise TakyonError("value.path must point to ad.mp4")

    seconds = record.get("seconds")
    if seconds not in (None, ""):
        seconds = float(seconds)
        if seconds < 0:
            raise TakyonError("value.seconds must be non-negative")
        record["seconds"] = seconds

    n_clips = record.get("n_clips")
    if n_clips not in (None, ""):
        n_clips = int(n_clips)
        if n_clips < 0:
            raise TakyonError("value.n_clips must be non-negative")
        record["n_clips"] = n_clips

    record["slug"] = slug
    record["path"] = path
    return record


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
            elif ecosystem in {"repo-node-dependencies", "node-dependencies", "claude-agent-sdk"}:
                ensure_results.append({
                    "ecosystem": ecosystem,
                    **_ensure_repo_node_dependencies(("@anthropic-ai/claude-agent-sdk",)),
                })
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
        "runtime_features": args.get("runtime_features"),
        "routes": args.get("routes") or [],
        "theme": args.get("theme") or {"source": "business design brief"},
        "constraints": args.get("constraints") or {},
        "publish_target": args.get("publish_target"),
        "publish_policy": args.get("publish_policy") or _DEFAULT_PRODUCT_PUBLISH_POLICY,
        "mode_behavior": args.get("mode_behavior") or _DEFAULT_PRODUCT_MODE_BEHAVIOR,
        "done_gate": args.get("done_gate") or _DEFAULT_PRODUCT_DONE_GATE,
        "notes": args.get("notes") or "",
        "metadata": args.get("metadata") or {},
    }
    return _commit_tool(args, operation)


def _finalize_product_surface_verification(
    *,
    store: "TakyonStore",
    business: str,
    surface: dict[str, Any],
    source_path: str,
    publish_target: str,
    requested_publish_policy: str,
    publish_policy: str,
    install: bool,
    timeout_seconds: int,
    receipt_path: str,
    verification_source: str,
) -> dict[str, Any]:
    verification = _verify_product_surface_path(
        store._business_root(business),
        source_path,
        surface=surface,
        install=install,
        timeout_seconds=timeout_seconds,
    )
    if verification.get("status") == "passed":
        inventory = verification.get("inventory") if isinstance(verification.get("inventory"), dict) else {}
        valid_surface, surface_error = _validate_product_surface_contract(inventory, surface)
        if not valid_surface:
            verification = {
                **verification,
                "status": "blocked",
                "error": surface_error,
            }
    if requested_publish_policy and _is_shared_renderer_publish_policy(requested_publish_policy):
        warnings = list(verification.get("warnings") or [])
        warnings.append("legacy shared_renderer policy ignored; publishing the real product source_path")
        verification = {
            **verification,
            "requested_publish_policy": requested_publish_policy,
            "effective_publish_policy": publish_policy,
            "warnings": warnings,
        }
    publish = _publish_product_surface_path(
        business_root=store._business_root(business),
        slug=business,
        source_path=str(verification.get("source_path") or source_path),
        publish_target=publish_target,
    )
    done_gate_status = "passed" if verification.get("status") == "passed" and publish.get("status") == "published" else "blocked"
    inventory = verification.get("inventory") if isinstance(verification.get("inventory"), dict) else {}
    if not inventory:
        inventory = _product_inventory(store._business_root(business), str(verification.get("source_path") or source_path), surface=surface)
    inventory = {
        **inventory,
        "public_url": publish.get("public_url") or inventory.get("public_url") or "",
        "publish_receipt_path": receipt_path,
    }
    return {
        **verification,
        "business": business,
        "receipt_path": receipt_path,
        "publish": publish,
        "inventory": inventory,
        "done_gate": _DEFAULT_PRODUCT_DONE_GATE,
        "done_gate_status": done_gate_status,
        "blocker": "" if done_gate_status == "passed" else (publish.get("blocker") or verification.get("error") or "product surface is not published"),
        "source": verification_source,
    }


def _product_surface_verification_operations(
    *,
    business: str,
    verification: dict[str, Any],
    surface: dict[str, Any],
    publish_target: str,
    publish_policy: str,
    requested_publish_policy: str,
    activate_on_success: bool,
) -> list[dict[str, Any]]:
    publish = verification.get("publish") if isinstance(verification.get("publish"), dict) else {}
    operations: list[dict[str, Any]] = [
        {
            "action": "artifact.write",
            "business": business,
            "path": str(verification["receipt_path"]),
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
                "publish_policy": publish_policy,
                "requested_publish_policy": requested_publish_policy,
                "error": verification.get("error"),
                "warnings": verification.get("warnings") or [],
                "inventory": verification.get("inventory") if isinstance(verification.get("inventory"), dict) else {},
                "receipt_path": verification.get("receipt_path"),
                "publish": publish,
                "done_gate_status": verification.get("done_gate_status"),
                "blocker": verification.get("blocker") or "",
            },
        },
    ]
    publish_blocker = publish.get("blocker") or verification.get("blocker") or verification.get("error") or ""
    publish_succeeded = publish.get("status") == "published"
    verification_passed = verification.get("status") == "passed"
    if activate_on_success and (verification_passed or publish_succeeded):
        next_status = "active" if publish.get("status") == "published" else "publish_blocked"
        operations.append(
            {
                "action": "app.surface.upsert",
                "business": business,
                "status": next_status,
                "design_brief_path": surface.get("design_brief_path") or "product/design-brief.md",
                "source_path": verification.get("source_path"),
                "runtime_api_base": surface.get("runtime_api_base"),
                "routes": surface.get("routes") or [],
                "theme": surface.get("theme") or {"source": "business design brief"},
                "constraints": surface.get("constraints") or {},
                "publish_target": publish_target,
                "publish_policy": publish_policy,
                "mode_behavior": surface.get("mode_behavior") or _DEFAULT_PRODUCT_MODE_BEHAVIOR,
                "done_gate": surface.get("done_gate") or _DEFAULT_PRODUCT_DONE_GATE,
                "notes": surface.get("notes") or "",
                "metadata": {**(surface.get("metadata") if isinstance(surface.get("metadata"), dict) else {}), "verification_receipt": verification.get("receipt_path")},
            }
        )
        operations.append(
            {
                "action": "app.surface.publish_result",
                "business": business,
                "publish_status": publish.get("status") or "blocked",
                "publish_target": publish_target,
                "public_url": publish.get("public_url") or "",
                "published_at": publish.get("published_at") or "",
                "receipt_path": verification.get("receipt_path"),
                "publish_source_path": publish.get("publish_source_path") or verification.get("source_path") or "",
                "blocker": publish_blocker,
            }
        )
    return operations


def handle_business_verify_product_surface(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or ""))
        if not business:
            raise TakyonError("business is required")
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise TakyonError("idempotency_key is required")
        summary = store.read(scope=f"business:{business}", query="summary", include=["app"])
        app = summary.get("app") if isinstance(summary.get("app"), dict) else {}
        surface = app.get("surface") or app.get("surface_contract") or {}
        if not isinstance(surface, dict):
            surface = {}
        source_path = str(args.get("source_path") or "").strip()
        if not source_path:
            source_path = str(surface.get("source_path") or "product/site")
        publish_target = _product_publish_target(business, args.get("publish_target") or surface.get("publish_target"))
        requested_publish_policy = str(args.get("publish_policy") or surface.get("publish_policy") or _DEFAULT_PRODUCT_PUBLISH_POLICY).strip() or _DEFAULT_PRODUCT_PUBLISH_POLICY
        legacy_shared_renderer = _is_shared_renderer_publish_policy(requested_publish_policy)
        publish_policy = "publish_after_verify" if legacy_shared_renderer else requested_publish_policy
        install = _boolish(args.get("install"), default=True)
        timeout_seconds = _clamp_int(args.get("timeout_seconds"), default=300, minimum=15, maximum=900)
        receipt_path = f"metrics/receipts/product-surface/{uuid.uuid4().hex}.json"
        verification = _finalize_product_surface_verification(
            store=store,
            business=business,
            surface=surface,
            source_path=source_path,
            publish_target=publish_target,
            requested_publish_policy=requested_publish_policy,
            publish_policy=publish_policy,
            install=install,
            timeout_seconds=timeout_seconds,
            receipt_path=receipt_path,
            verification_source="business_verify_product_surface",
        )
        result = store.commit(
            scope=f"business:{business}",
            operations=_product_surface_verification_operations(
                business=business,
                verification=verification,
                surface=surface,
                publish_target=publish_target,
                publish_policy=publish_policy,
                requested_publish_policy=requested_publish_policy,
                activate_on_success=_boolish(args.get("activate_on_success"), default=True),
            ),
            idempotency_key=idempotency_key,
            reason=args.get("reason") or "product surface publication",
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
                receipt_rel = f"metrics/receipts/app-magic-link/{link_id}.json"
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
                receipt_rel = f"metrics/receipts/app-checkout/{intent_id}.json"
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
        if _db_backend() == "postgres":
            # Canonical Postgres reconciliation. app_payments.record_webhook_and_process owns the
            # webhook_events dedup, the checkout/subscription dispatch, AND the net-new owner custody
            # accrual (gross minus the STRIPE_CONNECT_APPLICATION_FEE_BPS app fee) that the legacy
            # SQLite path below never performed — closing the flow-B hole where a sub-user payment
            # reconciled but never showed in the owner's custody balance. Delegated over the raw psycopg
            # connection lent by _leaf_conn, the same store->leaf pattern as seed_platform_owner: the
            # leaf's `with conn.transaction()` is the atomic unit and _PGConn commits/closes on exit.
            leaves = store._app_leaves()
            try:
                with store._connect() as conn:
                    with store._leaf_conn(conn) as raw:
                        outcome = leaves["payments"].record_webhook_and_process(raw, event)
            except leaves["payments"].AppPaymentError as exc:
                raise TakyonError(str(exc)) from exc
            # Flatten the leaf envelope ({provider_event_id, type, deduplicated, processed}) to the
            # SAME tool shape the SQLite path returns: top-level ids + `processed` = the inner
            # reconciliation dict (which is None on a deduplicated replay).
            return tool_result({
                "success": True,
                "provider_event_id": outcome.get("provider_event_id", event_id),
                "type": outcome.get("type", event_type),
                "deduplicated": outcome.get("deduplicated", False),
                "processed": outcome.get("processed"),
            })
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
        "destination_url": args.get("destination_url") or args.get("intended_destination_url"),
        "destination_label": args.get("destination_label"),
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
        body = _normalize_outreach_body(args.get("body") or args.get("content"))
        if not body:
            raise TakyonError("body is required")
        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            business_mode = str(business_row.get("mode") or "live")
            canonical_product_url = _canonical_product_url(store, conn, business)

        body, canonical_replacements = _canonicalize_business_product_links(
            body,
            business=business,
            canonical_url=canonical_product_url,
        )
        metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
        metadata = {
            **metadata,
            "canonical_product_url": canonical_product_url,
        }
        if canonical_replacements:
            metadata["canonicalized_product_links"] = canonical_replacements

        canonical_args = dict(args)
        canonical_args["business"] = business
        canonical_args["body"] = body
        canonical_args["metadata"] = metadata
        if business_mode == "test":
            return handle_business_publish_test_outreach(canonical_args)

        channel = str(args.get("channel") or args.get("provider") or "outreach").strip()
        provider = str(args.get("provider") or channel).strip()
        target = args.get("target") or args.get("recipient")
        destination_url = _outreach_destination_url(
            channel=channel,
            provider=provider,
            target=target,
            destination_url=args.get("destination_url") or args.get("intended_destination_url"),
            metadata=metadata,
        )
        destination_label = str(args.get("destination_label") or metadata.get("destination_label") or "").strip()
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
            "target": target,
            "recipient": args.get("recipient"),
            "subject": args.get("subject") or args.get("title"),
            "body": body,
            "destination_url": destination_url,
            "destination_label": destination_label,
            "thread_external_id": args.get("thread_external_id"),
            "metadata": metadata,
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


def handle_business_ugc_ad_write(args: dict, **_: Any) -> str:
    try:
        store = _store()
        business = _slugify(str(args.get("business") or ""))
        if not business:
            raise TakyonError("business is required")
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise TakyonError("idempotency_key is required")

        record = _ugc_ad_record(args)
        asset_path = store._resolve_business_file(business, record["path"])
        publication_dir = asset_path.parent
        publication_rel = str(publication_dir.relative_to(store._business_root(business)))
        if not asset_path.is_file():
            raise TakyonError(f"ugc ad file not found: {record['path']}")
        for filename in ("ad.mp4", "script.json", "reference.png"):
            if not (publication_dir / filename).is_file():
                raise TakyonError(f"ugc ad publication is incomplete; missing {publication_rel}/{filename}")

        result = store.commit(
            scope=f"business:{business}/product:ugc-ads/{record['slug']}",
            operations=[
                {
                    "action": "event.record",
                    "business": business,
                    "event_type": "ugc_ad.write",
                    "payload": {
                        "slug": record["slug"],
                        "path": record["path"],
                        "seconds": record.get("seconds"),
                        "n_clips": record.get("n_clips"),
                        "script": record.get("script"),
                        "publication_dir": publication_rel,
                    },
                }
            ],
            idempotency_key=idempotency_key,
            reason=args.get("reason") or "record ugc video ad",
            actor=args.get("actor") or "agent",
        )
        return tool_result(
            {
                "success": True,
                "action": "business_ugc_ad_write",
                "business": business,
                "slug": record["slug"],
                "path": record["path"],
                "publication_dir": publication_rel,
                "files": [
                    f"{publication_rel}/ad.mp4",
                    f"{publication_rel}/script.json",
                    f"{publication_rel}/reference.png",
                ],
                "event": result.get("event"),
                "value": record,
            }
        )
    except Exception as exc:
        return tool_error(str(exc), success=False)


_META_DEFAULT_GRAPH_VERSION = "v23.0"
_META_MAX_DAILY_BUDGET_USD_DEFAULT = 50.0
_META_VALID_CTA = {
    "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "DOWNLOAD", "GET_OFFER", "SUBSCRIBE",
    "BOOK_TRAVEL", "CONTACT_US", "APPLY_NOW", "GET_QUOTE", "WATCH_MORE",
    "NO_BUTTON", "ORDER_NOW", "SEE_MENU", "INSTALL_MOBILE_APP",
}


def _meta_daily_budget_cap() -> float:
    raw = os.getenv("TAKYON_META_MAX_DAILY_BUDGET_USD")
    try:
        return float(raw) if raw else _META_MAX_DAILY_BUDGET_USD_DEFAULT
    except (TypeError, ValueError):
        return _META_MAX_DAILY_BUDGET_USD_DEFAULT


def _meta_config(*, require_token: bool = True) -> dict[str, Any]:
    """Resolve Meta Marketing API config from env. Never returns the token to callers that print."""
    load_takyon_env()
    token = (
        os.getenv("META_SYSTEM_USER_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN")
        or os.getenv("FACEBOOK_ACCESS_TOKEN")
        or ""
    ).strip()
    if require_token and not token:
        raise TakyonError(
            "Meta action requires META_SYSTEM_USER_ACCESS_TOKEN or META_ACCESS_TOKEN"
        )
    version = (os.getenv("META_GRAPH_VERSION") or _META_DEFAULT_GRAPH_VERSION).strip().lstrip("/")
    if not version:
        version = _META_DEFAULT_GRAPH_VERSION
    elif not version.startswith("v"):
        version = f"v{version}"
    return {
        "token": token,
        "version": version,
        "ad_account_id": (os.getenv("META_AD_ACCOUNT_ID") or "").strip(),
        "page_id": (os.getenv("META_PAGE_ID") or "").strip(),
    }


def _meta_account_path(ad_account_id: str) -> str:
    acct = str(ad_account_id or "").strip()
    if not acct:
        raise TakyonError("Meta launch requires an ad account id (META_AD_ACCOUNT_ID or ad_account_id)")
    return acct if acct.startswith("act_") else f"act_{acct}"


def _meta_graph(
    method: str,
    path: str,
    params: dict[str, Any],
    cfg: dict[str, Any],
    *,
    host: str = "graph.facebook.com",
    timeout: int = 60,
) -> dict[str, Any]:
    """Call the Meta Graph API. Errors surface Meta's body but never the access token."""
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    clean["access_token"] = cfg["token"]
    rel = path.lstrip("/")
    url = f"https://{host}/{cfg['version']}/{rel}"
    method = method.upper()
    if method == "GET":
        request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(clean)}", method="GET")
    else:
        data = urllib.parse.urlencode(clean).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method=method,
        )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TakyonError(f"Meta Graph {method} /{rel} failed: {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise TakyonError(f"Meta Graph {method} /{rel} connection error: {exc.reason}") from exc


def _meta_upload_advideo(video_path: Path, cfg: dict[str, Any], *, name: str) -> str:
    """Upload a local mp4 as an AdVideo via multipart (graph-video host). Returns the video id."""
    acct = _meta_account_path(cfg["ad_account_id"])
    url = f"https://graph-video.facebook.com/{cfg['version']}/{acct}/advideos"
    try:
        import httpx  # lazy: only the live multipart upload needs it
    except Exception as exc:  # pragma: no cover - dependency missing
        raise TakyonError("Meta video upload requires the httpx package") from exc
    try:
        with video_path.open("rb") as handle:
            resp = httpx.post(
                url,
                data={"access_token": cfg["token"], "name": name},
                files={"source": (video_path.name, handle, "video/mp4")},
                timeout=180.0,
            )
    except httpx.HTTPError as exc:
        raise TakyonError(f"Meta video upload connection error: {exc}") from exc
    if resp.status_code >= 400:
        raise TakyonError(f"Meta video upload failed: {resp.status_code} {resp.text}")
    video_id = str((resp.json() or {}).get("id") or "").strip()
    if not video_id:
        raise TakyonError(f"Meta video upload returned no id: {resp.text}")
    return video_id


def _meta_video_thumbnail(video_id: str, cfg: dict[str, Any]) -> str | None:
    try:
        data = _meta_graph("GET", f"{video_id}/thumbnails", {"fields": "uri,is_preferred"}, cfg)
    except TakyonError:
        return None
    items = data.get("data") if isinstance(data, dict) else None
    if not items:
        return None
    preferred = next((i for i in items if i.get("is_preferred")), items[0])
    uri = str(preferred.get("uri") or "").strip()
    return uri or None


def _meta_launch_plan(args: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    if _boolish(args.get("activate"), default=False) or str(args.get("status") or "").strip().upper() == "ACTIVE":
        raise TakyonError(
            "business_meta_ad_launch only creates PAUSED objects; activation is intentionally not supported by this tool"
        )

    ad_video_path = _safe_relpath(str(args.get("ad_video_path") or ""), field="ad_video_path").as_posix()
    if Path(ad_video_path).suffix.lower() != ".mp4":
        raise TakyonError("ad_video_path must point to an .mp4 produced by the ugc-video-ad skill")

    campaign = args.get("campaign") if isinstance(args.get("campaign"), dict) else {}
    adset = args.get("adset") if isinstance(args.get("adset"), dict) else {}
    ad = args.get("ad") if isinstance(args.get("ad"), dict) else {}

    slug = _file_slug(
        str(args.get("slug") or campaign.get("name") or Path(ad_video_path).parent.name or "meta-ad"),
        "meta-ad",
    )

    raw_budget = adset.get("daily_budget_usd", adset.get("daily_budget"))
    try:
        daily_budget_usd = float(raw_budget) if raw_budget not in (None, "") else 5.0
    except (TypeError, ValueError):
        raise TakyonError("adset.daily_budget_usd must be a number (USD per day)")
    if daily_budget_usd <= 0:
        raise TakyonError("adset.daily_budget_usd must be positive")
    cap = _meta_daily_budget_cap()
    if daily_budget_usd > cap:
        raise TakyonError(
            f"adset.daily_budget_usd {daily_budget_usd} exceeds the safety cap of {cap} USD/day "
            "(set TAKYON_META_MAX_DAILY_BUDGET_USD to change)"
        )

    link = str(ad.get("link") or "").strip()
    if not link:
        raise TakyonError("ad.link is required (the destination URL the ad sends people to)")
    cta = str(ad.get("call_to_action") or "LEARN_MORE").strip().upper()
    if cta not in _META_VALID_CTA:
        raise TakyonError(f"ad.call_to_action '{cta}' is not a recognized Meta CTA type")

    targeting = adset.get("targeting") if isinstance(adset.get("targeting"), dict) else {"geo_locations": {"countries": ["US"]}}

    return {
        "slug": slug,
        "ad_video_path": ad_video_path,
        "objective": str(campaign.get("objective") or "OUTCOME_TRAFFIC").strip().upper(),
        "campaign_name": str(campaign.get("name") or f"{slug} campaign").strip(),
        "adset_name": str(adset.get("name") or f"{slug} ad set").strip(),
        "optimization_goal": str(adset.get("optimization_goal") or "LINK_CLICKS").strip().upper(),
        "billing_event": str(adset.get("billing_event") or "IMPRESSIONS").strip().upper(),
        "daily_budget_usd": round(daily_budget_usd, 2),
        "daily_budget_cents": int(round(daily_budget_usd * 100)),
        "targeting": targeting,
        "ad_name": str(ad.get("name") or f"{slug} ad").strip(),
        "message": str(ad.get("message") or "").strip(),
        "link": link,
        "call_to_action": cta,
        "page_id": str(ad.get("page_id") or cfg.get("page_id") or "").strip(),
        "ad_account_id": str(args.get("ad_account_id") or cfg.get("ad_account_id") or "").strip(),
        "image_url": (str(ad.get("image_url") or ad.get("thumbnail_url") or "").strip() or None),
    }


def handle_business_meta_ad_launch(args: dict, **_: Any) -> str:
    """Preflight or launch a PAUSED Meta ad from a UGC video. Never activates; never spends."""
    created: dict[str, Any] = {}
    receipt_rel: str | None = None
    business = ""
    try:
        store = _store()
        business = _slugify(str(args.get("business") or args.get("business_slug") or ""))
        if not business:
            raise TakyonError("business is required")
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise TakyonError("idempotency_key is required")
        mode = str(args.get("mode") or "launch").strip().lower()

        with store._connect() as conn:
            business_row = store._ensure_business(conn, business)
            business_mode = str(business_row.get("mode") or "live")

        cfg = _meta_config(require_token=(mode == "preflight" or business_mode != "test"))

        # ── read-only preflight: verify token + list ad accounts, create nothing ──
        if mode == "preflight":
            identity = _meta_graph("GET", "me", {"fields": "id,name"}, cfg)
            accounts = _meta_graph(
                "GET",
                "me/adaccounts",
                {"fields": "id,account_id,name,account_status,currency,is_prepay_account"},
                cfg,
            )
            return tool_result({
                "success": True,
                "action": "business_meta_ad_launch",
                "mode": "preflight",
                "read_only": True,
                "business": business,
                "business_mode": business_mode,
                "graph_version": cfg["version"],
                "identity": identity,
                "ad_accounts": accounts.get("data") if isinstance(accounts, dict) else None,
                "default_ad_account_id": cfg.get("ad_account_id") or None,
                "default_page_id": cfg.get("page_id") or None,
            })

        # ── launch (always PAUSED) ──
        plan = _meta_launch_plan(args, cfg)
        slug = plan["slug"]
        video_abs = store._resolve_business_file(business, plan["ad_video_path"])
        if not video_abs.is_file():
            raise TakyonError(
                f"ad video not found at {plan['ad_video_path']}; build it with the ugc-video-ad skill first"
            )

        pub_rel = f"distribution/meta-ads/{slug}"
        receipt_rel = f"{pub_rel}/receipt.json"
        receipt_abs = store._resolve_business_file(business, receipt_rel)

        # Idempotency guard: a receipt already written for this key means the Graph
        # objects exist — return it instead of creating duplicates on a retry.
        if receipt_abs.is_file():
            try:
                prior = json.loads(receipt_abs.read_text(encoding="utf-8"))
            except Exception:
                prior = None
            if isinstance(prior, dict) and prior.get("idempotency_key") == idempotency_key:
                return tool_result({
                    "success": True,
                    "action": "business_meta_ad_launch",
                    "business": business,
                    "slug": slug,
                    "idempotent": True,
                    "status": prior.get("status"),
                    "paused": True,
                    "receipt": receipt_rel,
                    "value": prior,
                })

        base_receipt = {
            "idempotency_key": idempotency_key,
            "business": business,
            "slug": slug,
            "paused": True,
            "ad_video_path": plan["ad_video_path"],
            "objective": plan["objective"],
            "daily_budget_usd": plan["daily_budget_usd"],
            "link": plan["link"],
            "call_to_action": plan["call_to_action"],
            "created_at": _now(),
        }

        # ── test mode: suppress all external calls, write a local receipt ──
        if business_mode == "test":
            receipt = {
                **base_receipt,
                "mode": "test",
                "status": "suppressed_test_mode",
                "external_side_effects": "suppressed",
                "note": "Test mode recorded the Meta ad launch plan locally; no objects were created on Meta.",
            }
            _atomic_write_text(receipt_abs, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            store.commit(
                scope=f"business:{business}/distribution:meta-ads/{slug}",
                operations=[{
                    "action": "event.record",
                    "business": business,
                    "event_type": "meta_ad.launch",
                    "payload": {**receipt, "publication_dir": pub_rel},
                }],
                idempotency_key=idempotency_key,
                reason=args.get("reason") or "record suppressed meta ad launch (test mode)",
                actor=args.get("actor") or "agent",
            )
            return tool_result({
                "success": True,
                "action": "business_meta_ad_launch",
                "business": business,
                "slug": slug,
                "mode": "test",
                "status": "suppressed_test_mode",
                "external_side_effects": "suppressed",
                "paused": True,
                "receipt": receipt_rel,
                "value": receipt,
            })

        # ── live mode: create everything PAUSED (no spend) ──
        if not plan["ad_account_id"]:
            raise TakyonError("live launch requires META_AD_ACCOUNT_ID or ad_account_id")
        if not plan["page_id"]:
            raise TakyonError("live launch requires META_PAGE_ID or ad.page_id (creatives must be tied to a Page)")
        cfg["ad_account_id"] = plan["ad_account_id"]
        acct = _meta_account_path(plan["ad_account_id"])

        # 1) upload the UGC mp4 as an AdVideo
        created["video_id"] = _meta_upload_advideo(video_abs, cfg, name=plan["ad_name"])

        # 2) creative (Meta requires a thumbnail image for a video creative)
        image_url = plan["image_url"] or _meta_video_thumbnail(created["video_id"], cfg)
        if not image_url:
            raise TakyonError(
                "Meta requires a thumbnail for a video creative but none was ready yet; "
                "pass ad.image_url or retry shortly after the video finishes processing"
            )
        story_spec = {
            "page_id": plan["page_id"],
            "video_data": {
                "video_id": created["video_id"],
                "message": plan["message"],
                "image_url": image_url,
                "call_to_action": {"type": plan["call_to_action"], "value": {"link": plan["link"]}},
            },
        }
        creative = _meta_graph("POST", f"{acct}/adcreatives", {
            "name": f"{plan['ad_name']} creative",
            "object_story_spec": json.dumps(story_spec),
        }, cfg)
        created["creative_id"] = str(creative.get("id") or "").strip()

        # 3) campaign (PAUSED)
        campaign = _meta_graph("POST", f"{acct}/campaigns", {
            "name": plan["campaign_name"],
            "objective": plan["objective"],
            "status": "PAUSED",
            "special_ad_categories": "[]",
        }, cfg)
        created["campaign_id"] = str(campaign.get("id") or "").strip()

        # 4) ad set (PAUSED, with its own daily budget)
        adset = _meta_graph("POST", f"{acct}/adsets", {
            "name": plan["adset_name"],
            "campaign_id": created["campaign_id"],
            "status": "PAUSED",
            "daily_budget": plan["daily_budget_cents"],
            "billing_event": plan["billing_event"],
            "optimization_goal": plan["optimization_goal"],
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "targeting": json.dumps(plan["targeting"]),
        }, cfg)
        created["adset_id"] = str(adset.get("id") or "").strip()

        # 5) ad (PAUSED)
        ad = _meta_graph("POST", f"{acct}/ads", {
            "name": plan["ad_name"],
            "adset_id": created["adset_id"],
            "status": "PAUSED",
            "creative": json.dumps({"creative_id": created["creative_id"]}),
        }, cfg)
        created["ad_id"] = str(ad.get("id") or "").strip()

        receipt = {
            **base_receipt,
            "mode": "live",
            "status": "created_paused",
            "external_side_effects": "created_paused_no_spend",
            "ad_account_id": acct,
            "page_id": plan["page_id"],
            "graph_version": cfg["version"],
            "ids": created,
            "thumbnail_url": image_url,
            "note": "All objects created PAUSED; nothing serves or spends until explicitly activated.",
        }
        _atomic_write_text(receipt_abs, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        store.commit(
            scope=f"business:{business}/distribution:meta-ads/{slug}",
            operations=[{
                "action": "event.record",
                "business": business,
                "event_type": "meta_ad.launch",
                "payload": {**receipt, "publication_dir": pub_rel},
            }],
            idempotency_key=idempotency_key,
            reason=args.get("reason") or "record meta ad launch (paused)",
            actor=args.get("actor") or "agent",
        )
        return tool_result({
            "success": True,
            "action": "business_meta_ad_launch",
            "business": business,
            "slug": slug,
            "mode": "live",
            "status": "created_paused",
            "paused": True,
            "ids": created,
            "receipt": receipt_rel,
            "value": receipt,
        })
    except Exception as exc:
        # Honest partial receipt: if some Meta objects were created before a later
        # step failed, persist what exists so it can be cleaned up — never fake success.
        if created and receipt_rel and business:
            try:
                partial_abs = _store()._resolve_business_file(business, receipt_rel)
                _atomic_write_text(
                    partial_abs,
                    json.dumps({
                        "status": "partial_failed",
                        "paused": True,
                        "ids": created,
                        "error": str(exc),
                        "created_at": _now(),
                    }, ensure_ascii=False, indent=2) + "\n",
                )
            except Exception:
                pass
        return tool_error(str(exc), success=False, created=created or None)


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


def handle_business_list_conversation_messages(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or args.get("business_slug") or ""))
        if not business:
            raise TakyonError("business is required")

        direction = str(args.get("direction") or "inbound").strip().lower()
        status = str(args.get("status") or "needs_response").strip().lower()
        source_filter = str(args.get("source") or "").strip()
        thread_id = str(args.get("thread_id") or "").strip()
        thread_external_id = str(args.get("thread_external_id") or "").strip()
        limit = _clamp_int(args.get("limit"), default=100, minimum=1, maximum=500)

        with store._connect() as conn:
            store._ensure_business(conn, business)
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
            if thread_id:
                filters.append("m.thread_id = ?")
                params.append(thread_id)
            if thread_external_id:
                filters.append("t.external_id = ?")
                params.append(thread_external_id)

            rows = conn.execute(
                f"""
                SELECT
                  m.*,
                  t.source AS thread_source,
                  t.external_id AS thread_external_id,
                  t.title AS thread_title,
                  t.url AS thread_url,
                  t.status AS thread_status
                FROM conversation_messages m
                JOIN conversation_threads t ON t.id = m.thread_id
                WHERE {" AND ".join(filters)}
                ORDER BY m.received_at DESC, m.created_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            messages = [store._row_to_dict(row) for row in rows]

        for message in messages:
            thread_meta = {
                "id": message.get("thread_id"),
                "source": message.get("thread_source") or message.get("source"),
                "external_id": message.get("thread_external_id") or message.get("thread_id"),
                "title": message.get("thread_title") or message.get("thread_id"),
                "url": message.get("thread_url"),
            }
            message["thread_file"] = store._conversation_thread_relpath(thread_meta)

        return tool_result(
            {
                "success": True,
                "action": "business_list_conversation_messages",
                "business": business,
                "filters": {
                    "direction": direction,
                    "status": status,
                    "source": source_filter,
                    "thread_id": thread_id,
                    "thread_external_id": thread_external_id,
                },
                "messages": messages,
            }
        )
    except Exception as exc:
        return tool_error(str(exc), success=False)


def handle_business_read_conversation_thread(args: dict, **_: Any) -> str:
    store = _store()
    try:
        business = _slugify(str(args.get("business") or args.get("business_slug") or ""))
        if not business:
            raise TakyonError("business is required")

        thread_id = str(args.get("thread_id") or "").strip()
        source_filter = str(args.get("source") or "").strip()
        thread_external_id = str(args.get("external_id") or args.get("thread_external_id") or "").strip()

        with store._connect() as conn:
            store._ensure_business(conn, business)
            if thread_id:
                thread_row = conn.execute(
                    "SELECT * FROM conversation_threads WHERE business_slug = ? AND id = ?",
                    (business, thread_id),
                ).fetchone()
            else:
                if not source_filter or not thread_external_id:
                    raise TakyonError("thread_id or source + external_id is required")
                thread_row = conn.execute(
                    "SELECT * FROM conversation_threads WHERE business_slug = ? AND source = ? AND external_id = ?",
                    (business, _file_slug(source_filter, "source"), thread_external_id),
                ).fetchone()
            if not thread_row:
                raise TakyonError("conversation thread not found")

            thread = store._row_to_dict(thread_row)
            rows = conn.execute(
                "SELECT * FROM conversation_messages WHERE business_slug = ? AND thread_id = ? ORDER BY received_at ASC, created_at ASC",
                (business, thread["id"]),
            ).fetchall()
            messages = [store._row_to_dict(row) for row in rows]

        return tool_result(
            {
                "success": True,
                "action": "business_read_conversation_thread",
                "business": business,
                "file": store._conversation_thread_relpath(thread),
                "thread": thread,
                "messages": messages,
            }
        )
    except Exception as exc:
        return tool_error(str(exc), success=False)


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
            if rel.startswith("metrics/receipts/"):
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
        app_summary = store.read(scope=f"business:{business}", query="summary", include=["app"], limit=20)
        app = app_summary.get("app") if isinstance(app_summary.get("app"), dict) else {}
        surface_for_worker = app.get("surface") or app.get("surface_contract") or {}

        business_root = store._business_root(business).resolve()
        workspace_path = business_root if workspace_raw in {".", ""} else store._resolve_business_file(business, workspace_raw).resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)
        if not workspace_path.is_dir():
            raise TakyonError(f"workspace is not a directory: {workspace_raw}")
        if business_root not in (workspace_path, *workspace_path.parents):
            raise TakyonError("workspace escaped business root")

        node = _resolve_runtime_executable("node")
        if not node:
            ensure_runtime = _ensure_javascript_runtime(package_manager=True)
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

        dependency_state = _ensure_repo_node_dependencies(("@anthropic-ai/claude-agent-sdk",))
        if not dependency_state.get("success"):
            missing = ", ".join(dependency_state.get("missing_packages") or ["@anthropic-ai/claude-agent-sdk"])
            raise TakyonError(
                "Claude Agent SDK dependencies unavailable before worker launch: "
                f"missing {missing}. {dependency_state.get('error') or 'run npm install in the Takyon repo root'}"
            )

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

        max_turns = _clamp_int(args.get("max_turns"), default=12, minimum=1, maximum=40)
        timeout_ms = _clamp_int(args.get("timeout_ms"), default=300_000, minimum=30_000, maximum=1_800_000)
        model = str(
            args.get("model")
            or os.getenv("TAKYON_CLAUDE_AGENT_MODEL")
            or _model_from_config("claude_agent_default", "deep_work_default")
            or DEFAULT_CLAUDE_AGENT_MODEL
        ).strip()
        guidance_skills = _normalize_guidance_skills(args.get("guidance_skills"))
        resolved_guidance_skills, guidance_block = _compose_worker_guidance_block(guidance_skills)
        workspace_contract = WORKSPACE_PATH_CONTRACT.format(workspace=workspace_raw)
        worker_instruction_parts = [instruction.rstrip()]
        if guidance_block:
            worker_instruction_parts.append(guidance_block)
        if _workspace_needs_customer_ai_copy_contract(workspace_raw):
            worker_instruction_parts.append(CUSTOMER_FACING_AI_COPY_CONTRACT)
        if _workspace_needs_runtime_ui_contract(workspace_raw):
            runtime_ui_contract = _runtime_ui_contract_block(surface_for_worker)
            if runtime_ui_contract:
                worker_instruction_parts.append(runtime_ui_contract)
        worker_instruction_parts.extend([workspace_contract, NO_PRETEND_PRODUCT_CONTRACT])
        worker_instruction = "\n\n".join(part for part in worker_instruction_parts if part)
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
        if sdk_result.get("success"):
            prefix_repair = _repair_nested_workspace_prefix(workspace_path, workspace_raw)
            if prefix_repair.get("repaired") or prefix_repair.get("blocked"):
                sdk_result["workspace_prefix_repair"] = prefix_repair
            if prefix_repair.get("blocked"):
                sdk_result["success"] = False
                sdk_result["error"] = (
                    "Claude Agent SDK output blocked because source files were written under a "
                    f"duplicate workspace prefix and could not be safely repaired: {prefix_repair.get('reason')}"
                )
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
            summary = store.read(scope=f"business:{business}", query="summary", include=["app"])
            app = summary.get("app") if isinstance(summary.get("app"), dict) else {}
            surface = app.get("surface") or app.get("surface_contract") or {}
            if not isinstance(surface, dict):
                surface = {}
            requested_publish_policy = str(surface.get("publish_policy") or _DEFAULT_PRODUCT_PUBLISH_POLICY).strip() or _DEFAULT_PRODUCT_PUBLISH_POLICY
            publish_policy = "publish_after_verify" if _is_shared_renderer_publish_policy(requested_publish_policy) else requested_publish_policy
            receipt_id = hashlib.sha256(f"{idempotency_key}:surface-verification:{workspace_raw}".encode("utf-8")).hexdigest()[:32]
            verification = _finalize_product_surface_verification(
                store=store,
                business=business,
                surface=surface,
                source_path=workspace_raw,
                publish_target=_product_publish_target(business, surface.get("publish_target")),
                requested_publish_policy=requested_publish_policy,
                publish_policy=publish_policy,
                install=_boolish(args.get("install"), default=True),
                timeout_seconds=_clamp_int(args.get("verification_timeout_seconds"), default=300, minimum=15, maximum=900),
                receipt_path=f"metrics/receipts/product-surface/{receipt_id}.json",
                verification_source="business_claude_agent_task",
            )
        status = "completed" if sdk_result.get("success") else "failed"
        if verification and verification.get("done_gate_status") != "passed":
            status = "blocked"

        record_operations: list[dict[str, Any]] = []
        if verification:
            record_operations.extend(
                _product_surface_verification_operations(
                    business=business,
                    verification=verification,
                    surface=surface,
                    publish_target=_product_publish_target(business, surface.get("publish_target")),
                    publish_policy=publish_policy,
                    requested_publish_policy=requested_publish_policy,
                    activate_on_success=True,
                )
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
                    "guidance_skills": resolved_guidance_skills,
                    "summary": sdk_result.get("summary") or "",
                    "error": sdk_result.get("error") or None,
                    "pretend_product_findings": pretend_findings,
                    "workspace_prefix_repair": sdk_result.get("workspace_prefix_repair"),
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
                "guidance_skills": resolved_guidance_skills,
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


TAKYON_TOOL_DEFINITIONS = [
    {
        "name": "business_list_businesses",
        "description": "List businesses and global control states.",
        "handler": handle_business_list_businesses,
        "schema": _schema("business_list_businesses", "List businesses.", {"limit": {"type": "integer"}}, []),
    },
    {
        "name": "business_read_business",
        "description": "Read one business summary, research and metrics indexes, workspaces, controls, ledger, jobs, and events.",
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
        "name": "business_list_conversation_messages",
        "description": "List business conversation messages with deterministic filters for backlog review and follow-up triage.",
        "handler": handle_business_list_conversation_messages,
        "schema": _schema(
            "business_list_conversation_messages",
            "List business conversation messages.",
            {
                "business": _BUSINESS_PROP,
                "direction": {"type": "string", "description": "inbound, outbound, internal, or all"},
                "status": {"type": "string", "description": "needs_response, responded, ignored, archived, or all"},
                "source": {"type": "string", "description": "Optional source/channel filter"},
                "thread_id": {"type": "string", "description": "Optional Takyon conversation thread id"},
                "thread_external_id": {"type": "string", "description": "Optional source-native thread id"},
                "limit": {"type": "integer", "description": "Maximum messages to return, default 100 and capped at 500"},
            },
            ["business"],
        ),
    },
    {
        "name": "business_read_conversation_thread",
        "description": "Read one business conversation thread with its messages and canonical filesystem mirror path.",
        "handler": handle_business_read_conversation_thread,
        "schema": _schema(
            "business_read_conversation_thread",
            "Read a business conversation thread.",
            {
                "business": _BUSINESS_PROP,
                "thread_id": {"type": "string", "description": "Takyon conversation thread id"},
                "source": {"type": "string", "description": "Optional source/channel when using external_id lookup"},
                "external_id": {"type": "string", "description": "Source-native thread id when thread_id is not known"},
                "thread_external_id": {"type": "string", "description": "Alias for external_id"},
            },
            ["business"],
        ),
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
        "description": "Write flexible per-business memory under research/ for strategy, pricing, product, distribution, learning, and CEO notes.",
        "handler": handle_business_record_memory,
        "schema": _schema("business_record_memory", "Write business research memory.", {"business": _BUSINESS_PROP, "path": {"type": "string"}, "content": {"type": "string"}, "mode": {"type": "string"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP}, ["business", "path", "content", "idempotency_key"]),
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
        "description": "Record the business-owned product surface contract: source/design/routes plus publish target, policy, and done gate.",
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
                "runtime_features": {"type": "array", "items": {"type": "string"}, "description": "Declared Takyon app-runtime features this product source should build toward, such as auth, checkout, billing, usage, generate, or entitlements."},
                "routes": {"type": "array", "items": {"type": "object"}},
                "theme": {"type": "object"},
                "constraints": {"type": "object"},
                "publish_target": {"type": "string", "description": "Public URL target; defaults to https://<business>.fourmanifold.com/"},
                "publish_policy": {"type": "string", "description": "Defaults to publish_after_verify. Legacy shared_renderer aliases are accepted only to publish the real source_path and will block if source files are missing."},
                "mode_behavior": {"type": "string", "description": "Defaults to test_mode_publishes_product_surface"},
                "done_gate": {"type": "string", "description": "Defaults to published, or exact blocker"},
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
        "description": "Publish a business product surface from real source files; missing source or publish blockers are reported as exact blockers.",
        "handler": handle_business_verify_product_surface,
        "schema": _schema(
            "business_verify_product_surface",
            "Publish product surface source/build output to the shared slug host and write a receipt with nonfatal inventory evidence.",
            {
                "business": _BUSINESS_PROP,
                "source_path": {"type": "string", "description": "Business-relative source path; defaults to the app surface contract source_path"},
                "publish_target": {"type": "string", "description": "Public URL target; defaults to the app surface contract or https://<business>.fourmanifold.com/"},
                "publish_policy": {"type": "string", "description": "Defaults to publish_after_verify. Legacy shared_renderer aliases are treated as source publishing and will not create fallback pages."},
                "install": {"type": "boolean", "description": "Run package install before build when package.json exists; default true"},
                "timeout_seconds": {"type": "integer", "description": "Per command timeout for explicit source builds; default 300"},
                "activate_on_success": {"type": "boolean", "description": "Update app surface status after publication; active only when publication succeeds; default true"},
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
        "description": "Publish outreach through one mode-aware intent using the canonical product URL: test mode creates a local suppressed receipt and conversation mirror; live mode records a gated provider publish job.",
        "handler": handle_business_publish_outreach,
        "schema": _schema(
            "business_publish_outreach",
            "Publish outreach using the business mode bright line.",
            {"business": _BUSINESS_PROP, "channel": {"type": "string"}, "provider": {"type": "string"}, "target": {"type": "string"}, "recipient": {"type": "string"}, "destination_url": {"type": "string", "description": "Exact URL or composer endpoint where this outreach would be posted or sent."}, "destination_label": {"type": "string"}, "subject": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "content": {"type": "string"}, "thread_external_id": {"type": "string"}, "metadata": {"type": "object"}, "kind": {"type": "string"}, "status": {"type": "string"}, "requires_api": _REQUIRES_API_PROP, "requires_env": _REQUIRES_ENV_PROP, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
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
            {"business": _BUSINESS_PROP, "channel": {"type": "string"}, "provider": {"type": "string"}, "target": {"type": "string"}, "recipient": {"type": "string"}, "destination_url": {"type": "string", "description": "Exact URL or composer endpoint where this outreach would be posted or sent."}, "destination_label": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "thread_external_id": {"type": "string"}, "metadata": {"type": "object"}, "idempotency_key": _IDEMPOTENCY_PROP, "reason": _REASON_PROP, "actor": _ACTOR_PROP},
            ["business", "body", "idempotency_key"],
        ),
    },
    {
        "name": "business_ugc_ad_write",
        "description": "Record an already-built ugc-video-ad publication under product/ugc-ads/<slug>/ after the copied skill writes the files.",
        "handler": handle_business_ugc_ad_write,
        "schema": _schema(
            "business_ugc_ad_write",
            "Record a finished ugc-video-ad publication.",
            {
                "business": _BUSINESS_PROP,
                "slug": {"type": "string", "description": "Optional fallback slug; the copied skill normally supplies this inside value.slug"},
                "path": {"type": "string", "description": "Optional fallback publication path; the copied skill normally supplies this inside value.path"},
                "value": {
                    "type": "object",
                    "description": "The exact payload printed by ugc-video-ad: slug, path, seconds, n_clips, and script.",
                },
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "value", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_ad_launch",
        "description": (
            "Launch or preflight a Meta (Facebook/Instagram) ad from a UGC video. "
            "mode=preflight verifies the access token and lists ad accounts (read-only, creates nothing). "
            "mode=launch creates a video AdCreative + Campaign + AdSet + Ad, ALWAYS PAUSED (it never serves or spends); "
            "test-mode businesses suppress everything to a local receipt with no Meta calls. "
            "Activation is intentionally not supported by this tool."
        ),
        "handler": handle_business_meta_ad_launch,
        "schema": _schema(
            "business_meta_ad_launch",
            "Preflight or create a PAUSED Meta ad from a UGC video.",
            {
                "business": _BUSINESS_PROP,
                "mode": {
                    "type": "string",
                    "enum": ["preflight", "launch"],
                    "description": "preflight = read-only token/account check; launch = create PAUSED objects. Default launch.",
                },
                "ad_video_path": {
                    "type": "string",
                    "description": "Business-relative path to the UGC .mp4, e.g. product/ugc-ads/<slug>/ad.mp4. Required for launch.",
                },
                "slug": {
                    "type": "string",
                    "description": "Publication slug under distribution/meta-ads/<slug>/; defaults from the campaign name or the video folder.",
                },
                "ad_account_id": {
                    "type": "string",
                    "description": "Override META_AD_ACCOUNT_ID (with or without the act_ prefix).",
                },
                "campaign": {
                    "type": "object",
                    "description": "{name, objective}; objective is a Meta Outcome objective (default OUTCOME_TRAFFIC).",
                },
                "adset": {
                    "type": "object",
                    "description": "{name, daily_budget_usd, optimization_goal, billing_event, targeting}; daily_budget_usd is capped by TAKYON_META_MAX_DAILY_BUDGET_USD.",
                },
                "ad": {
                    "type": "object",
                    "description": "{name, message, link (required), call_to_action, page_id, image_url}; image_url is an optional thumbnail fallback.",
                },
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "idempotency_key"],
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
                "guidance_skills": {"type": "array", "items": {"type": "string"}, "description": "Optional installed Hermes skill names to distill into the worker instruction, such as claude-design plus one shared style skill like claude-design-openai or claude-design-doodle for product/site UI work"},
                "budget_usd": {"type": "number", "description": "Per-task spend reservation, default 2.0 and capped at 25.0"},
                "model": {"type": "string", "description": "Optional Claude model override"},
                "max_turns": {"type": "integer", "description": "SDK turn cap, default 12"},
                "timeout_ms": {"type": "integer", "description": "Wall-clock timeout, default 300000"},
                "verify_surface": {"type": "boolean", "description": "Check product/website source after edits and write a receipt; product/* workspaces default to this source check"},
                "install": {"type": "boolean", "description": "Run package install before build during source check; default true"},
                "verification_timeout_seconds": {"type": "integer", "description": "Per source-check command timeout; default 300"},
                "idempotency_key": _IDEMPOTENCY_PROP,
                "reason": _REASON_PROP,
                "actor": _ACTOR_PROP,
            },
            ["business", "instruction", "idempotency_key"],
        ),
    },
    {
        "name": "business_upsert_conversation_thread",
        "description": "Create or update a business-owned conversation thread and its Markdown mirror under metrics/conversations/.",
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
