"""
Takyon Agent — Web UI server.

Provides a FastAPI backend serving the Vite/React frontend and REST API
endpoints for managing configuration, environment variables, and sessions.

Usage:
    python -m takyon_cli.main web          # Start on http://127.0.0.1:9119
    python -m takyon_cli.main web --port 8080
"""

import asyncio
import base64
import hashlib
import html
import hmac
import importlib.util
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from takyon_cli import __version__, __release_date__
from takyon_cli.config import (
    cfg_get,
    DEFAULT_CONFIG,
    OPTIONAL_ENV_VARS,
    get_config_path,
    get_env_path,
    get_takyon_home,
    load_config,
    load_env,
    save_config,
    save_env_value,
    remove_env_value,
    check_config_version,
    redact_key,
)
from gateway.status import get_running_pid, read_runtime_status
from plugins.takyon.core import (
    handle_business_cancel_app_subscription,
    handle_business_create_app_checkout,
    handle_business_enqueue_job,
    handle_business_meta_ad_bind_manual_launch,
    handle_business_meta_ad_insights_sync,
    handle_business_read_app_account,
    handle_business_read_app_profile,
    handle_business_record_app_usage,
    handle_business_record_stripe_webhook,
    handle_business_request_app_magic_link,
    handle_business_upsert_app_profile,
    handle_business_verify_app_magic_link,
    _is_reserved_public_subdomain,
)
from plugins.takyon import safebox as takyon_safebox

TAKYON_APP_SESSION_COOKIE = "takyon_app_session"

try:
    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError:
    # First try lazy-installing the dashboard extras. Only the user actually
    # running `takyon dashboard` needs fastapi+uvicorn; lazy install keeps
    # them out of every other install path. After install, re-import.
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("tool.dashboard", prompt=False)
        from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel, Field
    except Exception:
        raise SystemExit(
            "Web UI requires fastapi and uvicorn.\n"
            f"Install with: {sys.executable} -m pip install 'fastapi' 'uvicorn[standard]'"
        )

WEB_DIST = Path(os.environ["TAKYON_WEB_DIST"]) if "TAKYON_WEB_DIST" in os.environ else Path(__file__).parent / "web_dist"
_log = logging.getLogger(__name__)

app = FastAPI(title="Takyon Agent", version=__version__)

# ---------------------------------------------------------------------------
# Session token for protecting sensitive endpoints (reveal).
# Injected into the SPA HTML so only the legitimate web UI can use it.
# Persisted under TAKYON_HOME so dashboard restarts do not strand open tabs
# with a stale WebSocket/API token.
# ---------------------------------------------------------------------------
_SESSION_TOKEN_ENV = "TAKYON_DASHBOARD_SESSION_TOKEN"
_SESSION_TOKEN_FILE_ENV = "TAKYON_DASHBOARD_SESSION_TOKEN_FILE"
_SESSION_TOKEN_FILE_NAME = "dashboard_session_token"
_TAKYON_DIRECT_FILE_READ_BYTES = 512 * 1024
_TAKYON_DIRECT_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v"}
_TAKYON_DIRECT_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TAKYON_DIRECT_MEDIA_SUFFIXES = _TAKYON_DIRECT_VIDEO_SUFFIXES | _TAKYON_DIRECT_IMAGE_SUFFIXES


def _valid_session_token(value: str) -> bool:
    token = value.strip()
    return len(token) >= 32 and not any(ch.isspace() for ch in token)


def _dashboard_session_token_path() -> Path:
    override = os.getenv(_SESSION_TOKEN_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return get_takyon_home() / _SESSION_TOKEN_FILE_NAME


def _write_dashboard_session_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{token}\n")
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _takyon_direct_output_detail(path: str) -> tuple[str, str]:
    suffix = Path(path).suffix.lower()
    if suffix in _TAKYON_DIRECT_VIDEO_SUFFIXES:
        return "video", "Generated video asset"
    if suffix in _TAKYON_DIRECT_IMAGE_SUFFIXES:
        return "image", "Generated image asset"
    parts = path.split("/")
    top = parts[0] if parts else ""
    if path == "product/site/index.html":
        return "file", "Website surface (local source)"
    if path.startswith("product/site/"):
        return "file", "Website source asset"
    if path.startswith("metrics/receipts/outreach/"):
        return "receipt", "Outreach publish receipt"
    if path.startswith("metrics/receipts/creative-assets/"):
        return "receipt", "Creative asset receipt"
    if path.startswith("distribution/outreach-drafts"):
        return "file", "Outreach draft only"
    if "ugc" in path.lower() and suffix in {".md", ".txt"}:
        return "file", "Creative brief draft only"
    if path.startswith("metrics/receipts/") or top == "receipts":
        return "receipt", "Business receipt"
    if top in {"reports", "outputs"}:
        return "report", "Historical output"
    if path.startswith(("distribution/local-published/", "outreach/local-published/")):
        return "file", "Local published outreach"
    if top == "app":
        return "file", "App runtime artifact"
    if top == "brain":
        return "file", "Business brain artifact"
    if top == "product":
        return "file", "Product artifact"
    if top == "distribution":
        return "file", "Distribution artifact"
    return "file", "Business artifact"


def _takyon_direct_historical_outputs(store: Any, slug: str, *, limit: int = 40) -> list[dict[str, Any]]:
    try:
        root = store._business_root(slug, sync=False)
    except Exception:
        return []
    if not root.exists() or not root.is_dir():
        return []

    candidates: set[Path] = set()
    exact_paths = {
        "product/surface.md",
        "distribution/surface.md",
        "research/index.md",
        "metrics/summary.md",
        "metrics/wake-history.md",
        "product/mvp-spec.md",
        "product/site/index.html",
    }
    for rel in exact_paths:
        path = root / rel
        if path.is_file():
            candidates.add(path)

    recursive_roots = [
        "outputs",
        "reports",
        "campaigns",
        "distribution",
        "outreach/local-published",
        "product/site",
    ]
    allowed_suffixes = {".md", ".html", ".css", ".js", ".txt", ".json", *_TAKYON_DIRECT_MEDIA_SUFFIXES}
    for rel_root in recursive_roots:
        directory = root / rel_root
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed_suffixes:
                continue
            candidates.add(path)

    outputs: list[dict[str, Any]] = []
    for path in candidates:
        try:
            stat = path.stat()
            rel = str(path.relative_to(root))
        except Exception:
            continue
        kind, detail = _takyon_direct_output_detail(rel)
        outputs.append(
            {
                "id": f"historical:{slug}:{rel}",
                "title": path.name,
                "detail": detail,
                "path": rel,
                "kind": kind,
                "at": int(stat.st_mtime * 1000),
            }
        )

    outputs.sort(key=lambda item: int(item.get("at") or 0), reverse=True)
    return outputs[: max(1, min(int(limit or 40), 100))]


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _read_last_jsonl_object(path: Path) -> dict[str, Any] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _takyon_openable_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^https?://", text, re.IGNORECASE) or text.lower().startswith("data:"):
        return text
    if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/.*)?$", text, re.IGNORECASE):
        return f"https://{text}"
    return ""


def _takyon_latest_channel_job(jobs: list[dict[str, Any]], *needles: str) -> dict[str, Any] | None:
    wanted = [str(needle or "").strip().lower() for needle in needles if str(needle or "").strip()]
    if not wanted:
        return None
    for job in jobs:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        kind = str(job.get("kind") or "").strip().lower()
        payload_channel = str(payload.get("channel") or "").strip().lower()
        requested_skill = str(payload.get("requested_skill") or "").strip().lower()
        if payload_channel in wanted or any(token in kind or token in requested_skill for token in wanted):
            return {
                "id": str(job.get("id") or "").strip(),
                "kind": str(job.get("kind") or "").strip(),
                "status": str(job.get("status") or "queued").strip() or "queued",
                "label": str(job.get("label") or payload.get("summary") or _takyon_job_label(job.get("kind"))).strip(),
                "detail": str(job.get("detail") or payload.get("summary") or "").strip(),
                "updated_at": str(job.get("updated_at") or job.get("created_at") or "").strip(),
                "created_at": str(job.get("created_at") or "").strip(),
            }
    return None


def _takyon_collect_business_paid_campaigns(
    store: Any,
    business: str,
    publication_root: str,
    metrics_root: str,
    *,
    plan_secondary_key: str,
) -> list[dict[str, Any]]:
    try:
        business_root = store._business_root(business, sync=False)
    except TypeError:
        business_root = store._business_root(business)
    except Exception:
        return []
    root = business_root / publication_root
    if not root.is_dir():
        return []

    def _action_entries(actions_root: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not actions_root.is_dir():
            return entries
        for action_abs in actions_root.glob("*.json"):
            action = _read_json_object(action_abs)
            if not action:
                continue
            entries.append(
                {
                    "path": str(action_abs.relative_to(business_root)),
                    "sort_key": (
                        str(action.get("created_at") or action.get("updated_at") or ""),
                        int(action_abs.stat().st_mtime),
                        action_abs.name,
                    ),
                    "value": action,
                }
            )
        entries.sort(key=lambda item: item["sort_key"], reverse=True)
        return entries

    campaigns: list[dict[str, Any]] = []
    for receipt_abs in root.glob("*/receipt.json"):
        receipt = _read_json_object(receipt_abs)
        if not receipt:
            continue
        slug_name = receipt_abs.parent.name
        plan_abs = receipt_abs.parent / "plan.json"
        plan = _read_json_object(plan_abs) or {}
        campaign_block = plan.get("campaign") if isinstance(plan.get("campaign"), dict) else {}
        secondary_block = (
            plan.get(plan_secondary_key) if isinstance(plan.get(plan_secondary_key), dict) else {}
        )
        ad_block = plan.get("ad") if isinstance(plan.get("ad"), dict) else {}
        asset_path = str(
            receipt.get("ad_video_path")
            or receipt.get("ad_image_path")
            or plan.get("ad_video_path")
            or plan.get("ad_image_path")
            or ""
        ).strip()
        metrics_abs = business_root / metrics_root / slug_name / "insights.jsonl"
        latest_metrics = _read_last_jsonl_object(metrics_abs) or {}
        action_entries = _action_entries(receipt_abs.parent / "actions")
        latest_action = action_entries[0] if action_entries else None
        latest_state_action = next(
            (
                entry
                for entry in action_entries
                if isinstance(entry.get("value"), dict)
                and bool(entry["value"].get("success", True))
                and str(entry["value"].get("operation") or "").strip().lower() in {"activate", "pause"}
            ),
            None,
        )
        latest_budget_action = next(
            (
                entry
                for entry in action_entries
                if isinstance(entry.get("value"), dict)
                and bool(entry["value"].get("success", True))
                and str(entry["value"].get("operation") or "").strip().lower() == "set_budget"
            ),
            None,
        )
        effective_status = str(receipt.get("status") or "").strip()
        if latest_state_action is not None:
            action_value = latest_state_action["value"]
            effective_status = str(
                action_value.get("status") or action_value.get("operation") or effective_status
            ).strip()
        elif latest_action is not None:
            action_value = latest_action["value"]
            action_operation = str(action_value.get("operation") or "").strip().lower()
            if action_operation in {"activate", "pause"}:
                effective_status = str(action_value.get("status") or effective_status).strip()
        actual_daily_budget_usd = receipt.get("actual_daily_budget_usd")
        if actual_daily_budget_usd in {None, ""} and latest_budget_action is not None:
            actual_daily_budget_usd = latest_budget_action["value"].get("daily_budget_usd")
        campaigns.append(
            {
                "slug": slug_name,
                "status": effective_status,
                "launch_mode": str(receipt.get("launch_mode") or plan.get("launch_mode") or "").strip(),
                "asset_kind": str(receipt.get("asset_kind") or plan.get("asset_kind") or "").strip(),
                "asset_path": asset_path,
                "plan_path": str(plan_abs.relative_to(business_root)) if plan_abs.is_file() else "",
                "receipt_path": str(receipt_abs.relative_to(business_root)),
                "latest_action_path": str((latest_action or {}).get("path") or "").strip(),
                "latest_action": latest_action["value"] if latest_action is not None else None,
                "metrics_path": str(metrics_abs.relative_to(business_root)) if metrics_abs.is_file() else "",
                "created_at": str(receipt.get("created_at") or "").strip(),
                "updated_at": str(
                    (latest_action or {}).get("value", {}).get("created_at")
                    or receipt.get("updated_at")
                    or receipt.get("externally_launched_at")
                    or receipt.get("created_at")
                    or ""
                ).strip(),
                "objective": str(receipt.get("objective") or campaign_block.get("objective") or "").strip(),
                "campaign_name": str(receipt.get("campaign_name") or campaign_block.get("name") or "").strip(),
                "secondary_name": str(
                    receipt.get("adset_name")
                    or receipt.get("ad_group_name")
                    or secondary_block.get("name")
                    or ""
                ).strip(),
                "ad_name": str(receipt.get("ad_name") or ad_block.get("name") or "").strip(),
                "daily_budget_usd": receipt.get("daily_budget_usd") or secondary_block.get("daily_budget_usd"),
                "actual_daily_budget_usd": actual_daily_budget_usd,
                "message": str(receipt.get("message") or ad_block.get("message") or "").strip(),
                "link": str(receipt.get("link") or ad_block.get("link") or "").strip(),
                "tracked_link": str(receipt.get("tracked_link") or ad_block.get("tracked_link") or "").strip(),
                "call_to_action": str(receipt.get("call_to_action") or ad_block.get("call_to_action") or "").strip(),
                "ids": receipt.get("ids") if isinstance(receipt.get("ids"), dict) else {},
                "latest_metrics": latest_metrics,
                "open_url": _takyon_openable_url(
                    receipt.get("preview_url") or receipt.get("post_url") or receipt.get("link")
                ),
            }
        )
    campaigns.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return campaigns


def _takyon_channel_start_request_spec(
    channel: str,
    campaigns: list[dict[str, Any]] | None = None,
    latest_job: dict[str, Any] | None = None,
) -> dict[str, str]:
    key = str(channel or "").strip().lower()
    label = "X" if key == "x" else "Reddit" if key == "reddit" else "Meta" if key == "meta" else "Channel"
    latest_campaign = (campaigns or [None])[0] if campaigns else None
    latest_status = str(
        (latest_campaign or {}).get("status") or (latest_job or {}).get("status") or "missing"
    ).strip().lower()
    if latest_status in {"created_paused", "paused"}:
        return {
            "requested_action": "activate_or_continue_channel_campaign",
            "summary": f"Activate the paused {label} campaign if it is still the right move; otherwise continue the {label} lane truthfully.",
            "primary_action_label": "activate",
        }
    if latest_status in {"activated", "externally_launched", "active", "live"}:
        return {
            "requested_action": "continue_live_channel_campaign",
            "summary": f"Continue the live {label} lane truthfully: review results, sync metrics, and add another campaign only if warranted.",
            "primary_action_label": "continue",
        }
    if latest_job is not None and _takyon_job_status(latest_job.get("status")) in {"scheduled", "running"}:
        return {
            "requested_action": "create_or_continue_channel_campaign",
            "summary": f"Continue the in-flight {label} lane work truthfully and reuse existing state instead of duplicating it.",
            "primary_action_label": "continue",
        }
    return {
        "requested_action": "create_or_continue_channel_campaign",
        "summary": f"Start or continue the {label} outreach lane.",
        "primary_action_label": "start",
    }


def _takyon_collect_operator_meta_campaigns(store: Any, business_slugs: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for business in business_slugs:
        try:
            business_root = store._business_root(business, sync=False)
        except Exception:
            continue
        campaigns_root = business_root / "distribution" / "meta-ads"
        if not campaigns_root.exists() or not campaigns_root.is_dir():
            continue
        for receipt_abs in campaigns_root.glob("*/receipt.json"):
            receipt = _read_json_object(receipt_abs)
            if not receipt:
                continue
            campaign_slug = receipt_abs.parent.name
            plan_abs = receipt_abs.parent / "plan.json"
            plan = _read_json_object(plan_abs) or {}
            campaign_block = plan.get("campaign") if isinstance(plan.get("campaign"), dict) else {}
            adset_block = plan.get("adset") if isinstance(plan.get("adset"), dict) else {}
            ad_block = plan.get("ad") if isinstance(plan.get("ad"), dict) else {}
            asset_path = str(
                receipt.get("ad_video_path")
                or receipt.get("ad_image_path")
                or plan.get("ad_video_path")
                or plan.get("ad_image_path")
                or ""
            ).strip()
            metrics_abs = business_root / "metrics" / "meta-ads" / campaign_slug / "insights.jsonl"
            latest_metrics = _read_last_jsonl_object(metrics_abs)
            targeting = receipt.get("targeting")
            if not isinstance(targeting, dict):
                targeting = adset_block.get("targeting") if isinstance(adset_block.get("targeting"), dict) else {}
            items.append(
                {
                    "business_slug": business,
                    "slug": campaign_slug,
                    "status": receipt.get("status"),
                    "launch_mode": receipt.get("launch_mode") or plan.get("launch_mode"),
                    "asset_kind": receipt.get("asset_kind") or plan.get("asset_kind"),
                    "asset_path": asset_path or None,
                    "asset_download_url": (
                        f"/api/takyon/businesses/{urllib.parse.quote(business, safe='')}/asset?path="
                        f"{urllib.parse.quote(asset_path, safe='/')}"
                        if asset_path
                        else None
                    ),
                    "plan_path": receipt.get("plan_path")
                    or (str(plan_abs.relative_to(business_root)) if plan_abs.is_file() else None),
                    "receipt_path": str(receipt_abs.relative_to(business_root)),
                    "created_at": receipt.get("created_at"),
                    "updated_at": receipt.get("updated_at"),
                    "externally_launched_at": receipt.get("externally_launched_at"),
                    "objective": receipt.get("objective") or campaign_block.get("objective"),
                    "campaign_name": receipt.get("campaign_name") or campaign_block.get("name"),
                    "adset_name": receipt.get("adset_name") or adset_block.get("name"),
                    "ad_name": receipt.get("ad_name") or ad_block.get("name"),
                    "daily_budget_usd": receipt.get("daily_budget_usd") or adset_block.get("daily_budget_usd"),
                    "actual_daily_budget_usd": receipt.get("actual_daily_budget_usd"),
                    "message": receipt.get("message") or ad_block.get("message"),
                    "link": receipt.get("link") or ad_block.get("link"),
                    "tracked_link": receipt.get("tracked_link") or ad_block.get("tracked_link"),
                    "call_to_action": receipt.get("call_to_action") or ad_block.get("call_to_action"),
                    "targeting": targeting,
                    "manual_launch": receipt.get("manual_launch") if isinstance(receipt.get("manual_launch"), dict) else None,
                    "ids": receipt.get("ids") if isinstance(receipt.get("ids"), dict) else {},
                    "latest_metrics": latest_metrics,
                }
            )

    items.sort(
        key=lambda item: str(
            item.get("updated_at")
            or item.get("externally_launched_at")
            or item.get("created_at")
            or ""
        ),
        reverse=True,
    )
    return items


def _load_or_create_session_token() -> str:
    env_token = os.getenv(_SESSION_TOKEN_ENV, "").strip()
    if _valid_session_token(env_token):
        return env_token

    token_path = _dashboard_session_token_path()
    try:
        existing = token_path.read_text(encoding="utf-8").strip()
        if _valid_session_token(existing):
            return existing
    except FileNotFoundError:
        pass
    except OSError as exc:
        _log.warning("Could not read dashboard session token file %s: %s", token_path, exc)

    token = secrets.token_urlsafe(32)
    try:
        _write_dashboard_session_token(token_path, token)
    except OSError as exc:
        _log.warning("Could not persist dashboard session token file %s: %s", token_path, exc)
    return token


_SESSION_TOKEN = _load_or_create_session_token()
_SESSION_HEADER_NAME = "X-Takyon-Session-Token"

_AUTH0_SESSION_COOKIE = "takyon_dashboard_auth"
_AUTH0_STATE_COOKIE = "takyon_auth0_state"
_AUTH0_NONCE_COOKIE = "takyon_auth0_nonce"
_AUTH0_COOKIE_MAX_AGE_SECONDS = 12 * 60 * 60
_AUTH0_STATE_MAX_AGE_SECONDS = 10 * 60
_AUTH0_JWKS_CLIENTS: dict[str, Any] = {}
_AUTH0_CONFIG_CACHE_MISSING = object()
_AUTH0_CONFIG_CACHE_KEY: tuple[str, ...] | None = None
_AUTH0_CONFIG_CACHE_VALUE: object = _AUTH0_CONFIG_CACHE_MISSING
_RUNTIME_DATABASE_URL_ENV = ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL")
_POSTGRES_RUNTIME_ROUTES_MOUNTED = False
_REQUEST_RUNTIME_DATABASE_URL_ATTR = "_takyon_runtime_database_url"
_REQUEST_RUNTIME_DATABASE_URL_MISSING = object()


@dataclass(frozen=True)
class Auth0DashboardConfig:
    """Runtime Auth0 settings for the dashboard gate."""

    domain: str
    client_id: str
    client_secret: str
    secret: str
    base_url: str
    allowed_domains: tuple[str, ...]
    allowed_emails: tuple[str, ...]
    force: bool


class Auth0ConfigError(RuntimeError):
    """Raised when Auth0 is explicitly requested but not usable."""


def _env_value(key: str) -> str:
    """Read dashboard env values from the process or TAKYON_HOME/.env."""
    value = os.getenv(key)
    if value is not None:
        return value.strip()
    try:
        return str(load_env().get(key) or "").strip()
    except Exception:
        return ""


def _resolve_runtime_database_url() -> str:
    """Resolve the Postgres runtime URL from the same env sources the dashboard already uses."""
    from plugins.takyon.runtime_app import resolve_database_url

    return resolve_database_url(
        explicit=takyon_safebox.first_env_backed_value(*_RUNTIME_DATABASE_URL_ENV) or None
    )


def _request_runtime_database_url(request: Request) -> str | None:
    """Resolve the runtime DB URL once per request.

    Several dashboard routes resolve the operator principal first and then open a second
    Postgres connection for the endpoint body. Keeping the URL lookup on the request avoids
    paying the same env/Safebox resolution cost twice on the same click.
    """
    cached = getattr(request.state, _REQUEST_RUNTIME_DATABASE_URL_ATTR, _REQUEST_RUNTIME_DATABASE_URL_MISSING)
    if cached is not _REQUEST_RUNTIME_DATABASE_URL_MISSING:
        return cached
    try:
        value: str | None = _resolve_runtime_database_url()
    except Exception:
        value = None
    setattr(request.state, _REQUEST_RUNTIME_DATABASE_URL_ATTR, value)
    return value


def _env_flag(key: str) -> Optional[bool]:
    raw = _env_value(key).strip().lower()
    if raw in {"1", "true", "yes", "on", "required", "force"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled", "disable"}:
        return False
    return None


def _csv_env(*keys: str) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        raw = _env_value(key)
        if not raw:
            continue
        for item in raw.replace(";", ",").split(","):
            cleaned = item.strip().lower()
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return tuple(values)


def _normalise_auth0_domain(domain: str) -> str:
    domain = domain.strip().rstrip("/")
    if not domain:
        return ""
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return domain.rstrip("/")


def _default_public_base_url() -> str:
    return (
        _env_value("TAKYON_DASHBOARD_PUBLIC_URL")
        or _env_value("APP_BASE_URL")
        or _env_value("APP_URL")
        or ""
    ).rstrip("/")


def _auth0_config_cache_key() -> tuple[str, ...]:
    return (
        str(os.environ.get("TAKYON_DASHBOARD_AUTH0") or ""),
        _normalise_auth0_domain(_env_value("AUTH0_DOMAIN")),
        _env_value("AUTH0_CLIENT_ID"),
        _default_public_base_url(),
        str(os.environ.get("TAKYON_SAFEBOX_URL") or ""),
    )


def _clear_auth0_config_cache() -> None:
    global _AUTH0_CONFIG_CACHE_KEY, _AUTH0_CONFIG_CACHE_VALUE
    _AUTH0_CONFIG_CACHE_KEY = None
    _AUTH0_CONFIG_CACHE_VALUE = _AUTH0_CONFIG_CACHE_MISSING


def _auth0_locally_enabled() -> bool:
    force = _env_flag("TAKYON_DASHBOARD_AUTH0")
    if force is False:
        return False
    domain = _normalise_auth0_domain(_env_value("AUTH0_DOMAIN"))
    client_id = _env_value("AUTH0_CLIENT_ID")
    if not domain or not client_id:
        return False
    if force is True:
        return True
    return bool(_configured_public_host())


def _auth0_config() -> Optional[Auth0DashboardConfig]:
    """Return Auth0 settings when configured.

    Auth0 only becomes mandatory for requests when `_auth0_required_for_host`
    says so. This lets a machine keep using the localhost dashboard even when
    the shared Fourmanifold deployment secrets are present.
    """
    global _AUTH0_CONFIG_CACHE_KEY, _AUTH0_CONFIG_CACHE_VALUE
    cache_key = _auth0_config_cache_key()
    if cache_key == _AUTH0_CONFIG_CACHE_KEY and _AUTH0_CONFIG_CACHE_VALUE is not _AUTH0_CONFIG_CACHE_MISSING:
        cached = _AUTH0_CONFIG_CACHE_VALUE
        return cached if isinstance(cached, Auth0DashboardConfig) else None

    force = _env_flag("TAKYON_DASHBOARD_AUTH0")
    if force is False:
        _AUTH0_CONFIG_CACHE_KEY = cache_key
        _AUTH0_CONFIG_CACHE_VALUE = None
        return None

    domain = _normalise_auth0_domain(_env_value("AUTH0_DOMAIN"))
    client_id = _env_value("AUTH0_CLIENT_ID")
    client_secret = takyon_safebox.read_env_backed_value("AUTH0_CLIENT_SECRET")
    secret = takyon_safebox.read_env_backed_value("AUTH0_SECRET")
    base_url = _default_public_base_url()

    required = {
        "AUTH0_DOMAIN": domain,
        "AUTH0_CLIENT_ID": client_id,
        "AUTH0_CLIENT_SECRET": client_secret,
        "AUTH0_SECRET": secret,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        if force is True:
            raise Auth0ConfigError(
                "Auth0 dashboard auth is enabled but missing: "
                + ", ".join(sorted(missing))
            )
        _AUTH0_CONFIG_CACHE_KEY = cache_key
        _AUTH0_CONFIG_CACHE_VALUE = None
        return None

    cfg = Auth0DashboardConfig(
        domain=domain,
        client_id=client_id,
        client_secret=client_secret,
        secret=secret,
        base_url=base_url,
        allowed_domains=_csv_env(
            "TAKYON_DASHBOARD_ALLOWED_EMAIL_DOMAINS",
            "AUTH0_ALLOWED_EMAIL_DOMAINS",
            "ARGON_BETA_ALLOWED_EMAIL_DOMAINS",
        ),
        allowed_emails=_csv_env(
            "TAKYON_DASHBOARD_ALLOWED_EMAILS",
            "AUTH0_ALLOWED_EMAILS",
        ),
        force=force is True,
    )
    _AUTH0_CONFIG_CACHE_KEY = cache_key
    _AUTH0_CONFIG_CACHE_VALUE = cfg
    return cfg


def _host_without_port(host_header: str) -> str:
    host = (host_header or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        close = host.find("]")
        return host[1:close] if close != -1 else host.strip("[]")
    return host.rsplit(":", 1)[0] if ":" in host else host


def _configured_public_host() -> str:
    base_url = _default_public_base_url()
    if not base_url:
        return ""
    parsed = urllib.parse.urlparse(base_url)
    return _host_without_port(parsed.netloc)


def _configured_skill_lab_host() -> str:
    base = _company_base_domain()
    return f"skills.{base}" if base else ""


def _configured_public_hosts() -> frozenset[str]:
    hosts: set[str] = set()
    public_host = _configured_public_host()
    if public_host:
        hosts.add(public_host)
    skill_lab_host = _configured_skill_lab_host()
    if skill_lab_host:
        hosts.add(skill_lab_host)
    return frozenset(hosts)


def _request_host(headers: Any) -> str:
    return _host_without_port(
        headers.get("x-forwarded-host")
        or headers.get("host")
        or ""
    )


def _auth0_required_for_host(headers: Any) -> bool:
    if not _auth0_locally_enabled():
        return False
    force = _env_flag("TAKYON_DASHBOARD_AUTH0")
    if force is True:
        return True
    return _request_host(headers) in _configured_public_hosts()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def _sign_payload(secret: str, payload: dict[str, Any]) -> str:
    body = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _unsign_payload(secret: str, token: str) -> Optional[dict[str, Any]]:
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(
            secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64url_decode(sig), expected):
            return None
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _cookie_value(cookie_header: str, name: str) -> str:
    cookie = SimpleCookie(cookie_header or "")
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


def _https_for_cookie(headers: Any, base_url: str) -> bool:
    if str(headers.get("x-forwarded-proto") or "").lower() == "https":
        return True
    return urllib.parse.urlparse(base_url).scheme == "https"


def _same_origin_path(path: str) -> str:
    if not path or not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def _auth0_request_base_url(cfg: Auth0DashboardConfig, request: Request) -> str:
    """Resolve the callback/logout origin for this login flow.

    Auth0 state/nonce cookies are host-only, so shared dashboard hosts like
    ``app.<base>`` and ``skills.<base>`` must round-trip through the exact same
    origin that initiated login. Falling back to the configured public base URL
    is still useful for non-public/local requests where Auth0 is not normally
    required.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        request_base = f"{proto}://{host}".rstrip("/")
        if cfg.force or _request_host(request.headers) in _configured_public_hosts():
            return request_base
    if cfg.base_url:
        return cfg.base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def _auth0_redirect_uri(cfg: Auth0DashboardConfig, request: Request) -> str:
    base_url = _auth0_request_base_url(cfg, request)
    return f"{base_url.rstrip('/')}/auth/callback"


def _auth0_login_path(request: Request) -> str:
    return_to = request.url.path
    if request.url.query:
        return_to = f"{return_to}?{request.url.query}"
    return "/auth/login?" + urllib.parse.urlencode({"return_to": return_to})


def _auth0_cookie_response(
    response: Response,
    cfg: Auth0DashboardConfig,
    request: Request,
    *,
    key: str,
    value: str,
    max_age: int,
) -> None:
    response.set_cookie(
        key,
        value,
        max_age=max_age,
        path="/",
        secure=_https_for_cookie(request.headers, cfg.base_url),
        httponly=True,
        samesite="lax",
    )


def _clear_auth0_cookies(response: Response, *, include_session: bool = True) -> None:
    keys = [_AUTH0_STATE_COOKIE, _AUTH0_NONCE_COOKIE]
    if include_session:
        keys.insert(0, _AUTH0_SESSION_COOKIE)
    for key in keys:
        response.delete_cookie(key, path="/")


def _email_allowed(email: str, cfg: Auth0DashboardConfig) -> bool:
    email = email.strip().lower()
    if not email or "@" not in email:
        return False
    if cfg.allowed_emails and email in cfg.allowed_emails:
        return True
    domain = email.rsplit("@", 1)[1]
    if cfg.allowed_domains:
        return domain in cfg.allowed_domains
    return True


def _session_from_cookie_header(
    cookie_header: str,
    cfg: Auth0DashboardConfig,
) -> Optional[dict[str, Any]]:
    token = _cookie_value(cookie_header, _AUTH0_SESSION_COOKIE)
    if not token:
        return None
    payload = _unsign_payload(cfg.secret, token)
    if not payload:
        return None
    try:
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
    except (TypeError, ValueError):
        return None
    email = str(payload.get("email") or "")
    return payload if _email_allowed(email, cfg) else None


def _auth0_public_path(path: str) -> bool:
    if path.startswith("/auth/"):
        return True
    if path == "/api/product-tls/ask":
        return True
    if path in {"/", "/chat", "/index.html", "/favicon.ico", "/robots.txt"}:
        return True
    return path.startswith((
        "/assets/",
        "/litebulb/",
        "/fonts/",
        "/fonts-terminal/",
        "/ds-assets/",
        "/dashboard-plugins/",
        "/v1/",
        "/internal/ai-gateway/",
        "/internal/creative-gateway/",
    ))


async def _auth0_exchange_code(
    cfg: Auth0DashboardConfig,
    *,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{cfg.domain}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict) or not data.get("id_token"):
        raise Auth0ConfigError("Auth0 token response did not include an id_token")
    return data


def _auth0_verify_id_token(
    cfg: Auth0DashboardConfig,
    *,
    id_token: str,
    expected_nonce: str,
) -> dict[str, Any]:
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as exc:  # pragma: no cover - dependency is pinned.
        raise Auth0ConfigError("PyJWT[crypto] is required for Auth0 validation") from exc

    issuer = f"{cfg.domain}/"
    jwks_client = _AUTH0_JWKS_CLIENTS.get(cfg.domain)
    if jwks_client is None:
        jwks_client = PyJWKClient(f"{cfg.domain}/.well-known/jwks.json")
        _AUTH0_JWKS_CLIENTS[cfg.domain] = jwks_client
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=cfg.client_id,
        issuer=issuer,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )
    if claims.get("nonce") != expected_nonce:
        raise Auth0ConfigError("Auth0 nonce mismatch")
    return claims


def _auth0_authorize_claims(
    cfg: Auth0DashboardConfig,
    claims: dict[str, Any],
) -> dict[str, Any]:
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise Auth0ConfigError("Auth0 profile did not include an email address")
    if claims.get("email_verified") is not True:
        raise Auth0ConfigError("Auth0 email address is not verified")
    if not _email_allowed(email, cfg):
        raise Auth0ConfigError(f"{email} is not allowed for this dashboard")
    return {
        "sub": str(claims.get("sub") or ""),
        "email": email,
        "name": str(claims.get("name") or email),
        "email_verified": True,
    }


def _auth0_error_response(message: str, status_code: int = 403) -> Response:
    safe_message = html.escape(message)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Takyon Auth</title></head>
<body style="font-family:system-ui,sans-serif;margin:3rem;max-width:42rem">
<h1>Access denied</h1><p>{safe_message}</p><p><a href="/auth/login">Try again</a></p>
</body></html>""",
        status_code=status_code,
    )


# In-browser Chat tab (/chat, /api/pty, …).  Off unless ``takyon dashboard --tui``
# or TAKYON_DASHBOARD_TUI=1.  Set from :func:`start_server`.
_DASHBOARD_EMBEDDED_CHAT_ENABLED = False

# Simple rate limiter for the reveal endpoint
_reveal_timestamps: List[float] = []
_REVEAL_MAX_PER_WINDOW = 5
_REVEAL_WINDOW_SECONDS = 30

# CORS: restrict to localhost origins only.  The web UI is intended to run
# locally; binding to 0.0.0.0 with allow_origins=["*"] would let any website
# read/modify config and secrets.

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Endpoints that do NOT require the session token.  Everything else under
# /api/ is gated by the auth middleware below.  Keep this list minimal —
# only truly non-sensitive, read-only endpoints belong here.
# ---------------------------------------------------------------------------
_PUBLIC_API_PATHS: frozenset = frozenset({
    "/api/status",
    "/api/config/defaults",
    "/api/config/schema",
    "/api/model/info",
    "/api/product-tls/ask",
    "/api/takyon/operator/home",
    "/api/dashboard/themes",
    "/api/dashboard/plugins",
    "/api/dashboard/plugins/rescan",
})

_HOST_ROLE_ENV = "TAKYON_HOST_ROLE"
_HOST_ROLE_COMBINED = "combined"
_HOST_ROLE_OPERATOR = "operator"
_HOST_ROLE_SUBUSER = "subuser"
_HOST_ROLE_ALIASES = {
    "": _HOST_ROLE_COMBINED,
    "all": _HOST_ROLE_COMBINED,
    "combined": _HOST_ROLE_COMBINED,
    "default": _HOST_ROLE_COMBINED,
    "operator": _HOST_ROLE_OPERATOR,
    "dashboard": _HOST_ROLE_OPERATOR,
    "subuser": _HOST_ROLE_SUBUSER,
    "app": _HOST_ROLE_SUBUSER,
    "product": _HOST_ROLE_SUBUSER,
}
_APP_PLANE_PATH_PREFIXES: tuple[str, ...] = (
    "/api/takyon/apps/",
    "/api/generated-apps/",
    "/site/",
)
_APP_PLANE_EXACT_PATHS: frozenset[str] = frozenset({
    "/api/product-tls/ask",
    "/api/webhooks/stripe",
})
_OPERATOR_ONLY_HTTP_PATH_PREFIXES: tuple[str, ...] = (
    "/api/pty",
    "/api/ws",
    "/api/tui/rpc",
    "/api/pub",
    "/api/events",
)


def _host_role() -> str:
    raw = str(os.getenv(_HOST_ROLE_ENV) or "").strip().lower()
    return _HOST_ROLE_ALIASES.get(raw, _HOST_ROLE_COMBINED)


def _is_app_plane_path(path: str) -> bool:
    return path in _APP_PLANE_EXACT_PATHS or path.startswith(_APP_PLANE_PATH_PREFIXES)


def _http_path_allowed_for_host_role(*, role: str, host: str, path: str) -> bool:
    product_business = _business_slug_from_product_host(_host_without_port(host))
    if role == _HOST_ROLE_SUBUSER:
        if path == "/healthz":
            return True
        if path in _APP_PLANE_EXACT_PATHS:
            return True
        if path.startswith(_APP_PLANE_PATH_PREFIXES):
            return True
        if product_business:
            if not path.startswith("/api/"):
                return True
            return _normalize_product_rail_route(path) is not None
        return False
    if role == _HOST_ROLE_OPERATOR:
        if product_business:
            return _normalize_product_rail_route(path) is not None or _is_app_plane_path(path)
        if path == "/api/product-tls/ask":
            return True
        if _is_app_plane_path(path):
            return False
    return True


def _is_public_api_path(path: str) -> bool:
    if path in _PUBLIC_API_PATHS:
        return True
    return path.startswith((
        "/api/takyon/apps/",
        "/api/generated-apps/",
        "/api/webhooks/stripe",
    ))


def _has_valid_session_token(request: Request) -> bool:
    """True if the request carries a valid dashboard session token.

    The dedicated session header avoids collisions with reverse proxies that
    already use ``Authorization`` (for example Caddy ``basic_auth``). We still
    accept the legacy Bearer path for backward compatibility with older
    dashboard bundles.
    """
    session_header = request.headers.get(_SESSION_HEADER_NAME, "")
    if session_header and hmac.compare_digest(
        session_header.encode(),
        _SESSION_TOKEN.encode(),
    ):
        return True

    if request.url.path.startswith("/api/takyon/site-preview/"):
        query_token = request.query_params.get("token", "")
        if query_token and hmac.compare_digest(query_token.encode(), _SESSION_TOKEN.encode()):
            return True

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {_SESSION_TOKEN}"
    return hmac.compare_digest(auth.encode(), expected.encode())


def _require_token(request: Request) -> None:
    """Validate the ephemeral session token.  Raises 401 on mismatch."""
    if not _has_valid_session_token(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


# Accepted Host header values for loopback binds. DNS rebinding attacks
# point a victim browser at an attacker-controlled hostname (evil.test)
# which resolves to 127.0.0.1 after a TTL flip — bypassing same-origin
# checks because the browser now considers evil.test and our dashboard
# "same origin". Validating the Host header at the app layer rejects any
# request whose Host isn't one we bound for. See GHSA-ppp5-vxwm-4cf7.
_LOOPBACK_HOST_VALUES: frozenset = frozenset({
    "localhost", "127.0.0.1", "::1",
})


def _is_accepted_host(host_header: str, bound_host: str) -> bool:
    """True if the Host header targets the interface we bound to.

    Accepts:
    - Exact bound host (with or without port suffix)
    - Loopback aliases when bound to loopback
    - Any host when bound to 0.0.0.0 (explicit opt-in to non-loopback,
      no protection possible at this layer)
    """
    if not host_header:
        return False
    # Strip port suffix. IPv6 addresses use bracket notation:
    #   [::1]         — no port
    #   [::1]:9119    — with port
    # Plain hosts/v4:
    #   localhost:9119
    #   127.0.0.1:9119
    host_only = _host_without_port(host_header)
    if _business_slug_from_product_host(host_only):
        return True

    # 0.0.0.0 bind means operator explicitly opted into all-interfaces
    # (requires --insecure per web_server.start_server). No Host-layer
    # defence can protect that mode; rely on operator network controls.
    if bound_host in {"0.0.0.0", "::"}:
        return True

    # Loopback bind: accept the loopback names
    bound_lc = bound_host.lower()
    if bound_lc in _LOOPBACK_HOST_VALUES:
        public_hosts = _configured_public_hosts() if _auth0_locally_enabled() else frozenset()
        if host_only in public_hosts:
            return True
        return host_only in _LOOPBACK_HOST_VALUES

    # Explicit non-loopback bind: require exact host match
    return host_only == bound_lc


@app.middleware("http")
async def host_header_middleware(request: Request, call_next):
    """Reject requests whose Host header doesn't match the bound interface.

    Defends against DNS rebinding: a victim browser on a localhost
    dashboard is tricked into fetching from an attacker hostname that
    TTL-flips to 127.0.0.1. CORS and same-origin checks don't help —
    the browser now treats the attacker origin as same-origin with the
    dashboard. Host-header validation at the app layer catches it.

    See GHSA-ppp5-vxwm-4cf7.
    """
    # Store the bound host on app.state so this middleware can read it —
    # set by start_server() at listen time.
    bound_host = getattr(app.state, "bound_host", None)
    if bound_host:
        host_header = request.headers.get("host", "")
        if not _is_accepted_host(host_header, bound_host):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "Invalid Host header. Dashboard requests must use "
                        "the hostname the server was bound to."
                    ),
                },
            )
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require the session token on all /api/ routes except the public list."""
    path = request.url.path
    if path.startswith("/api/") and not _is_public_api_path(path):
        if not _has_valid_session_token(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
            )
    return await call_next(request)


@app.middleware("http")
async def auth0_middleware(request: Request, call_next):
    """Optional Auth0 gate for the public dashboard host."""
    if _business_slug_from_product_host(_host_without_port(request.headers.get("host", ""))):
        return await call_next(request)
    if not _auth0_required_for_host(request.headers):
        return await call_next(request)

    path = request.url.path
    if _auth0_public_path(path):
        return await call_next(request)

    cookie_header = request.headers.get("cookie", "")
    has_auth0_session = bool(_cookie_value(cookie_header, _AUTH0_SESSION_COOKIE))
    if not has_auth0_session:
        if path == "/api/takyon/operator/home":
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Auth0 login required"},
            )
        return RedirectResponse(_auth0_login_path(request), status_code=302)

    try:
        cfg = _auth0_config()
    except Auth0ConfigError as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not cfg:
        return await call_next(request)

    session = _session_from_cookie_header(
        cookie_header,
        cfg,
    )
    if session:
        request.state.auth0_user = session
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Auth0 login required"},
        )
    return RedirectResponse(_auth0_login_path(request), status_code=302)


@app.middleware("http")
async def product_app_rail_middleware(request: Request, call_next):
    """Resolve product-app rail calls to the host's business.

    On a product host the business is identified by the hostname, so any
    recognised rail request (bare, ``/api/``-prefixed, or with an embedded
    slug) is dispatched to the canonical shared-runtime handler for that
    business. This prevents "rail not wired" 404s when a generated front-end
    guesses the wrong API base, and scopes every rail call to the host's
    business so a product page cannot reach another business's rails.

    Defined last so it runs outermost: a matched rail short-circuits before
    the dashboard auth gates, which otherwise 401 bare ``/api/`` paths that
    are not in the public allowlist.
    """
    business = _business_slug_from_product_host(
        _host_without_port(request.headers.get("host", ""))
    )
    if business:
        route = _normalize_product_rail_route(request.url.path)
        if route:
            if request.method == "GET":
                return await _takyon_app_get(request, business, route)
            if request.method == "POST":
                return await _takyon_app_post(request, business, route)
    return await call_next(request)


@app.middleware("http")
async def host_role_middleware(request: Request, call_next):
    """Restrict the server surface to the configured host role.

    ``combined`` keeps the historical single-host behavior. ``operator`` drops
    the public product-app plane; ``subuser`` drops dashboard/operator routes
    and serves only product hosts + the narrow app-runtime rails.
    """
    role = _host_role()
    if role != _HOST_ROLE_COMBINED and not _http_path_allowed_for_host_role(
        role=role,
        host=request.headers.get("host", ""),
        path=request.url.path,
    ):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


@app.get("/auth/login")
async def auth0_login(request: Request):
    cfg = _auth0_config()
    if not cfg:
        return JSONResponse(
            {"detail": "Auth0 dashboard auth is not configured"},
            status_code=404,
        )

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    return_to = _same_origin_path(request.query_params.get("return_to") or "/")
    state_payload = _sign_payload(
        cfg.secret,
        {
            "state": state,
            "return_to": return_to,
            "iat": int(time.time()),
        },
    )
    redirect_uri = _auth0_redirect_uri(cfg, request)
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
    }
    auth_url = f"{cfg.domain}/authorize?{urllib.parse.urlencode(params)}"
    response = RedirectResponse(auth_url, status_code=302)
    _auth0_cookie_response(
        response,
        cfg,
        request,
        key=_AUTH0_STATE_COOKIE,
        value=state_payload,
        max_age=_AUTH0_STATE_MAX_AGE_SECONDS,
    )
    _auth0_cookie_response(
        response,
        cfg,
        request,
        key=_AUTH0_NONCE_COOKIE,
        value=_sign_payload(cfg.secret, {"nonce": nonce, "iat": int(time.time())}),
        max_age=_AUTH0_STATE_MAX_AGE_SECONDS,
    )
    return response


def _provision_dashboard_user_if_postgres(user: dict[str, Any]) -> None:
    """Just-in-time provision the top-level Takyon user for a verified dashboard login (task #6).

    Runs ONLY on the Postgres backend — in the SQLite era there is no ``users`` table to provision
    into, and the dashboard still authenticates purely via its signed session cookie exactly as before,
    so this is a guarded no-op there. On Postgres it ensures the ``users`` row for this Auth0 ``sub``
    exists (minting THE single API key + opening billing/custody on first creation, one txn, via
    ``control_plane.provision_user_on_first_login``). It NEVER raises: the dashboard cookie tier is a
    separate auth tier from the control-plane API-key boundary, so a provisioning hiccup must not lock
    the operator out of the dashboard — it is logged loudly instead (invariant #8: surfaced, never
    silently swallowed). The one-time raw key (only on a brand-new ``sub``) is logged exactly once — it
    is never stored in clear and never placed in a cookie."""
    try:
        from plugins.takyon.core import _db_backend

        if _db_backend() != "postgres":
            return
        import psycopg

        from plugins.takyon.control_plane import provision_user_on_first_login
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            url = _resolve_runtime_database_url()
        except RuntimeNotConfigured:
            _log.error(
                "Auth0 login on the Postgres backend but no DATABASE_URL configured; "
                "top-level user was NOT provisioned"
            )
            return
        sub = str(user.get("sub") or "")
        if not sub:
            return
        email = str(user.get("email") or "") or None
        conn = psycopg.connect(url, autocommit=True)
        try:
            user_id, created, raw_key = provision_user_on_first_login(conn, sub, email)
        finally:
            conn.close()
        if created:
            _log.info(
                "Provisioned Takyon user %s for an Auth0 sub on first dashboard login", user_id
            )
            if raw_key:
                _log.warning(
                    "One-time Takyon API key minted for user %s (shown once, store it securely): %s",
                    user_id,
                    raw_key,
                )
        else:
            _log.debug("Dashboard login for already-provisioned Takyon user %s", user_id)
    except Exception as exc:  # noqa: BLE001 - never block dashboard login on a provisioning failure
        _log.error("JIT provisioning for dashboard login failed (login still allowed): %s", exc)


def _resolve_dashboard_principal(
    user: dict[str, Any] | None,
    *,
    runtime_database_url: str | None = None,
) -> Any | None:
    """Resolve the logged-in dashboard user to the canonical PG principal."""
    if not user:
        return None
    try:
        from plugins.takyon.core import _db_backend

        if _db_backend() != "postgres":
            return None
        import psycopg

        from plugins.takyon.control_plane import resolve_auth0_principal
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            url = runtime_database_url or _resolve_runtime_database_url()
        except RuntimeNotConfigured:
            return None
        conn = psycopg.connect(url, autocommit=True)
        try:
            return resolve_auth0_principal(
                conn,
                str(user.get("sub") or ""),
                str(user.get("email") or "") or None,
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - auth stays cookie-based if principal resolution hiccups
        _log.warning("dashboard principal resolution failed: %s", exc)
        return None


def _resolve_local_dashboard_principal(*, runtime_database_url: str | None = None) -> Any | None:
    """Return the server-side localhost/dashboard principal when Auth0 is not required."""
    try:
        from plugins.takyon.core import _db_backend

        if _db_backend() != "postgres":
            return None
        import psycopg

        from plugins.takyon.control_plane import (
            resolve_platform_owner_id,
            resolve_user_principal,
        )
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            url = runtime_database_url or _resolve_runtime_database_url()
        except RuntimeNotConfigured:
            return None
        conn = psycopg.connect(url, autocommit=True)
        try:
            owner_user_id = resolve_platform_owner_id(conn)
            if not owner_user_id:
                return None
            return resolve_user_principal(
                conn,
                owner_user_id,
                key_id="dashboard-local-platform-owner",
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - localhost falls back gracefully
        _log.warning("local dashboard principal resolution failed: %s", exc)
        return None


def _resolve_dashboard_headers_principal(headers: Any) -> Any | None:
    cfg = _auth0_config()
    principal = _resolve_dashboard_principal(
        _session_from_cookie_header(headers.get("cookie", ""), cfg) if cfg else None
    )
    if principal is not None:
        return principal
    if _auth0_required_for_host(headers):
        return None
    return _resolve_local_dashboard_principal()


def _resolve_dashboard_request_principal(request: Request) -> Any | None:
    runtime_database_url = _request_runtime_database_url(request)
    principal = _resolve_dashboard_principal(
        getattr(request.state, "auth0_user", None),
        runtime_database_url=runtime_database_url,
    )
    if principal is not None:
        return principal
    if _auth0_required_for_host(request.headers):
        return None
    return _resolve_local_dashboard_principal(runtime_database_url=runtime_database_url)


def _tui_turn_session_id(reservation_key: str) -> str:
    key = str(reservation_key or "").strip()
    if not key.startswith("tui-turn:"):
        return ""
    parts = key.split(":", 2)
    if len(parts) != 3:
        return ""
    return parts[1].strip()


def _running_tui_turn_session_ids() -> set[str]:
    try:
        from tui_gateway import server as tui_server

        sessions = getattr(tui_server, "_sessions", {})
    except Exception:
        return set()

    running: set[str] = set()
    if not isinstance(sessions, dict):
        return running
    for sid, session in list(sessions.items()):
        if isinstance(session, dict) and session.get("running"):
            running.add(str(sid))
    return running


def _operator_reservation_stale_seconds() -> int:
    raw = str(os.getenv("TAKYON_OPERATOR_RESERVATION_STALE_SECONDS") or "").strip()
    if raw:
        try:
            return max(300, int(raw))
        except ValueError:
            pass
    return 86400


def _job_reservation_job_id(reservation_key: str) -> str:
    key = str(reservation_key or "").strip()
    if not key.startswith("job:"):
        return ""
    parts = key.split(":", 3)
    if len(parts) < 3:
        return ""
    return str(parts[1] or "").strip()


def _reservation_age_seconds(created_at: Any) -> float | None:
    if created_at is None or not hasattr(created_at, "timestamp"):
        return None
    try:
        return max(0.0, time.time() - float(created_at.timestamp()))
    except Exception:
        return None


def _release_stale_operator_reservations(conn, user_id: str) -> int:
    """Refund orphaned operator holds before rendering the billing snapshot.

    This keeps the operator account honest across the known reservation families:
    live TUI turns, queued/running jobs, and bounded Claude/create-name inline tasks.
    """
    operator_user_id = str(user_id or "").strip()
    if not operator_user_id:
        return 0

    from plugins.takyon import billing

    stale_seconds = _operator_reservation_stale_seconds()
    running_session_ids = _running_tui_turn_session_ids()
    rows = conn.execute(
        "select r.reservation_key, min(r.created_at) "
        "from billing_entries r "
        "where r.user_id = %s "
        "  and r.kind = 'reserve' "
        "  and r.reservation_key is not null "
        "  and not exists ("
        "    select 1 from billing_entries f "
        "    where f.reservation_key = r.reservation_key "
        "      and f.kind in ('settle', 'refund')"
        "  ) "
        "group by r.reservation_key",
        (operator_user_id,),
    ).fetchall()
    released = 0
    for row in rows:
        key = str(row[0] or "").strip()
        if not key:
            continue
        created_at = row[1] if len(row) > 1 else None
        keep = False
        if key.startswith("tui-turn:"):
            session_id = _tui_turn_session_id(key)
            keep = bool(session_id) and session_id in running_session_ids
        elif key.startswith("job:"):
            job_id = _job_reservation_job_id(key)
            job_row = (
                conn.execute(
                    "select status, reserved_billing_entry_id, locked_at from jobs where id = %s",
                    (job_id,),
                ).fetchone()
                if job_id
                else None
            )
            if job_row is not None:
                status = str(job_row[0] or "").strip().lower()
                reserved_key = str(job_row[1] or "").strip()
                age_seconds = _reservation_age_seconds(job_row[2])
                keep = (
                    status == "running"
                    and reserved_key == key
                    and age_seconds is not None
                    and age_seconds < stale_seconds
                )
        else:
            age_seconds = _reservation_age_seconds(created_at)
            keep = age_seconds is None or age_seconds < stale_seconds
        if keep:
            continue
        try:
            billing.refund(conn, key)
            released += 1
        except billing.UnknownReservation:
            continue
    return released


def _release_stale_tui_turn_reservations(conn, user_id: str) -> int:
    """Refund orphaned dashboard-turn holds for this operator.

    A healthy TUI turn settles/refunds its `tui-turn:<sid>:...` reservation in the
    turn thread's `finally`. If the dashboard process dies mid-turn, that finalizer
    never runs and the operator budget can show a permanent hold. The live gateway
    session table is the canonical liveness signal for these reservations: if a
    `tui-turn:*` hold has no matching running session, it is stale and safe to
    release before we render the account snapshot.
    """
    operator_user_id = str(user_id or "").strip()
    if not operator_user_id:
        return 0

    from plugins.takyon import billing

    running_session_ids = _running_tui_turn_session_ids()
    rows = conn.execute(
        "select distinct r.reservation_key "
        "from billing_entries r "
        "where r.user_id = %s "
        "  and r.kind = 'reserve' "
        "  and r.reservation_key like 'tui-turn:%%' "
        "  and not exists ("
        "    select 1 from billing_entries f "
        "    where f.reservation_key = r.reservation_key "
        "      and f.kind in ('settle', 'refund')"
        "  )",
        (operator_user_id,),
    ).fetchall()
    released = 0
    for row in rows:
        key = str(row[0] or "").strip()
        session_id = _tui_turn_session_id(key)
        if not session_id or session_id in running_session_ids:
            continue
        try:
            billing.refund(conn, key)
            released += 1
        except billing.UnknownReservation:
            continue
    return released


def _seed_platform_owner_if_postgres() -> None:
    """Serving-flip startup seed for the dashboard server (Phase 8, mediationplan.md owner-wiring
    finding). The shell-side twin of this runs in ``cli.py``; both call the SAME idempotent
    ``TakyonStore.seed_platform_owner``. On the Postgres backend the local CEO/shell owns every
    business it creates as ONE config-keyed platform owner (``TAKYON_PLATFORM_OWNER_SUB``), and
    ``business.upsert`` resolves that owner read-only and blocks if it is unprovisioned (invariant #8);
    seeding it at dashboard start makes the same owner exist for a dashboard run that shares the
    Postgres control plane. Guarded no-op off Postgres; NEVER raises (a seed hiccup must not stop the
    dashboard from binding) — it is logged loudly instead. The one-time raw key (first PG startup
    only) is logged exactly once and never stored in clear."""
    try:
        from plugins.takyon.core import TakyonStore, _db_backend

        if _db_backend() != "postgres":
            return
        user_id, raw_key = TakyonStore(get_takyon_home()).seed_platform_owner()
        if raw_key:
            _log.warning(
                "Provisioned the platform owner (user %s) on the Postgres backend at dashboard "
                "start. One-time API key (shown once, store it securely): %s",
                user_id,
                raw_key,
            )
        elif user_id:
            _log.debug("Platform owner already provisioned (user %s)", user_id)
    except Exception as exc:  # noqa: BLE001 - never block the dashboard on a startup seed failure
        _log.error("Platform-owner startup seed failed (dashboard still starting): %s", exc)


_DASHBOARD_WORKER_THREAD: threading.Thread | None = None
_DASHBOARD_WORKER_LOCK = threading.Lock()


def _dashboard_worker_poll_seconds() -> float:
    raw = str(os.getenv("TAKYON_DASHBOARD_WORKER_POLL_SECONDS") or "").strip()
    if not raw:
        return 2.0
    try:
        return max(0.25, float(raw))
    except ValueError:
        return 2.0


def _start_dashboard_worker_if_postgres() -> None:
    """Mount a local worker loop alongside the dashboard when no separate daemon is assumed.

    The jobs queue is already multi-worker safe (`FOR UPDATE SKIP LOCKED` + idempotent handlers),
    so an explicit opt-in lightweight in-process drain remains available for local/dev runtimes.
    Production Postgres should drain through the tracked VPS worker service, not whichever dashboard
    process happened to start nearby.
    """
    raw = str(os.getenv("TAKYON_DASHBOARD_EMBEDDED_WORKER") or "").strip().lower()
    if raw not in {"1", "true", "yes", "on", "enable", "enabled"}:
        return
    try:
        from plugins.takyon.core import _db_backend

        if _db_backend() != "postgres":
            return
    except Exception as exc:  # noqa: BLE001 - never block the dashboard on worker bootstrap
        _log.error("Dashboard worker preflight failed (dashboard still starting): %s", exc)
        return

    global _DASHBOARD_WORKER_THREAD
    with _DASHBOARD_WORKER_LOCK:
        if _DASHBOARD_WORKER_THREAD is not None and _DASHBOARD_WORKER_THREAD.is_alive():
            return

        def _run_dashboard_worker() -> None:
            try:
                from plugins.takyon.worker import run_worker_loop

                run_worker_loop(
                    worker_id=f"dashboard-worker-{os.getpid()}",
                    poll_interval=_dashboard_worker_poll_seconds(),
                    dispatch=True,
                )
            except Exception as exc:  # noqa: BLE001 - keep the dashboard alive and log the failure
                _log.exception("Dashboard embedded worker exited: %s", exc)

        _DASHBOARD_WORKER_THREAD = threading.Thread(
            target=_run_dashboard_worker,
            name="takyon-dashboard-worker",
            daemon=True,
        )
        _DASHBOARD_WORKER_THREAD.start()


@app.get("/auth/callback")
async def auth0_callback(request: Request):
    cfg = _auth0_config()
    if not cfg:
        return JSONResponse(
            {"detail": "Auth0 dashboard auth is not configured"},
            status_code=404,
        )

    if request.query_params.get("error"):
        return _auth0_error_response(
            str(request.query_params.get("error_description") or "Auth0 login failed"),
            status_code=403,
        )

    code = request.query_params.get("code") or ""
    state = request.query_params.get("state") or ""
    if not code or not state:
        return _auth0_error_response("Missing Auth0 callback code or state", 400)

    state_payload = _unsign_payload(
        cfg.secret,
        _cookie_value(request.headers.get("cookie", ""), _AUTH0_STATE_COOKIE),
    )
    nonce_payload = _unsign_payload(
        cfg.secret,
        _cookie_value(request.headers.get("cookie", ""), _AUTH0_NONCE_COOKIE),
    )
    now = int(time.time())
    if (
        not state_payload
        or state_payload.get("state") != state
        or int(state_payload.get("iat") or 0) < now - _AUTH0_STATE_MAX_AGE_SECONDS
    ):
        return _auth0_error_response("Auth0 state mismatch", 400)
    if (
        not nonce_payload
        or int(nonce_payload.get("iat") or 0) < now - _AUTH0_STATE_MAX_AGE_SECONDS
    ):
        return _auth0_error_response("Auth0 nonce expired", 400)

    try:
        token_data = await _auth0_exchange_code(
            cfg,
            code=code,
            redirect_uri=_auth0_redirect_uri(cfg, request),
        )
        claims = _auth0_verify_id_token(
            cfg,
            id_token=str(token_data["id_token"]),
            expected_nonce=str(nonce_payload.get("nonce") or ""),
        )
        user = _auth0_authorize_claims(cfg, claims)
    except Exception as exc:
        _log.warning("Auth0 dashboard login rejected: %s", exc)
        return _auth0_error_response(str(exc), 403)

    # Task #6: JIT-provision the top-level Takyon user for this verified identity. Guarded no-op off
    # Postgres; never raises (the dashboard cookie tier is independent of the control-plane boundary).
    _provision_dashboard_user_if_postgres(user)

    expires_at = now + _AUTH0_COOKIE_MAX_AGE_SECONDS
    session_token = _sign_payload(cfg.secret, {**user, "iat": now, "exp": expires_at})
    response = RedirectResponse(
        _same_origin_path(str(state_payload.get("return_to") or "/")),
        status_code=302,
    )
    _auth0_cookie_response(
        response,
        cfg,
        request,
        key=_AUTH0_SESSION_COOKIE,
        value=session_token,
        max_age=_AUTH0_COOKIE_MAX_AGE_SECONDS,
    )
    _clear_auth0_cookies(response, include_session=False)
    return response


@app.get("/auth/logout")
async def auth0_logout(request: Request):
    cfg = _auth0_config()
    return_to = _same_origin_path(request.query_params.get("return_to") or "/")
    response: Response
    if cfg:
        base_url = _auth0_request_base_url(cfg, request)
        logout_params = {
            "client_id": cfg.client_id,
            "returnTo": f"{base_url.rstrip('/')}{return_to}",
        }
        response = RedirectResponse(
            f"{cfg.domain}/v2/logout?{urllib.parse.urlencode(logout_params)}",
            status_code=302,
        )
    else:
        response = RedirectResponse(return_to, status_code=302)
    _clear_auth0_cookies(response)
    return response


@app.get("/auth/me")
async def auth0_me(request: Request):
    cfg = _auth0_config()
    if not cfg or not _auth0_required_for_host(request.headers):
        return {"authenticated": False, "auth0_required": False}
    session = _session_from_cookie_header(request.headers.get("cookie", ""), cfg)
    if not session:
        return JSONResponse(
            {"authenticated": False, "auth0_required": True},
            status_code=401,
        )
    return {
        "authenticated": True,
        "auth0_required": True,
        "user": {
            "email": session.get("email"),
            "name": session.get("name"),
            "sub": session.get("sub"),
        },
    }


def _takyon_app_tool(raw: str) -> tuple[int, dict[str, Any]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return int(HTTPStatus.INTERNAL_SERVER_ERROR), {"success": False, "error": str(exc)}
    if not isinstance(payload, dict):
        return int(HTTPStatus.INTERNAL_SERVER_ERROR), {"success": False, "error": "tool did not return a JSON object"}
    if payload.get("success") is False or payload.get("error"):
        return int(HTTPStatus.BAD_REQUEST), payload
    return int(HTTPStatus.OK), payload


def _takyon_app_json(status: int | HTTPStatus, payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=int(status), content=payload)


def _takyon_app_session_token(request: Request) -> str:
    return _cookie_value(request.headers.get("cookie", ""), TAKYON_APP_SESSION_COOKIE)


def _takyon_app_origin(request: Request, body: dict[str, Any] | None = None) -> str:
    body_origin = (body or {}).get("origin")
    if isinstance(body_origin, str) and body_origin.strip():
        return body_origin.strip().rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}" if host else str(request.base_url).rstrip("/")


def _dashboard_origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}" if host else str(request.base_url).rstrip("/")


def _dashboard_absolute_url(request: Request, path: str) -> str:
    safe_path = _same_origin_path(path or "/")
    prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
    if prefix:
        if safe_path == "/":
            safe_path = prefix
        elif safe_path != prefix and not safe_path.startswith(f"{prefix}/"):
            safe_path = f"{prefix}{safe_path}"
    return f"{_dashboard_origin(request).rstrip('/')}{safe_path}"


def _takyon_app_set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        TAKYON_APP_SESSION_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        path="/",
        secure=_https_for_cookie(request.headers, _takyon_app_origin(request)),
        httponly=True,
        samesite="lax",
    )


def _takyon_test_checkout_receipt_path(business: str, intent_id: str) -> Path:
    safe_business = re.sub(r"[^a-z0-9-]", "", str(business or "").strip().lower())
    safe_intent = re.sub(r"[^a-z0-9]", "", str(intent_id or "").strip().lower())
    return get_takyon_home() / "businesses" / safe_business / "metrics" / "receipts" / "app-checkout" / f"{safe_intent}.json"


def _takyon_render_test_checkout_page(*, business: str, receipt: dict[str, Any]) -> HTMLResponse:
    plan_key = html.escape(str(receipt.get("plan_key") or "monthly"))
    app_name = html.escape(str(receipt.get("business") or business))
    success_url = str(receipt.get("success_url") or "/app").strip() or "/app"
    cancel_url = str(receipt.get("cancel_url") or "/pricing").strip() or "/pricing"
    customer_email = str(receipt.get("customer_email") or "").strip()
    customer_line = (
        f"<p class='muted'>Prepared for <strong>{html.escape(customer_email)}</strong>.</p>"
        if customer_email
        else ""
    )
    body = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{app_name} checkout</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f3ea;
        --card: #fffdf8;
        --ink: #17130d;
        --muted: #6e665c;
        --accent: #0d8f79;
        --border: #ded6ca;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
        background: radial-gradient(circle at top, #fffaf0 0%, var(--bg) 58%);
        color: var(--ink);
        font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      .card {{
        width: min(560px, 100%);
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 24px;
        box-shadow: 0 24px 80px rgba(23, 19, 13, 0.08);
        padding: 28px;
      }}
      .eyebrow {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 6px 12px;
        border-radius: 999px;
        background: rgba(13, 143, 121, 0.10);
        color: var(--accent);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}
      h1 {{ margin: 16px 0 12px; font-size: clamp(28px, 5vw, 40px); line-height: 1.05; }}
      p {{ margin: 0 0 14px; font-size: 16px; line-height: 1.55; }}
      .muted {{ color: var(--muted); }}
      .actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 22px; }}
      .button {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 46px;
        padding: 0 18px;
        border-radius: 999px;
        border: 1px solid transparent;
        text-decoration: none;
        font-weight: 700;
      }}
      .button-primary {{ background: var(--accent); color: white; }}
      .button-secondary {{ border-color: var(--border); color: var(--ink); background: transparent; }}
    </style>
  </head>
  <body>
    <main class="card">
      <div class="eyebrow">Test checkout</div>
      <h1>Subscription flow is wired.</h1>
      <p>This business is currently running in test mode, so no real charge was created for the <strong>{plan_key}</strong> plan.</p>
      {customer_line}
      <p class="muted">You can continue back into the product and keep testing the paid flow without touching real billing.</p>
      <div class="actions">
        <a class="button button-primary" href="{html.escape(success_url, quote=True)}">Continue to app</a>
        <a class="button button-secondary" href="{html.escape(cancel_url, quote=True)}">Back to pricing</a>
      </div>
    </main>
  </body>
</html>
"""
    return HTMLResponse(body, status_code=int(HTTPStatus.OK))


async def _takyon_app_read_json(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw.strip():
        return {}
    data = json.loads(raw.decode("utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


def _takyon_app_route_parts(route: str) -> list[str]:
    return [part for part in route.split("/") if part]


def _takyon_app_gateway_error_payload(detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict):
        payload = {"success": False}
        payload.update(detail)
        if "error" not in payload and "detail" not in payload:
            payload["detail"] = "gateway_error"
        return payload
    return {"success": False, "error": str(detail)}


def _takyon_owner_token_on_app_plane(request: Request) -> bool:
    auth = str(request.headers.get("authorization") or "").strip()
    return auth.startswith("Bearer tk_")


def _takyon_app_broker_generate(
    *,
    business: str,
    body: dict[str, Any],
    session_token: str,
) -> tuple[int, dict[str, Any]]:
    from plugins.takyon.ai_gateway import (
        GatewayMessageError,
        broker_message_for_business,
    )
    from plugins.takyon.core import _db_backend
    from plugins.takyon.runtime_app import RuntimeNotConfigured

    if _db_backend() != "postgres":
        return int(HTTPStatus.SERVICE_UNAVAILABLE), {
            "success": False,
            "error": "app generate requires the Postgres runtime authority",
        }

    try:
        resolved_url = _resolve_runtime_database_url()
    except RuntimeNotConfigured:
        return int(HTTPStatus.SERVICE_UNAVAILABLE), {
            "success": False,
            "error": "app generate authority is not configured",
        }

    try:
        import psycopg
    except Exception:
        return int(HTTPStatus.SERVICE_UNAVAILABLE), {
            "success": False,
            "error": "app generate authority requires psycopg",
        }

    conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
    try:
        payload = broker_message_for_business(
            conn,
            business_slug=business,
            raw_session_token=session_token,
            body=body,
            audit_route=f"/api/takyon/apps/{business}/generate",
        )
        return int(HTTPStatus.OK), payload
    except GatewayMessageError as exc:
        return exc.status_code, _takyon_app_gateway_error_payload(exc.detail)
    finally:
        conn.close()


# Canonical product-app rail sub-routes served by the shared Hermes app
# runtime. On a product host (<slug>.<company-base-domain>) the business is
# fixed by the hostname, so a generated front-end can call these rails at any
# reasonable path — bare ("/auth/request"), "/api/"-prefixed, or with an
# embedded slug ("/api/takyon/apps/<slug>/...") — and the runtime resolves
# them to the host's business. This removes the recurring "rail not wired"
# 404 when a generated site guesses the wrong API base. See CLAUDE.md: the
# shared app runtime owns auth/session/account/profile/checkout/usage/generate.
_PRODUCT_APP_RAIL_ROUTES: frozenset = frozenset({
    "auth/request",
    "auth/verify",
    "session",
    "account",
    "profile",
    "checkout",
    "usage",
    "generate",
})


def _normalize_product_rail_route(path: str) -> Optional[str]:
    """Map any reasonable product-app rail path to its canonical sub-route.

    Returns the canonical rail route (e.g. ``auth/request``) when ``path`` is
    a recognised rail call, else ``None``. Strips an optional ``api/`` prefix
    and an optional ``takyon/apps/<slug>/`` or ``generated-apps/<slug>/``
    segment so the embedded slug (if any) is ignored in favour of the host.
    """
    candidate = (path or "").strip("/").lower()
    if candidate.startswith("api/"):
        candidate = candidate[len("api/"):]
    for prefix in ("takyon/apps/", "generated-apps/"):
        if candidate.startswith(prefix):
            _slug, _, tail = candidate[len(prefix):].partition("/")
            candidate = tail
            break
    return candidate if candidate in _PRODUCT_APP_RAIL_ROUTES else None


async def _takyon_app_get(request: Request, business: str, route: str) -> Response:
    if _takyon_owner_token_on_app_plane(request):
        return _takyon_app_json(
            HTTPStatus.FORBIDDEN,
            {"success": False, "error": "owner_token_rejected_on_app_plane"},
        )
    parts = _takyon_app_route_parts(route)
    if parts == ["auth", "verify"]:
        status, payload = _takyon_app_tool(handle_business_verify_app_magic_link({
            "business": business,
            "token": request.query_params.get("token") or "",
        }))
        if status != int(HTTPStatus.OK):
            return _takyon_app_json(status, payload)
        redirect = _same_origin_path(request.query_params.get("redirect") or "/?signed_in=1")
        response = RedirectResponse(redirect, status_code=int(HTTPStatus.FOUND))
        _takyon_app_set_session_cookie(response, request, str(payload["session_token"]))
        return response

    if parts in (["session"], ["account"]):
        token = _takyon_app_session_token(request)
        if not token:
            return _takyon_app_json(HTTPStatus.OK, {"success": True, "authenticated": False})
        status, payload = _takyon_app_tool(handle_business_read_app_account({
            "business": business,
            "session_token": token,
        }))
        payload["authenticated"] = status == int(HTTPStatus.OK)
        return _takyon_app_json(status, payload)

    if parts == ["profile"]:
        token = _takyon_app_session_token(request)
        if not token:
            return _takyon_app_json(HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"})
        status, payload = _takyon_app_tool(handle_business_read_app_profile({
            "business": business,
            "session_token": token,
        }))
        return _takyon_app_json(status, payload)

    if parts == ["checkout"]:
        intent_id = str(
            request.query_params.get("checkout_intent_id")
            or request.query_params.get("intent_id")
            or request.query_params.get("intent")
            or ""
        ).strip()
        if not intent_id:
            return _takyon_app_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})
        receipt_path = _takyon_test_checkout_receipt_path(business, intent_id)
        if not receipt_path.exists():
            return _takyon_app_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "checkout intent not found"})
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except Exception:
            return _takyon_app_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"success": False, "error": "checkout receipt unreadable"})
        if str(receipt.get("mode") or "") != "test":
            return _takyon_app_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "checkout intent not found"})
        return _takyon_render_test_checkout_page(business=business, receipt=receipt)

    return _takyon_app_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})


async def _takyon_app_post(request: Request, business: str, route: str) -> Response:
    if _takyon_owner_token_on_app_plane(request):
        return _takyon_app_json(
            HTTPStatus.FORBIDDEN,
            {"success": False, "error": "owner_token_rejected_on_app_plane"},
        )
    try:
        body = await _takyon_app_read_json(request)
    except json.JSONDecodeError as exc:
        return _takyon_app_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": f"invalid JSON body: {exc}"})

    parts = _takyon_app_route_parts(route)
    if parts == ["auth", "request"]:
        status, payload = _takyon_app_tool(handle_business_request_app_magic_link({
            "business": business,
            "email": body.get("email"),
            "name": body.get("name"),
            "origin": _takyon_app_origin(request, body),
            "app_slug": body.get("app_slug") or business,
            "product_name": body.get("product_name") or business,
            "send_email": bool(body.get("send_email", True)),
        }))
        if body.get("return_token") is not True:
            payload.pop("token", None)
        return _takyon_app_json(status, payload)

    if parts == ["checkout"]:
        token = _takyon_app_session_token(request)
        account: dict[str, Any] = {}
        if token:
            _account_status, account = _takyon_app_tool(handle_business_read_app_account({
                "business": business,
                "session_token": token,
            }))
        status, payload = _takyon_app_tool(handle_business_create_app_checkout({
            "business": business,
            "plan_key": body.get("plan_key") or body.get("planKey"),
            "success_url": body.get("success_url") or body.get("successUrl"),
            "cancel_url": body.get("cancel_url") or body.get("cancelUrl"),
            "customer_email": body.get("customer_email") or body.get("customerEmail") or (account.get("user") or {}).get("email"),
            "app_user_id": (account.get("user") or {}).get("id"),
            "origin": _takyon_app_origin(request, body),
            "metadata": body.get("metadata") or {},
        }))
        return _takyon_app_json(status, payload)

    if parts == ["account"]:
        action = (
            str(body.get("action") or "")
            .strip()
            .lower()
            .replace("-", "_")
        )
        if action == "cancel_subscription":
            token = _takyon_app_session_token(request)
            if not token:
                return _takyon_app_json(HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"})
            status, payload = _takyon_app_tool(handle_business_cancel_app_subscription({
                "business": business,
                "session_token": token,
            }))
            return _takyon_app_json(status, payload)
        return _takyon_app_json(
            HTTPStatus.BAD_REQUEST,
            {"success": False, "error": "unsupported_account_action"},
        )

    if parts == ["profile"]:
        token = _takyon_app_session_token(request)
        if not token:
            return _takyon_app_json(HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"})
        status, payload = _takyon_app_tool(handle_business_upsert_app_profile({
            "business": business,
            "session_token": token,
            "display_name": body["display_name"] if "display_name" in body else body.get("displayName"),
            "headline": body["headline"] if "headline" in body else body.get("headline"),
            "bio": body["bio"] if "bio" in body else body.get("bio"),
            "attributes": body["attributes"] if "attributes" in body else None,
            "metadata": body["metadata"] if "metadata" in body else None,
            "idempotency_key": body.get("idempotency_key") or body.get("idempotencyKey") or f"profile:{business}:{uuid.uuid4().hex}",
        }))
        return _takyon_app_json(status, payload)

    if parts == ["usage"]:
        token = _takyon_app_session_token(request)
        if not token:
            return _takyon_app_json(HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"})
        account_status, account = _takyon_app_tool(handle_business_read_app_account({
            "business": business,
            "session_token": token,
        }))
        if account_status != int(HTTPStatus.OK):
            return _takyon_app_json(account_status, account)
        user = account.get("user") or {}
        status, payload = _takyon_app_tool(handle_business_record_app_usage({
            "business": business,
            "app_user_id": user.get("id"),
            "app_user_tier": user.get("tier"),
            "purpose": body.get("purpose") or "product_usage",
            "route": body.get("route") or request.url.path,
            "status": body.get("status") or "completed",
            "estimated_cost_microusd": body.get("estimated_cost_microusd") or body.get("estimatedCostMicrousd") or 0,
            "actual_cost_microusd": body.get("actual_cost_microusd") or body.get("actualCostMicrousd") or 0,
            "input_tokens": body.get("input_tokens") or body.get("inputTokens"),
            "output_tokens": body.get("output_tokens") or body.get("outputTokens"),
            "provider_request_id": body.get("provider_request_id") or body.get("providerRequestId"),
            "provider": body.get("provider"),
            "model": body.get("model"),
            "metadata": body.get("metadata") or {},
            "idempotency_key": body.get("idempotency_key") or body.get("idempotencyKey") or f"usage:{business}:{user.get('id')}:{uuid.uuid4().hex}",
        }))
        return _takyon_app_json(status, payload)

    if parts == ["generate"]:
        token = _takyon_app_session_token(request)
        if not token:
            return _takyon_app_json(HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"})
        status, payload = _takyon_app_broker_generate(
            business=business,
            body=body,
            session_token=token,
        )
        return _takyon_app_json(status, payload)

    return _takyon_app_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})


@app.get("/api/takyon/apps/{business}/{route:path}")
async def takyon_app_api_get(request: Request, business: str, route: str):
    return await _takyon_app_get(request, business, route)


@app.post("/api/takyon/apps/{business}/{route:path}")
async def takyon_app_api_post(request: Request, business: str, route: str):
    return await _takyon_app_post(request, business, route)


@app.get("/api/generated-apps/{business}/{route:path}")
async def takyon_generated_app_api_get(request: Request, business: str, route: str):
    return await _takyon_app_get(request, business, route)


@app.post("/api/generated-apps/{business}/{route:path}")
async def takyon_generated_app_api_post(request: Request, business: str, route: str):
    return await _takyon_app_post(request, business, route)


@app.post("/api/webhooks/stripe")
async def takyon_app_stripe_webhook(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    status, payload = _takyon_app_tool(handle_business_record_stripe_webhook({
        "raw_body": raw_body,
        "stripe_signature": request.headers.get("stripe-signature") or "",
    }))
    return _takyon_app_json(status, payload)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "role": _host_role()}


# ---------------------------------------------------------------------------
# Config schema — auto-generated from DEFAULT_CONFIG
# ---------------------------------------------------------------------------

# Manual overrides for fields that need select options or custom types
_SCHEMA_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "model": {
        "type": "string",
        "description": "Default model (e.g. anthropic/claude-sonnet-4.6)",
        "category": "general",
    },
    "model_context_length": {
        "type": "number",
        "description": "Context window override (0 = auto-detect from model metadata)",
        "category": "general",
    },
    "terminal.backend": {
        "type": "select",
        "description": "Terminal execution backend",
        "options": ["local", "docker", "ssh", "modal", "daytona", "vercel_sandbox", "singularity"],
    },
    "terminal.vercel_runtime": {
        "type": "select",
        "description": "Vercel Sandbox runtime",
        "options": ["node24", "node22", "python3.13"],  # sync with _SUPPORTED_VERCEL_RUNTIMES in terminal_tool.py
    },
    "terminal.modal_mode": {
        "type": "select",
        "description": "Modal sandbox mode",
        "options": ["sandbox", "function"],
    },
    "tts.provider": {
        "type": "select",
        "description": "Text-to-speech provider",
        "options": ["edge", "elevenlabs", "openai", "neutts"],
    },
    "stt.provider": {
        "type": "select",
        "description": "Speech-to-text provider",
        # "mistral" temporarily removed — mistralai PyPI package quarantined
        # (malicious 2.4.6 release on 2026-05-12). Restore once available.
        "options": ["local", "openai"],
    },
    "display.skin": {
        "type": "select",
        "description": "CLI visual theme",
        "options": ["default", "ares", "mono", "slate"],
    },
    "dashboard.theme": {
        "type": "select",
        "description": "Web dashboard visual theme",
        "options": ["default", "midnight", "ember", "mono", "cyberpunk", "rose"],
    },
    "display.resume_display": {
        "type": "select",
        "description": "How resumed sessions display history",
        "options": ["minimal", "full", "off"],
    },
    "display.busy_input_mode": {
        "type": "select",
        "description": "Input behavior while agent is running",
        "options": ["interrupt", "queue", "steer"],
    },
    "memory.provider": {
        "type": "select",
        "description": "Memory provider plugin",
        "options": ["builtin", "honcho"],
    },
    "approvals.mode": {
        "type": "select",
        "description": "Dangerous command approval mode",
        "options": ["ask", "yolo", "deny"],
    },
    "context.engine": {
        "type": "select",
        "description": "Context management engine",
        "options": ["default", "custom"],
    },
    "human_delay.mode": {
        "type": "select",
        "description": "Simulated typing delay mode",
        "options": ["off", "typing", "fixed"],
    },
    "logging.level": {
        "type": "select",
        "description": "Log level for agent.log",
        "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
    },
    "agent.service_tier": {
        "type": "select",
        "description": "API service tier (OpenAI/Anthropic)",
        "options": ["", "auto", "default", "flex"],
    },
    "delegation.reasoning_effort": {
        "type": "select",
        "description": "Reasoning effort for delegated subagents",
        "options": ["", "low", "medium", "high"],
    },
}

# Categories with fewer fields get merged into "general" to avoid tab sprawl.
_CATEGORY_MERGE: Dict[str, str] = {
    "privacy": "security",
    "context": "agent",
    "skills": "agent",
    "cron": "agent",
    "network": "agent",
    "checkpoints": "agent",
    "approvals": "security",
    "human_delay": "display",
    "dashboard": "display",
    "code_execution": "agent",
    "prompt_caching": "agent",
    "goals": "agent",
    # Only `telegram.reactions` currently lives under telegram — fold it in
    # with the other messaging-platform config (discord) so it isn't an
    # orphan tab of one field.
    "telegram": "discord",
}

# Display order for tabs — unlisted categories sort alphabetically after these.
_CATEGORY_ORDER = [
    "general", "agent", "terminal", "display", "delegation",
    "memory", "compression", "security", "browser", "voice",
    "tts", "stt", "logging", "discord", "auxiliary",
]


def _infer_type(value: Any) -> str:
    """Infer a UI field type from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _build_schema_from_config(
    config: Dict[str, Any],
    prefix: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Walk DEFAULT_CONFIG and produce a flat dot-path → field schema dict."""
    schema: Dict[str, Dict[str, Any]] = {}
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key

        # Skip internal / version keys
        if full_key in {"_config_version",}:
            continue

        # Category is the first path component for nested keys, or "general"
        # for top-level scalar fields (model, toolsets, timezone, etc.).
        if prefix:
            category = prefix.split(".")[0]
        elif isinstance(value, dict):
            category = key
        else:
            category = "general"

        if isinstance(value, dict):
            # Recurse into nested dicts
            schema.update(_build_schema_from_config(value, full_key))
        else:
            entry: Dict[str, Any] = {
                "type": _infer_type(value),
                "description": full_key.replace(".", " → ").replace("_", " ").title(),
                "category": category,
            }
            # Apply manual overrides
            if full_key in _SCHEMA_OVERRIDES:
                entry.update(_SCHEMA_OVERRIDES[full_key])
            # Merge small categories
            entry["category"] = _CATEGORY_MERGE.get(entry["category"], entry["category"])
            schema[full_key] = entry
    return schema


CONFIG_SCHEMA = _build_schema_from_config(DEFAULT_CONFIG)

# Inject virtual fields that don't live in DEFAULT_CONFIG but are surfaced
# by the normalize/denormalize cycle.  Insert model_context_length right after
# the "model" key so it renders adjacent in the frontend.
_mcl_entry = _SCHEMA_OVERRIDES["model_context_length"]
_ordered_schema: Dict[str, Dict[str, Any]] = {}
for _k, _v in CONFIG_SCHEMA.items():
    _ordered_schema[_k] = _v
    if _k == "model":
        _ordered_schema["model_context_length"] = _mcl_entry
CONFIG_SCHEMA = _ordered_schema


class ConfigUpdate(BaseModel):
    config: dict


class EnvVarUpdate(BaseModel):
    key: str
    value: str


class EnvVarDelete(BaseModel):
    key: str


class EnvVarReveal(BaseModel):
    key: str


class TuiRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    method: str
    params: Dict[str, Any] = Field(default_factory=dict)


class ModelAssignment(BaseModel):
    """Payload for POST /api/model/set — assign a provider/model to a slot.

    scope="main"        → writes model.provider + model.default
    scope="auxiliary"   → writes auxiliary.<task>.provider + auxiliary.<task>.model
    scope="auxiliary" with task=""  → applied to every auxiliary.* slot
    scope="auxiliary" with task="__reset__"  → resets every slot to provider="auto"
    """
    scope: str
    provider: str
    model: str
    task: str = ""


_GATEWAY_HEALTH_URL = os.getenv("GATEWAY_HEALTH_URL")
try:
    _GATEWAY_HEALTH_TIMEOUT = float(os.getenv("GATEWAY_HEALTH_TIMEOUT", "3"))
except (ValueError, TypeError):
    _log.warning(
        "Invalid GATEWAY_HEALTH_TIMEOUT value %r — using default 3.0s",
        os.getenv("GATEWAY_HEALTH_TIMEOUT"),
    )
    _GATEWAY_HEALTH_TIMEOUT = 3.0

# DEPRECATED (scheduled for removal): GATEWAY_HEALTH_URL / GATEWAY_HEALTH_TIMEOUT.
# Cross-container / cross-host gateway liveness detection will be folded into a
# first-class dashboard config key so it's no longer Docker-adjacent lore buried
# in env vars.  The env vars still work for now so existing Compose deployments
# don't break.  Do not add new callers — wire new uses through the planned
# config surface.


def _probe_gateway_health() -> tuple[bool, dict | None]:
    """Probe the gateway via its HTTP health endpoint (cross-container).

    .. deprecated::
        Driven by the deprecated ``GATEWAY_HEALTH_URL`` /
        ``GATEWAY_HEALTH_TIMEOUT`` env vars.  Scheduled for removal alongside
        a move to a first-class dashboard config key.  See
        :data:`_GATEWAY_HEALTH_URL` for context.

    Uses ``/health/detailed`` first (returns full state), falling back to
    the simpler ``/health`` endpoint.  Returns ``(is_alive, body_dict)``.

    Accepts any of these as ``GATEWAY_HEALTH_URL``:
    - ``http://gateway:8642``                (base URL — recommended)
    - ``http://gateway:8642/health``         (explicit health path)
    - ``http://gateway:8642/health/detailed`` (explicit detailed path)

    This is a **blocking** call — run via ``run_in_executor`` from async code.
    """
    if not _GATEWAY_HEALTH_URL:
        return False, None

    # Normalise to base URL so we always probe the right paths regardless of
    # whether the user included /health or /health/detailed in the env var.
    base = _GATEWAY_HEALTH_URL.rstrip("/")
    if base.endswith("/health/detailed"):
        base = base[: -len("/health/detailed")]
    elif base.endswith("/health"):
        base = base[: -len("/health")]

    for path in (f"{base}/health/detailed", f"{base}/health"):
        try:
            req = urllib.request.Request(path, method="GET")
            with urllib.request.urlopen(req, timeout=_GATEWAY_HEALTH_TIMEOUT) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read())
                    return True, body
        except Exception:
            continue
    return False, None


@app.get("/api/status")
async def get_status():
    current_ver, latest_ver = check_config_version()

    # --- Gateway liveness detection ---
    # Try local PID check first (same-host).  If that fails and a remote
    # GATEWAY_HEALTH_URL is configured, probe the gateway over HTTP so the
    # dashboard works when the gateway runs in a separate container.
    gateway_pid = get_running_pid()
    gateway_running = gateway_pid is not None
    remote_health_body: dict | None = None

    if not gateway_running and _GATEWAY_HEALTH_URL:
        loop = asyncio.get_running_loop()
        alive, remote_health_body = await loop.run_in_executor(
            None, _probe_gateway_health
        )
        if alive:
            gateway_running = True
            # PID from the remote container (display only — not locally valid)
            if remote_health_body:
                gateway_pid = remote_health_body.get("pid")

    gateway_state = None
    gateway_platforms: dict = {}
    gateway_exit_reason = None
    gateway_updated_at = None
    configured_gateway_platforms: set[str] | None = None
    try:
        from gateway.config import load_gateway_config

        gateway_config = load_gateway_config()
        configured_gateway_platforms = {
            platform.value for platform in gateway_config.get_connected_platforms()
        }
    except Exception:
        configured_gateway_platforms = None

    # Prefer the detailed health endpoint response (has full state) when the
    # local runtime status file is absent or stale (cross-container).
    runtime = read_runtime_status()
    if runtime is None and remote_health_body and remote_health_body.get("gateway_state"):
        runtime = remote_health_body

    if runtime:
        gateway_state = runtime.get("gateway_state")
        gateway_platforms = runtime.get("platforms") or {}
        if configured_gateway_platforms is not None:
            gateway_platforms = {
                key: value
                for key, value in gateway_platforms.items()
                if key in configured_gateway_platforms
            }
        gateway_exit_reason = runtime.get("exit_reason")
        gateway_updated_at = runtime.get("updated_at")
        if not gateway_running:
            gateway_state = gateway_state if gateway_state in {"stopped", "startup_failed"} else "stopped"
            gateway_platforms = {}
        elif gateway_running and remote_health_body is not None:
            # The health probe confirmed the gateway is alive, but the local
            # runtime status file may be stale (cross-container).  Override
            # stopped/None state so the dashboard shows the correct badge.
            if gateway_state in {None, "stopped"}:
                gateway_state = "running"

    # If there was no runtime info at all but the health probe confirmed alive,
    # ensure we still report the gateway as running (no shared volume scenario).
    if gateway_running and gateway_state is None and remote_health_body is not None:
        gateway_state = "running"

    active_sessions = 0
    try:
        from takyon_state import SessionDB
        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(limit=50)
            now = time.time()
            active_sessions = sum(
                1 for s in sessions
                if s.get("ended_at") is None
                and (now - s.get("last_active", s.get("started_at", 0))) < 300
            )
        finally:
            db.close()
    except Exception:
        pass

    return {
        "version": __version__,
        "release_date": __release_date__,
        "takyon_home": str(get_takyon_home()),
        "config_path": str(get_config_path()),
        "env_path": str(get_env_path()),
        "config_version": current_ver,
        "latest_config_version": latest_ver,
        "gateway_running": gateway_running,
        "gateway_pid": gateway_pid,
        "gateway_health_url": _GATEWAY_HEALTH_URL,
        "gateway_state": gateway_state,
        "gateway_platforms": gateway_platforms,
        "gateway_exit_reason": gateway_exit_reason,
        "gateway_updated_at": gateway_updated_at,
        "active_sessions": active_sessions,
    }


@app.get("/api/takyon/operator/account")
async def get_takyon_operator_account(request: Request) -> dict[str, Any]:
    """Read-only operator billing snapshot for the dashboard UI."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        return {
            "available": False,
            "reason": "operator_principal_unavailable",
        }

    return _takyon_operator_account_payload(request, principal)


def _takyon_operator_account_payload(request: Request, principal: Any) -> dict[str, Any]:
    try:
        from plugins.takyon import billing
        from plugins.takyon.control_api import (
            get_operator_payout_state,
            sync_operator_subscription_allowance,
        )
        from plugins.takyon.core import _db_backend
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        if _db_backend() != "postgres":
            return {
                "available": False,
                "reason": "postgres_required",
                "owned_business_count": len(principal.business_slugs),
                "status": principal.status,
                "user_id": str(principal.user_id),
            }

        try:
            url = _request_runtime_database_url(request)
            if not url:
                raise RuntimeNotConfigured("database_unconfigured")
        except RuntimeNotConfigured:
            return {
                "available": False,
                "reason": "database_unconfigured",
                "owned_business_count": len(principal.business_slugs),
                "status": principal.status,
                "user_id": str(principal.user_id),
            }

        import psycopg

        conn = psycopg.connect(url, autocommit=True)
        try:
            released = _release_stale_operator_reservations(conn, str(principal.user_id))
            if released:
                _log.info(
                    "released %s stale operator reservation(s) for operator %s",
                    released,
                    principal.user_id,
                )
            subscription_state = sync_operator_subscription_allowance(
                conn,
                str(principal.user_id),
                refresh_live=True,
            )
            balances = billing.get_billing_balances(conn, str(principal.user_id))
            reconciled = billing.reconcile_billing(conn, str(principal.user_id))
            payout_state = get_operator_payout_state(
                conn, str(principal.user_id), refresh_live=True
            )
        finally:
            conn.close()

        allowance_included = max(0, int(balances.allowance_included_cents))
        allowance_used = max(0, int(balances.allowance_used_cents))
        allowance_remaining = max(0, int(balances.allowance_remaining_cents))
        topup_balance = max(0, int(balances.topup_balance_cents))
        reserved = max(0, int(reconciled.get("reserved_cents", balances.reserved_cents)))
        allowance_percent_remaining = (
            round((allowance_remaining / allowance_included) * 100, 1)
            if allowance_included > 0
            else None
        )
        allowance_percent_used = (
            round((allowance_used / allowance_included) * 100, 1)
            if allowance_included > 0
            else None
        )
        return {
            "available": True,
            "allowance_included_cents": allowance_included,
            "allowance_remaining_cents": allowance_remaining,
            "allowance_used_cents": allowance_used,
            "allowance_percent_remaining": allowance_percent_remaining,
            "allowance_percent_used": allowance_percent_used,
            "operator_plan_name": subscription_state.plan_name,
            "operator_plan_weekly_allowance_cents": int(subscription_state.weekly_allowance_cents or 0),
            "allowance_period_start": (
                balances.allowance_period_start.isoformat()
                if getattr(balances, "allowance_period_start", None) is not None
                else subscription_state.allowance_period_start
            ),
            "allowance_resets_at": (
                balances.allowance_resets_at.isoformat()
                if getattr(balances, "allowance_resets_at", None) is not None
                else subscription_state.allowance_resets_at
            ),
            "owned_business_count": len(principal.business_slugs),
            "reserved_cents": reserved,
            "reserved_allowance_cents": int(reconciled.get("reserved_allowance_cents", 0) or 0),
            "reserved_topup_cents": int(reconciled.get("reserved_topup_cents", 0) or 0),
            "spendable_cents": allowance_remaining + topup_balance,
            "status": principal.status,
            "topup_balance_cents": topup_balance,
            "operator_subscription_status": subscription_state.subscription_status,
            "owed_balance_cents": int(payout_state.owed_balance_cents),
            "paid_out_cents": int(payout_state.paid_out_cents),
            "payout_currency": payout_state.payout_currency,
            "stripe_connect_status": payout_state.stripe_connect_status,
            "payouts_enabled": bool(payout_state.payouts_enabled),
            "details_submitted": bool(payout_state.details_submitted),
            "user_id": str(principal.user_id),
        }
    except Exception as exc:  # noqa: BLE001 - UI should degrade honestly, not crash
        _log.warning("dashboard operator account read failed: %s", exc)
        return {
            "available": False,
            "reason": "read_failed",
            "owned_business_count": len(principal.business_slugs),
            "status": principal.status,
            "user_id": str(principal.user_id),
        }


def _takyon_operator_businesses_payload(principal: Any) -> dict[str, Any]:
    try:
        from plugins.takyon.core import TakyonStore

        store = TakyonStore(operator_user_id=str(principal.user_id))
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM businesses WHERE owner_user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (str(principal.user_id), 200),
            ).fetchall()
        items = [store._row_to_dict(row) for row in rows]
        return {
            "available": True,
            "businesses": items,
            "owned_business_count": len(items),
            "user_id": str(principal.user_id),
        }
    except Exception as exc:  # noqa: BLE001 - UI should degrade honestly, not crash
        _log.warning("dashboard operator businesses read failed: %s", exc)
        return {
            "available": False,
            "businesses": [],
            "owned_business_count": len(principal.business_slugs),
            "reason": "read_failed",
            "user_id": str(principal.user_id),
        }


def _takyon_job_label(kind: Any) -> str:
    value = str(kind or "").strip().lower()
    if value == "ceo_bootstrap":
        return "CEO bootstrap"
    if value == "ceo_wake":
        return "CEO wake"
    if value == "ceo_turn":
        return "CEO turn"
    if value == "product.deploy":
        return "Publish product site"
    if value == "product.build":
        return "Build product surface"
    if value.startswith("distribution.") or value.startswith("outreach."):
        return "Publish or test outreach"
    if "creative" in value or "ad" in value:
        return "Generate ad creative"
    if not value:
        return "Recorded work"
    value = re.sub(r"[._-]+", " ", value)
    return " ".join(part.capitalize() for part in value.split())


def _takyon_job_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if re.search(r"blocked|fail|error", text):
        return "blocked"
    if re.search(r"queued|scheduled|waiting|pending", text):
        return "scheduled"
    if re.search(r"running|active|working", text):
        return "running"
    if re.search(r"done|complete|completed|success|succeeded|passed", text):
        return "done"
    return text or "idle"


def _takyon_status_tone(value: Any) -> str:
    status = _takyon_job_status(value)
    if status == "blocked":
        return "blocked"
    if status == "scheduled":
        return "waiting"
    if status == "running":
        return "active"
    if status == "done":
        return "done"
    return "neutral"


def _takyon_blank_outreach_channels() -> dict[str, Any]:
    return {
        "x": {
            "channel": "x",
            "label": "X",
            "status": "missing",
            "updated_at": "",
            "primary_action_label": "start",
            "draft_path": "",
            "items": [],
            "campaigns": [],
            "latest_job": None,
            "published_count": 0,
            "campaign_count": 0,
            "metrics_count": 0,
        },
        "reddit": {
            "channel": "reddit",
            "label": "Reddit",
            "status": "missing",
            "updated_at": "",
            "primary_action_label": "start",
            "items": [],
            "campaigns": [],
            "latest_job": None,
            "published_count": 0,
            "campaign_count": 0,
            "metrics_count": 0,
        },
        "meta": {
            "channel": "meta",
            "label": "Meta",
            "status": "missing",
            "updated_at": "",
            "primary_action_label": "start",
            "items": [],
            "campaigns": [],
            "latest_job": None,
            "published_count": 0,
            "campaign_count": 0,
            "metrics_count": 0,
        },
    }


def _takyon_business_home_payload(operator_user_id: str, business: str) -> dict[str, Any]:
    from plugins.takyon.core import TakyonStore

    slug = str(business or "").strip().lower()
    store = TakyonStore(operator_user_id=operator_user_id)
    with store._connect() as conn:
        store._enforce_operator_business_access(conn, slug)
        current = store._ensure_business(conn, slug)
        surface = store._app_surface_contract(conn, slug)

        latest_jobs: list[dict[str, Any]] = []
        for table_name, source_name in (
            (store._work_requests_table(), "job"),
            ("jobs", "worker"),
        ):
            try:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM {table_name}
                    WHERE business_slug = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT 6
                    """,
                    (slug,),
                ).fetchall()
            except Exception:
                continue
            for row in rows:
                item = store._row_to_dict(row)
                if not item:
                    continue
                item["source"] = source_name
                latest_jobs.append(item)

    latest_jobs.sort(
        key=lambda item: (
            str(item.get("updated_at") or item.get("created_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    latest_jobs = latest_jobs[:8]

    product_blocker = str(surface.get("publish_blocker") or "").strip()
    current_action = {
        "source": "idle",
        "label": "Business is synced.",
        "status": "idle",
        "detail": "",
        "blocker": product_blocker,
    }
    if latest_jobs:
        preferred = next(
            (
                item
                for item in latest_jobs
                if _takyon_job_status(item.get("status")) in {"running", "scheduled", "blocked"}
            ),
            latest_jobs[0],
        )
        preferred_status = _takyon_job_status(preferred.get("status"))
        current_action = {
            "source": str(preferred.get("source") or "job").strip(),
            "label": _takyon_job_label(preferred.get("kind")),
            "status": preferred_status,
            "detail": str(
                (
                    preferred.get("payload")
                    if isinstance(preferred.get("payload"), dict)
                    else {}
                ).get("detail")
                or preferred.get("detail")
                or ""
            ).strip(),
            "blocker": product_blocker,
        }
    elif product_blocker:
        current_action = {
            "source": "product",
            "label": "Product publish blocker",
            "status": "blocked",
            "detail": product_blocker,
            "blocker": product_blocker,
        }

    task_cards = [
        {
            "id": f"{str(item.get('source') or 'job')}:{str(item.get('id') or index)}",
            "source": str(item.get("source") or "job").strip(),
            "label": _takyon_job_label(item.get("kind")),
            "status": _takyon_job_status(item.get("status")),
            "detail": str(
                (
                    item.get("payload")
                    if isinstance(item.get("payload"), dict)
                    else {}
                ).get("detail")
                or item.get("detail")
                or ""
            ).strip(),
            "tone": _takyon_status_tone(item.get("status")),
            "updated_at": str(item.get("updated_at") or item.get("created_at") or "").strip(),
        }
        for index, item in enumerate(latest_jobs[:8])
    ]

    public_url = str(surface.get("public_url") or "").strip()
    publish_status = str(surface.get("publish_status") or "").strip()
    website_status = "published" if public_url else ("publish_blocked" if product_blocker else "missing")
    outreach_channels = _takyon_blank_outreach_channels()
    reddit_campaigns = _takyon_collect_business_paid_campaigns(
        store,
        slug,
        "distribution/reddit-ads",
        "metrics/reddit-ads",
        plan_secondary_key="ad_group",
    )
    reddit_job = _takyon_latest_channel_job(latest_jobs, "reddit")
    reddit_start_spec = _takyon_channel_start_request_spec("reddit", reddit_campaigns, reddit_job)
    outreach_channels["reddit"].update(
        {
            "status": str(
                (reddit_campaigns[0] if reddit_campaigns else {}).get("status")
                or (reddit_job or {}).get("status")
                or "missing"
            ).strip()
            or "missing",
            "updated_at": str(
                (reddit_campaigns[0] if reddit_campaigns else {}).get("updated_at")
                or (reddit_job or {}).get("updated_at")
                or ""
            ).strip(),
            "primary_action_label": reddit_start_spec["primary_action_label"],
            "campaigns": reddit_campaigns[:8],
            "latest_job": reddit_job,
            "campaign_count": len(reddit_campaigns),
            "metrics_count": sum(1 for item in reddit_campaigns if isinstance(item.get("latest_metrics"), dict) and item.get("latest_metrics")),
        }
    )
    meta_campaigns = _takyon_collect_business_paid_campaigns(
        store,
        slug,
        "distribution/meta-ads",
        "metrics/meta-ads",
        plan_secondary_key="adset",
    )
    meta_job = _takyon_latest_channel_job(latest_jobs, "meta")
    meta_start_spec = _takyon_channel_start_request_spec("meta", meta_campaigns, meta_job)
    outreach_channels["meta"].update(
        {
            "status": str(
                (meta_campaigns[0] if meta_campaigns else {}).get("status")
                or (meta_job or {}).get("status")
                or "missing"
            ).strip()
            or "missing",
            "updated_at": str(
                (meta_campaigns[0] if meta_campaigns else {}).get("updated_at")
                or (meta_job or {}).get("updated_at")
                or ""
            ).strip(),
            "primary_action_label": meta_start_spec["primary_action_label"],
            "campaigns": meta_campaigns[:8],
            "latest_job": meta_job,
            "campaign_count": len(meta_campaigns),
            "metrics_count": sum(1 for item in meta_campaigns if isinstance(item.get("latest_metrics"), dict) and item.get("latest_metrics")),
        }
    )

    return {
        "business_slug": slug,
        "current": {
            "slug": slug,
            "name": str(current.get("name") or slug).strip() or slug,
            "goal": str(current.get("goal") or "").strip(),
            "mode": str(current.get("mode") or current.get("status") or "test").strip().lower() or "test",
        },
        "overview": {
            "product": {
                "status": str(surface.get("status") or "missing").strip() or "missing",
                "publish_status": publish_status,
                "public_url": public_url,
                "publish_blocker": product_blocker,
                "publish_receipt_path": str(surface.get("publish_receipt_path") or "").strip(),
                "source_path": str(surface.get("source_path") or "").strip(),
            },
            "metrics": {
                "users": 0,
                "paid_customers": 0,
                "mrr_cents": 0,
                "revenue_cents": 0,
                "checkout_intents": 0,
                "usage_events": 0,
                "unresolved_inbound": 0,
                "queued_jobs": sum(1 for item in latest_jobs if _takyon_job_status(item.get("status")) == "scheduled"),
            },
            "budget": {
                "business_amount": None,
                "business_status": "",
                "app_status": "",
                "app_limit_microusd": 0,
                "app_spent_microusd": 0,
                "app_remaining_microusd": 0,
            },
            "cron": [],
            "files": [],
            "jobs": [
                {
                    "id": str(item.get("id") or "").strip(),
                    "kind": str(item.get("kind") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "updated_at": str(item.get("updated_at") or "").strip(),
                    "created_at": str(item.get("created_at") or "").strip(),
                    "label": _takyon_job_label(item.get("kind")),
                    "detail": str(
                        (
                            item.get("payload")
                            if isinstance(item.get("payload"), dict)
                            else {}
                        ).get("detail")
                        or item.get("detail")
                        or ""
                    ).strip(),
                    "tone": _takyon_status_tone(item.get("status")),
                }
                for item in latest_jobs
            ],
            "agent_runs": [],
            "workers": [],
            "trace": [],
            "tasks": task_cards,
            "status_cards": [
                {
                    "label": "Current action",
                    "status": current_action["status"],
                    "detail": current_action["label"],
                    "tone": _takyon_status_tone(current_action["status"]),
                },
                {
                    "label": "Product publish",
                    "status": website_status,
                    "detail": product_blocker or publish_status or "Not published yet.",
                    "tone": "blocked" if product_blocker else ("done" if public_url else "waiting"),
                },
            ],
            "current_action": current_action,
            "ceo_loop": {
                "status": current_action["status"],
                "headline": current_action["label"],
                "detail": current_action["detail"],
                "next_action": current_action["detail"] or current_action["label"],
            },
            "wake_health": {},
            "research": {"status": "needed", "latest_path": "", "count": 0, "outputs": []},
            "research_outputs": [],
            "posts": [],
            "artifacts": {
                "website": {
                    "status": website_status,
                    "path": "",
                    "updated_at": "",
                    "deploy_status": "",
                    "source_path": str(surface.get("source_path") or "").strip(),
                    "public_url": public_url,
                    "publish_target": str(surface.get("publish_target") or "").strip(),
                    "publish_policy": str(surface.get("publish_policy") or "").strip(),
                    "publish_status": publish_status,
                    "publish_blocker": product_blocker,
                    "publish_receipt_path": str(surface.get("publish_receipt_path") or "").strip(),
                },
                "outreach": {
                    "status": next(
                        (
                            str(channel.get("status") or "").strip()
                            for channel in outreach_channels.values()
                            if str(channel.get("status") or "").strip()
                            and str(channel.get("status") or "").strip() != "missing"
                        ),
                        "missing",
                    ),
                    "path": "",
                    "receipt": "",
                    "updated_at": next(
                        (
                            str(channel.get("updated_at") or "").strip()
                            for channel in outreach_channels.values()
                            if str(channel.get("updated_at") or "").strip()
                        ),
                        "",
                    ),
                    "published_count": 0,
                    "items": [],
                    "receipts": [],
                    "channels": outreach_channels,
                },
                "creative_assets": {
                    "status": "missing",
                    "path": "",
                    "receipt": "",
                    "updated_at": "",
                    "count": 0,
                },
            },
            "conversations": {
                "active_threads": 0,
                "unresolved_messages": 0,
                "latest_message_at": "",
            },
            "generated_at": "",
            "pulse_warning": "",
        },
        "outputs": [],
        "background_run": None,
    }


def _read_takyon_business_site_preview(
    operator_user_id: str,
    business: str,
    requested_path: str,
) -> dict[str, Any]:
    from plugins.takyon.core import TakyonStore
    from tui_gateway.server import _TAKYON_MAX_SITE_PREVIEW_BYTES, _takyon_inline_static_site

    store = TakyonStore(operator_user_id=operator_user_id)
    normalized_requested_path = requested_path.strip().strip("/") or "product/site"
    if normalized_requested_path in {"product/site", "product/site/index.html"}:
        try:
            summary = store.read(
                scope=f"business:{business}",
                query="summary",
                include=["app"],
                limit=20,
            )
        except Exception:
            summary = {}
        app = summary.get("app") if isinstance(summary, dict) and isinstance(summary.get("app"), dict) else {}
        surface = app.get("surface") or app.get("surface_contract") or {}
        if isinstance(surface, dict):
            publish_status = str(surface.get("publish_status") or "").strip().lower()
            public_url = str(surface.get("public_url") or "").strip()
            if re.match(r"^https?://", public_url, re.IGNORECASE):
                return {
                    "business_slug": business,
                    "path": normalized_requested_path,
                    "size": 0,
                    "url": public_url,
                    "mode": "live_url",
                    "status": publish_status or "ready",
                }
    candidate = store._resolve_business_file(business, requested_path, sync=False)
    if candidate.is_dir() or not candidate.suffix:
        candidate = candidate / "index.html"
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"site preview not found: {requested_path}")
    if candidate.name != "index.html" and candidate.suffix.lower() != ".html":
        raise HTTPException(status_code=400, detail="site preview requires an HTML file or site directory")
    size = candidate.stat().st_size
    if size > _TAKYON_MAX_SITE_PREVIEW_BYTES:
        raise HTTPException(status_code=413, detail=f"site preview is too large: {size} bytes")
    business_root = store._business_root(business, sync=False)
    source_root = (business_root / "product/site").resolve()
    candidate_resolved = candidate.resolve()
    html_text = _takyon_inline_static_site(
        candidate,
        site_root=source_root if source_root in (candidate_resolved, *candidate_resolved.parents) else None,
    )
    encoded = base64.b64encode(html_text.encode("utf-8")).decode("ascii")
    rel = str(candidate.relative_to(business_root))
    return {
        "business_slug": business,
        "path": rel,
        "size": len(html_text.encode("utf-8")),
        "url": f"data:text/html;charset=utf-8;base64,{encoded}",
        "mode": "inline_html",
        "status": "ready",
    }


def _takyon_live_site_preview_wrapper_html(url: str) -> str:
    preview_url = str(url or "").strip()
    if not re.match(r"^https?://", preview_url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="preview url required")
    escaped_url = html.escape(preview_url, quote=True)
    return (
        "<!doctype html>"
        "<html><head>"
        '<meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        "<title>Takyon Product Preview</title>"
        "<style>"
        "html,body{margin:0;height:100%;background:#fff;overflow:hidden;}"
        "iframe{display:block;width:100%;height:100%;border:0;background:#fff;}"
        "</style>"
        "</head><body>"
        f'<iframe src="{escaped_url}" loading="eager" referrerpolicy="strict-origin-when-cross-origin"></iframe>'
        "</body></html>"
    )


def _read_takyon_business_workspace(
    operator_user_id: str,
    business: str,
    *,
    limit: int = 50,
    view: str = "full",
) -> dict[str, Any]:
    from tui_gateway.server import _takyon_workspace_payload

    payload = _takyon_workspace_payload(
        {"takyon_operator_user_id": operator_user_id},
        business,
        output_limit=max(1, min(int(limit or 50), 100)),
        view=str(view or "full").strip().lower() or "full",
    )
    return payload if isinstance(payload, dict) else {
        "business_slug": business,
        "current": {},
        "overview": {},
        "outputs": [],
        "deliverables": [],
        "background_run": None,
        "live_state": {
            "status": "idle",
            "label": "Idle",
            "detail": "",
            "updated_at": "",
            "tasks": [],
        },
    }


def _read_takyon_business_traction(
    operator_user_id: str,
    business: str,
    *,
    range_key: str = "M",
) -> dict[str, Any]:
    from plugins.takyon.core import TakyonStore

    store = TakyonStore(operator_user_id=operator_user_id)
    return store.traction_timeseries(business, range_key=str(range_key or "M"))


def _read_takyon_business_home(operator_user_id: str, business: str) -> dict[str, Any]:
    return _read_takyon_business_workspace(
        operator_user_id,
        business,
        limit=12,
        view="boot",
    )


@app.get("/api/takyon/operator/home")
async def get_takyon_operator_home(request: Request) -> dict[str, Any]:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        reason = "auth0_login_required" if _auth0_required_for_host(request.headers) else "operator_principal_unavailable"
        return {
            "available": False,
            "businesses": [],
            "account": {"available": False, "reason": reason},
            "reason": reason,
        }
    businesses = _takyon_operator_businesses_payload(principal)
    account = _takyon_operator_account_payload(request, principal)
    return {
        "available": bool(businesses.get("available") or account.get("available")),
        "businesses": businesses.get("businesses", []),
        "account": account,
        "owned_business_count": businesses.get("owned_business_count", len(principal.business_slugs)),
        "user_id": str(principal.user_id),
    }


@app.post("/api/takyon/operator/topup/checkout")
async def create_takyon_operator_topup_checkout(request: Request) -> dict[str, Any]:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        amount_cents = int(body.get("amount_cents") or 0)
    except (TypeError, ValueError):
        amount_cents = 0
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="amount_cents must be > 0")
    return_path = _same_origin_path(str(body.get("return_path") or "/"))
    customer_id = None
    try:
        from plugins.takyon.control_api import (
            create_topup_checkout_session,
            ensure_operator_billing_customer,
        )
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            url = _request_runtime_database_url(request)
            if not url:
                raise RuntimeNotConfigured("database_unconfigured")
            import psycopg

            conn = psycopg.connect(url, autocommit=True)
            try:
                customer = ensure_operator_billing_customer(conn, str(principal.user_id))
                customer_id = str(customer.get("id") or "").strip() or None
            finally:
                conn.close()
        except RuntimeNotConfigured:
            customer_id = None
        except Exception:
            customer_id = None

        session = create_topup_checkout_session(
            str(principal.user_id),
            amount_cents=amount_cents,
            success_url=_dashboard_absolute_url(request, return_path),
            cancel_url=_dashboard_absolute_url(request, return_path),
            customer_id=customer_id,
        )
    except Exception as exc:  # noqa: BLE001 - surface an honest UI error
        message = str(exc)
        if "STRIPE_SECRET_KEY" in message:
            raise HTTPException(status_code=503, detail="topup_unconfigured") from exc
        raise HTTPException(status_code=502, detail=message) from exc
    return {
        "checkout_url": session.get("url"),
        "session_id": session.get("id"),
        "amount_cents": amount_cents,
    }


@app.post("/api/takyon/operator/billing/portal")
async def create_takyon_operator_billing_portal(request: Request) -> dict[str, Any]:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    return_path = _same_origin_path(str(body.get("return_path") or "/"))
    try:
        from plugins.takyon.control_api import create_operator_billing_portal_session
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            url = _request_runtime_database_url(request)
            if not url:
                raise RuntimeNotConfigured("database_unconfigured")
        except RuntimeNotConfigured as exc:
            raise HTTPException(status_code=503, detail="database_unconfigured") from exc
        import psycopg

        conn = psycopg.connect(url, autocommit=True)
        try:
            session = create_operator_billing_portal_session(
                conn,
                str(principal.user_id),
                return_url=_dashboard_absolute_url(request, return_path),
            )
        finally:
            conn.close()
    except HTTPException:
        raise
    except ValueError as exc:
        if "operator_email_unavailable" in str(exc):
            raise HTTPException(status_code=409, detail="operator_email_unavailable") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface an honest UI error
        message = str(exc)
        if "STRIPE_SECRET_KEY" in message:
            raise HTTPException(status_code=503, detail="billing_portal_unconfigured") from exc
        raise HTTPException(status_code=502, detail=message) from exc
    return {
        "portal_url": session.get("url"),
        "customer_id": session.get("customer"),
    }


@app.post("/api/takyon/operator/payouts/connect")
async def create_takyon_operator_payout_connect(request: Request) -> dict[str, Any]:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    return_path = _same_origin_path(str(body.get("return_path") or "/"))
    refresh_qs = urllib.parse.urlencode({"return_to": return_path})
    refresh_path = f"/api/takyon/operator/payouts/connect/refresh?{refresh_qs}"
    try:
        from plugins.takyon.control_api import create_operator_payout_connect_link
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            url = _request_runtime_database_url(request)
            if not url:
                raise RuntimeNotConfigured("database_unconfigured")
        except RuntimeNotConfigured as exc:
            raise HTTPException(status_code=503, detail="database_unconfigured") from exc
        import psycopg
        conn = psycopg.connect(url, autocommit=True)
        try:
            link = create_operator_payout_connect_link(
                conn,
                str(principal.user_id),
                return_url=_dashboard_absolute_url(request, return_path),
                refresh_url=_dashboard_absolute_url(request, refresh_path),
            )
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface an honest UI error
        message = str(exc)
        if "STRIPE_SECRET_KEY" in message:
            raise HTTPException(
                status_code=503, detail="payout_connect_unconfigured"
            ) from exc
        raise HTTPException(status_code=502, detail=message) from exc
    return {
        "connect_url": link.get("url"),
        "link_type": link.get("link_type"),
        "stripe_connect_account_id": link.get("stripe_connect_account_id"),
        "stripe_connect_status": link.get("stripe_connect_status"),
    }


@app.get("/api/takyon/operator/payouts/connect/refresh")
async def refresh_takyon_operator_payout_connect(
    request: Request,
    return_to: str = "/",
) -> Response:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    safe_return = _same_origin_path(return_to or "/")
    refresh_qs = urllib.parse.urlencode({"return_to": safe_return})
    refresh_path = f"/api/takyon/operator/payouts/connect/refresh?{refresh_qs}"
    try:
        from plugins.takyon.control_api import create_operator_payout_connect_link
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            url = _request_runtime_database_url(request)
            if not url:
                raise RuntimeNotConfigured("database_unconfigured")
        except RuntimeNotConfigured as exc:
            raise HTTPException(status_code=503, detail="database_unconfigured") from exc
        import psycopg
        conn = psycopg.connect(url, autocommit=True)
        try:
            link = create_operator_payout_connect_link(
                conn,
                str(principal.user_id),
                return_url=_dashboard_absolute_url(request, safe_return),
                refresh_url=_dashboard_absolute_url(request, refresh_path),
            )
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface an honest UI error
        message = str(exc)
        if "STRIPE_SECRET_KEY" in message:
            raise HTTPException(
                status_code=503, detail="payout_connect_unconfigured"
            ) from exc
        raise HTTPException(status_code=502, detail=message) from exc
    target = str(link.get("url") or "").strip()
    if not target:
        raise HTTPException(status_code=502, detail="missing_connect_url")
    return RedirectResponse(target, status_code=302)


@app.get("/api/takyon/operator/businesses")
async def get_takyon_operator_businesses(request: Request) -> dict[str, Any]:
    """Read-only operator business list for the dashboard sidebar.

    This stays deliberately separate from session/workspace scope hydration so
    a failed business open cannot erase the global portfolio list.
    """
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        return {
            "available": False,
            "businesses": [],
            "reason": "operator_principal_unavailable",
        }
    return _takyon_operator_businesses_payload(principal)


@app.get("/api/takyon/businesses/{slug}/file")
async def get_takyon_business_file(request: Request, slug: str, path: str = "") -> dict[str, Any]:
    """Direct authenticated business file read for the dashboard viewer.

    Uses the operator principal + canonical Takyon store directly instead of
    routing through session-scoped chat RPC state.
    """
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    rel_path = str(path or "").strip()
    if not business:
        raise HTTPException(status_code=400, detail="business slug required")
    if not rel_path:
        raise HTTPException(status_code=400, detail="path required")
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        from plugins.takyon.core import TakyonStore

        store = TakyonStore(operator_user_id=str(principal.user_id))
        file_path = store._resolve_business_file(business, rel_path, sync=False)
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {rel_path}")
        size = file_path.stat().st_size
        with file_path.open("rb") as fh:
            raw = fh.read(min(size, _TAKYON_DIRECT_FILE_READ_BYTES))
        rel = str(file_path.relative_to(store._business_root(business, sync=False)))
        return {
            "business_slug": business,
            "path": rel,
            "size": size,
            "content": raw.decode("utf-8", errors="replace"),
            "truncated": size > _TAKYON_DIRECT_FILE_READ_BYTES,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - viewer should fail honestly
        _log.warning("dashboard business file read failed for %s:%s: %s", business, rel_path, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/takyon/businesses/{slug}/asset")
async def get_takyon_business_asset(request: Request, slug: str, path: str = ""):
    """Direct authenticated media download for manual launch handoff."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    rel_path = str(path or "").strip()
    if not business:
        raise HTTPException(status_code=400, detail="business slug required")
    if not rel_path:
        raise HTTPException(status_code=400, detail="path required")
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        from plugins.takyon.core import TakyonStore

        store = TakyonStore(operator_user_id=str(principal.user_id))
        file_path = store._resolve_business_file(business, rel_path, sync=False)
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"asset not found: {rel_path}")
        suffix = file_path.suffix.lower()
        if suffix not in _TAKYON_DIRECT_MEDIA_SUFFIXES:
            raise HTTPException(status_code=400, detail="asset endpoint only serves image/video files")
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
            ".m4v": "video/mp4",
        }
        return FileResponse(
            file_path,
            media_type=media_types.get(suffix, "application/octet-stream"),
            filename=file_path.name,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - handoff should fail honestly
        _log.warning("dashboard business asset download failed for %s:%s: %s", business, rel_path, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/takyon/businesses/{slug}/site-preview")
async def get_takyon_business_site_preview(
    request: Request,
    slug: str,
    path: str = "",
) -> dict[str, Any]:
    """Direct authenticated local site preview for a business workspace.

    Mirrors the gateway's HTML inlining behavior without depending on the
    session-scoped chat RPC lane.
    """
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    requested_path = str(path or "").strip() or "product/site"
    if not business:
        raise HTTPException(status_code=400, detail="business slug required")
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        return await asyncio.to_thread(
            _read_takyon_business_site_preview,
            str(principal.user_id),
            business,
            requested_path,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - preview should fail honestly
        _log.warning("dashboard business site preview failed for %s:%s: %s", business, requested_path, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/takyon/site-preview/{slug}")
async def get_takyon_site_preview_document(
    request: Request,
    slug: str,
    path: str = "",
) -> HTMLResponse:
    _require_token(request)
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    requested_path = str(path or "").strip() or "product/site"
    if not business:
        raise HTTPException(status_code=400, detail="business slug required")
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")

    preview = await asyncio.to_thread(
        _read_takyon_business_site_preview,
        str(principal.user_id),
        business,
        requested_path,
    )
    mode = str(preview.get("mode") or "").strip().lower()
    if mode == "inline_html":
        payload = str(preview.get("url") or "")
        prefix = "data:text/html;charset=utf-8;base64,"
        if not payload.startswith(prefix):
            raise HTTPException(status_code=500, detail="preview payload malformed")
        try:
            html_text = base64.b64decode(payload[len(prefix):]).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - preview should fail honestly
            raise HTTPException(status_code=500, detail=f"preview payload decode failed: {exc}") from exc
    elif mode == "live_url":
        html_text = _takyon_live_site_preview_wrapper_html(str(preview.get("url") or ""))
    else:
        raise HTTPException(status_code=404, detail="site preview not available")
    return HTMLResponse(
        html_text,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/api/takyon/businesses/{slug}/workspace")
async def get_takyon_business_workspace(
    request: Request,
    slug: str,
    limit: int = 50,
    view: str = "full",
) -> dict[str, Any]:
    """Direct authenticated workspace snapshot for a single business.

    The dashboard can render a business workspace from backend truth without
    waiting for the chat-session scope lane to finish hydrating.
    """
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    if not business:
        raise HTTPException(status_code=400, detail="business slug required")
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        return await asyncio.to_thread(
            _read_takyon_business_workspace,
            str(principal.user_id),
            business,
            limit=limit,
            view=view,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - workspace should fail honestly
        _log.warning("dashboard business workspace read failed for %s: %s", business, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/takyon/businesses/{slug}/traction")
async def get_takyon_business_traction(
    request: Request,
    slug: str,
    range: str = "M",
) -> dict[str, Any]:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    if not business:
        raise HTTPException(status_code=400, detail="business slug required")
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        return await asyncio.to_thread(
            _read_takyon_business_traction,
            str(principal.user_id),
            business,
            range_key=range,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - traction should fail honestly
        _log.warning("dashboard business traction read failed for %s: %s", business, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/takyon/businesses/{slug}/home")
async def get_takyon_business_home(
    request: Request,
    slug: str,
) -> dict[str, Any]:
    """Cheap first-paint business shell payload for the dashboard UI."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    if not business:
        raise HTTPException(status_code=400, detail="business slug required")
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        return await asyncio.to_thread(
            _read_takyon_business_home,
            str(principal.user_id),
            business,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - shell should fail honestly
        _log.warning("dashboard business home read failed for %s: %s", business, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/takyon/businesses/{slug}/creative-credits")
async def get_takyon_business_creative_credits(request: Request, slug: str) -> dict[str, Any]:
    """Read-only business creative-credit snapshot for the dashboard UI."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        return {
            "available": False,
            "business_slug": slug,
            "reason": "operator_principal_unavailable",
        }
    if slug not in principal.business_slugs:
        return {
            "available": False,
            "business_slug": slug,
            "reason": "not_found",
        }
    try:
        from plugins.takyon import business_credits
        from plugins.takyon.core import (
            TakyonStore,
            _creative_credit_budget_snapshot_from_conn,
            _db_backend,
        )
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        if _db_backend() != "postgres":
            return {
                "available": False,
                "business_slug": slug,
                "reason": "postgres_required",
            }

        try:
            url = _request_runtime_database_url(request)
            if not url:
                raise RuntimeNotConfigured("database_unconfigured")
        except RuntimeNotConfigured:
            return {
                "available": False,
                "business_slug": slug,
                "reason": "database_unconfigured",
            }

        store = TakyonStore(database_url=url, operator_user_id=str(principal.user_id))
        conn = store._connect()
        try:
            business_credits.open_business_credit_account(conn, slug)
            balances = business_credits.get_business_credit_balances(conn, slug)
            snapshot = _creative_credit_budget_snapshot_from_conn(
                store,
                conn,
                slug,
                balances=balances,
            )
        finally:
            conn.close()

        return {
            "available": True,
            "business_slug": slug,
            "balance_credits": int(balances.balance_credits),
            "reserved_credits": int(balances.reserved_credits),
            "channels": snapshot.get("channels", {}),
            "channel_budgets": snapshot.get("channels", {}),
            "total_allocated_credits": int(snapshot.get("total_allocated_credits") or 0),
            "total_used_credits": int(snapshot.get("total_used_credits") or 0),
            "budget_capacity_credits": int(snapshot.get("budget_capacity_credits") or 0),
            "unallocated_credits": int(snapshot.get("unallocated_credits") or 0),
            "unbucketed_used_credits": int(snapshot.get("unbucketed_used_credits") or 0),
        }
    except Exception as exc:  # noqa: BLE001 - UI should degrade honestly, not crash
        _log.warning("dashboard business creative credits read failed for %s: %s", slug, exc)
        return {
            "available": False,
            "business_slug": slug,
            "reason": "read_failed",
        }


@app.post("/api/takyon/businesses/{slug}/creative-credits/budgets")
async def set_takyon_business_creative_credit_budgets(request: Request, slug: str) -> dict[str, Any]:
    """Persist the business's channel credit allocations for X / Meta / Reddit."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    if slug not in principal.business_slugs:
        raise HTTPException(status_code=404, detail="not_found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_allocations = body.get("allocations")
    if not isinstance(raw_allocations, dict):
        raw_allocations = body.get("channels")
    if not isinstance(raw_allocations, dict):
        raise HTTPException(status_code=400, detail="allocations is required")
    try:
        from uuid import uuid4

        from plugins.takyon import business_credits
        from plugins.takyon.core import (
            TakyonStore,
            _creative_credit_budget_payload,
            _creative_credit_budget_relpath,
            _creative_credit_budget_snapshot_from_conn,
            _db_backend,
            _validate_creative_credit_channel_allocations,
        )
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        if _db_backend() != "postgres":
            raise HTTPException(status_code=503, detail="postgres_required")

        try:
            url = _request_runtime_database_url(request)
            if not url:
                raise RuntimeNotConfigured("database_unconfigured")
        except RuntimeNotConfigured as exc:
            raise HTTPException(status_code=503, detail="database_unconfigured") from exc

        store = TakyonStore(database_url=url, operator_user_id=str(principal.user_id))
        with store._connect() as conn:
            business_credits.open_business_credit_account(conn, slug)
            balances = business_credits.get_business_credit_balances(conn, slug)
            snapshot = _creative_credit_budget_snapshot_from_conn(
                store,
                conn,
                slug,
                balances=balances,
            )
        allocations = _validate_creative_credit_channel_allocations(
            raw_allocations,
            snapshot=snapshot,
        )
        store.commit(
            scope=f"business:{slug}/metrics:channel-credit-budgets",
            operations=[
                {
                    "action": "artifact.write",
                    "business": slug,
                    "path": _creative_credit_budget_relpath(),
                    "content": _creative_credit_budget_payload(allocations),
                }
            ],
            idempotency_key=str(body.get("idempotency_key") or f"dashboard-channel-credit-budgets:{slug}:{uuid4().hex}"),
            reason=str(body.get("reason") or "dashboard set channel creative credit budgets"),
            actor="dashboard",
        )
        return await get_takyon_business_creative_credits(request, slug)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface honest dashboard error
        _log.warning("dashboard business creative credit budgets update failed for %s: %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/takyon/operator/meta-campaigns")
async def get_takyon_operator_meta_campaigns(request: Request) -> dict[str, Any]:
    """Read-only Meta campaign handoff queue for the SAI operator surface."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        return {
            "available": False,
            "campaigns": [],
            "reason": "operator_principal_unavailable",
        }
    try:
        from plugins.takyon.core import TakyonStore

        store = TakyonStore(operator_user_id=str(principal.user_id))
        campaigns = _takyon_collect_operator_meta_campaigns(
            store,
            sorted({str(slug or "").strip() for slug in (principal.business_slugs or []) if str(slug or "").strip()}),
        )
        return {
            "available": True,
            "campaigns": campaigns,
            "owned_business_count": len(principal.business_slugs),
        }
    except Exception as exc:  # noqa: BLE001 - queue should degrade honestly
        _log.warning("dashboard operator meta campaign queue failed: %s", exc)
        return {
            "available": False,
            "campaigns": [],
            "reason": "read_failed",
        }


@app.get("/api/takyon/businesses/{slug}/creative-credits/packs")
async def get_takyon_business_creative_credit_packs(
    request: Request, slug: str
) -> dict[str, Any]:
    """Dashboard wrapper exposing the existing creative-credit pack catalog."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    if slug not in principal.business_slugs:
        raise HTTPException(status_code=404, detail="not_found")
    try:
        from plugins.takyon.control_api import configured_creative_credit_packs
        from plugins.takyon.core import _db_backend

        if _db_backend() != "postgres":
            raise HTTPException(status_code=503, detail="postgres_required")
        return {
            "business_slug": slug,
            "packs": configured_creative_credit_packs(),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface honest dashboard error
        _log.warning("dashboard business creative credit packs failed for %s: %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/takyon/businesses/{slug}/creative-credits/checkout")
async def create_takyon_business_creative_credit_checkout(
    request: Request, slug: str
) -> dict[str, Any]:
    """Dashboard wrapper for the existing creative-credit Stripe checkout rail."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    if slug not in principal.business_slugs:
        raise HTTPException(status_code=404, detail="not_found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        credits = int(body.get("credits") or 0)
    except (TypeError, ValueError):
        credits = 0
    pack_id = str(body.get("pack_id") or "").strip() or None
    if credits <= 0 and not pack_id:
        raise HTTPException(status_code=400, detail="credits must be > 0")
    success_path = _same_origin_path(str(body.get("success_path") or "/"))
    cancel_path = _same_origin_path(str(body.get("cancel_path") or success_path))
    try:
        from plugins.takyon import stripe_util
        from plugins.takyon.control_api import create_creative_credit_checkout_session
        from plugins.takyon.core import _db_backend

        if _db_backend() != "postgres":
            raise HTTPException(status_code=503, detail="postgres_required")

        session, charge = create_creative_credit_checkout_session(
            str(principal.user_id),
            slug,
            credits=credits if credits > 0 else None,
            pack_id=pack_id,
            success_url=_dashboard_absolute_url(request, success_path),
            cancel_url=_dashboard_absolute_url(request, cancel_path),
        )
    except HTTPException:
        raise
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="unknown_credit_pack") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except stripe_util.StripeError as exc:
        message = str(exc)
        if "STRIPE_SECRET_KEY" in message:
            raise HTTPException(
                status_code=503, detail="creative_credit_checkout_unconfigured"
            ) from exc
        raise HTTPException(status_code=502, detail=f"stripe_error: {message}") from exc
    except Exception as exc:  # noqa: BLE001 - surface honest dashboard error
        _log.warning("dashboard business creative credit checkout failed for %s: %s", slug, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "checkout_url": session.get("url"),
        "session_id": session.get("id"),
        "business_slug": slug,
        "pack_id": charge.get("pack_id"),
        "credits": charge["credits"],
        "amount_cents": charge["amount_cents"],
        "price_cents_per_credit": charge.get("price_cents_per_credit"),
    }


@app.post("/api/takyon/businesses/{slug}/outreach/start")
async def start_takyon_business_outreach_channel(
    request: Request,
    slug: str,
) -> JSONResponse:
    """Record a durable start request for one outreach lane from the dashboard."""
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    channel = str(body.get("channel") or "").strip().lower()
    channel_specs = {
        "x": {
            "label": "X",
            "kind": "x-social.campaign_start",
            "requested_skill": "takyon-x",
        },
        "reddit": {
            "label": "Reddit",
            "kind": "reddit.campaign_start",
            "requested_skill": "takyon-reddit-ads",
        },
        "meta": {
            "label": "Meta",
            "kind": "meta.campaign_start",
            "requested_skill": "takyon-meta-ads",
        },
    }
    spec = channel_specs.get(channel)
    if spec is None:
        raise HTTPException(status_code=400, detail="channel must be one of: x, reddit, meta")
    campaigns: list[dict[str, Any]] = []
    if channel in {"reddit", "meta"}:
        try:
            from plugins.takyon.core import TakyonStore

            store = TakyonStore(operator_user_id=str(principal.user_id))
            publication_root = "distribution/reddit-ads" if channel == "reddit" else "distribution/meta-ads"
            metrics_root = "metrics/reddit-ads" if channel == "reddit" else "metrics/meta-ads"
            secondary_key = "ad_group" if channel == "reddit" else "adset"
            campaigns = _takyon_collect_business_paid_campaigns(
                store,
                business,
                publication_root,
                metrics_root,
                plan_secondary_key=secondary_key,
            )
        except Exception:
            campaigns = []
    start_spec = _takyon_channel_start_request_spec(channel, campaigns)
    status, payload = _takyon_app_tool(
        handle_business_enqueue_job(
            {
                "business": business,
                "scope": f"business:{business}/distribution:campaign",
                "kind": spec["kind"],
                "status": "queued",
                "payload": {
                    "channel": channel,
                    "channel_label": spec["label"],
                    "requested_skill": spec["requested_skill"],
                    "requested_action": start_spec["requested_action"],
                    "workspace": "distribution/campaign/",
                    "ui_origin": "litebulb.outreach_panel",
                    "summary": start_spec["summary"],
                },
                "idempotency_key": str(body.get("idempotency_key") or f"dashboard-outreach-start-{uuid.uuid4().hex}"),
                "reason": f"start {spec['label']} outreach lane from dashboard panel",
                "actor": "dashboard",
            }
        )
    )
    return _takyon_app_json(status, payload)


@app.post("/api/takyon/businesses/{slug}/meta-campaigns/{campaign_slug}/bind-manual-launch")
async def bind_takyon_business_meta_manual_launch(
    request: Request,
    slug: str,
    campaign_slug: str,
) -> JSONResponse:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status, payload = _takyon_app_tool(
        handle_business_meta_ad_bind_manual_launch(
            {
                **body,
                "business": business,
                "slug": str(campaign_slug or "").strip(),
                "idempotency_key": str(body.get("idempotency_key") or f"dashboard-manual-bind-{uuid.uuid4().hex}"),
                "actor": "dashboard",
                "reason": "bind manual meta launch from campaign ops",
            }
        )
    )
    return _takyon_app_json(status, payload)


@app.post("/api/takyon/businesses/{slug}/meta-campaigns/{campaign_slug}/manual-metrics")
async def sync_takyon_business_meta_manual_metrics(
    request: Request,
    slug: str,
    campaign_slug: str,
) -> JSONResponse:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status, payload = _takyon_app_tool(
        handle_business_meta_ad_insights_sync(
            {
                **body,
                "business": business,
                "slug": str(campaign_slug or "").strip(),
                "source": "manual",
                "level": str(body.get("level") or "campaign"),
                "idempotency_key": str(body.get("idempotency_key") or f"dashboard-manual-metrics-{uuid.uuid4().hex}"),
                "actor": "dashboard",
                "reason": "record manual meta metrics from campaign ops",
            }
        )
    )
    return _takyon_app_json(status, payload)


@app.post("/api/takyon/businesses/{slug}/meta-campaigns/{campaign_slug}/sync")
async def sync_takyon_business_meta_metrics(
    request: Request,
    slug: str,
    campaign_slug: str,
) -> JSONResponse:
    principal = _resolve_dashboard_request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="operator_principal_unavailable")
    business = str(slug or "").strip()
    if business not in set(getattr(principal, "business_slugs", ()) or ()):
        raise HTTPException(status_code=404, detail="business not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status, payload = _takyon_app_tool(
        handle_business_meta_ad_insights_sync(
            {
                **body,
                "business": business,
                "slug": str(campaign_slug or "").strip(),
                "source": "meta_api",
                "level": str(body.get("level") or "campaign"),
                "date_preset": str(body.get("date_preset") or "today"),
                "idempotency_key": str(body.get("idempotency_key") or f"dashboard-meta-sync-{uuid.uuid4().hex}"),
                "actor": "dashboard",
                "reason": "sync meta metrics from campaign ops",
            }
        )
    )
    return _takyon_app_json(status, payload)


# ---------------------------------------------------------------------------
# Gateway + update actions (invoked from the Status page).
#
# Both commands are spawned as detached subprocesses so the HTTP request
# returns immediately.  stdin is closed (``DEVNULL``) so any stray ``input()``
# calls fail fast with EOF rather than hanging forever.  stdout/stderr are
# streamed to a per-action log file under ``~/.takyon/logs/<action>.log`` so
# the dashboard can tail them back to the user.
# ---------------------------------------------------------------------------

_ACTION_LOG_DIR: Path = get_takyon_home() / "logs"

# Short ``name`` (from the URL) → absolute log file path.
_ACTION_LOG_FILES: Dict[str, str] = {
    "gateway-restart": "gateway-restart.log",
    "takyon-update": "takyon-update.log",
}

# ``name`` → most recently spawned Popen handle.  Used so ``status`` can
# report liveness and exit code without shelling out to ``ps``.
_ACTION_PROCS: Dict[str, subprocess.Popen] = {}


def _spawn_takyon_action(subcommand: List[str], name: str) -> subprocess.Popen:
    """Spawn ``takyon <subcommand>`` detached and record the Popen handle.

    Uses the running interpreter's ``takyon_cli.main`` module so the action
    inherits the same venv/PYTHONPATH the web server is using.
    """
    log_file_name = _ACTION_LOG_FILES[name]
    _ACTION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _ACTION_LOG_DIR / log_file_name
    log_file = open(log_path, "ab", buffering=0)
    log_file.write(
        f"\n=== {name} started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode()
    )

    cmd = [sys.executable, "-m", "takyon_cli.main", *subcommand]

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "env": {**os.environ, "TAKYON_NONINTERACTIVE": "1"},
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    _ACTION_PROCS[name] = proc
    return proc


def _tail_lines(path: Path, n: int) -> List[str]:
    """Return the last ``n`` lines of ``path``.  Reads the whole file — fine
    for our small per-action logs.  Binary-decoded with ``errors='replace'``
    so log corruption doesn't 500 the endpoint."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-n:] if n > 0 else lines


@app.post("/api/gateway/restart")
async def restart_gateway():
    """Kick off a ``takyon gateway restart`` in the background."""
    try:
        proc = _spawn_takyon_action(["gateway", "restart"], "gateway-restart")
    except Exception as exc:
        _log.exception("Failed to spawn gateway restart")
        raise HTTPException(status_code=500, detail=f"Failed to restart gateway: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "gateway-restart",
    }


@app.post("/api/takyon/update")
async def update_takyon():
    """Kick off ``takyon update`` in the background."""
    try:
        proc = _spawn_takyon_action(["update"], "takyon-update")
    except Exception as exc:
        _log.exception("Failed to spawn takyon update")
        raise HTTPException(status_code=500, detail=f"Failed to start update: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "takyon-update",
    }


@app.get("/api/actions/{name}/status")
async def get_action_status(name: str, lines: int = 200):
    """Tail an action log and report whether the process is still running."""
    log_file_name = _ACTION_LOG_FILES.get(name)
    if log_file_name is None:
        raise HTTPException(status_code=404, detail=f"Unknown action: {name}")

    log_path = _ACTION_LOG_DIR / log_file_name
    tail = _tail_lines(log_path, min(max(lines, 1), 2000))

    proc = _ACTION_PROCS.get(name)
    if proc is None:
        running = False
        exit_code: Optional[int] = None
        pid: Optional[int] = None
    else:
        exit_code = proc.poll()
        running = exit_code is None
        pid = proc.pid

    return {
        "name": name,
        "running": running,
        "exit_code": exit_code,
        "pid": pid,
        "lines": tail,
    }


@app.get("/api/sessions")
async def get_sessions(limit: int = 20, offset: int = 0):
    try:
        from takyon_state import SessionDB
        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(limit=limit, offset=offset)
            total = db.session_count()
            now = time.time()
            for s in sessions:
                s["is_active"] = (
                    s.get("ended_at") is None
                    and (now - s.get("last_active", s.get("started_at", 0))) < 300
                )
            return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/sessions/search")
async def search_sessions(q: str = "", limit: int = 20):
    """Full-text search across session message content using FTS5."""
    if not q or not q.strip():
        return {"results": []}
    try:
        from takyon_state import SessionDB
        db = SessionDB()
        try:
            # Auto-add prefix wildcards so partial words match
            # e.g. "nimb" → "nimb*" matches "nimby"
            # Preserve quoted phrases and existing wildcards as-is
            import re
            terms = []
            for token in re.findall(r'"[^"]*"|\S+', q.strip()):
                if token.startswith('"') or token.endswith("*"):
                    terms.append(token)
                else:
                    terms.append(token + "*")
            prefix_query = " ".join(terms)
            matches = db.search_messages(query=prefix_query, limit=limit)
            # Group by session_id — return unique sessions with their best snippet
            seen: dict = {}
            for m in matches:
                sid = m["session_id"]
                if sid not in seen:
                    seen[sid] = {
                        "session_id": sid,
                        "snippet": m.get("snippet", ""),
                        "role": m.get("role"),
                        "source": m.get("source"),
                        "model": m.get("model"),
                        "session_started": m.get("session_started"),
                    }
            return {"results": list(seen.values())}
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions/search failed")
        raise HTTPException(status_code=500, detail="Search failed")


def _normalize_config_for_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize config for the web UI.

    Takyon supports ``model`` as either a bare string (``"anthropic/claude-sonnet-4"``)
    or a dict (``{default: ..., provider: ..., base_url: ...}``).  The schema is built
    from DEFAULT_CONFIG where ``model`` is a string, but user configs often have the
    dict form.  Normalize to the string form so the frontend schema matches.

    Also surfaces ``model_context_length`` as a top-level field so the web UI can
    display and edit it.  A value of 0 means "auto-detect".
    """
    config = dict(config)  # shallow copy
    model_val = config.get("model")
    if isinstance(model_val, dict):
        # Extract context_length before flattening the dict
        ctx_len = model_val.get("context_length", 0)
        config["model"] = model_val.get("default", model_val.get("name", ""))
        config["model_context_length"] = ctx_len if isinstance(ctx_len, int) else 0
    else:
        config["model_context_length"] = 0
    return config


@app.get("/api/config")
async def get_config():
    config = _normalize_config_for_web(load_config())
    # Strip internal keys that the frontend shouldn't see or send back
    return {k: v for k, v in config.items() if not k.startswith("_")}


@app.get("/api/config/defaults")
async def get_defaults():
    return DEFAULT_CONFIG


@app.get("/api/config/schema")
async def get_schema():
    return {"fields": CONFIG_SCHEMA, "category_order": _CATEGORY_ORDER}


_EMPTY_MODEL_INFO: dict = {
    "model": "",
    "provider": "",
    "auto_context_length": 0,
    "config_context_length": 0,
    "effective_context_length": 0,
    "capabilities": {},
}


@app.get("/api/model/info")
def get_model_info():
    """Return resolved model metadata for the currently configured model.

    Calls the same context-length resolution chain the agent uses, so the
    frontend can display "Auto-detected: 200K" alongside the override field.
    Also returns model capabilities (vision, reasoning, tools) when available.
    """
    try:
        cfg = load_config()
        model_cfg = cfg.get("model", "")

        # Extract model name and provider from the config
        if isinstance(model_cfg, dict):
            model_name = model_cfg.get("default", model_cfg.get("name", ""))
            provider = model_cfg.get("provider", "")
            base_url = model_cfg.get("base_url", "")
            config_ctx = model_cfg.get("context_length")
        else:
            model_name = str(model_cfg) if model_cfg else ""
            provider = ""
            base_url = ""
            config_ctx = None

        if not model_name:
            return dict(_EMPTY_MODEL_INFO, provider=provider)

        # Resolve auto-detected context length (pass config_ctx=None to get
        # purely auto-detected value, then separately report the override)
        try:
            from agent.model_metadata import get_model_context_length
            auto_ctx = get_model_context_length(
                model=model_name,
                base_url=base_url,
                provider=provider,
                config_context_length=None,  # ignore override — we want auto value
            )
        except Exception:
            auto_ctx = 0

        config_ctx_int = 0
        if isinstance(config_ctx, int) and config_ctx > 0:
            config_ctx_int = config_ctx

        # Effective is what the agent actually uses
        effective_ctx = config_ctx_int if config_ctx_int > 0 else auto_ctx

        # Try to get model capabilities from models.dev
        caps = {}
        try:
            from agent.models_dev import get_model_capabilities
            mc = get_model_capabilities(provider=provider, model=model_name)
            if mc is not None:
                caps = {
                    "supports_tools": mc.supports_tools,
                    "supports_vision": mc.supports_vision,
                    "supports_reasoning": mc.supports_reasoning,
                    "context_window": mc.context_window,
                    "max_output_tokens": mc.max_output_tokens,
                    "model_family": mc.model_family,
                }
        except Exception:
            pass

        return {
            "model": model_name,
            "provider": provider,
            "auto_context_length": auto_ctx,
            "config_context_length": config_ctx_int,
            "effective_context_length": effective_ctx,
            "capabilities": caps,
        }
    except Exception:
        _log.exception("GET /api/model/info failed")
        return dict(_EMPTY_MODEL_INFO)


# ---------------------------------------------------------------------------
# Model assignment — pick provider+model for main slot or auxiliary slots.
# Mirrors the model.options JSON-RPC from tui_gateway but uses REST so the
# Models page (which has no chat PTY open) can drive it.
# ---------------------------------------------------------------------------

# Canonical auxiliary task slots. Keep in sync with DEFAULT_CONFIG["auxiliary"]
# in takyon_cli/config.py — listed here for deterministic ordering in the UI.
_AUX_TASK_SLOTS: Tuple[str, ...] = (
    "vision",
    "web_extract",
    "compression",
    "session_search",
    "skills_hub",
    "approval",
    "mcp",
    "title_generation",
    "curator",
)


@app.get("/api/model/options")
def get_model_options():
    """Return authenticated providers + their curated model lists.

    REST equivalent of the ``model.options`` JSON-RPC on tui_gateway, so the
    dashboard Models page can render the picker without a live chat session.
    The response shape matches ``model.options`` 1:1 so ``ModelPickerDialog``
    can share the same types.
    """
    try:
        from takyon_cli.inventory import build_models_payload, load_picker_context

        return build_models_payload(load_picker_context(), max_models=50)
    except Exception:
        _log.exception("GET /api/model/options failed")
        raise HTTPException(status_code=500, detail="Failed to list model options")


@app.get("/api/model/auxiliary")
def get_auxiliary_models():
    """Return current auxiliary task assignments.

    Shape:
      {
        "tasks": [
          {"task": "vision", "provider": "auto", "model": "", "base_url": ""},
          ...
        ],
        "main": {"provider": "openrouter", "model": "anthropic/claude-opus-4.7"},
      }
    """
    try:
        cfg = load_config()
        aux_cfg = cfg.get("auxiliary", {})
        if not isinstance(aux_cfg, dict):
            aux_cfg = {}

        tasks = []
        for slot in _AUX_TASK_SLOTS:
            slot_cfg = aux_cfg.get(slot, {}) if isinstance(aux_cfg.get(slot), dict) else {}
            tasks.append({
                "task": slot,
                "provider": str(slot_cfg.get("provider", "auto") or "auto"),
                "model": str(slot_cfg.get("model", "") or ""),
                "base_url": str(slot_cfg.get("base_url", "") or ""),
            })

        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            main = {
                "provider": str(model_cfg.get("provider", "") or ""),
                "model": str(model_cfg.get("default", model_cfg.get("name", "")) or ""),
            }
        else:
            main = {"provider": "", "model": str(model_cfg) if model_cfg else ""}

        return {"tasks": tasks, "main": main}
    except Exception:
        _log.exception("GET /api/model/auxiliary failed")
        raise HTTPException(status_code=500, detail="Failed to read auxiliary config")


@app.post("/api/model/set")
async def set_model_assignment(body: ModelAssignment):
    """Assign a model to the main slot or an auxiliary task slot.

    Writes to ``~/.takyon/config.yaml`` — applies to **new** sessions only.
    The currently running chat PTY (if any) is not affected; use the
    ``/model`` slash command inside a chat to hot-swap that specific session.
    """
    scope = (body.scope or "").strip().lower()
    provider = (body.provider or "").strip()
    model = (body.model or "").strip()
    task = (body.task or "").strip().lower()

    if scope not in {"main", "auxiliary"}:
        raise HTTPException(status_code=400, detail="scope must be 'main' or 'auxiliary'")

    try:
        cfg = load_config()

        if scope == "main":
            if not provider or not model:
                raise HTTPException(status_code=400, detail="provider and model required for main")
            model_cfg = cfg.get("model", {})
            if not isinstance(model_cfg, dict):
                model_cfg = {}
            model_cfg["provider"] = provider
            model_cfg["default"] = model
            # Clear stale base_url so the resolver picks the provider's own default.
            if "base_url" in model_cfg and model_cfg.get("base_url"):
                model_cfg["base_url"] = ""
            # Also clear hardcoded context_length override — new model may have
            # a different context window.
            if "context_length" in model_cfg:
                model_cfg.pop("context_length", None)
            cfg["model"] = model_cfg
            save_config(cfg)
            return {"ok": True, "scope": "main", "provider": provider, "model": model}

        # scope == "auxiliary"
        aux = cfg.get("auxiliary")
        if not isinstance(aux, dict):
            aux = {}

        if task == "__reset__":
            # Reset every slot to provider="auto", model="" — keeps other fields intact.
            for slot in _AUX_TASK_SLOTS:
                slot_cfg = aux.get(slot)
                if not isinstance(slot_cfg, dict):
                    slot_cfg = {}
                slot_cfg["provider"] = "auto"
                slot_cfg["model"] = ""
                aux[slot] = slot_cfg
            cfg["auxiliary"] = aux
            save_config(cfg)
            return {"ok": True, "scope": "auxiliary", "reset": True}

        if not provider:
            raise HTTPException(status_code=400, detail="provider required for auxiliary")

        targets = [task] if task else list(_AUX_TASK_SLOTS)
        for slot in targets:
            if slot not in _AUX_TASK_SLOTS:
                raise HTTPException(status_code=400, detail=f"unknown auxiliary task: {slot}")
            slot_cfg = aux.get(slot)
            if not isinstance(slot_cfg, dict):
                slot_cfg = {}
            slot_cfg["provider"] = provider
            slot_cfg["model"] = model
            aux[slot] = slot_cfg

        cfg["auxiliary"] = aux
        save_config(cfg)
        return {
            "ok": True,
            "scope": "auxiliary",
            "tasks": targets,
            "provider": provider,
            "model": model,
        }
    except HTTPException:
        raise
    except Exception:
        _log.exception("POST /api/model/set failed")
        raise HTTPException(status_code=500, detail="Failed to save model assignment")




def _denormalize_config_from_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse _normalize_config_for_web before saving.

    Reconstructs ``model`` as a dict by reading the current on-disk config
    to recover model subkeys (provider, base_url, api_mode, etc.) that were
    stripped from the GET response.  The frontend only sees model as a flat
    string; the rest is preserved transparently.

    Also handles ``model_context_length`` — writes it back into the model dict
    as ``context_length``.  A value of 0 or absent means "auto-detect" (omitted
    from the dict so get_model_context_length() uses its normal resolution).
    """
    config = dict(config)
    # Remove any _model_meta that might have leaked in (shouldn't happen
    # with the stripped GET response, but be defensive)
    config.pop("_model_meta", None)

    # Extract and remove model_context_length before processing model
    ctx_override = config.pop("model_context_length", 0)
    if not isinstance(ctx_override, int):
        try:
            ctx_override = int(ctx_override)
        except (TypeError, ValueError):
            ctx_override = 0

    model_val = config.get("model")
    if isinstance(model_val, str) and model_val:
        # Read the current disk config to recover model subkeys
        try:
            disk_config = load_config()
            disk_model = disk_config.get("model")
            if isinstance(disk_model, dict):
                # Preserve all subkeys, update default with the new value
                disk_model["default"] = model_val
                # Write context_length into the model dict (0 = remove/auto)
                if ctx_override > 0:
                    disk_model["context_length"] = ctx_override
                else:
                    disk_model.pop("context_length", None)
                config["model"] = disk_model
            # Model was previously a bare string — upgrade to dict if
            # user is setting a context_length override
            elif ctx_override > 0:
                config["model"] = {
                    "default": model_val,
                    "context_length": ctx_override,
                }
        except Exception:
            pass  # can't read disk config — just use the string form
    return config


@app.put("/api/config")
async def update_config(body: ConfigUpdate):
    try:
        save_config(_denormalize_config_from_web(body.config))
        return {"ok": True}
    except Exception:
        _log.exception("PUT /api/config failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/env")
async def get_env_vars():
    env_on_disk = load_env()
    result = {}
    for var_name, info in OPTIONAL_ENV_VARS.items():
        if takyon_safebox.is_sensitive_env_key(var_name):
            value = takyon_safebox.read_env_backed_value(var_name)
        else:
            value = env_on_disk.get(var_name)
        result[var_name] = {
            "is_set": bool(value),
            "redacted_value": redact_key(value) if value else None,
            "description": info.get("description", ""),
            "url": info.get("url"),
            "category": info.get("category", ""),
            "is_password": info.get("password", False),
            "tools": info.get("tools", []),
            "advanced": info.get("advanced", False),
        }
    return result


@app.put("/api/env")
async def set_env_var(body: EnvVarUpdate):
    try:
        save_env_value(body.key, body.value)
        return {"ok": True, "key": body.key}
    except Exception:
        _log.exception("PUT /api/env failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/api/env")
async def remove_env_var(body: EnvVarDelete):
    try:
        removed = remove_env_value(body.key)
        if not removed:
            raise HTTPException(status_code=404, detail=f"{body.key} not found in .env")
        return {"ok": True, "key": body.key}
    except HTTPException:
        raise
    except Exception:
        _log.exception("DELETE /api/env failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/env/reveal")
async def reveal_env_var(body: EnvVarReveal, request: Request):
    """Return the real (unredacted) value of a single env var.

    Protected by:
    - Ephemeral session token (generated per server start, injected into SPA)
    - Rate limiting (max 5 reveals per 30s window)
    - Audit logging
    """
    # --- Token check ---
    _require_token(request)

    # --- Rate limit ---
    now = time.time()
    cutoff = now - _REVEAL_WINDOW_SECONDS
    _reveal_timestamps[:] = [t for t in _reveal_timestamps if t > cutoff]
    if len(_reveal_timestamps) >= _REVEAL_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="Too many reveal requests. Try again shortly.")
    _reveal_timestamps.append(now)

    # --- Reveal ---
    if takyon_safebox.is_sensitive_env_key(body.key):
        value = takyon_safebox.read_env_backed_value(body.key)
    else:
        env_on_disk = load_env()
        value = env_on_disk.get(body.key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"{body.key} not found in .env")

    _log.info("env/reveal: %s", body.key)
    return {"key": body.key, "value": value}


# ---------------------------------------------------------------------------
# OAuth provider endpoints — status + disconnect (Phase 1)
# ---------------------------------------------------------------------------
#
# Phase 1 surfaces *which OAuth providers exist* and whether each is
# connected, plus a disconnect button. The actual login flow (PKCE for
# Anthropic, device-code for Nous/Codex) still runs in the CLI for now;
# Phase 2 will add in-browser flows. For unconnected providers we return
# the canonical ``takyon auth add <provider>`` command so the dashboard
# can surface a one-click copy.


def _truncate_token(value: Optional[str], visible: int = 6) -> str:
    """Return ``...XXXXXX`` (last N chars) for safe display in the UI.

    We never expose more than the trailing ``visible`` characters of an
    OAuth access token. JWT prefixes (the part before the first dot) are
    stripped first when present so the visible suffix is always part of
    the signing region rather than a meaningless header chunk.

    Returns the Entra-ID placeholder when handed a callable (Azure Foundry
    bearer provider) — the callable is NEVER invoked here.
    """
    if not value:
        return ""
    if callable(value) and not isinstance(value, str):
        # Entra ID bearer provider — never reveal a minted token in the UI.
        return "<entra-id-bearer>"
    s = str(value)
    if "." in s and s.count(".") >= 2:
        # Looks like a JWT — show the trailing piece of the signature only.
        s = s.rsplit(".", 1)[-1]
    if len(s) <= visible:
        return s
    return f"…{s[-visible:]}"


def _anthropic_oauth_status() -> Dict[str, Any]:
    """Combined status across the three Anthropic credential sources we read.

    Takyon resolves Anthropic creds in this order at runtime:
    1. ``~/.takyon/.anthropic_oauth.json`` — Takyon-managed PKCE flow
    2. ``~/.claude/.credentials.json`` — Claude Code CLI credentials (auto)
    3. ``ANTHROPIC_TOKEN`` / ``ANTHROPIC_API_KEY`` env vars
    The dashboard reports the highest-priority source that's actually present.
    """
    try:
        from agent.anthropic_adapter import (
            read_takyon_oauth_credentials,
            read_claude_code_credentials,
            _TAKYON_OAUTH_FILE,
        )
    except ImportError:
        read_claude_code_credentials = None  # type: ignore
        read_takyon_oauth_credentials = None  # type: ignore
        _TAKYON_OAUTH_FILE = None  # type: ignore

    takyon_creds = None
    if read_takyon_oauth_credentials:
        try:
            takyon_creds = read_takyon_oauth_credentials()
        except Exception:
            takyon_creds = None
    if takyon_creds and takyon_creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "takyon_pkce",
            "source_label": f"Takyon PKCE ({_TAKYON_OAUTH_FILE})",
            "token_preview": _truncate_token(takyon_creds.get("accessToken")),
            "expires_at": takyon_creds.get("expiresAt"),
            "has_refresh_token": bool(takyon_creds.get("refreshToken")),
        }

    cc_creds = None
    if read_claude_code_credentials:
        try:
            cc_creds = read_claude_code_credentials()
        except Exception:
            cc_creds = None
    if cc_creds and cc_creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "claude_code",
            "source_label": "Claude Code (~/.claude/.credentials.json)",
            "token_preview": _truncate_token(cc_creds.get("accessToken")),
            "expires_at": cc_creds.get("expiresAt"),
            "has_refresh_token": bool(cc_creds.get("refreshToken")),
        }

    env_token = takyon_safebox.first_env_backed_value("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return {
            "logged_in": True,
            "source": "env_var",
            "source_label": "ANTHROPIC_TOKEN environment variable",
            "token_preview": _truncate_token(env_token),
            "expires_at": None,
            "has_refresh_token": False,
        }
    return {"logged_in": False, "source": None}


def _claude_code_only_status() -> Dict[str, Any]:
    """Surface Claude Code CLI credentials as their own provider entry.

    Independent of the Anthropic entry above so users can see whether their
    Claude Code subscription tokens are actively flowing into Takyon even
    when they also have a separate Takyon-managed PKCE login.
    """
    try:
        from agent.anthropic_adapter import read_claude_code_credentials
        creds = read_claude_code_credentials()
    except Exception:
        creds = None
    if creds and creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "claude_code_cli",
            "source_label": "~/.claude/.credentials.json",
            "token_preview": _truncate_token(creds.get("accessToken")),
            "expires_at": creds.get("expiresAt"),
            "has_refresh_token": bool(creds.get("refreshToken")),
        }
    return {"logged_in": False, "source": None}


# Provider catalog. The order matters — it's how we render the UI list.
# ``cli_command`` is what the dashboard surfaces as the copy-to-clipboard
# fallback while Phase 2 (in-browser flows) isn't built yet.
# ``flow`` describes the OAuth shape so the future modal can pick the
# right UI: ``pkce`` = open URL + paste callback code, ``device_code`` =
# show code + verification URL + poll, ``external`` = read-only (delegated
# to a third-party CLI like Claude Code or Qwen).
_OAUTH_PROVIDER_CATALOG: tuple[Dict[str, Any], ...] = (
    {
        "id": "anthropic",
        "name": "Anthropic (Claude API)",
        "flow": "pkce",
        "cli_command": "takyon auth add anthropic",
        "docs_url": "https://docs.claude.com/en/api/getting-started",
        "status_fn": _anthropic_oauth_status,
    },
    {
        "id": "claude-code",
        "name": "Claude Code (subscription)",
        "flow": "external",
        "cli_command": "claude setup-token",
        "docs_url": "https://docs.claude.com/en/docs/claude-code",
        "status_fn": _claude_code_only_status,
    },
    {
        "id": "nous",
        "name": "Nous Portal",
        "flow": "device_code",
        "cli_command": "takyon auth add nous",
        "docs_url": "https://portal.nousresearch.com",
        "status_fn": None,  # dispatched via auth.get_nous_auth_status
    },
    {
        "id": "openai-codex",
        "name": "OpenAI Codex (ChatGPT)",
        "flow": "device_code",
        "cli_command": "takyon auth add openai-codex",
        "docs_url": "https://platform.openai.com/docs",
        "status_fn": None,  # dispatched via auth.get_codex_auth_status
    },
    {
        "id": "qwen-oauth",
        "name": "Qwen (via Qwen CLI)",
        "flow": "external",
        "cli_command": "takyon auth add qwen-oauth",
        "docs_url": "https://github.com/QwenLM/qwen-code",
        "status_fn": None,  # dispatched via auth.get_qwen_auth_status
    },
    {
        "id": "minimax-oauth",
        "name": "MiniMax (OAuth)",
        # MiniMax's flow is structurally device-code (verification URI +
        # user code, backend polls the token endpoint) with a PKCE
        # extension for code-binding. The dashboard renders the same UX
        # as Nous's device-code flow; the PKCE bit is a security
        # extension that doesn't change the operator experience.
        "flow": "device_code",
        "cli_command": "takyon auth add minimax-oauth",
        "docs_url": "https://www.minimax.io",
        "status_fn": None,  # dispatched via auth.get_minimax_oauth_auth_status
    },
)


def _resolve_provider_status(provider_id: str, status_fn) -> Dict[str, Any]:
    """Dispatch to the right status helper for an OAuth provider entry."""
    if status_fn is not None:
        try:
            return status_fn()
        except Exception as e:
            return {"logged_in": False, "error": str(e)}
    try:
        from takyon_cli import auth as hauth
        if provider_id == "nous":
            raw = hauth.get_nous_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "nous_portal",
                "source_label": raw.get("portal_base_url") or "Nous Portal",
                "token_preview": _truncate_token(raw.get("access_token")),
                "expires_at": raw.get("access_expires_at"),
                "has_refresh_token": bool(raw.get("has_refresh_token")),
            }
        if provider_id == "openai-codex":
            raw = hauth.get_codex_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": raw.get("source") or "openai_codex",
                "source_label": raw.get("auth_mode") or "OpenAI Codex",
                "token_preview": _truncate_token(raw.get("api_key")),
                "expires_at": None,
                "has_refresh_token": False,
                "last_refresh": raw.get("last_refresh"),
            }
        if provider_id == "qwen-oauth":
            raw = hauth.get_qwen_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "qwen_cli",
                "source_label": raw.get("auth_store_path") or "Qwen CLI",
                "token_preview": _truncate_token(raw.get("access_token")),
                "expires_at": raw.get("expires_at"),
                "has_refresh_token": bool(raw.get("has_refresh_token")),
            }
        if provider_id == "minimax-oauth":
            raw = hauth.get_minimax_oauth_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "minimax_oauth",
                "source_label": f"MiniMax ({raw.get('region', 'global')})",
                "token_preview": None,
                "expires_at": raw.get("expires_at"),
                "has_refresh_token": True,
            }
    except Exception as e:
        return {"logged_in": False, "error": str(e)}
    return {"logged_in": False}


@app.get("/api/providers/oauth")
async def list_oauth_providers():
    """Enumerate every OAuth-capable LLM provider with current status.

    Response shape (per provider):
        id              stable identifier (used in DELETE path)
        name            human label
        flow            "pkce" | "device_code" | "external"
        cli_command     fallback CLI command for users to run manually
        docs_url        external docs/portal link for the "Learn more" link
        status:
          logged_in        bool — currently has usable creds
          source           short slug ("takyon_pkce", "claude_code", ...)
          source_label     human-readable origin (file path, env var name)
          token_preview    last N chars of the token, never the full token
          expires_at       ISO timestamp string or null
          has_refresh_token bool
    """
    providers = []
    for p in _OAUTH_PROVIDER_CATALOG:
        status = _resolve_provider_status(p["id"], p.get("status_fn"))
        providers.append({
            "id": p["id"],
            "name": p["name"],
            "flow": p["flow"],
            "cli_command": p["cli_command"],
            "docs_url": p["docs_url"],
            "status": status,
        })
    return {"providers": providers}


@app.delete("/api/providers/oauth/{provider_id}")
async def disconnect_oauth_provider(provider_id: str, request: Request):
    """Disconnect an OAuth provider. Token-protected (matches /env/reveal)."""
    _require_token(request)

    valid_ids = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
    if provider_id not in valid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider_id}. "
                   f"Available: {', '.join(sorted(valid_ids))}",
        )

    # Anthropic and claude-code clear the same Takyon-managed PKCE file
    # AND forget the Claude Code import. We don't touch ~/.claude/* directly
    # — that's owned by the Claude Code CLI; users can re-auth there if they
    # want to undo a disconnect.
    if provider_id in {"anthropic", "claude-code"}:
        try:
            from agent.anthropic_adapter import _TAKYON_OAUTH_FILE
            if _TAKYON_OAUTH_FILE.exists():
                _TAKYON_OAUTH_FILE.unlink()
        except Exception:
            pass
        # Also clear the credential pool entry if present.
        try:
            from takyon_cli.auth import clear_provider_auth
            clear_provider_auth("anthropic")
        except Exception:
            pass
        _log.info("oauth/disconnect: %s", provider_id)
        return {"ok": True, "provider": provider_id}

    try:
        from takyon_cli.auth import clear_provider_auth
        cleared = clear_provider_auth(provider_id)
        _log.info("oauth/disconnect: %s (cleared=%s)", provider_id, cleared)
        return {"ok": bool(cleared), "provider": provider_id}
    except Exception as e:
        _log.exception("disconnect %s failed", provider_id)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# OAuth Phase 2 — in-browser PKCE & device-code flows
# ---------------------------------------------------------------------------
#
# Two flow shapes are supported:
#
#   PKCE (Anthropic):
#     1. POST /api/providers/oauth/anthropic/start
#          → server generates code_verifier + challenge, builds claude.ai
#            authorize URL, stashes verifier in _oauth_sessions[session_id]
#          → returns { session_id, flow: "pkce", auth_url }
#     2. UI opens auth_url in a new tab. User authorizes, copies code.
#     3. POST /api/providers/oauth/anthropic/submit { session_id, code }
#          → server exchanges (code + verifier) → tokens at console.anthropic.com
#          → persists to ~/.takyon/.anthropic_oauth.json AND credential pool
#          → returns { ok: true, status: "approved" }
#
#   Device code (Nous, OpenAI Codex):
#     1. POST /api/providers/oauth/{nous|openai-codex}/start
#          → server hits provider's device-auth endpoint
#          → gets { user_code, verification_url, device_code, interval, expires_in }
#          → spawns background poller thread that polls the token endpoint
#            every `interval` seconds until approved/expired
#          → stores poll status in _oauth_sessions[session_id]
#          → returns { session_id, flow: "device_code", user_code,
#                      verification_url, expires_in, poll_interval }
#     2. UI opens verification_url in a new tab and shows user_code.
#     3. UI polls GET /api/providers/oauth/{provider}/poll/{session_id}
#          every 2s until status != "pending".
#     4. On "approved" the background thread has already saved creds; UI
#        refreshes the providers list.
#
# Sessions are kept in-memory only (single-process FastAPI) and time out
# after 15 minutes. A periodic cleanup runs on each /start call to GC
# expired sessions so the dict doesn't grow without bound.

_OAUTH_SESSION_TTL_SECONDS = 15 * 60
_oauth_sessions: Dict[str, Dict[str, Any]] = {}
_oauth_sessions_lock = threading.Lock()

# Import OAuth constants from canonical source instead of duplicating.
# Guarded so takyon web still starts if anthropic_adapter is unavailable;
# Phase 2 endpoints will return 501 in that case.
try:
    from agent.anthropic_adapter import (
        _OAUTH_CLIENT_ID as _ANTHROPIC_OAUTH_CLIENT_ID,
        _OAUTH_TOKEN_URL as _ANTHROPIC_OAUTH_TOKEN_URL,
        _OAUTH_REDIRECT_URI as _ANTHROPIC_OAUTH_REDIRECT_URI,
        _OAUTH_SCOPES as _ANTHROPIC_OAUTH_SCOPES,
        _generate_pkce as _generate_pkce_pair,
    )
    _ANTHROPIC_OAUTH_AVAILABLE = True
except ImportError:
    _ANTHROPIC_OAUTH_AVAILABLE = False
_ANTHROPIC_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"


def _gc_oauth_sessions() -> None:
    """Drop expired sessions. Called opportunistically on /start."""
    cutoff = time.time() - _OAUTH_SESSION_TTL_SECONDS
    with _oauth_sessions_lock:
        stale = [sid for sid, sess in _oauth_sessions.items() if sess["created_at"] < cutoff]
        for sid in stale:
            _oauth_sessions.pop(sid, None)


def _new_oauth_session(provider_id: str, flow: str) -> tuple[str, Dict[str, Any]]:
    """Create + register a new OAuth session, return (session_id, session_dict)."""
    sid = secrets.token_urlsafe(16)
    sess = {
        "session_id": sid,
        "provider": provider_id,
        "flow": flow,
        "created_at": time.time(),
        "status": "pending",  # pending | approved | denied | expired | error
        "error_message": None,
    }
    with _oauth_sessions_lock:
        _oauth_sessions[sid] = sess
    return sid, sess


def _save_anthropic_oauth_creds(access_token: str, refresh_token: str, expires_at_ms: int) -> None:
    """Persist Anthropic PKCE creds to both Takyon file AND credential pool.

    Mirrors what auth_commands.add_command does so the dashboard flow leaves
    the system in the same state as ``takyon auth add anthropic``.
    """
    from agent.anthropic_adapter import _TAKYON_OAUTH_FILE
    payload = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_ms,
    }
    _TAKYON_OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TAKYON_OAUTH_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Best-effort credential-pool insert. Failure here doesn't invalidate
    # the file write — pool registration only matters for the rotation
    # strategy, not for runtime credential resolution.
    try:
        from agent.credential_pool import (
            PooledCredential,
            load_pool,
            AUTH_TYPE_OAUTH,
            SOURCE_MANUAL,
        )
        import uuid
        pool = load_pool("anthropic")
        # Avoid duplicate entries: delete any prior dashboard-issued OAuth entry
        existing = [e for e in pool.entries() if getattr(e, "source", "").startswith(f"{SOURCE_MANUAL}:dashboard_pkce")]
        for e in existing:
            try:
                pool.remove_entry(getattr(e, "id", ""))
            except Exception:
                pass
        entry = PooledCredential(
            provider="anthropic",
            id=uuid.uuid4().hex[:6],
            label="dashboard PKCE",
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:dashboard_pkce",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at_ms=expires_at_ms,
        )
        pool.add_entry(entry)
    except Exception as e:
        _log.warning("anthropic pool add (dashboard) failed: %s", e)


def _start_anthropic_pkce() -> Dict[str, Any]:
    """Begin PKCE flow. Returns the auth URL the UI should open."""
    if not _ANTHROPIC_OAUTH_AVAILABLE:
        raise HTTPException(status_code=501, detail="Anthropic OAuth not available (missing adapter)")
    verifier, challenge = _generate_pkce_pair()
    sid, sess = _new_oauth_session("anthropic", "pkce")
    sess["verifier"] = verifier
    sess["state"] = verifier  # Anthropic round-trips verifier as state
    params = {
        "code": "true",
        "client_id": _ANTHROPIC_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _ANTHROPIC_OAUTH_REDIRECT_URI,
        "scope": _ANTHROPIC_OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    auth_url = f"{_ANTHROPIC_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return {
        "session_id": sid,
        "flow": "pkce",
        "auth_url": auth_url,
        "expires_in": _OAUTH_SESSION_TTL_SECONDS,
    }


def _submit_anthropic_pkce(session_id: str, code_input: str) -> Dict[str, Any]:
    """Exchange authorization code for tokens. Persists on success."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess or sess["provider"] != "anthropic" or sess["flow"] != "pkce":
        raise HTTPException(status_code=404, detail="Unknown or expired session")
    if sess["status"] != "pending":
        return {"ok": False, "status": sess["status"], "message": sess.get("error_message")}

    # Anthropic's redirect callback page formats the code as `<code>#<state>`.
    # Strip the state suffix if present (we already have the verifier server-side).
    parts = code_input.strip().split("#", 1)
    code = parts[0].strip()
    if not code:
        return {"ok": False, "status": "error", "message": "No code provided"}
    state_from_callback = parts[1] if len(parts) > 1 else ""

    exchange_data = json.dumps({
        "grant_type": "authorization_code",
        "client_id": _ANTHROPIC_OAUTH_CLIENT_ID,
        "code": code,
        "state": state_from_callback or sess["state"],
        "redirect_uri": _ANTHROPIC_OAUTH_REDIRECT_URI,
        "code_verifier": sess["verifier"],
    }).encode()
    req = urllib.request.Request(
        _ANTHROPIC_OAUTH_TOKEN_URL,
        data=exchange_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "takyon-dashboard/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Token exchange failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expires_in = int(result.get("expires_in") or 3600)
    if not access_token:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = "No access token returned"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    expires_at_ms = int(time.time() * 1000) + (expires_in * 1000)
    try:
        _save_anthropic_oauth_creds(access_token, refresh_token, expires_at_ms)
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Save failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}
    with _oauth_sessions_lock:
        sess["status"] = "approved"
    _log.info("oauth/pkce: anthropic login completed (session=%s)", session_id)
    return {"ok": True, "status": "approved"}


async def _start_device_code_flow(provider_id: str) -> Dict[str, Any]:
    """Initiate a device-code flow (Nous, OpenAI Codex, or MiniMax).

    Calls the provider's device-auth endpoint via the existing CLI helpers,
    then spawns a background poller. Returns the user-facing display fields
    so the UI can render the verification page link + user code.
    """
    if provider_id == "nous":
        from takyon_cli.auth import (
            _nous_device_scope_with_env_override,
            _request_nous_device_code_with_scope_fallback,
            PROVIDER_REGISTRY,
        )
        import httpx
        pconfig = PROVIDER_REGISTRY["nous"]
        portal_base_url = (
            os.getenv("TAKYON_PORTAL_BASE_URL")
            or os.getenv("NOUS_PORTAL_BASE_URL")
            or pconfig.portal_base_url
        ).rstrip("/")
        client_id = pconfig.client_id
        scope, explicit_scope = _nous_device_scope_with_env_override(
            None,
            default_scope=pconfig.scope,
        )

        def _do_nous_device_request():
            with httpx.Client(
                timeout=httpx.Timeout(15.0),
                headers={"Accept": "application/json"},
            ) as client:
                return _request_nous_device_code_with_scope_fallback(
                    client=client,
                    portal_base_url=portal_base_url,
                    client_id=client_id,
                    scope=scope,
                    allow_legacy_fallback=not explicit_scope,
                )

        device_data, effective_scope = await asyncio.get_running_loop().run_in_executor(
            None, _do_nous_device_request
        )
        sid, sess = _new_oauth_session("nous", "device_code")
        sess["device_code"] = str(device_data["device_code"])
        sess["interval"] = int(device_data["interval"])
        sess["expires_at"] = time.time() + int(device_data["expires_in"])
        sess["portal_base_url"] = portal_base_url
        sess["client_id"] = client_id
        sess["scope"] = effective_scope
        threading.Thread(
            target=_nous_poller, args=(sid,), daemon=True, name=f"oauth-poll-{sid[:6]}"
        ).start()
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": str(device_data["user_code"]),
            "verification_url": str(device_data["verification_uri_complete"]),
            "expires_in": int(device_data["expires_in"]),
            "poll_interval": int(device_data["interval"]),
        }

    if provider_id == "openai-codex":
        # Codex uses fixed OpenAI device-auth endpoints; reuse the helper.
        sid, _ = _new_oauth_session("openai-codex", "device_code")
        # Use the helper but in a thread because it polls inline.
        # We can't extract just the start step without refactoring auth.py,
        # so we run the full helper in a worker and proxy the user_code +
        # verification_url back via the session dict. The helper prints
        # to stdout — we capture nothing here, just status.
        threading.Thread(
            target=_codex_full_login_worker, args=(sid,), daemon=True,
            name=f"oauth-codex-{sid[:6]}",
        ).start()
        # Block briefly until the worker has populated the user_code, OR error.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with _oauth_sessions_lock:
                s = _oauth_sessions.get(sid)
            if s and (s.get("user_code") or s["status"] != "pending"):
                break
            await asyncio.sleep(0.1)
        with _oauth_sessions_lock:
            s = _oauth_sessions.get(sid, {})
        if s.get("status") == "error":
            raise HTTPException(status_code=500, detail=s.get("error_message") or "device-auth failed")
        if not s.get("user_code"):
            raise HTTPException(status_code=504, detail="device-auth timed out before returning a user code")
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": s["user_code"],
            "verification_url": s["verification_url"],
            "expires_in": int(s.get("expires_in") or 900),
            "poll_interval": int(s.get("interval") or 5),
        }

    if provider_id == "minimax-oauth":
        # MiniMax uses a device-code-style flow (verification URI + user
        # code + background poll) with a PKCE extension on top. From the
        # operator's perspective it's identical to Nous's device-code
        # flow; the PKCE bit (verifier + challenge from
        # _minimax_pkce_pair) is a security extension that binds the
        # token exchange to the original session.
        from takyon_cli.auth import (
            _minimax_pkce_pair,
            _minimax_request_user_code,
            MINIMAX_OAUTH_CLIENT_ID,
            MINIMAX_OAUTH_GLOBAL_BASE,
        )
        import httpx
        verifier, challenge, state = _minimax_pkce_pair()
        portal_base_url = (
            os.getenv("MINIMAX_PORTAL_BASE_URL") or MINIMAX_OAUTH_GLOBAL_BASE
        ).rstrip("/")
        def _do_minimax_request():
            with httpx.Client(
                timeout=httpx.Timeout(15.0),
                headers={"Accept": "application/json"},
                follow_redirects=True,
            ) as client:
                return _minimax_request_user_code(
                    client=client,
                    portal_base_url=portal_base_url,
                    client_id=MINIMAX_OAUTH_CLIENT_ID,
                    code_challenge=challenge,
                    state=state,
                )
        device_data = await asyncio.get_event_loop().run_in_executor(
            None, _do_minimax_request
        )
        sid, sess = _new_oauth_session("minimax-oauth", "device_code")
        # The CLI flow names this `interval_ms` because MiniMax's
        # `interval` field is in milliseconds (defensive default 2000ms
        # in _minimax_poll_token).
        interval_raw = device_data.get("interval")
        sess["interval_ms"] = (
            int(interval_raw) if interval_raw is not None else None
        )
        sess["user_code"] = str(device_data["user_code"])
        sess["code_verifier"] = verifier
        sess["state"] = state
        sess["portal_base_url"] = portal_base_url
        sess["client_id"] = MINIMAX_OAUTH_CLIENT_ID
        sess["region"] = "global"
        # `expired_in` from MiniMax is overloaded — could be a unix-ms
        # timestamp OR a seconds-from-now duration. Mirror the heuristic
        # in _minimax_poll_token. Stash the raw value for the poller;
        # compute a derived expires_at + UI-friendly expires_in seconds.
        expired_in_raw = int(device_data["expired_in"])
        sess["expired_in_raw"] = expired_in_raw
        if expired_in_raw > 1_000_000_000_000:  # likely unix-ms
            expires_at_ts = expired_in_raw / 1000.0
            expires_in_seconds = max(0, int(expires_at_ts - time.time()))
        else:
            expires_at_ts = time.time() + expired_in_raw
            expires_in_seconds = expired_in_raw
        sess["expires_at"] = expires_at_ts
        threading.Thread(
            target=_minimax_poller,
            args=(sid,),
            daemon=True,
            name=f"oauth-poll-{sid[:6]}",
        ).start()
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": str(device_data["user_code"]),
            "verification_url": str(device_data["verification_uri"]),
            "expires_in": expires_in_seconds,
            "poll_interval": max(2, (sess["interval_ms"] or 2000) // 1000),
        }

    raise HTTPException(status_code=400, detail=f"Provider {provider_id} does not support device-code flow")


def _nous_poller(session_id: str) -> None:
    """Background poller that drives a Nous device-code flow to completion."""
    from takyon_cli.auth import (
        NOUS_INFERENCE_AUTH_MODE_FRESH,
        _poll_for_token,
        refresh_nous_oauth_from_state,
    )
    from datetime import datetime, timezone
    import httpx
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        return
    portal_base_url = sess["portal_base_url"]
    client_id = sess["client_id"]
    device_code = sess["device_code"]
    interval = sess["interval"]
    scope = sess.get("scope")
    expires_in = max(60, int(sess["expires_at"] - time.time()))
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), headers={"Accept": "application/json"}) as client:
            token_data = _poll_for_token(
                client=client,
                portal_base_url=portal_base_url,
                client_id=client_id,
                device_code=device_code,
                expires_in=expires_in,
                poll_interval=interval,
            )
        # Same post-processing as _nous_device_code_login (mint agent key)
        now = datetime.now(timezone.utc)
        token_ttl = int(token_data.get("expires_in") or 0)
        auth_state = {
            "portal_base_url": portal_base_url,
            "inference_base_url": token_data.get("inference_base_url"),
            "client_id": client_id,
            "scope": token_data.get("scope") or scope,
            "token_type": token_data.get("token_type", "Bearer"),
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "obtained_at": now.isoformat(),
            "expires_at": (
                datetime.fromtimestamp(now.timestamp() + token_ttl, tz=timezone.utc).isoformat()
                if token_ttl else None
            ),
            "expires_in": token_ttl,
        }
        full_state = refresh_nous_oauth_from_state(
            auth_state,
            min_key_ttl_seconds=300,
            timeout_seconds=15.0,
            force_refresh=False,
            inference_auth_mode=NOUS_INFERENCE_AUTH_MODE_FRESH,
        )
        from takyon_cli.auth import persist_nous_credentials
        persist_nous_credentials(full_state)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: nous login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("nous device-code poll failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = str(e)


def _minimax_poller(session_id: str) -> None:
    """Background poller that drives a MiniMax OAuth flow to completion.

    Mirrors `_nous_poller` but calls the MiniMax-specific token endpoint,
    which uses a PKCE-style ``code_verifier`` + ``user_code`` rather than
    the ``device_code`` field used by Nous. On success, builds the same
    auth_state dict that ``_minimax_oauth_login`` (the CLI flow) builds
    and persists via ``_minimax_save_auth_state`` — so the dashboard
    path leaves the system in the same state as
    ``takyon auth add minimax-oauth``.
    """
    from takyon_cli.auth import (
        _minimax_poll_token,
        _minimax_resolve_token_expiry_unix,
        _minimax_save_auth_state,
        MINIMAX_OAUTH_GLOBAL_INFERENCE,
        MINIMAX_OAUTH_SCOPE,
    )
    from datetime import datetime, timezone
    import httpx
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        return
    portal_base_url = sess["portal_base_url"]
    client_id = sess["client_id"]
    user_code = sess["user_code"]
    code_verifier = sess["code_verifier"]
    interval_ms = sess.get("interval_ms")
    expired_in_raw = sess["expired_in_raw"]
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            token_data = _minimax_poll_token(
                client=client,
                portal_base_url=portal_base_url,
                client_id=client_id,
                user_code=user_code,
                code_verifier=code_verifier,
                expired_in=expired_in_raw,
                interval_ms=interval_ms,
            )
        # Build the auth_state dict in the same shape as the CLI flow's
        # `_minimax_oauth_login` so `_minimax_save_auth_state` writes
        # the canonical record. Region is fixed to "global" for the
        # dashboard path; cn-region operators can still use the CLI
        # flow which supports `--region cn`.
        now = datetime.now(timezone.utc)
        expires_at_ts = _minimax_resolve_token_expiry_unix(
            int(token_data["expired_in"]), now=now,
        )
        expires_in_s = max(0, int(expires_at_ts - now.timestamp()))
        auth_state = {
            "provider": "minimax-oauth",
            "region": sess.get("region", "global"),
            "portal_base_url": portal_base_url,
            "inference_base_url": MINIMAX_OAUTH_GLOBAL_INFERENCE,
            "client_id": client_id,
            "scope": MINIMAX_OAUTH_SCOPE,
            "token_type": token_data.get("token_type", "Bearer"),
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "resource_url": token_data.get("resource_url"),
            "obtained_at": now.isoformat(),
            "expires_at": datetime.fromtimestamp(
                expires_at_ts, tz=timezone.utc
            ).isoformat(),
            "expires_in": expires_in_s,
        }
        _minimax_save_auth_state(auth_state)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: minimax login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("minimax device-code poll failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = str(e)


def _codex_full_login_worker(session_id: str) -> None:
    """Run the complete OpenAI Codex device-code flow.

    Codex doesn't use the standard OAuth device-code endpoints; it has its
    own ``/api/accounts/deviceauth/usercode`` (JSON body, returns
    ``device_auth_id``) and ``/api/accounts/deviceauth/token`` (JSON body
    polled until 200). On success the response carries an
    ``authorization_code`` + ``code_verifier`` that get exchanged at
    CODEX_OAUTH_TOKEN_URL with grant_type=authorization_code.

    The flow is replicated inline (rather than calling
    _codex_device_code_login) because that helper prints/blocks/polls in a
    single function — we need to surface the user_code to the dashboard the
    moment we receive it, well before polling completes.
    """
    try:
        import httpx
        from takyon_cli.auth import (
            CODEX_OAUTH_CLIENT_ID,
            CODEX_OAUTH_TOKEN_URL,
            DEFAULT_CODEX_BASE_URL,
        )
        issuer = "https://auth.openai.com"

        # Step 1: request device code
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            resp = client.post(
                f"{issuer}/api/accounts/deviceauth/usercode",
                json={"client_id": CODEX_OAUTH_CLIENT_ID},
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"deviceauth/usercode returned {resp.status_code}")
        device_data = resp.json()
        user_code = device_data.get("user_code", "")
        device_auth_id = device_data.get("device_auth_id", "")
        poll_interval = max(3, int(device_data.get("interval", "5")))
        if not user_code or not device_auth_id:
            raise RuntimeError("device-code response missing user_code or device_auth_id")
        verification_url = f"{issuer}/codex/device"
        with _oauth_sessions_lock:
            sess = _oauth_sessions.get(session_id)
            if not sess:
                return
            sess["user_code"] = user_code
            sess["verification_url"] = verification_url
            sess["device_auth_id"] = device_auth_id
            sess["interval"] = poll_interval
            sess["expires_in"] = 15 * 60  # OpenAI's effective limit
            sess["expires_at"] = time.time() + sess["expires_in"]

        # Step 2: poll until authorized
        deadline = time.monotonic() + sess["expires_in"]
        code_resp = None
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            while time.monotonic() < deadline:
                time.sleep(poll_interval)
                poll = client.post(
                    f"{issuer}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll.status_code == 200:
                    code_resp = poll.json()
                    break
                if poll.status_code in {403, 404}:
                    continue  # user hasn't authorized yet
                raise RuntimeError(f"deviceauth/token poll returned {poll.status_code}")

        if code_resp is None:
            with _oauth_sessions_lock:
                sess["status"] = "expired"
                sess["error_message"] = "Device code expired before approval"
            return

        # Step 3: exchange authorization_code for tokens
        authorization_code = code_resp.get("authorization_code", "")
        code_verifier = code_resp.get("code_verifier", "")
        if not authorization_code or not code_verifier:
            raise RuntimeError("device-auth response missing authorization_code/code_verifier")
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            token_resp = client.post(
                CODEX_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": f"{issuer}/deviceauth/callback",
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if token_resp.status_code != 200:
            raise RuntimeError(f"token exchange returned {token_resp.status_code}")
        tokens = token_resp.json()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        if not access_token:
            raise RuntimeError("token exchange did not return access_token")

        # Persist via credential pool — same shape as auth_commands.add_command
        from agent.credential_pool import (
            PooledCredential,
            load_pool,
            AUTH_TYPE_OAUTH,
            SOURCE_MANUAL,
        )
        import uuid as _uuid
        pool = load_pool("openai-codex")
        base_url = (
            os.getenv("TAKYON_CODEX_BASE_URL", "").strip().rstrip("/")
            or DEFAULT_CODEX_BASE_URL
        )
        entry = PooledCredential(
            provider="openai-codex",
            id=_uuid.uuid4().hex[:6],
            label="dashboard device_code",
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:dashboard_device_code",
            access_token=access_token,
            refresh_token=refresh_token,
            base_url=base_url,
        )
        pool.add_entry(entry)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: openai-codex login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("codex device-code worker failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            s = _oauth_sessions.get(session_id)
            if s:
                s["status"] = "error"
                s["error_message"] = str(e)


@app.post("/api/providers/oauth/{provider_id}/start")
async def start_oauth_login(provider_id: str, request: Request):
    """Initiate an OAuth login flow. Token-protected."""
    _require_token(request)
    _gc_oauth_sessions()
    valid = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
    if provider_id not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown provider {provider_id}")
    catalog_entry = next(p for p in _OAUTH_PROVIDER_CATALOG if p["id"] == provider_id)
    if catalog_entry["flow"] == "external":
        raise HTTPException(
            status_code=400,
            detail=f"{provider_id} uses an external CLI; run `{catalog_entry['cli_command']}` manually",
        )
    try:
        # The pkce branch is gated on provider_id == "anthropic" because
        # `_start_anthropic_pkce()` is hardcoded to the Anthropic flow.
        # Routing any other future pkce-flagged provider through it would
        # silently launch the Anthropic OAuth flow (the bug fixed in this
        # change for MiniMax). New PKCE providers must add their own
        # start function and an explicit branch here.
        if catalog_entry["flow"] == "pkce" and provider_id == "anthropic":
            return _start_anthropic_pkce()
        if catalog_entry["flow"] == "device_code":
            return await _start_device_code_flow(provider_id)
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("oauth/start %s failed", provider_id)
        raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=400, detail="Unsupported flow")


class OAuthSubmitBody(BaseModel):
    session_id: str
    code: str


@app.post("/api/providers/oauth/{provider_id}/submit")
async def submit_oauth_code(provider_id: str, body: OAuthSubmitBody, request: Request):
    """Submit the auth code for PKCE flows. Token-protected."""
    _require_token(request)
    if provider_id == "anthropic":
        return await asyncio.get_running_loop().run_in_executor(
            None, _submit_anthropic_pkce, body.session_id, body.code,
        )
    raise HTTPException(status_code=400, detail=f"submit not supported for {provider_id}")


@app.get("/api/providers/oauth/{provider_id}/poll/{session_id}")
async def poll_oauth_session(provider_id: str, session_id: str):
    """Poll a device-code session's status (no auth — read-only state)."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    if sess["provider"] != provider_id:
        raise HTTPException(status_code=400, detail="Provider mismatch for session")
    return {
        "session_id": session_id,
        "status": sess["status"],
        "error_message": sess.get("error_message"),
        "expires_at": sess.get("expires_at"),
    }


@app.delete("/api/providers/oauth/sessions/{session_id}")
async def cancel_oauth_session(session_id: str, request: Request):
    """Cancel a pending OAuth session. Token-protected."""
    _require_token(request)
    with _oauth_sessions_lock:
        sess = _oauth_sessions.pop(session_id, None)
    if sess is None:
        return {"ok": False, "message": "session not found"}
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Session detail endpoints
# ---------------------------------------------------------------------------



def _session_latest_descendant(session_id: str):
    """Resolve a session id to the newest child leaf session.

    /model may create child sessions. Dashboard refresh should continue the
    newest child instead of reopening the old parent.
    """
    from takyon_state import SessionDB

    def row_get(row, key, index):
        if isinstance(row, dict):
            return row.get(key)
        try:
            return row[key]
        except Exception:
            try:
                return row[index]
            except Exception:
                return None

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid or not db.get_session(sid):
            return None, []

        conn = (
            getattr(db, "conn", None)
            or getattr(db, "_conn", None)
            or getattr(db, "connection", None)
            or getattr(db, "_connection", None)
        )

        rows = []
        if conn is not None:
            raw_rows = conn.execute(
                "SELECT id, parent_session_id, started_at FROM sessions"
            ).fetchall()
            for row in raw_rows:
                rows.append({
                    "id": row_get(row, "id", 0),
                    "parent_session_id": row_get(row, "parent_session_id", 1),
                    "started_at": row_get(row, "started_at", 2),
                })
        else:
            rows = db.list_sessions_rich(limit=10000, offset=0)

        children = {}
        for row in rows:
            rid = row.get("id")
            parent = row.get("parent_session_id")
            if rid and parent:
                children.setdefault(parent, []).append(row)

        def started(row):
            try:
                return float(row.get("started_at") or 0)
            except Exception:
                return 0.0

        current = sid
        path = [sid]
        seen = {sid}

        while children.get(current):
            candidates = [r for r in children[current] if r.get("id") not in seen]
            if not candidates:
                break
            candidates.sort(key=started, reverse=True)
            current = candidates[0]["id"]
            path.append(current)
            seen.add(current)

        return current, path
    finally:
        db.close()

@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    from takyon_state import SessionDB
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        session = db.get_session(sid) if sid else None
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    finally:
        db.close()



@app.get("/api/sessions/{session_id}/latest-descendant")
async def get_session_latest_descendant(session_id: str):
    latest, path = _session_latest_descendant(session_id)
    if not latest:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "requested_session_id": path[0] if path else session_id,
        "session_id": latest,
        "path": path,
        "changed": bool(path and latest != path[0]),
    }

@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    from takyon_state import SessionDB
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = db.get_messages(sid)
        return {"session_id": sid, "messages": messages}
    finally:
        db.close()


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    from takyon_state import SessionDB
    db = SessionDB()
    try:
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Log viewer endpoint
# ---------------------------------------------------------------------------


@app.get("/api/logs")
async def get_logs(
    file: str = "agent",
    lines: int = 100,
    level: Optional[str] = None,
    component: Optional[str] = None,
    search: Optional[str] = None,
):
    from takyon_cli.logs import _read_tail, LOG_FILES

    log_name = LOG_FILES.get(file)
    if not log_name:
        raise HTTPException(status_code=400, detail=f"Unknown log file: {file}")
    log_path = get_takyon_home() / "logs" / log_name
    if not log_path.exists():
        return {"file": file, "lines": []}

    try:
        from takyon_logging import COMPONENT_PREFIXES
    except ImportError:
        COMPONENT_PREFIXES = {}

    # Normalize "ALL" / "all" / empty → no filter. _matches_filters treats an
    # empty tuple as "must match a prefix" (startswith(()) is always False),
    # so passing () instead of None silently drops every line.
    min_level = level if level and level.upper() != "ALL" else None
    if component and component.lower() != "all":
        comp_prefixes = COMPONENT_PREFIXES.get(component)
        if comp_prefixes is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown component: {component}. "
                       f"Available: {', '.join(sorted(COMPONENT_PREFIXES))}",
            )
    else:
        comp_prefixes = None

    has_filters = bool(min_level or comp_prefixes or search)
    result = _read_tail(
        log_path, min(lines, 500) if not search else 2000,
        has_filters=has_filters,
        min_level=min_level,
        component_prefixes=comp_prefixes,
    )
    # Post-filter by search term (case-insensitive substring match).
    # _read_tail doesn't support free-text search, so we filter here and
    # trim to the requested line count afterward.
    if search:
        needle = search.lower()
        result = [l for l in result if needle in l.lower()][-min(lines, 500):]
    return {"file": file, "lines": result}


# ---------------------------------------------------------------------------
# Cron job management endpoints
# ---------------------------------------------------------------------------


class CronJobCreate(BaseModel):
    prompt: str
    schedule: str
    name: str = ""
    deliver: str = "local"


class CronJobUpdate(BaseModel):
    updates: dict


_CRON_PROFILE_LOCK = threading.RLock()


def _cron_profile_dicts() -> List[Dict[str, Any]]:
    """Return dashboard profile records, falling back to a directory scan."""
    from takyon_cli import profiles as profiles_mod
    try:
        return [_profile_to_dict(p) for p in profiles_mod.list_profiles()]
    except Exception:
        _log.exception("Failed to list profiles for cron dashboard; falling back to directory scan")
        return _fallback_profile_dicts(profiles_mod)


def _cron_profile_home(profile: Optional[str]) -> Tuple[str, Path]:
    """Resolve a profile query value to (profile_name, TAKYON_HOME)."""
    from takyon_cli import profiles as profiles_mod

    raw = (profile or "default").strip() or "default"
    try:
        canon = profiles_mod.normalize_profile_name(raw)
        profiles_mod.validate_profile_name(canon)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not profiles_mod.profile_exists(canon):
        raise HTTPException(status_code=404, detail=f"Profile '{canon}' does not exist.")
    return canon, profiles_mod.get_profile_dir(canon)


def _annotate_cron_job(job: Dict[str, Any], profile: str, home: Path) -> Dict[str, Any]:
    annotated = dict(job)
    annotated["profile"] = profile
    annotated["profile_name"] = profile
    annotated["takyon_home"] = str(home)
    annotated["is_default_profile"] = profile == "default"
    return annotated


def _call_cron_for_profile(profile: Optional[str], func_name: str, *args, **kwargs):
    """Run cron.jobs helpers against the selected profile's cron directory.

    cron.jobs keeps CRON_DIR/JOBS_FILE/OUTPUT_DIR as module globals resolved
    from the process TAKYON_HOME at import time. The dashboard is a single
    process that can inspect many profiles, so temporarily retarget those
    globals while holding a lock and restore them immediately after the call.
    """
    profile_name, home = _cron_profile_home(profile)
    with _CRON_PROFILE_LOCK:
        from cron import jobs as cron_jobs

        old_cron_dir = cron_jobs.CRON_DIR
        old_jobs_file = cron_jobs.JOBS_FILE
        old_output_dir = cron_jobs.OUTPUT_DIR
        cron_jobs.CRON_DIR = home / "cron"
        cron_jobs.JOBS_FILE = cron_jobs.CRON_DIR / "jobs.json"
        cron_jobs.OUTPUT_DIR = cron_jobs.CRON_DIR / "output"
        try:
            result = getattr(cron_jobs, func_name)(*args, **kwargs)
        finally:
            cron_jobs.CRON_DIR = old_cron_dir
            cron_jobs.JOBS_FILE = old_jobs_file
            cron_jobs.OUTPUT_DIR = old_output_dir

    if isinstance(result, list):
        return [_annotate_cron_job(j, profile_name, home) for j in result]
    if isinstance(result, dict):
        return _annotate_cron_job(result, profile_name, home)
    return result


def _find_cron_job_profile(job_id: str) -> Optional[str]:
    for profile in _cron_profile_dicts():
        name = str(profile.get("name") or "")
        if not name:
            continue
        jobs = _call_cron_for_profile(name, "list_jobs", True)
        if any(j.get("id") == job_id or j.get("name") == job_id for j in jobs):
            return name
    return None


@app.get("/api/cron/jobs")
async def list_cron_jobs(profile: str = "all"):
    requested = (profile or "all").strip()
    if requested.lower() != "all":
        return _call_cron_for_profile(requested, "list_jobs", True)

    jobs: List[Dict[str, Any]] = []
    for item in _cron_profile_dicts():
        name = str(item.get("name") or "")
        if not name:
            continue
        try:
            jobs.extend(_call_cron_for_profile(name, "list_jobs", True))
        except Exception:
            _log.exception("Failed to list cron jobs for profile %s", name)
    return jobs


@app.get("/api/cron/jobs/{job_id}")
async def get_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "get_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs")
async def create_cron_job(body: CronJobCreate, profile: str = "default"):
    try:
        return _call_cron_for_profile(
            profile,
            "create_job",
            prompt=body.prompt,
            schedule=body.schedule,
            name=body.name,
            deliver=body.deliver,
        )
    except Exception as e:
        _log.exception("POST /api/cron/jobs failed")
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, body: CronJobUpdate, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "update_job", job_id, body.updates)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs/{job_id}/pause")
async def pause_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "pause_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs/{job_id}/resume")
async def resume_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "resume_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/cron/jobs/{job_id}/trigger")
async def trigger_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "trigger_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/api/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _call_cron_for_profile(selected, "remove_job", job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Profile management endpoints (minimal — list/create/rename/delete + SOUL.md)
# ---------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    name: str
    clone_from_default: bool = False
    no_skills: bool = False


class ProfileRename(BaseModel):
    new_name: str


class ProfileSoulUpdate(BaseModel):
    content: str


def _profile_attr(info, name: str, default: Any = None) -> Any:
    try:
        return getattr(info, name)
    except Exception:
        return default


def _profile_to_dict(info) -> Dict[str, Any]:
    return {
        "name": _profile_attr(info, "name", ""),
        "path": str(_profile_attr(info, "path", "")),
        "is_default": bool(_profile_attr(info, "is_default", False)),
        "model": _profile_attr(info, "model"),
        "provider": _profile_attr(info, "provider"),
        "has_env": bool(_profile_attr(info, "has_env", False)),
        "skill_count": int(_profile_attr(info, "skill_count", 0) or 0),
    }


def _fallback_profile_dicts(profiles_mod) -> List[Dict[str, Any]]:
    def _safe(callable_, default):
        try:
            return callable_()
        except Exception:
            return default

    profiles: List[Dict[str, Any]] = []
    default_home = profiles_mod._get_default_takyon_home()
    if default_home.is_dir():
        model, provider = _safe(lambda: profiles_mod._read_config_model(default_home), (None, None))
        profiles.append({
            "name": "default",
            "path": str(default_home),
            "is_default": True,
            "model": model,
            "provider": provider,
            "has_env": (default_home / ".env").exists(),
            "skill_count": _safe(lambda: profiles_mod._count_skills(default_home), 0),
        })

    profiles_root = profiles_mod._get_profiles_root()
    if profiles_root.is_dir():
        for entry in sorted(profiles_root.iterdir()):
            if not entry.is_dir() or not profiles_mod._PROFILE_ID_RE.match(entry.name):
                continue
            model, provider = _safe(lambda entry=entry: profiles_mod._read_config_model(entry), (None, None))
            profiles.append({
                "name": entry.name,
                "path": str(entry),
                "is_default": False,
                "model": model,
                "provider": provider,
                "has_env": (entry / ".env").exists(),
                "skill_count": _safe(lambda entry=entry: profiles_mod._count_skills(entry), 0),
            })

    return profiles


def _resolve_profile_dir(name: str) -> Path:
    """Validate ``name`` and resolve to its directory or raise an HTTPException."""
    from takyon_cli import profiles as profiles_mod
    try:
        profiles_mod.validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not profiles_mod.profile_exists(name):
        raise HTTPException(status_code=404, detail=f"Profile '{name}' does not exist.")
    return profiles_mod.get_profile_dir(name)


def _profile_setup_command(name: str) -> str:
    """Return the shell command used to configure a profile in the CLI."""
    _resolve_profile_dir(name)
    return "takyon setup" if name == "default" else f"{name} setup"


@app.get("/api/profiles")
async def list_profiles_endpoint():
    from takyon_cli import profiles as profiles_mod
    try:
        return {"profiles": [_profile_to_dict(p) for p in profiles_mod.list_profiles()]}
    except Exception:
        _log.exception("GET /api/profiles failed; falling back to profile directory scan")
        return {"profiles": _fallback_profile_dicts(profiles_mod)}


@app.post("/api/profiles")
async def create_profile_endpoint(body: ProfileCreate):
    from takyon_cli import profiles as profiles_mod
    try:
        path = profiles_mod.create_profile(
            name=body.name,
            clone_from="default" if body.clone_from_default else None,
            clone_config=body.clone_from_default,
            no_skills=body.no_skills,
        )
        # Match the CLI's profile-create flow: fresh named profiles get the
        # bundled skills installed. When cloning from default, create_profile()
        # has already copied the source profile's skills, including any
        # user-installed skills. When no_skills=True, create_profile() wrote
        # the opt-out marker and seed_profile_skills() will no-op.
        if not body.clone_from_default:
            profiles_mod.seed_profile_skills(path, quiet=True)

        # Match the CLI's profile-create flow: named profiles should get a
        # wrapper in ~/.local/bin when the alias is safe to create.
        collision = profiles_mod.check_alias_collision(body.name)
        if not collision:
            profiles_mod.create_wrapper_script(body.name)
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("POST /api/profiles failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "name": body.name, "path": str(path)}


@app.get("/api/profiles/{name}/setup-command")
async def get_profile_setup_command(name: str):
    return {"command": _profile_setup_command(name)}


@app.post("/api/profiles/{name}/open-terminal")
async def open_profile_terminal_endpoint(name: str):
    try:
        command = _profile_setup_command(name)

        if sys.platform.startswith("win"):
            subprocess.Popen(["cmd.exe", "/c", "start", "", command])
        elif sys.platform == "darwin":
            escaped = command.replace("\\", "\\\\").replace('"', '\\"')
            applescript = (
                'tell application "Terminal"\n'
                "activate\n"
                f'do script "{escaped}"\n'
                "end tell"
            )
            subprocess.Popen(["osascript", "-e", applescript])
        else:
            terminal_commands = [
                ("x-terminal-emulator", ["x-terminal-emulator", "-e", "sh", "-lc", command]),
                ("gnome-terminal", ["gnome-terminal", "--", "sh", "-lc", command]),
                ("konsole", ["konsole", "-e", "sh", "-lc", command]),
                ("xfce4-terminal", ["xfce4-terminal", "-e", f"sh -lc '{command}'"]),
                ("mate-terminal", ["mate-terminal", "-e", f"sh -lc '{command}'"]),
                ("lxterminal", ["lxterminal", "-e", f"sh -lc '{command}'"]),
                ("tilix", ["tilix", "-e", "sh", "-lc", command]),
                ("alacritty", ["alacritty", "-e", "sh", "-lc", command]),
                ("kitty", ["kitty", "sh", "-lc", command]),
                ("xterm", ["xterm", "-e", "sh", "-lc", command]),
            ]
            for executable, popen_args in terminal_commands:
                if subprocess.call(
                    ["which", executable],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ) == 0:
                    subprocess.Popen(popen_args)
                    break
            else:
                raise HTTPException(
                    status_code=400,
                    detail="No supported terminal emulator found",
                )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("POST /api/profiles/%s/open-terminal failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "command": command}


@app.patch("/api/profiles/{name}")
async def rename_profile_endpoint(name: str, body: ProfileRename):
    from takyon_cli import profiles as profiles_mod
    try:
        path = profiles_mod.rename_profile(name, body.new_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("PATCH /api/profiles/%s failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "name": body.new_name, "path": str(path)}


@app.delete("/api/profiles/{name}")
async def delete_profile_endpoint(name: str):
    """Delete a profile. The dashboard collects the user's confirmation in
    its own dialog before this request, so we always pass ``yes=True`` to
    skip the CLI's interactive prompt."""
    from takyon_cli import profiles as profiles_mod
    try:
        path = profiles_mod.delete_profile(name, yes=True)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("DELETE /api/profiles/%s failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "path": str(path)}


@app.get("/api/profiles/{name}/soul")
async def get_profile_soul(name: str):
    soul_path = _resolve_profile_dir(name) / "SOUL.md"
    if soul_path.exists():
        try:
            return {"content": soul_path.read_text(encoding="utf-8"), "exists": True}
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Could not read SOUL.md: {e}")
    return {"content": "", "exists": False}


@app.put("/api/profiles/{name}/soul")
async def update_profile_soul(name: str, body: ProfileSoulUpdate):
    soul_path = _resolve_profile_dir(name) / "SOUL.md"
    try:
        soul_path.write_text(body.content, encoding="utf-8")
    except OSError as e:
        _log.exception("PUT /api/profiles/%s/soul failed", name)
        raise HTTPException(status_code=500, detail=f"Could not write SOUL.md: {e}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Skills & Tools endpoints
# ---------------------------------------------------------------------------


class SkillToggle(BaseModel):
    name: str
    enabled: bool


@app.get("/api/skills")
async def get_skills():
    from tools.skills_tool import _find_all_skills
    from takyon_cli.skills_config import get_disabled_skills
    config = load_config()
    disabled = get_disabled_skills(config)
    skills = _find_all_skills(skip_disabled=True)
    for s in skills:
        s["enabled"] = s["name"] not in disabled
    return skills


@app.put("/api/skills/toggle")
async def toggle_skill(body: SkillToggle):
    from takyon_cli.skills_config import get_disabled_skills, save_disabled_skills
    config = load_config()
    disabled = get_disabled_skills(config)
    if body.enabled:
        disabled.discard(body.name)
    else:
        disabled.add(body.name)
    save_disabled_skills(config, disabled)
    return {"ok": True, "name": body.name, "enabled": body.enabled}


@app.get("/api/tools/toolsets")
async def get_toolsets():
    from takyon_cli.tools_config import (
        _get_effective_configurable_toolsets,
        _get_platform_tools,
        _toolset_has_keys,
    )
    from toolsets import resolve_toolset

    config = load_config()
    enabled_toolsets = _get_platform_tools(
        config,
        "cli",
        include_default_mcp_servers=False,
    )
    result = []
    for name, label, desc in _get_effective_configurable_toolsets():
        try:
            tools = sorted(set(resolve_toolset(name)))
        except Exception:
            tools = []
        is_enabled = name in enabled_toolsets
        result.append({
            "name": name, "label": label, "description": desc,
            "enabled": is_enabled,
            "available": is_enabled,
            "configured": _toolset_has_keys(name, config),
            "tools": tools,
        })
    return result


# ---------------------------------------------------------------------------
# Raw YAML config endpoint
# ---------------------------------------------------------------------------


class RawConfigUpdate(BaseModel):
    yaml_text: str


@app.get("/api/config/raw")
async def get_config_raw():
    path = get_config_path()
    if not path.exists():
        return {"yaml": ""}
    return {"yaml": path.read_text(encoding="utf-8")}


@app.put("/api/config/raw")
async def update_config_raw(body: RawConfigUpdate):
    try:
        parsed = yaml.safe_load(body.yaml_text)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML must be a mapping")
        save_config(parsed)
        return {"ok": True}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")


# ---------------------------------------------------------------------------
# Token / cost analytics endpoint
# ---------------------------------------------------------------------------


@app.get("/api/analytics/usage")
async def get_usage_analytics(days: int = 30):
    from takyon_state import SessionDB
    from agent.insights import InsightsEngine

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)
        cur = db._conn.execute("""
            SELECT date(started_at, 'unixepoch') as day,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ?
            GROUP BY day ORDER BY day
        """, (cutoff,))
        daily = [dict(r) for r in cur.fetchall()]

        cur2 = db._conn.execute("""
            SELECT model,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL
            GROUP BY model ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        by_model = [dict(r) for r in cur2.fetchall()]

        cur3 = db._conn.execute("""
            SELECT SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ?
        """, (cutoff,))
        totals = dict(cur3.fetchone())
        insights_report = InsightsEngine(db).generate(days=days)
        skills = insights_report.get("skills", {
            "summary": {
                "total_skill_loads": 0,
                "total_skill_edits": 0,
                "total_skill_actions": 0,
                "distinct_skills_used": 0,
            },
            "top_skills": [],
        })

        return {
            "daily": daily,
            "by_model": by_model,
            "totals": totals,
            "period_days": days,
            "skills": skills,
        }
    finally:
        db.close()


@app.get("/api/analytics/models")
async def get_models_analytics(days: int = 30):
    """Rich per-model analytics for the Models dashboard page.

    Returns token/cost/session breakdown per model plus capability metadata
    from models.dev (context window, vision, tools, reasoning, etc.).
    """
    from takyon_state import SessionDB

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)

        cur = db._conn.execute("""
            SELECT model,
                   billing_provider,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls,
                   SUM(tool_call_count) as tool_calls,
                   MAX(started_at) as last_used_at,
                   AVG(input_tokens + output_tokens) as avg_tokens_per_session
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
            GROUP BY model, billing_provider
            ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]

        models = []
        for row in rows:
            provider = row.get("billing_provider") or ""
            model_name = row["model"]
            caps = {}
            try:
                from agent.models_dev import get_model_capabilities
                mc = get_model_capabilities(provider=provider, model=model_name)
                if mc is not None:
                    caps = {
                        "supports_tools": mc.supports_tools,
                        "supports_vision": mc.supports_vision,
                        "supports_reasoning": mc.supports_reasoning,
                        "context_window": mc.context_window,
                        "max_output_tokens": mc.max_output_tokens,
                        "model_family": mc.model_family,
                    }
            except Exception:
                pass

            models.append({
                "model": model_name,
                "provider": provider,
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "reasoning_tokens": row["reasoning_tokens"],
                "estimated_cost": row["estimated_cost"],
                "actual_cost": row["actual_cost"],
                "sessions": row["sessions"],
                "api_calls": row["api_calls"],
                "tool_calls": row["tool_calls"],
                "last_used_at": row["last_used_at"],
                "avg_tokens_per_session": row["avg_tokens_per_session"],
                "capabilities": caps,
            })

        totals_cur = db._conn.execute("""
            SELECT COUNT(DISTINCT model) as distinct_models,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
        """, (cutoff,))
        totals = dict(totals_cur.fetchone())

        return {
            "models": models,
            "totals": totals,
            "period_days": days,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# /api/pty — PTY-over-WebSocket bridge for the dashboard "Chat" tab.
#
# The endpoint spawns the same ``takyon --tui`` binary the CLI uses, behind
# a POSIX pseudo-terminal, and forwards bytes + resize escapes across a
# WebSocket.  The browser renders the ANSI through xterm.js (see
# web/src/pages/ChatPage.tsx).
#
# Auth: ``?token=<session_token>`` query param (browsers can't set
# Authorization on the WS upgrade).  Same ephemeral ``_SESSION_TOKEN`` as
# REST.  Localhost-only — we defensively reject non-loopback clients even
# though uvicorn binds to 127.0.0.1.
# ---------------------------------------------------------------------------

import re
import asyncio

# PTY bridge is POSIX-only (depends on fcntl/termios/ptyprocess).  On native
# Windows the import raises; catch and leave PtyBridge=None so the rest of
# the dashboard (sessions, jobs, metrics, config editor) still loads and the
# /api/pty endpoint cleanly refuses with a WSL-suggested message.
try:
    from takyon_cli.pty_bridge import PtyBridge, PtyUnavailableError
    _PTY_BRIDGE_AVAILABLE = True
except ImportError as _pty_import_err:  # pragma: no cover - Windows-only path
    PtyBridge = None  # type: ignore[assignment]
    _PTY_BRIDGE_AVAILABLE = False

    class PtyUnavailableError(RuntimeError):  # type: ignore[no-redef]
        """Stub on platforms where pty_bridge can't be imported."""
        pass

_RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")
_PTY_READ_CHUNK_TIMEOUT = 0.2
_VALID_CHANNEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Starlette's TestClient reports the peer as "testclient"; treat it as
# loopback so tests don't need to rewrite request scope.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _is_public_bind() -> bool:
    """True when bound to all-interfaces (operator used --insecure)."""
    return getattr(app.state, "bound_host", "") in {"0.0.0.0", "::"}


def _ws_client_is_allowed(ws: "WebSocket") -> bool:
    """Check if the WebSocket client IP is acceptable.

    Allows loopback always; allows any IP when bound to all-interfaces
    (--insecure mode, guarded by session token auth).
    """
    if _is_public_bind():
        return True
    client_host = ws.client.host if ws.client else ""
    if not client_host:
        return True
    return client_host in _LOOPBACK_HOSTS


def _ws_auth0_session_is_allowed(ws: "WebSocket") -> bool:
    """Require the Auth0 dashboard cookie on the configured public host."""
    try:
        cfg = _auth0_config()
    except Auth0ConfigError:
        return False
    if not cfg or not _auth0_required_for_host(ws.headers):
        return True
    return _session_from_cookie_header(ws.headers.get("cookie", ""), cfg) is not None


def _ws_auth0_reject_reason(ws: "WebSocket") -> str:
    """Return the exact Auth0 rejection reason, or empty when allowed."""
    try:
        cfg = _auth0_config()
    except Auth0ConfigError as exc:
        return f"auth0_config_error:{str(exc).splitlines()[0][:80]}"
    if not cfg or not _auth0_required_for_host(ws.headers):
        return ""
    if _session_from_cookie_header(ws.headers.get("cookie", ""), cfg) is not None:
        return ""
    return "auth0_cookie_missing"


async def _ws_reject(ws: "WebSocket", endpoint: str, code: int, reason: str) -> None:
    """Close a rejected dashboard WebSocket with enough server-side forensics."""
    client = ws.client.host if ws.client else ""
    host = ws.headers.get("host", "")
    origin = ws.headers.get("origin", "")
    xff = ws.headers.get("x-forwarded-for", "")
    xf_proto = ws.headers.get("x-forwarded-proto", "")
    _log.warning(
        "dashboard websocket rejected endpoint=%s reason=%s code=%s host=%r origin=%r client=%r x_forwarded_for=%r x_forwarded_proto=%r",
        endpoint,
        reason,
        code,
        host,
        origin,
        client,
        xff,
        xf_proto,
    )
    await ws.close(code=code, reason=reason[:120])


def _dashboard_product_site_root() -> Path:
    raw = (
        os.getenv("TAKYON_PRODUCT_SITE_ROOT", "").strip()
        or os.getenv("PUBLIC_COMPANY_SITE_ROOT", "").strip()
        or os.getenv("TAKYON_STATIC_SITE_ROOT", "").strip()
    )
    return Path(raw).expanduser().resolve() if raw else get_takyon_home() / "product-sites"


def _company_base_domain() -> str:
    return (
        os.getenv("PUBLIC_COMPANY_BASE_DOMAIN", "").strip().lower()
        or os.getenv("TAKYON_COMPANY_BASE_DOMAIN", "").strip().lower()
        or "fourmanifold.com"
    ).strip(".")


def _business_slug_from_product_host(host: str) -> str:
    host = (host or "").strip().lower().strip(".")
    base = _company_base_domain()
    if not host or not base or not host.endswith(f".{base}"):
        return ""
    slug = host[: -(len(base) + 1)]
    if _is_reserved_public_subdomain(slug):
        return ""
    try:
        return _safe_product_slug(slug)
    except HTTPException:
        return ""


_PRODUCT_HOST_BUSINESS_CACHE_LOCK = threading.Lock()
_PRODUCT_HOST_BUSINESS_CACHE: dict[str, tuple[float, bool, str]] = {}
_PRODUCT_HOST_BUSINESS_POSITIVE_TTL_SECONDS = 60.0
_PRODUCT_HOST_BUSINESS_NEGATIVE_TTL_SECONDS = 5.0
_PRODUCT_HOST_BUSINESS_CACHE_MAX = 2048
_PRODUCT_SITE_MATERIALIZE_LOCK = threading.Lock()
_PRODUCT_SITE_MATERIALIZE_LOCKS: dict[str, threading.Lock] = {}


def _product_site_materialize_lock_for_slug(slug: str) -> threading.Lock:
    normalized = str(slug or "").strip().lower()
    with _PRODUCT_SITE_MATERIALIZE_LOCK:
        lock = _PRODUCT_SITE_MATERIALIZE_LOCKS.get(normalized)
        if lock is None:
            lock = threading.Lock()
            _PRODUCT_SITE_MATERIALIZE_LOCKS[normalized] = lock
        return lock


def _product_host_has_business_uncached(domain: str) -> tuple[bool, str]:
    slug = _business_slug_from_product_host(_host_without_port(domain))
    if not slug:
        return False, "not_product_subdomain"
    try:
        from plugins.takyon.core import TakyonStore

        store = TakyonStore(get_takyon_home())
        with store._connect() as conn:
            if store._business(conn, slug) is None:
                return False, "business_not_found"
    except Exception:
        return False, "business_not_found"
    return True, slug


def _product_host_has_business(domain: str) -> tuple[bool, str]:
    normalized = _host_without_port(domain).strip().lower()
    if not normalized:
        return False, "not_product_subdomain"

    now = time.monotonic()
    with _PRODUCT_HOST_BUSINESS_CACHE_LOCK:
        cached = _PRODUCT_HOST_BUSINESS_CACHE.get(normalized)
        if cached is not None:
            expires_at, ok, reason = cached
            if expires_at > now:
                return ok, reason
            _PRODUCT_HOST_BUSINESS_CACHE.pop(normalized, None)

    result = _product_host_has_business_uncached(normalized)
    ttl = (
        _PRODUCT_HOST_BUSINESS_POSITIVE_TTL_SECONDS
        if result[0]
        else _PRODUCT_HOST_BUSINESS_NEGATIVE_TTL_SECONDS
    )
    with _PRODUCT_HOST_BUSINESS_CACHE_LOCK:
        if len(_PRODUCT_HOST_BUSINESS_CACHE) >= _PRODUCT_HOST_BUSINESS_CACHE_MAX:
            stale = [
                key
                for key, (expires_at, _ok, _reason) in _PRODUCT_HOST_BUSINESS_CACHE.items()
                if expires_at <= now
            ]
            for key in stale:
                _PRODUCT_HOST_BUSINESS_CACHE.pop(key, None)
            while len(_PRODUCT_HOST_BUSINESS_CACHE) >= _PRODUCT_HOST_BUSINESS_CACHE_MAX:
                _PRODUCT_HOST_BUSINESS_CACHE.pop(next(iter(_PRODUCT_HOST_BUSINESS_CACHE)))
        _PRODUCT_HOST_BUSINESS_CACHE[normalized] = (now + ttl, result[0], result[1])
    return result


@app.get("/api/product-tls/ask")
async def product_tls_ask(domain: str = "") -> Response:
    """Caddy on-demand TLS gate for shared product subdomains.

    Caddy calls this before issuing a certificate. Keep it narrow so a random
    hostname cannot make the VPS mint certificates unless Takyon already has a
    matching business in its canonical store.
    """
    ok, reason = await asyncio.to_thread(_product_host_has_business, domain)
    if ok:
        return Response(status_code=200, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {"detail": reason},
        status_code=404,
        headers={"Cache-Control": "no-store"},
    )


def _configure_local_product_publish(host: str, port: int) -> None:
    """Give local dashboard runs an honest local product URL without prod deploy."""
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if host not in local_hosts:
        return
    if _configured_public_host():
        return
    root = _dashboard_product_site_root()
    os.environ.setdefault("TAKYON_PRODUCT_SITE_ROOT", str(root))
    os.environ.setdefault("TAKYON_PRODUCT_CADDYFILE", str(root / "Caddyfile"))
    os.environ.setdefault("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "1")
    os.environ.setdefault("TAKYON_PRODUCT_SKIP_PUBLIC_PROBE", "1")
    display_host = "127.0.0.1" if host in {"::1", "localhost"} else host
    os.environ.setdefault("TAKYON_PRODUCT_LOCAL_BASE_URL", f"http://{display_host}:{port}/site")


def _safe_product_slug(value: str) -> str:
    slug = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,78}[a-z0-9]", slug) and not re.fullmatch(r"[a-z0-9]", slug):
        raise HTTPException(status_code=404, detail="product site not found")
    return slug


def _materialize_product_site_from_storage(business: str) -> Path | None:
    slug = _safe_product_slug(business)
    publish_root = _dashboard_product_site_root().resolve()
    target_root = (publish_root / slug).resolve()
    if publish_root not in (target_root, *target_root.parents):
        return None
    materialize_lock = _product_site_materialize_lock_for_slug(slug)

    with materialize_lock:
        try:
            from plugins.takyon.core import (
                TakyonStore,
                _product_static_publish_source,
                _replace_directory_tree_atomic,
            )
            from plugins.takyon.storage import get_storage_backend, sync_down
        except Exception as exc:
            _log.warning("product site materialize imports failed for %s: %s", slug, exc)
            return None

        try:
            app = (
                TakyonStore(get_takyon_home())
                .read(scope=f"business:{slug}", query="summary", include=["app"], limit=1)
                .get("app")
                or {}
            )
        except Exception as exc:
            _log.warning("product site summary read failed for %s: %s", slug, exc)
            return None

        surface = app.get("surface_contract") or {}
        if str(surface.get("publish_status") or "").strip().lower() != "published":
            return None
        published_at = str(surface.get("published_at") or "").strip()
        source_path = str(surface.get("source_path") or "product/site").strip()
        rel = Path(source_path.replace("\\", "/"))
        if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
            _log.warning("product site source path is unsafe for %s: %r", slug, source_path)
            return None

        marker = target_root / ".takyon-published-at"
        if target_root.is_dir() and published_at:
            try:
                if marker.read_text(encoding="utf-8").strip() == published_at:
                    return target_root
            except OSError:
                pass

        try:
            backend = get_storage_backend()
        except Exception as exc:
            _log.warning("product site backend unavailable for %s: %s", slug, exc)
            return None

        with tempfile.TemporaryDirectory(prefix=f"takyon-product-site-{slug}-") as tmp_dir:
            business_root = Path(tmp_dir) / "businesses" / slug
            try:
                sync_down(backend, slug, business_root, delete_local=True)
            except Exception as exc:
                _log.warning("product site sync_down failed for %s: %s", slug, exc)
                return None
            source_root = (business_root / rel).resolve()
            if business_root.resolve() not in (source_root, *source_root.parents):
                _log.warning("product site source escaped business root for %s: %s", slug, source_root)
                return None
            publish_source, _publish_label = _product_static_publish_source(source_root)
            if publish_source is None:
                return None

            publish_root.mkdir(parents=True, exist_ok=True)

            def ignore(_directory: str, names: list[str]) -> set[str]:
                return {
                    name
                    for name in names
                    if name in {"node_modules", ".git", ".next", ".cache", "__pycache__"}
                    or name.endswith(".pyc")
                }

            _replace_directory_tree_atomic(publish_source, target_root, ignore=ignore)
            if published_at:
                marker.write_text(published_at + "\n", encoding="utf-8")

    return target_root if target_root.is_dir() else None


async def _serve_product_site_file(business: str, full_path: str = "") -> Response:
    slug = _safe_product_slug(business)
    root = _dashboard_product_site_root().resolve()
    rel = full_path.strip("/") or "index.html"
    site_root = (root / slug).resolve()
    target = (site_root / rel).resolve()
    if target.is_dir():
        target = target / "index.html"
    if root in (target, *target.parents) and target.is_file():
        return FileResponse(target)
    if not Path(rel).suffix:
        for candidate in (target / "index.html", target.with_suffix(".html")):
            candidate = candidate.resolve()
            if root in (candidate, *candidate.parents) and candidate.is_file():
                return FileResponse(candidate)
    # Lazy publish hydrate can hit object storage. Keep that sync path off the
    # main server loop so one cold product host cannot wedge the whole runtime.
    materialized_root = await asyncio.to_thread(_materialize_product_site_from_storage, slug)
    if materialized_root is not None:
        target = (materialized_root / rel).resolve()
        if target.is_dir():
            target = target / "index.html"
        if materialized_root in (target, *target.parents) and target.is_file():
            return FileResponse(target)
        if not Path(rel).suffix:
            for candidate in (target / "index.html", target.with_suffix(".html")):
                candidate = candidate.resolve()
                if materialized_root in (candidate, *candidate.parents) and candidate.is_file():
                    return FileResponse(candidate)
    detail = {
        "error": "product site file not found",
        "business": slug,
        "requested_path": rel,
        "expected_file": str(target),
        "site_root": str(site_root),
    }
    return JSONResponse(detail, status_code=404, headers={"Cache-Control": "no-store"})


@app.get("/site/{business}")
async def product_site_index(business: str):
    return await _serve_product_site_file(business)


@app.get("/site/{business}/{full_path:path}")
async def product_site_file(business: str, full_path: str):
    return await _serve_product_site_file(business, full_path)

# Per-channel subscriber registry used by /api/pub (PTY-side gateway → dashboard)
# and /api/events (dashboard → browser sidebar).  Keyed by an opaque channel id
# the chat tab generates on mount; entries auto-evict when the last subscriber
# drops AND the publisher has disconnected.
_event_channels: dict[str, set] = {}
_event_lock = asyncio.Lock()


def _resolve_chat_argv(
    resume: Optional[str] = None,
    sidecar_url: Optional[str] = None,
) -> tuple[list[str], Optional[str], Optional[dict]]:
    """Resolve the argv + cwd + env for the chat PTY.

    Default: whatever ``takyon --tui`` would run.  Tests monkeypatch this
    function to inject a tiny fake command (``cat``, ``sh -c 'printf …'``)
    so nothing has to build Node or the TUI bundle.

    Session resume is propagated via the ``TAKYON_TUI_RESUME`` env var —
    matching what ``takyon_cli.main._launch_tui`` does for the CLI path.
    Appending ``--resume <id>`` to argv doesn't work because ``ui-tui`` does
    not parse its argv.

    `sidecar_url` (when set) is forwarded as ``TAKYON_TUI_SIDECAR_URL`` so
    the spawned ``tui_gateway.entry`` can mirror dispatcher emits to the
    dashboard's ``/api/pub`` endpoint (see :func:`pub_ws`).
    """
    from takyon_cli.main import PROJECT_ROOT, _make_tui_argv

    argv, cwd = _make_tui_argv(PROJECT_ROOT / "ui-tui", tui_dev=False)
    env = os.environ.copy()
    env.setdefault("NODE_ENV", "production")
    # Browser-embedded chat should prefer stable wheel-based scrollback over
    # native terminal mouse tracking. When mouse tracking is enabled, wheel
    # events are consumed by the TUI and forwarded as terminal input, which
    # makes browser-side transcript scrolling feel broken. Keep the terminal
    # build unchanged for native CLI usage; only disable mouse tracking for
    # the dashboard PTY path.
    env.setdefault("TAKYON_TUI_DISABLE_MOUSE", "1")
    env.setdefault("TAKYON_TUI_INLINE", "1")

    if resume:
        latest_resume, _latest_path = _session_latest_descendant(resume)
        if latest_resume:
            resume = latest_resume
        env["TAKYON_TUI_RESUME"] = resume

    if sidecar_url:
        env["TAKYON_TUI_SIDECAR_URL"] = sidecar_url

    return list(argv), str(cwd) if cwd else None, env


def _build_sidecar_url(channel: str) -> Optional[str]:
    """ws:// URL the PTY child should publish events to, or None when unbound."""
    host = getattr(app.state, "bound_host", None)
    port = getattr(app.state, "bound_port", None)

    if not host or not port:
        return None

    netloc = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    qs = urllib.parse.urlencode({"token": _SESSION_TOKEN, "channel": channel})

    return f"ws://{netloc}/api/pub?{qs}"


async def _broadcast_event(channel: str, payload: str) -> None:
    """Fan out one publisher frame to every subscriber on `channel`."""
    async with _event_lock:
        subs = list(_event_channels.get(channel, ()))

    for sub in subs:
        try:
            await sub.send_text(payload)
        except Exception:
            # Subscriber went away mid-send; the /api/events finally clause
            # will remove it from the registry on its next iteration.
            pass


def _channel_or_close_code(ws: WebSocket) -> Optional[str]:
    """Return the channel id from the query string or None if invalid."""
    channel = ws.query_params.get("channel", "")

    return channel if _VALID_CHANNEL_RE.match(channel) else None


async def _ws_reject_if_host_role_disallows(ws: WebSocket) -> bool:
    if _host_role() == _HOST_ROLE_SUBUSER:
        await ws.close(code=4404)
        return True
    return False


@app.websocket("/api/pty")
async def pty_ws(ws: WebSocket) -> None:
    if await _ws_reject_if_host_role_disallows(ws):
        return
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    # --- auth + loopback check (before accept so we can close cleanly) ---
    token = ws.query_params.get("token", "")
    expected = _SESSION_TOKEN
    if not hmac.compare_digest(token.encode(), expected.encode()):
        await ws.close(code=4401)
        return

    if not _ws_auth0_session_is_allowed(ws):
        await ws.close(code=4401)
        return

    if not _ws_client_is_allowed(ws):
        await ws.close(code=4403)
        return

    principal = _resolve_dashboard_headers_principal(ws.headers)

    await ws.accept()

    # On native Windows, the POSIX PTY bridge can't be imported.  Tell the
    # client and close cleanly rather than pretending the feature works.
    if not _PTY_BRIDGE_AVAILABLE:
        await ws.send_text(
            "\r\n\x1b[31mChat unavailable: the embedded terminal requires a "
            "POSIX PTY, which native Windows Python doesn't provide.\x1b[0m\r\n"
            "\x1b[33mInstall Takyon inside WSL2 to use the dashboard's /chat "
            "tab — the rest of the dashboard works here.\x1b[0m\r\n"
        )
        await ws.close(code=1011)
        return

    # --- spawn PTY ------------------------------------------------------
    resume = ws.query_params.get("resume") or None
    channel = _channel_or_close_code(ws)
    sidecar_url = _build_sidecar_url(channel) if channel else None

    try:
        argv, cwd, env = _resolve_chat_argv(resume=resume, sidecar_url=sidecar_url)
    except SystemExit as exc:
        # _make_tui_argv calls sys.exit(1) when node/npm is missing.
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return

    if principal is not None:
        env["TAKYON_OPERATOR_USER_ID"] = str(principal.user_id)


    try:
        bridge = PtyBridge.spawn(argv, cwd=cwd, env=env)
    except PtyUnavailableError as exc:
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return
    except (FileNotFoundError, OSError) as exc:
        await ws.send_text(f"\r\n\x1b[31mChat failed to start: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return

    loop = asyncio.get_running_loop()

    # --- reader task: PTY master → WebSocket ----------------------------
    async def pump_pty_to_ws() -> None:
        while True:
            chunk = await loop.run_in_executor(
                None, bridge.read, _PTY_READ_CHUNK_TIMEOUT
            )
            if chunk is None:  # EOF
                return
            if not chunk:  # no data this tick; yield control and retry
                await asyncio.sleep(0)
                continue
            try:
                await ws.send_bytes(chunk)
            except Exception:
                return

    reader_task = asyncio.create_task(pump_pty_to_ws())

    # --- writer loop: WebSocket → PTY master ----------------------------
    try:
        while True:
            msg = await ws.receive()
            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                break
            raw = msg.get("bytes")
            if raw is None:
                text = msg.get("text")
                raw = text.encode("utf-8") if isinstance(text, str) else b""
            if not raw:
                continue

            # Resize escape is consumed locally, never written to the PTY.
            match = _RESIZE_RE.match(raw)
            if match and match.end() == len(raw):
                cols = int(match.group(1))
                rows = int(match.group(2))
                bridge.resize(cols=cols, rows=rows)
                continue

            bridge.write(raw)
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        bridge.close()


# ---------------------------------------------------------------------------
# /api/ws — JSON-RPC WebSocket sidecar for the dashboard "Chat" tab.
#
# Drives the same `tui_gateway.dispatch` surface Ink uses over stdio, so the
# dashboard can render structured metadata (model badge, tool-call sidebar,
# slash launcher, session info) alongside the xterm.js terminal that PTY
# already paints. Both transports bind to the same session id when one is
# active, so a tool.start emitted by the agent fans out to both sinks.
# ---------------------------------------------------------------------------


@app.websocket("/api/ws")
async def gateway_ws(ws: WebSocket) -> None:
    if await _ws_reject_if_host_role_disallows(ws):
        return
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await _ws_reject(ws, "/api/ws", 4403, "embedded_chat_disabled")
        return

    token = ws.query_params.get("token", "")
    if not token:
        await _ws_reject(ws, "/api/ws", 4401, "missing_token")
        return
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await _ws_reject(ws, "/api/ws", 4401, "bad_token")
        return

    auth0_reason = _ws_auth0_reject_reason(ws)
    if auth0_reason:
        await _ws_reject(ws, "/api/ws", 4401, auth0_reason)
        return

    if not _ws_client_is_allowed(ws):
        await _ws_reject(ws, "/api/ws", 4403, "client_host_rejected")
        return

    from tui_gateway.ws import handle_ws

    principal = _resolve_dashboard_headers_principal(ws.headers)
    session_id = str(ws.query_params.get("session_id", "") or "").strip()

    await ws.accept()
    await handle_ws(
        ws,
        principal=principal,
        preaccepted=True,
        session_id=session_id,
    )


@app.post("/api/tui/rpc")
async def tui_rpc(body: TuiRpcRequest, request: Request) -> dict:
    """HTTP fallback for the dashboard chat JSON-RPC transport.

    The WebSocket path remains the live stream. This endpoint intentionally
    reuses the same tui_gateway handler table so scope, slash commands, prompt
    submission, interrupts, file/status reads, and session history keep working
    when an intermediary drops WebSocket upgrades.
    """
    if _host_role() == _HOST_ROLE_SUBUSER:
        raise HTTPException(status_code=404, detail="Not Found")
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="embedded chat disabled")

    req = {
        "jsonrpc": body.jsonrpc or "2.0",
        "id": body.id,
        "method": body.method,
        "params": body.params or {},
    }

    params = dict(req.get("params") or {})
    params["_takyon_request_host"] = str(request.headers.get("host", "") or "")
    params["_takyon_request_origin"] = str(request.headers.get("origin", "") or "")
    req["params"] = params

    if req["method"] == "session.create":
        principal = _resolve_dashboard_request_principal(request)
        if principal is not None:
            params.setdefault("_takyon_operator_user_id", str(principal.user_id))
            req["params"] = params

    from tui_gateway import server as tui_server

    try:
        return await asyncio.get_running_loop().run_in_executor(
            None,
            tui_server.handle_request,
            req,
        )
    except Exception as exc:
        _log.exception("dashboard HTTP tui rpc failed for method=%s", body.method)
        return {
            "jsonrpc": "2.0",
            "id": body.id,
            "error": {"code": -32000, "message": str(exc)},
        }


# ---------------------------------------------------------------------------
# /api/pub + /api/events — chat-tab event broadcast.
#
# The PTY-side ``tui_gateway.entry`` opens /api/pub at startup (driven by
# TAKYON_TUI_SIDECAR_URL set in /api/pty's PTY env) and writes every
# dispatcher emit through it.  The dashboard fans those frames out to any
# subscriber that opened /api/events on the same channel id.  This is what
# gives the React sidebar its tool-call feed without breaking the PTY
# child's stdio handshake with Ink.
# ---------------------------------------------------------------------------


@app.websocket("/api/pub")
async def pub_ws(ws: WebSocket) -> None:
    if await _ws_reject_if_host_role_disallows(ws):
        return
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await _ws_reject(ws, "/api/pub", 4403, "embedded_chat_disabled")
        return

    token = ws.query_params.get("token", "")
    if not token:
        await _ws_reject(ws, "/api/pub", 4401, "missing_token")
        return
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await _ws_reject(ws, "/api/pub", 4401, "bad_token")
        return

    auth0_reason = _ws_auth0_reject_reason(ws)
    if auth0_reason:
        await _ws_reject(ws, "/api/pub", 4401, auth0_reason)
        return

    if not _ws_client_is_allowed(ws):
        await _ws_reject(ws, "/api/pub", 4403, "client_host_rejected")
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await _ws_reject(ws, "/api/pub", 4400, "missing_or_bad_channel")
        return

    await ws.accept()

    try:
        while True:
            await _broadcast_event(channel, await ws.receive_text())
    except WebSocketDisconnect:
        pass


@app.websocket("/api/events")
async def events_ws(ws: WebSocket) -> None:
    if await _ws_reject_if_host_role_disallows(ws):
        return
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await _ws_reject(ws, "/api/events", 4403, "embedded_chat_disabled")
        return

    token = ws.query_params.get("token", "")
    if not token:
        await _ws_reject(ws, "/api/events", 4401, "missing_token")
        return
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await _ws_reject(ws, "/api/events", 4401, "bad_token")
        return

    auth0_reason = _ws_auth0_reject_reason(ws)
    if auth0_reason:
        await _ws_reject(ws, "/api/events", 4401, auth0_reason)
        return

    if not _ws_client_is_allowed(ws):
        await _ws_reject(ws, "/api/events", 4403, "client_host_rejected")
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await _ws_reject(ws, "/api/events", 4400, "missing_or_bad_channel")
        return

    await ws.accept()

    async with _event_lock:
        _event_channels.setdefault(channel, set()).add(ws)

    try:
        while True:
            # Subscribers don't speak — the receive() just blocks until
            # disconnect so the connection stays open as long as the
            # browser holds it.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _event_lock:
            subs = _event_channels.get(channel)

            if subs is not None:
                subs.discard(ws)

                if not subs:
                    _event_channels.pop(channel, None)


def _normalise_prefix(raw: Optional[str]) -> str:
    """Normalise an X-Forwarded-Prefix header value.

    Returns a string like ``"/takyon"`` (no trailing slash) or ``""`` when
    no prefix is set / the header is malformed. We deliberately reject
    anything containing ``..`` or non-printable bytes so a hostile proxy
    can't inject HTML via the prefix.
    """
    if not raw:
        return ""
    p = raw.strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    if "//" in p or ".." in p or any(c in p for c in ('"', "'", "<", ">", " ", "\n", "\r", "\t")):
        return ""
    if len(p) > 64:
        return ""
    return p


def mount_spa(application: FastAPI):
    """Mount the built SPA. Falls back to index.html for client-side routing.

    The session token is injected into index.html via a ``<script>`` tag so
    the SPA can authenticate against protected API endpoints without a
    separate (unauthenticated) token-dispensing endpoint.

    When served behind a path-prefix reverse proxy (e.g.
    ``mission-control.tilos.com/takyon/*`` -> local Caddy -> :9119), the
    proxy injects ``X-Forwarded-Prefix: /takyon`` on every request. We
    rewrite the served ``index.html`` so absolute asset URLs (``/assets/...``)
    and the SPA's runtime ``__TAKYON_BASE_PATH__`` honour that prefix
    without rebuilding the bundle.
    """
    if not WEB_DIST.exists():
        @application.get("/{full_path:path}")
        async def no_frontend(full_path: str, request: Request):
            product_business = _business_slug_from_product_host(
                _host_without_port(request.headers.get("host", ""))
            )
            if product_business:
                return await _serve_product_site_file(product_business, full_path)
            return JSONResponse(
                {"error": "Frontend not built. Run: cd web && npm run build"},
                status_code=404,
            )
        return

    _index_path = WEB_DIST / "index.html"
    _litebulb_index_candidates = (
        WEB_DIST / "litebulb" / "litebulb.html",
        WEB_DIST / "litebulb" / "index.html",
    )

    def _serve_index(prefix: str = ""):
        """Return index.html with the session token + base-path injected.

        ``prefix`` is the normalised ``X-Forwarded-Prefix`` (e.g. ``/takyon``)
        or empty string when served at root.
        """
        html = _index_path.read_text()
        chat_js = "true" if _DASHBOARD_EMBEDDED_CHAT_ENABLED else "false"
        token_script = (
            f'<script>window.__TAKYON_SESSION_TOKEN__="{_SESSION_TOKEN}";'
            f"window.__TAKYON_DASHBOARD_EMBEDDED_CHAT__={chat_js};"
            f'window.__TAKYON_BASE_PATH__="{prefix}";</script>'
        )
        if prefix:
            # Rewrite absolute asset URLs baked into the Vite build so the
            # browser fetches them through the same proxy prefix.
            html = html.replace('href="/assets/', f'href="{prefix}/assets/')
            html = html.replace('src="/assets/', f'src="{prefix}/assets/')
            html = html.replace('href="/favicon.ico"', f'href="{prefix}/favicon.ico"')
            html = html.replace('href="/fonts/', f'href="{prefix}/fonts/')
            html = html.replace('href="/ds-assets/', f'href="{prefix}/ds-assets/')
            html = html.replace('src="/ds-assets/', f'src="{prefix}/ds-assets/')
        html = html.replace("</head>", f"{token_script}</head>", 1)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    def _serve_litebulb_index(prefix: str = ""):
        """Serve the Litebulb operator workspace as the top-level document.

        Litebulb is a self-contained HTML document. Older builds emitted
        ``index.html`` while the newer unified bundle emits ``litebulb.html``.
        Prefer the new artifact when present so ``/chat`` never serves a stale
        legacy shell after a partial or mixed build directory sync. Falls back
        to the SPA index when neither asset exists so the dashboard never
        hard-fails.
        """
        litebulb_index_path = next(
            (candidate for candidate in _litebulb_index_candidates if candidate.is_file()),
            None,
        )
        if litebulb_index_path is None:
            return _serve_index(prefix)
        html = litebulb_index_path.read_text()
        chat_js = "true" if _DASHBOARD_EMBEDDED_CHAT_ENABLED else "false"
        adapter_path = WEB_DIST / "litebulb" / "takyon-adapter.js"
        adapter_version = (
            str(int(adapter_path.stat().st_mtime_ns))
            if adapter_path.is_file()
            else "0"
        )
        token_script = (
            f'<script>window.__TAKYON_SESSION_TOKEN__="{_SESSION_TOKEN}";'
            f"window.__TAKYON_DASHBOARD_EMBEDDED_CHAT__={chat_js};"
            f'window.__TAKYON_BASE_PATH__="{prefix}";</script>'
        )
        # Served at ``/`` the page's relative ``./takyon-adapter.js`` would
        # resolve to ``/takyon-adapter.js``; point it at the real static path
        # (honouring any reverse-proxy prefix).
        html = html.replace(
            'src="./takyon-adapter.js"',
            f'src="{prefix}/litebulb/takyon-adapter.js?v={adapter_version}"',
        )
        html = html.replace('src="./assets/', f'src="{prefix}/litebulb/assets/')
        html = html.replace('href="./assets/', f'href="{prefix}/litebulb/assets/')
        html = html.replace("</head>", f"{token_script}</head>", 1)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    # When served behind a path-prefix proxy, the built CSS contains
    # absolute ``url(/fonts/...)`` and ``url(/ds-assets/...)`` references.
    # Browsers resolve those against the document origin, which means
    # under ``/takyon`` they'd hit ``mission-control.tilos.com/fonts/...``
    # (the MC Pages app), not the Takyon backend. Intercept CSS asset
    # requests BEFORE the StaticFiles mount and rewrite the absolute paths
    # when a prefix is in play.
    @application.get("/assets/{filename}.css")
    async def serve_css(filename: str, request: Request):
        css_path = WEB_DIST / "assets" / f"{filename}.css"
        if not css_path.is_file() or not css_path.resolve().is_relative_to(
            WEB_DIST.resolve()
        ):
            return JSONResponse({"error": "not found"}, status_code=404)
        prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
        css = css_path.read_text()
        if prefix:
            for asset_dir in ("/fonts/", "/fonts-terminal/", "/ds-assets/", "/assets/"):
                css = css.replace(f"url({asset_dir}", f"url({prefix}{asset_dir}")
                css = css.replace(f"url(\"{asset_dir}", f"url(\"{prefix}{asset_dir}")
                css = css.replace(f"url('{asset_dir}", f"url('{prefix}{asset_dir}")
        return Response(content=css, media_type="text/css")

    application.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @application.get("/{full_path:path}")
    async def serve_spa(full_path: str, request: Request):
        product_business = _business_slug_from_product_host(
            _host_without_port(request.headers.get("host", ""))
        )
        if product_business:
            return await _serve_product_site_file(product_business, full_path)
        request_host = _request_host(request.headers)
        skill_lab_host = request_host == _configured_skill_lab_host()
        prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
        if _DASHBOARD_EMBEDDED_CHAT_ENABLED and full_path == "":
            target_path = f"{prefix}/chat" if prefix else "/chat"
            query = str(request.url.query or "").strip()
            if query:
                target_path = f"{target_path}?{query}"
            return RedirectResponse(url=target_path, status_code=307)
        if _DASHBOARD_EMBEDDED_CHAT_ENABLED and skill_lab_host and full_path == "index.html":
            target_path = f"{prefix}/chat" if prefix else "/chat"
            query = str(request.url.query or "").strip()
            if query:
                target_path = f"{target_path}?{query}"
            return RedirectResponse(url=target_path, status_code=307)
        file_path = WEB_DIST / full_path
        # Prevent path traversal via url-encoded sequences (%2e%2e/)
        if (
            full_path
            and file_path.resolve().is_relative_to(WEB_DIST.resolve())
            and file_path.exists()
            and file_path.is_file()
        ):
            return FileResponse(file_path)
        # Operator landing: in embedded/--tui mode the business workspace IS
        # the Litebulb UI, served directly (no iframe, no React bundle).  Every
        # other route still renders the SPA shell for client-side routing.
        if _DASHBOARD_EMBEDDED_CHAT_ENABLED and full_path == "chat":
            return _serve_litebulb_index(prefix)
        if _DASHBOARD_EMBEDDED_CHAT_ENABLED and skill_lab_host:
            target_path = f"{prefix}/chat" if prefix else "/chat"
            query = str(request.url.query or "").strip()
            if query:
                target_path = f"{target_path}?{query}"
            return RedirectResponse(url=target_path, status_code=307)
        return _serve_index(prefix)


# ---------------------------------------------------------------------------
# Dashboard theme endpoints
# ---------------------------------------------------------------------------

# Built-in dashboard themes — label + description only.  The actual color
# definitions live in the frontend (web/src/themes/presets.ts).
_BUILTIN_DASHBOARD_THEMES = [
    {"name": "default",       "label": "Takyon Teal",         "description": "Classic dark teal — the canonical Takyon look"},
    {"name": "default-large", "label": "Takyon Teal (Large)", "description": "Takyon Teal with bigger fonts and roomier spacing"},
    {"name": "midnight",      "label": "Midnight",            "description": "Deep blue-violet with cool accents"},
    {"name": "ember",     "label": "Ember",          "description": "Warm crimson and bronze — forge vibes"},
    {"name": "mono",      "label": "Mono",           "description": "Clean grayscale — minimal and focused"},
    {"name": "cyberpunk", "label": "Cyberpunk",      "description": "Neon green on black — matrix terminal"},
    {"name": "rose",      "label": "Rosé",           "description": "Soft pink and warm ivory — easy on the eyes"},
]


def _parse_theme_layer(value: Any, default_hex: str, default_alpha: float = 1.0) -> Optional[Dict[str, Any]]:
    """Normalise a theme layer spec from YAML into `{hex, alpha}` form.

    Accepts shorthand (a bare hex string) or full dict form.  Returns
    ``None`` on garbage input so the caller can fall back to a built-in
    default rather than blowing up.
    """
    if value is None:
        return {"hex": default_hex, "alpha": default_alpha}
    if isinstance(value, str):
        return {"hex": value, "alpha": default_alpha}
    if isinstance(value, dict):
        hex_val = value.get("hex", default_hex)
        alpha_val = value.get("alpha", default_alpha)
        if not isinstance(hex_val, str):
            return None
        try:
            alpha_f = float(alpha_val)
        except (TypeError, ValueError):
            alpha_f = default_alpha
        return {"hex": hex_val, "alpha": max(0.0, min(1.0, alpha_f))}
    return None


_THEME_DEFAULT_TYPOGRAPHY: Dict[str, str] = {
    "fontSans": 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    "fontMono": 'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace',
    "baseSize": "15px",
    "lineHeight": "1.55",
    "letterSpacing": "0",
}

_THEME_DEFAULT_LAYOUT: Dict[str, str] = {
    "radius": "0.5rem",
    "density": "comfortable",
}

_THEME_OVERRIDE_KEYS = {
    "card", "cardForeground", "popover", "popoverForeground",
    "primary", "primaryForeground", "secondary", "secondaryForeground",
    "muted", "mutedForeground", "accent", "accentForeground",
    "destructive", "destructiveForeground", "success", "warning",
    "border", "input", "ring",
}

# Well-known named asset slots themes can populate.  Any other keys under
# ``assets.custom`` are exposed as ``--theme-asset-custom-<key>`` CSS vars
# for plugin/shell use.
_THEME_NAMED_ASSET_KEYS = {"bg", "hero", "logo", "crest", "sidebar", "header"}

# Component-style buckets themes can override.  The value under each bucket
# is a mapping from camelCase property name to CSS string; each pair emits
# ``--component-<bucket>-<kebab-property>`` on :root.  The frontend's shell
# components (Card, App header, Backdrop, etc.) consume these vars so themes
# can restyle chrome (clip-path, border-image, segmented progress, etc.)
# without shipping their own CSS.
_THEME_COMPONENT_BUCKETS = {
    "card", "header", "footer", "sidebar", "tab",
    "progress", "badge", "backdrop", "page",
}

_THEME_LAYOUT_VARIANTS = {"standard", "cockpit", "tiled"}

# Cap on customCSS length so a malformed/oversized theme YAML can't blow up
# the response payload or the <style> tag.  32 KiB is plenty for every
# practical reskin (the Strike Freedom demo is ~2 KiB).
_THEME_CUSTOM_CSS_MAX = 32 * 1024


def _normalise_theme_definition(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise a user theme YAML into the wire format `ThemeProvider`
    expects.  Returns ``None`` if the theme is unusable.

    Accepts both the full schema (palette/typography/layout) and a loose
    form with bare hex strings, so hand-written YAMLs stay friendly.
    """
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    # Palette
    palette_src = data.get("palette", {}) if isinstance(data.get("palette"), dict) else {}
    # Allow top-level `colors.background` as a shorthand too.
    colors_src = data.get("colors", {}) if isinstance(data.get("colors"), dict) else {}

    def _layer(key: str, default_hex: str, default_alpha: float = 1.0) -> Dict[str, Any]:
        spec = palette_src.get(key, colors_src.get(key))
        parsed = _parse_theme_layer(spec, default_hex, default_alpha)
        return parsed if parsed is not None else {"hex": default_hex, "alpha": default_alpha}

    palette = {
        "background": _layer("background", "#041c1c", 1.0),
        "midground": _layer("midground", "#ffe6cb", 1.0),
        "foreground": _layer("foreground", "#ffffff", 0.0),
        "warmGlow": palette_src.get("warmGlow") or data.get("warmGlow") or "rgba(255, 189, 56, 0.35)",
        "noiseOpacity": 1.0,
    }
    raw_noise = palette_src.get("noiseOpacity", data.get("noiseOpacity"))
    try:
        palette["noiseOpacity"] = float(raw_noise) if raw_noise is not None else 1.0
    except (TypeError, ValueError):
        palette["noiseOpacity"] = 1.0

    # Typography
    typo_src = data.get("typography", {}) if isinstance(data.get("typography"), dict) else {}
    typography = dict(_THEME_DEFAULT_TYPOGRAPHY)
    for key in ("fontSans", "fontMono", "fontDisplay", "fontUrl", "baseSize", "lineHeight", "letterSpacing"):
        val = typo_src.get(key)
        if isinstance(val, str) and val.strip():
            typography[key] = val

    # Layout
    layout_src = data.get("layout", {}) if isinstance(data.get("layout"), dict) else {}
    layout = dict(_THEME_DEFAULT_LAYOUT)
    radius = layout_src.get("radius")
    if isinstance(radius, str) and radius.strip():
        layout["radius"] = radius
    density = layout_src.get("density")
    if isinstance(density, str) and density in {"compact", "comfortable", "spacious"}:
        layout["density"] = density

    # Color overrides — keep only valid keys with string values.
    overrides_src = data.get("colorOverrides", {})
    color_overrides: Dict[str, str] = {}
    if isinstance(overrides_src, dict):
        for key, val in overrides_src.items():
            if key in _THEME_OVERRIDE_KEYS and isinstance(val, str) and val.strip():
                color_overrides[key] = val

    # Assets — named slots + arbitrary user-defined keys.  Values must be
    # strings (URLs or CSS ``url(...)``/``linear-gradient(...)`` expressions).
    # We don't fetch remote assets here; the frontend just injects them as
    # CSS vars.  Empty values are dropped so a theme can explicitly clear a
    # slot by setting ``hero: ""``.
    assets_out: Dict[str, Any] = {}
    assets_src = data.get("assets", {}) if isinstance(data.get("assets"), dict) else {}
    for key in _THEME_NAMED_ASSET_KEYS:
        val = assets_src.get(key)
        if isinstance(val, str) and val.strip():
            assets_out[key] = val
    custom_assets_src = assets_src.get("custom")
    if isinstance(custom_assets_src, dict):
        custom_assets: Dict[str, str] = {}
        for key, val in custom_assets_src.items():
            if (
                isinstance(key, str)
                and key.replace("-", "").replace("_", "").isalnum()
                and isinstance(val, str)
                and val.strip()
            ):
                custom_assets[key] = val
        if custom_assets:
            assets_out["custom"] = custom_assets

    # Custom CSS — raw CSS text the frontend injects as a scoped <style>
    # tag on theme apply.  Clipped to _THEME_CUSTOM_CSS_MAX to keep the
    # payload bounded.  We intentionally do NOT parse/sanitise the CSS
    # here — the dashboard is localhost-only and themes are user-authored
    # YAML in ~/.takyon/, same trust level as the config file itself.
    custom_css_val = data.get("customCSS")
    custom_css: Optional[str] = None
    if isinstance(custom_css_val, str) and custom_css_val.strip():
        custom_css = custom_css_val[:_THEME_CUSTOM_CSS_MAX]

    # Component style overrides — per-bucket dicts of camelCase CSS
    # property -> CSS string.  The frontend converts these into CSS vars
    # that shell components (Card, App header, Backdrop) consume.
    component_styles_src = data.get("componentStyles", {})
    component_styles: Dict[str, Dict[str, str]] = {}
    if isinstance(component_styles_src, dict):
        for bucket, props in component_styles_src.items():
            if bucket not in _THEME_COMPONENT_BUCKETS or not isinstance(props, dict):
                continue
            clean: Dict[str, str] = {}
            for prop, value in props.items():
                if (
                    isinstance(prop, str)
                    and prop.replace("-", "").replace("_", "").isalnum()
                    and isinstance(value, (str, int, float))
                    and str(value).strip()
                ):
                    clean[prop] = str(value)
            if clean:
                component_styles[bucket] = clean

    layout_variant_src = data.get("layoutVariant")
    layout_variant = (
        layout_variant_src
        if isinstance(layout_variant_src, str) and layout_variant_src in _THEME_LAYOUT_VARIANTS
        else "standard"
    )

    result: Dict[str, Any] = {
        "name": name,
        "label": data.get("label") or name,
        "description": data.get("description", ""),
        "palette": palette,
        "typography": typography,
        "layout": layout,
        "layoutVariant": layout_variant,
    }
    if color_overrides:
        result["colorOverrides"] = color_overrides
    if assets_out:
        result["assets"] = assets_out
    if custom_css is not None:
        result["customCSS"] = custom_css
    if component_styles:
        result["componentStyles"] = component_styles
    return result


def _discover_user_themes() -> list:
    """Scan ~/.takyon/dashboard-themes/*.yaml for user-created themes.

    Returns a list of fully-normalised theme definitions ready to ship
    to the frontend, so the client can apply them without a secondary
    round-trip or a built-in stub.
    """
    themes_dir = get_takyon_home() / "dashboard-themes"
    if not themes_dir.is_dir():
        return []
    result = []
    for f in sorted(themes_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        normalised = _normalise_theme_definition(data)
        if normalised is not None:
            result.append(normalised)
    return result


@app.get("/api/dashboard/themes")
async def get_dashboard_themes():
    """Return available themes and the currently active one.

    Built-in entries ship name/label/description only (the frontend owns
    their full definitions in `web/src/themes/presets.ts`).  User themes
    from `~/.takyon/dashboard-themes/*.yaml` ship with their full
    normalised definition under `definition`, so the client can apply
    them without a stub.
    """
    config = load_config()
    active = cfg_get(config, "dashboard", "theme", default="default")
    user_themes = _discover_user_themes()
    seen = set()
    themes = []
    for t in _BUILTIN_DASHBOARD_THEMES:
        seen.add(t["name"])
        themes.append(t)
    for t in user_themes:
        if t["name"] in seen:
            continue
        themes.append({
            "name": t["name"],
            "label": t["label"],
            "description": t["description"],
            "definition": t,
        })
        seen.add(t["name"])
    return {"themes": themes, "active": active}


class ThemeSetBody(BaseModel):
    name: str


@app.put("/api/dashboard/theme")
async def set_dashboard_theme(body: ThemeSetBody):
    """Set the active dashboard theme (persists to config.yaml)."""
    config = load_config()
    if "dashboard" not in config:
        config["dashboard"] = {}
    config["dashboard"]["theme"] = body.name
    save_config(config)
    return {"ok": True, "theme": body.name}


# ---------------------------------------------------------------------------
# Dashboard plugin system
# ---------------------------------------------------------------------------

def _discover_dashboard_plugins() -> list:
    """Scan plugins/*/dashboard/manifest.json for dashboard extensions.

    Checks three plugin sources (same as takyon_cli.plugins):
    1. User plugins:    ~/.takyon/plugins/<name>/dashboard/manifest.json
    2. Bundled plugins: <repo>/plugins/<name>/dashboard/manifest.json  (memory/, etc.)
    3. Project plugins: ./.takyon/plugins/  (only if TAKYON_ENABLE_PROJECT_PLUGINS)
    """
    plugins = []
    seen_names: set = set()

    from takyon_cli.plugins import get_bundled_plugins_dir
    bundled_root = get_bundled_plugins_dir()
    search_dirs = [
        (get_takyon_home() / "plugins", "user"),
        (bundled_root / "memory", "bundled"),
        (bundled_root, "bundled"),
    ]
    if os.environ.get("TAKYON_ENABLE_PROJECT_PLUGINS"):
        search_dirs.append((Path.cwd() / ".takyon" / "plugins", "project"))

    for plugins_root, source in search_dirs:
        if not plugins_root.is_dir():
            continue
        for child in sorted(plugins_root.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "dashboard" / "manifest.json"
            if not manifest_file.exists():
                continue
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                name = data.get("name", child.name)
                if name in seen_names:
                    continue
                seen_names.add(name)
                # Tab options: ``path`` + ``position`` for a new tab, optional
                # ``override`` to replace a built-in route, and ``hidden`` to
                # register the plugin component/slots without adding a tab
                # (useful for slot-only plugins like a header-crest injector).
                raw_tab = data.get("tab", {}) if isinstance(data.get("tab"), dict) else {}
                tab_info = {
                    "path": raw_tab.get("path", f"/{name}"),
                    "position": raw_tab.get("position", "end"),
                }
                override_path = raw_tab.get("override")
                if isinstance(override_path, str) and override_path.startswith("/"):
                    tab_info["override"] = override_path
                if bool(raw_tab.get("hidden")):
                    tab_info["hidden"] = True
                # Slots: list of named slot locations this plugin populates.
                # The frontend exposes ``registerSlot(pluginName, slotName, Component)``
                # on window; plugins with non-empty slots call it from their JS bundle.
                slots_src = data.get("slots")
                slots: List[str] = []
                if isinstance(slots_src, list):
                    slots = [s for s in slots_src if isinstance(s, str) and s]
                plugins.append({
                    "name": name,
                    "label": data.get("label", name),
                    "description": data.get("description", ""),
                    "icon": data.get("icon", "Puzzle"),
                    "version": data.get("version", "0.0.0"),
                    "tab": tab_info,
                    "slots": slots,
                    "entry": data.get("entry", "dist/index.js"),
                    "css": data.get("css"),
                    "has_api": bool(data.get("api")),
                    "source": source,
                    "_dir": str(child / "dashboard"),
                    "_api_file": data.get("api"),
                })
            except Exception as exc:
                _log.warning("Bad dashboard plugin manifest %s: %s", manifest_file, exc)
                continue
    return plugins


# Cache discovered plugins per-process (refresh on explicit re-scan).
_dashboard_plugins_cache: Optional[list] = None


def _get_dashboard_plugins(force_rescan: bool = False) -> list:
    global _dashboard_plugins_cache
    if _dashboard_plugins_cache is None or force_rescan:
        _dashboard_plugins_cache = _discover_dashboard_plugins()
    elif _dashboard_plugins_cache:
        if any(not Path(p["_dir"]).is_dir() for p in _dashboard_plugins_cache):
            _dashboard_plugins_cache = _discover_dashboard_plugins()
    return _dashboard_plugins_cache


@app.get("/api/dashboard/plugins")
async def get_dashboard_plugins():
    """Return discovered dashboard plugins (excludes user-hidden ones)."""
    plugins = _get_dashboard_plugins()
    # Read user's hidden plugins list from config.
    config = load_config()
    hidden: list = cfg_get(config, "dashboard", "hidden_plugins", default=[]) or []
    # Strip internal fields before sending to frontend and filter out hidden.
    return [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in plugins
        if p["name"] not in hidden
    ]


@app.get("/api/dashboard/plugins/rescan")
async def rescan_dashboard_plugins():
    """Force re-scan of dashboard plugins."""
    plugins = _get_dashboard_plugins(force_rescan=True)
    return {"ok": True, "count": len(plugins)}


class _AgentPluginInstallBody(BaseModel):
    identifier: str
    force: bool = False
    enable: bool = True


def _strip_dashboard_manifest(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in p.items() if not k.startswith("_")}


def _merged_plugins_hub() -> Dict[str, Any]:
    """Agent discovery + dashboard manifests + optional provider picker metadata."""
    from takyon_cli.plugins_cmd import (
        _discover_all_plugins,
        _get_current_context_engine,
        _get_current_memory_provider,
        _discover_context_engines,
        _discover_memory_providers,
        _get_disabled_set,
        _get_enabled_set,
        _read_manifest as _read_plugin_manifest_at,
    )

    dashboard_list = _get_dashboard_plugins()
    dash_by_name = {str(p["name"]): p for p in dashboard_list}

    disabled_set = _get_disabled_set()
    enabled_set = _get_enabled_set()

    # Read user-hidden plugins from config for the user_hidden field.
    config = load_config()
    hidden_plugins: list = cfg_get(config, "dashboard", "hidden_plugins", default=[]) or []

    plugins_root_resolved = (get_takyon_home() / "plugins").resolve()
    rows: List[Dict[str, Any]] = []

    for name, version, description, source, dir_str in _discover_all_plugins():
        if name in disabled_set:
            runtime_status = "disabled"
        elif name in enabled_set:
            runtime_status = "enabled"
        else:
            runtime_status = "inactive"

        dir_path = Path(dir_str)
        dm = dash_by_name.get(name)
        has_dash_manifest = dm is not None or (dir_path / "dashboard" / "manifest.json").exists()

        under_user_tree = False
        try:
            dir_path.resolve().relative_to(plugins_root_resolved)
            under_user_tree = True
        except ValueError:
            pass

        can_remove_update = (
            source in {"user", "git"} and under_user_tree and Path(dir_str).is_dir()
        )

        # Check if this plugin provides tools that require auth
        auth_required = False
        auth_command = ""
        manifest_data = _read_plugin_manifest_at(dir_path)
        provides_tools = manifest_data.get("provides_tools") or []
        if provides_tools:
            try:
                from tools.registry import registry
                for tname in provides_tools:
                    entry = registry.get_entry(tname)
                    if entry and entry.check_fn and not entry.check_fn():
                        auth_required = True
                        auth_command = f"takyon auth {name}"
                        break
            except Exception:
                pass

        rows.append({
            "name": name,
            "version": version or "",
            "description": description or "",
            "source": source,
            "runtime_status": runtime_status,
            "has_dashboard_manifest": has_dash_manifest,
            "dashboard_manifest": _strip_dashboard_manifest(dm) if dm else None,
            "path": dir_str,
            "can_remove": can_remove_update,
            "can_update_git": can_remove_update and (Path(dir_str) / ".git").exists(),
            "auth_required": auth_required,
            "auth_command": auth_command,
            "user_hidden": name in hidden_plugins,
        })

    agent_names = {r["name"] for r in rows}
    orphan_dashboard = [
        _strip_dashboard_manifest(p)
        for p in dashboard_list
        if str(p["name"]) not in agent_names
    ]

    memory_providers: List[Dict[str, str]] = []
    try:
        for n, desc in _discover_memory_providers():
            memory_providers.append({"name": n, "description": desc})
    except Exception:
        memory_providers = []

    context_engines: List[Dict[str, str]] = []
    try:
        for n, desc in _discover_context_engines():
            context_engines.append({"name": n, "description": desc})
    except Exception:
        context_engines = []

    return {
        "plugins": rows,
        "orphan_dashboard_plugins": orphan_dashboard,
        "providers": {
            "memory_provider": _get_current_memory_provider() or "",
            "memory_options": memory_providers,
            "context_engine": _get_current_context_engine(),
            "context_options": context_engines,
        },
    }


@app.get("/api/dashboard/plugins/hub")
async def get_plugins_hub(request: Request):
    """Unified agent plugins + dashboard extension metadata (session protected)."""
    _require_token(request)
    try:
        return _merged_plugins_hub()
    except Exception as exc:
        _log.warning("plugins/hub failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to build plugins hub.") from exc


@app.post("/api/dashboard/agent-plugins/install")
async def post_agent_plugin_install(request: Request, body: _AgentPluginInstallBody):
    _require_token(request)
    from takyon_cli.plugins_cmd import dashboard_install_plugin

    result = dashboard_install_plugin(
        body.identifier.strip(),
        force=body.force,
        enable=body.enable,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "Install failed.",
        )
    _get_dashboard_plugins(force_rescan=True)
    # Strip internal paths from the response
    result.pop("after_install_path", None)
    return result


def _validate_plugin_name(name: str) -> str:
    """Reject path-traversal attempts in plugin name URL parameters."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid plugin name.")
    return name


@app.post("/api/dashboard/agent-plugins/{name}/enable")
async def post_agent_plugin_enable(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from takyon_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

    result = dashboard_set_agent_plugin_enabled(name, enabled=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Enable failed.")
    return result


@app.post("/api/dashboard/agent-plugins/{name}/disable")
async def post_agent_plugin_disable(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from takyon_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

    result = dashboard_set_agent_plugin_enabled(name, enabled=False)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Disable failed.")
    return result


@app.post("/api/dashboard/agent-plugins/{name}/update")
async def post_agent_plugin_update(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from takyon_cli.plugins_cmd import dashboard_update_user_plugin

    result = dashboard_update_user_plugin(name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Update failed.")
    _get_dashboard_plugins(force_rescan=True)
    return result


@app.delete("/api/dashboard/agent-plugins/{name}")
async def delete_agent_plugin(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from takyon_cli.plugins_cmd import dashboard_remove_user_plugin

    result = dashboard_remove_user_plugin(name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Remove failed.")
    _get_dashboard_plugins(force_rescan=True)
    return result


class _PluginProvidersPutBody(BaseModel):
    memory_provider: Optional[str] = None
    context_engine: Optional[str] = None


@app.put("/api/dashboard/plugin-providers")
async def put_plugin_providers(request: Request, body: _PluginProvidersPutBody):
    """Persist memory provider / context engine selection (writes config.yaml)."""
    _require_token(request)
    from takyon_cli.plugins_cmd import (
        _save_context_engine,
        _save_memory_provider,
    )

    if body.memory_provider is not None:
        _save_memory_provider(body.memory_provider)
    if body.context_engine is not None:
        _save_context_engine(body.context_engine)
    return {"ok": True}


class _PluginVisibilityBody(BaseModel):
    hidden: bool


@app.post("/api/dashboard/plugins/{name}/visibility")
async def post_plugin_visibility(request: Request, name: str, body: _PluginVisibilityBody):
    """Toggle a plugin's sidebar visibility (persists to config.yaml dashboard.hidden_plugins)."""
    _require_token(request)
    name = _validate_plugin_name(name)

    config = load_config()
    if "dashboard" not in config or not isinstance(config.get("dashboard"), dict):
        config["dashboard"] = {}
    hidden_list: list = config["dashboard"].get("hidden_plugins") or []
    if not isinstance(hidden_list, list):
        hidden_list = []

    if body.hidden and name not in hidden_list:
        hidden_list.append(name)
    elif not body.hidden and name in hidden_list:
        hidden_list.remove(name)

    config["dashboard"]["hidden_plugins"] = hidden_list
    save_config(config)
    return {"ok": True, "name": name, "hidden": body.hidden}


@app.get("/dashboard-plugins/{plugin_name}/{file_path:path}")
async def serve_plugin_asset(plugin_name: str, file_path: str):
    """Serve static assets from a dashboard plugin directory.

    Only serves files from the plugin's ``dashboard/`` subdirectory.
    Path traversal is blocked by checking ``resolve().is_relative_to()``.
    """
    plugins = _get_dashboard_plugins()
    plugin = next((p for p in plugins if p["name"] == plugin_name), None)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")

    base = Path(plugin["_dir"])
    target = (base / file_path).resolve()

    if not target.is_relative_to(base.resolve()):
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Guess content type
    suffix = target.suffix.lower()
    content_types = {
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".html": "text/html",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
    }
    media_type = content_types.get(suffix, "application/octet-stream")
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


def _mount_plugin_api_routes():
    """Import and mount backend API routes from plugins that declare them.

    Each plugin's ``api`` field points to a Python file that must expose
    a ``router`` (FastAPI APIRouter).  Routes are mounted under
    ``/api/plugins/<name>/``.
    """
    for plugin in _get_dashboard_plugins():
        api_file_name = plugin.get("_api_file")
        if not api_file_name:
            continue
        api_path = Path(plugin["_dir"]) / api_file_name
        if not api_path.exists():
            _log.warning("Plugin %s declares api=%s but file not found", plugin["name"], api_file_name)
            continue
        try:
            module_name = f"takyon_dashboard_plugin_{plugin['name']}"
            spec = importlib.util.spec_from_file_location(module_name, api_path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE exec_module so pydantic/FastAPI
            # can resolve forward references (e.g. models defined in a file
            # that uses `from __future__ import annotations`). Without this,
            # TypeAdapter lazy-build fails at first request with
            # "is not fully defined" because the module namespace isn't
            # reachable by name for string-annotation resolution.
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
            router = getattr(mod, "router", None)
            if router is None:
                _log.warning("Plugin %s api file has no 'router' attribute", plugin["name"])
                continue
            app.include_router(router, prefix=f"/api/plugins/{plugin['name']}")
            _log.info("Mounted plugin API routes: /api/plugins/%s/", plugin["name"])
        except Exception as exc:
            _log.warning("Failed to load plugin %s API routes: %s", plugin["name"], exc)


def _mount_postgres_runtime_routes() -> None:
    """Expose the existing Postgres control-plane/gateway routers from the live dashboard host."""
    global _POSTGRES_RUNTIME_ROUTES_MOUNTED
    if _POSTGRES_RUNTIME_ROUTES_MOUNTED:
        return
    try:
        from plugins.takyon.core import _db_backend

        if _db_backend() != "postgres":
            return

        import psycopg

        from plugins.takyon.ai_gateway import build_ai_gateway_router, get_gateway_conn
        from plugins.takyon.control_api import build_control_router, get_control_conn
        from plugins.takyon.creative_gateway import build_creative_gateway_router
        from plugins.takyon.runtime_app import RuntimeNotConfigured

        try:
            resolved_url = _resolve_runtime_database_url()
        except RuntimeNotConfigured:
            _log.warning(
                "Postgres backend enabled but no DATABASE_URL is configured; "
                "skipping /v1, /internal/ai-gateway, and /internal/creative-gateway mount"
            )
            return

        def control_conn():
            conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
            try:
                yield conn
            finally:
                conn.close()

        app.include_router(build_control_router())
        app.include_router(build_ai_gateway_router())
        app.include_router(build_creative_gateway_router())
        app.dependency_overrides[get_control_conn] = control_conn
        app.dependency_overrides[get_gateway_conn] = control_conn
        _POSTGRES_RUNTIME_ROUTES_MOUNTED = True
        _log.info(
            "Mounted Postgres control, AI gateway, and creative gateway routers into the dashboard host."
        )
    except Exception as exc:  # noqa: BLE001 - do not prevent the dashboard from starting
        _log.warning("Failed to mount Postgres control/gateway routers: %s", exc)


# Mount the Postgres runtime routers before plugin routes and the SPA catch-all.
_mount_postgres_runtime_routes()

# Mount plugin API routes before the SPA catch-all.
_mount_plugin_api_routes()

mount_spa(app)


def start_server(
    host: str = "127.0.0.1",
    port: int = 9119,
    open_browser: bool = True,
    allow_public: bool = False,
    *,
    embedded_chat: bool = False,
):
    """Start the web UI server."""
    import uvicorn

    global _DASHBOARD_EMBEDDED_CHAT_ENABLED
    _DASHBOARD_EMBEDDED_CHAT_ENABLED = embedded_chat

    auth0_cfg = _auth0_config()
    auth0_all_hosts = bool(auth0_cfg and auth0_cfg.force)
    _LOCALHOST = ("127.0.0.1", "localhost", "::1")
    if host not in _LOCALHOST and not allow_public and not auth0_all_hosts:
        raise SystemExit(
            f"Refusing to bind to {host} — the dashboard exposes API keys "
            f"and config without robust authentication.\n"
            f"Use --insecure to override (NOT recommended on untrusted networks), "
            f"or set TAKYON_DASHBOARD_AUTH0=1 to require Auth0 on every host."
        )
    if host not in _LOCALHOST:
        if auth0_all_hosts:
            _log.info("Binding to %s with Auth0 required on every host.", host)
        else:
            _log.warning(
                "Binding to %s with --insecure — dashboard Auth0 is only "
                "host-scoped unless TAKYON_DASHBOARD_AUTH0=1 is set.", host,
            )

    # Record the bound host so host_header_middleware can validate incoming
    # Host headers against it. Defends against DNS rebinding (GHSA-ppp5-vxwm-4cf7).
    # bound_port is also stashed so /api/pty can build the back-WS URL the
    # PTY child uses to publish events to the dashboard sidebar.
    app.state.bound_host = host
    app.state.bound_port = port
    _configure_local_product_publish(host, port)
    role = _host_role()

    # Phase 8 serving flip: on the Postgres backend, idempotently seed the single platform owner so
    # the shared control plane has a resolvable owner for any business this runtime creates. Guarded
    # no-op off Postgres; never raises (it must not stop the dashboard from binding).
    if role != _HOST_ROLE_SUBUSER:
        _seed_platform_owner_if_postgres()
        _start_dashboard_worker_if_postgres()

    if open_browser:
        import webbrowser

        # On headless Linux (no DISPLAY or WAYLAND_DISPLAY) some registered
        # browsers are TUI programs (links, lynx, www-browser) that try to
        # take over the terminal.  That can send SIGHUP to the server process
        # and cause an immediate exit even though uvicorn bound successfully.
        # Skip the auto-open attempt on headless systems and let the user
        # open the URL manually.  macOS and Windows are always considered
        # display-capable.
        _has_display = (
            sys.platform != "linux"
            or bool(os.environ.get("DISPLAY"))
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )

        if _has_display:
            def _open():
                try:
                    time.sleep(1.0)
                    webbrowser.open(f"http://{host}:{port}")
                except Exception:
                    pass

            threading.Thread(target=_open, daemon=True).start()
        else:
            _log.debug(
                "Skipping browser-open: no DISPLAY or WAYLAND_DISPLAY detected "
                "(headless Linux). Pass --no-open to suppress this detection."
            )

    print(f"  Takyon Web UI → http://{host}:{port}")
    print(f"  Host role → {role}")
    if auth0_cfg:
        public_host = _configured_public_host()
        scope = "all hosts" if auth0_cfg.force else (public_host or "configured public host")
        allowed = ", ".join(auth0_cfg.allowed_domains) or "all Auth0 users"
        print(f"  Auth0 gate → {scope} ({allowed})")
    # proxy_headers=False so _ws_client_is_allowed sees the real connection peer
    # rather than X-Forwarded-For's rewritten value (which would defeat the
    # loopback gate when behind a reverse proxy).
    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=False)
