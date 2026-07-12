"""Tests for the takyon-meta-ads-v2 hybrid skill handlers.

The module under test (``plugins.takyon.meta_ads_v2``) implements the proven two-token
Meta launch flow:

  * media bytes are uploaded with the SYSTEM-USER Graph token via ``meta_graph``
    (``act_<id>/advideos`` for video, ``act_<id>/adimages`` for image);
  * the ad OBJECTS (creative, campaign, ad set, ad) are created with the MCP OAuth
    token via ``meta_mcp.call_tool``;
  * activation (campaign -> adset -> ad ``ACTIVE``) only happens in ``mode='live'``
    on a non-test business, otherwise everything is left PAUSED;
  * a truthful receipt is written to ``distribution/meta-ads/<slug>/receipt.json``.

These tests are intentionally self-contained: rather than importing the heavy real
``plugins.takyon.core`` (which needs Postgres + the safebox broker), we install
lightweight in-process fakes for ``plugins.takyon.core``, ``plugins.takyon.meta_mcp``
and ``plugins.takyon.meta_graph`` into ``sys.modules`` *before* importing the module
under test, so ``meta_ads_v2``'s ``from . import core, meta_mcp, meta_graph`` binds to
the fakes. Every external Meta call (Graph + MCP) is captured so we can assert the
call sequence without touching the network.

The module under test is loaded from the handoff source tree (the package is a
drop-in replacement that has not necessarily been copied into an installed
``plugins.takyon`` package yet), so the fakes are registered under the fully-qualified
names the module imports.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Locate the module under test inside the handoff tree.
# --------------------------------------------------------------------------- #
# tests/plugins/test_takyon_meta_ads_v2.py -> handoff/plugins/takyon/meta_ads_v2.py
_HANDOFF_ROOT = Path(__file__).resolve().parents[2]
_META_ADS_V2_PATH = _HANDOFF_ROOT / "plugins" / "takyon" / "meta_ads_v2.py"


# --------------------------------------------------------------------------- #
# Fakes for the three dependency modules the handler imports.
# --------------------------------------------------------------------------- #
class _FakeTakyonError(RuntimeError):
    """Stand-in for ``core.TakyonError`` (which subclasses RuntimeError)."""


class _FakeInsufficientCreativeCredits(_FakeTakyonError):
    def __init__(self, *, requested_credits=0, available_credits=0, **_):
        self.requested_credits = requested_credits
        self.available_credits = available_credits
        super().__init__(
            f"insufficient creative credits: need {requested_credits}, have {available_credits}"
        )


class _FakeCreativeCreditBudgetExceeded(_FakeTakyonError):
    def __init__(self, *, budget_bucket="meta", **_):
        self.budget_bucket = budget_bucket
        super().__init__(f"creative credit budget exceeded for bucket {budget_bucket}")


class _FakeStore:
    """Resolves business-relative paths under ``root/businesses/<slug>/``."""

    def __init__(self, root: Path):
        self.root = Path(root)
        # Records every workspace-remote sync the handler triggers after a write.
        self.synced_businesses: list[str] = []
        self.sync_status = "synced"

    def _resolve_business_file(self, business: str, rel: str, **_) -> Path:
        return self.root / "businesses" / business / rel

    def _sync_business_workspace_remote(self, business: str, **_) -> str:
        # The real store pushes the business workspace to its remote after a write;
        # the fake just records the call so tests stay offline.
        self.synced_businesses.append(business)
        return self.sync_status


class _FakeSafebox:
    """Records every resolution and returns canned token values."""

    def __init__(self, graph_rec: "_GraphRecorder", mcp_rec: "_MCPRecorder"):
        self.graph_rec = graph_rec
        self.mcp_rec = mcp_rec
        self.values: dict[str, str] = {}
        self.calls: list[tuple[str, ...]] = []

    def first_env_backed_value(self, *keys: str) -> str:
        self.calls.append(tuple(keys))
        for key in keys:
            if key in self.values:
                return self.values[key]
        return ""

    def meta_config(self) -> dict:
        return {
            "token": "",
            "has_token": True,
            "has_mcp_oauth_token": True,
            "mcp_endpoint": "https://mcp.facebook.com/ads",
            "version": "v21.0",
            "ad_account_id": self.values.get("META_AD_ACCOUNT_ID", ""),
            "page_id": self.values.get("META_PAGE_ID", ""),
            "instagram_user_id": self.values.get("META_INSTAGRAM_ID", ""),
        }

    def meta_graph_upload_video(self, *, ad_account_id: str, video_bytes: bytes, name: str, poll=True, timeout=180.0) -> str:
        self.graph_rec.uploads_video.append(
            {
                "token": "",
                "ad_account_id": ad_account_id,
                "path": f"act_{str(ad_account_id).replace('act_', '')}/advideos",
                "bytes": video_bytes,
                "name": name,
            }
        )
        return "video-1"

    def meta_graph_upload_image(self, *, ad_account_id: str, image_bytes: bytes, name: str, timeout=180.0) -> dict:
        self.graph_rec.uploads_image.append(
            {
                "token": "",
                "ad_account_id": ad_account_id,
                "path": f"act_{str(ad_account_id).replace('act_', '')}/adimages",
                "bytes": image_bytes,
                "name": name,
            }
        )
        return {"hash": "image-hash-1", "url": "https://scontent.example/img.png"}

    def meta_mcp_call(self, *, tool_name: str, arguments=None, timeout=60.0) -> dict:
        self.mcp_rec.calls.append(
            {"tool": tool_name, "arguments": dict(arguments or {}), "token": "", "endpoint": None}
        )
        payload = self.mcp_rec.responses.get(tool_name, {"id": f"{tool_name}-id"})
        return {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "structuredContent": payload,
            **payload,
        }

    def meta_graph_forward(self, *, method: str, path: str, params=None, host="graph.facebook.com", timeout=60.0) -> dict:
        clean_params = dict(params or {})
        self.graph_rec.graph_calls.append(
            {"method": method, "path": path, "params": clean_params, "token": "", "version": "v21.0", "host": host}
        )
        clean_method = str(method or "").upper()
        if clean_method == "GET":
            return dict(self.graph_rec.graph_forward_get_response)
        if "daily_budget" in clean_params:
            self.graph_rec.budget_calls.append({"token": "", "object_id": path, "daily_budget_cents": clean_params.get("daily_budget")})
            return {"success": True, "id": path, "daily_budget": clean_params.get("daily_budget")}
        if "status" in clean_params:
            self.graph_rec.status_calls.append({"token": "", "object_id": path, "status": clean_params.get("status")})
            return {"success": True, "id": path, "status": clean_params.get("status")}
        return {"success": True}

    def meta_graph_ensure_custom_conversion(self, *, ad_account_id: str, name: str, rule: str, custom_event_type: str, event_source_id: str = "", timeout=60.0) -> dict:
        self.graph_rec.custom_conversions.append(
            {
                "token": "",
                "ad_account_id": ad_account_id,
                "name": name,
                "rule": rule,
                "custom_event_type": custom_event_type,
                "event_source_id": event_source_id,
            }
        )
        return {
            "id": "custom-conv-1", "name": name, "existed": False, "verified": True,
            "custom_event_type": str(custom_event_type).upper(),
            "pixel_id": str(event_source_id), "rule": rule,
        }


def _build_fake_core(tmp_path: Path, graph_rec: "_GraphRecorder", mcp_rec: "_MCPRecorder") -> types.ModuleType:
    """Construct a fake ``plugins.takyon.core`` module the handler can call."""

    mod = types.ModuleType("plugins.takyon.core")

    store = _FakeStore(tmp_path)
    safebox = _FakeSafebox(graph_rec, mcp_rec)

    # Token aliases the proven flow relies on.
    safebox.values["META_SYSTEM_USER_ACCESS_TOKEN"] = "sut-graph-token"
    safebox.values["META_MCP_OAUTH_TOKEN"] = "mcp-oauth-token"
    safebox.values["META_AD_ACCOUNT_ID"] = "123456"
    safebox.values["META_PAGE_ID"] = "654321"

    # Per-business mode registry. Default is "test" so an un-registered business never
    # accidentally makes external calls.
    mode_registry: dict[str, str] = {}

    # Records of credit-guard activity for assertions.
    reservations: list[dict] = []
    commits: list[dict] = []
    releases: list[dict] = []
    budget_authz_calls: list[dict] = []

    def _store():
        return store

    def tool_result(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def tool_error(message, *, success=False, **extra) -> str:
        # Mirrors core.tool_error: a JSON envelope with success=False and the message.
        payload = {"success": bool(success), "error": str(message)}
        payload.update({k: v for k, v in extra.items() if v is not None})
        return json.dumps(payload, ensure_ascii=False)

    def _business_mode(store_arg, business: str) -> str:
        # The handler calls ``core._business_mode(store, business)`` (two args); the
        # fake ignores the store handle and reads the per-business registry.
        return mode_registry.get(business, "test")

    def _resolved_business_slug(args, *, required=False) -> str:
        slug = str((args or {}).get("business") or "").strip()
        if not slug and required:
            raise _FakeTakyonError("business is required")
        return slug

    def _file_slug(value: str, fallback: str = "") -> str:
        raw = str(value or "").strip().lower()
        out = []
        prev_dash = False
        for ch in raw:
            if ch.isalnum():
                out.append(ch)
                prev_dash = False
            else:
                if not prev_dash:
                    out.append("-")
                    prev_dash = True
        slug = "".join(out).strip("-")
        return slug or str(fallback or "").strip() or "item"

    def _now() -> str:
        return "2026-06-26T00:00:00Z"

    def _atomic_write_text(path, text) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _read_existing_receipt(path, idempotency_key):
        p = Path(path)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        # Match the real helper: only return the prior receipt when the idempotency
        # key lines up (None means "no usable prior receipt").
        if idempotency_key and str(data.get("idempotency_key") or "") != str(idempotency_key):
            return None
        return data

    def _meta_int_metric(value) -> int:
        raw = str(value if value is not None else "").strip()
        if not raw:
            return 0
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return 0

    _purchase_action_types = ("omni_purchase", "offsite_conversion.fb_pixel_purchase", "purchase")

    def _first_action_metric(entries, action_types):
        if not isinstance(entries, list):
            return None
        by_type = {}
        for entry in entries:
            if isinstance(entry, dict):
                at = str(entry.get("action_type") or "").strip().lower()
                if at and at not in by_type:
                    by_type[at] = entry.get("value")
        for wanted in action_types:
            if wanted in by_type:
                try:
                    return float(str(by_type[wanted]).strip())
                except (TypeError, ValueError):
                    return None
        return None

    # REAL aggregator — the fake module delegates instead of maintaining a duplicate
    # implementation that can drift from the code under test (review 2026-07-12).
    from plugins.takyon.core import (
        _meta_aggregate_insights_rows as _real_meta_aggregate_insights_rows,
        _META_PURCHASE_ACTION_TYPES as _real_meta_purchase_action_types,
    )

    def _meta_aggregate_insights_rows(rows, *, purchase_action_types):
        return _real_meta_aggregate_insights_rows(
            rows, purchase_action_types=purchase_action_types)

    class _FakeCreativeCreditBackend:
        InsufficientCreativeCredits = _FakeInsufficientCreativeCredits

    def _creative_credit_backend():
        return _FakeCreativeCreditBackend

    def _reserve_creative_credits(business, *, action, reservation_key, budget_bucket="meta", metadata=None, **_):
        record = {
            "business": business,
            "action": action,
            "reservation_key": reservation_key,
            "budget_bucket": budget_bucket,
            "metadata": metadata or {},
        }
        reservations.append(record)
        return {
            "reservation_key": reservation_key,
            "requested_credits": 1,
            "balance_credits": 999,
            "reserved_credits": 999,
            "budget_bucket": budget_bucket,
        }

    def _commit_creative_credits(reservation_key, *, action, budget_bucket="meta", metadata=None, **_):
        record = {
            "reservation_key": reservation_key,
            "action": action,
            "budget_bucket": budget_bucket,
            "metadata": metadata or {},
        }
        commits.append(record)
        return {"reservation_key": reservation_key, "committed": True}

    def _release_creative_credits(reservation_key, *, action="", budget_bucket="meta", metadata=None, **_):
        record = {
            "reservation_key": reservation_key,
            "action": action,
            "budget_bucket": budget_bucket,
            "metadata": metadata or {},
        }
        releases.append(record)
        return {"reservation_key": reservation_key, "released": True}

    def _assert_ad_set_budget_authorized(
        *,
        channel,
        business,
        slug,
        target_id,
        daily_budget_cents,
        safety_cap_cents=0,
    ):
        budget_authz_calls.append(
            {
                "channel": channel,
                "business": business,
                "slug": slug,
                "target_id": target_id,
                "daily_budget_cents": daily_budget_cents,
                "safety_cap_cents": safety_cap_cents,
            }
        )

    # Pixel site-install fakes (surface flag flip + live-dist inject + edge republish),
    # mirroring core's _surface_enable_meta_pixel / _meta_pixel_config /
    # _product_live_current_root / _inject_meta_pixel_snippet / _republish_live_dist_to_r2.
    pixel_site: dict = {"surface_flips": [], "injections": [], "republishes": []}

    def _surface_enable_meta_pixel(business):
        pixel_site["surface_flips"].append(business)
        return {"enabled": True, "changed": True}

    def _meta_pixel_config():
        if "config" in pixel_site:
            return dict(pixel_site["config"])
        return {"enabled": True, "pixel_id": "PIX-TEST-1", "script_src": ""}

    def _product_live_current_root(business):
        root = Path(tmp_path) / "live" / business / "current"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _inject_meta_pixel_snippet(site_root, *, pixel_id, script_src=""):
        pixel_site["injections"].append({"root": str(site_root), "pixel_id": pixel_id})
        index = Path(site_root) / "index.html"
        if not index.is_file():
            return False
        html = index.read_text(encoding="utf-8")
        if pixel_id not in html:
            index.write_text(
                html.replace("</head>", f"<script>fbq('init','{pixel_id}')</script></head>"),
                encoding="utf-8")
        return True

    def _republish_live_dist_to_r2(business, live_root):
        pixel_site["republishes"].append({"business": business, "live_root": str(live_root)})
        return {"status": "published", "live_build_id": "build-test-1", "blocker": ""}

    # Media-spend budget rail (reddit-parity): the launch path derives a bounded schedule from
    # remaining channel credits, reserves them as the campaign's budget authority, and registers
    # the campaign in the ad-spend policy registry; sync settles at the cap. Faithful-but-small
    # stand-ins mirroring core's signatures/return shapes, with recorded calls for assertions.
    channel_spend: dict = {"reservations": [], "releases": [], "settles": []}
    ad_spend_policies: dict = {}

    def _creative_credit_int(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _creative_credit_budget_snapshot(business):
        return {"channels": {"meta": {"remaining_credits": mod._test_meta_channel_credits}}}

    def _ad_channel_live_media_spend_credits(channel, remaining_channel_credits, *, setup_credits=0):
        credits = max(0, _creative_credit_int(remaining_channel_credits) - _creative_credit_int(setup_credits))
        if credits <= 0:
            raise _FakeTakyonError(f"{channel} channel credits are fully consumed")
        return credits

    def _parse_iso_datetime(value):
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value)) if value else None
        except (TypeError, ValueError):
            return None

    def _derive_ad_spend_schedule(*, channel, reserved_credits, requested_daily_budget_usd=None,
                                  requested_start_at=None, requested_end_at=None):
        total = _creative_credit_int(reserved_credits)
        if total <= 0:
            raise _FakeTakyonError(f"{channel} launch requires at least 1 reserved credit")
        if requested_daily_budget_usd not in (None, ""):
            daily = int(round(float(requested_daily_budget_usd) * 100))
        else:
            daily = min(total, 1000)
        daily = max(1, min(daily, total))
        start = _parse_iso_datetime(requested_start_at) or (datetime.now(timezone.utc) + timedelta(minutes=5))
        days = max(1, (total + daily - 1) // daily)
        end = _parse_iso_datetime(requested_end_at) or (start + timedelta(days=days))
        return {
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "day_count": days,
            "daily_budget_cents": daily,
            "daily_budget_usd": round(daily / 100.0, 2),
            "total_budget_cents": total,
            "total_budget_usd": round(total / 100.0, 2),
        }

    def _reserve_channel_spend_credits(business, *, channel, requested_credits, reservation_key, metadata=None):
        entry = {"business": business, "channel": channel,
                 "credits": _creative_credit_int(requested_credits),
                 "reservation_key": reservation_key, "metadata": dict(metadata or {})}
        channel_spend["reservations"].append(entry)
        return {"success": True, **entry}

    def _release_channel_spend_credits(reservation_key, *, business, channel, metadata=None):
        channel_spend["releases"].append({"reservation_key": reservation_key, "business": business,
                                          "channel": channel, "metadata": dict(metadata or {})})
        return {"success": True}

    def _settle_channel_spend_credits(reservation_key, *, business, channel, actual_credits, metadata=None):
        channel_spend["settles"].append({"reservation_key": reservation_key, "business": business,
                                         "channel": channel, "actual_credits": _creative_credit_int(actual_credits),
                                         "metadata": dict(metadata or {})})
        return {"balance_credits": 0, "reserved_credits": 0}

    def _upsert_ad_spend_policy(business, *, channel, slug, reservation_key, reserved_credits,
                                daily_budget_cents, total_budget_cents, start_at, end_at,
                                provider_account_id=None, provider_campaign_id=None,
                                provider_group_id=None, provider_ad_id=None, provider_post_id=None,
                                status="reserved", metadata=None):
        policy = types.SimpleNamespace(
            business_slug=business, channel=channel, slug=slug, reservation_key=reservation_key,
            reserved_credits=_creative_credit_int(reserved_credits),
            daily_budget_cents=int(daily_budget_cents), total_budget_cents=int(total_budget_cents),
            start_at=start_at, end_at=end_at, provider_account_id=provider_account_id,
            provider_campaign_id=provider_campaign_id, provider_group_id=provider_group_id,
            provider_ad_id=provider_ad_id, provider_post_id=provider_post_id, status=status,
            last_synced_spend_cents=0, settled_credits=0, metadata=dict(metadata or {}),
        )
        ad_spend_policies[(business, channel, slug)] = policy
        return policy

    def _update_ad_spend_policy(business, *, channel, slug, status=None,
                                last_synced_spend_cents=None, settled_credits=None,
                                metadata_patch=None):
        policy = ad_spend_policies.get((business, channel, slug))
        if policy is None:
            raise _FakeTakyonError(f"no ad spend policy for {business}/{channel}/{slug}")
        if status is not None:
            policy.status = status
        if last_synced_spend_cents is not None:
            policy.last_synced_spend_cents = int(last_synced_spend_cents)
        if settled_credits is not None:
            policy.settled_credits = int(settled_credits)
        if metadata_patch:
            policy.metadata.update(dict(metadata_patch))
        return policy

    def _load_ad_spend_policy(business, *, channel, slug):
        policy = ad_spend_policies.get((business, channel, slug))
        if policy is None:
            raise _FakeTakyonError(f"no ad spend policy for {business}/{channel}/{slug}")
        return policy

    def _list_ad_spend_policies(business, *, statuses=None):
        wanted = {str(s) for s in statuses} if statuses else None
        return [
            policy for (biz, _chan, _slug), policy in ad_spend_policies.items()
            if biz == business and (wanted is None or str(policy.status) in wanted)
        ]

    mod._creative_credit_int = _creative_credit_int
    mod._creative_credit_budget_snapshot = _creative_credit_budget_snapshot
    mod._ad_channel_live_media_spend_credits = _ad_channel_live_media_spend_credits
    mod._parse_iso_datetime = _parse_iso_datetime
    mod._derive_ad_spend_schedule = _derive_ad_spend_schedule
    mod._reserve_channel_spend_credits = _reserve_channel_spend_credits
    mod._release_channel_spend_credits = _release_channel_spend_credits
    mod._settle_channel_spend_credits = _settle_channel_spend_credits
    mod._upsert_ad_spend_policy = _upsert_ad_spend_policy
    mod._update_ad_spend_policy = _update_ad_spend_policy
    mod._load_ad_spend_policy = _load_ad_spend_policy
    mod._list_ad_spend_policies = _list_ad_spend_policies
    mod._test_meta_channel_credits = 2000  # $20 of channel media budget by default
    mod._test_channel_spend = channel_spend
    mod._test_ad_spend_policies = ad_spend_policies

    # Public surface.
    mod._store = _store
    mod._pixel_site_rec = pixel_site
    mod._surface_enable_meta_pixel = _surface_enable_meta_pixel
    mod._meta_pixel_config = _meta_pixel_config
    mod._product_live_current_root = _product_live_current_root
    mod._product_publish_target = lambda business, explicit=None: f"https://{business}.coscale.app/"
    mod._inject_meta_pixel_snippet = _inject_meta_pixel_snippet
    mod._republish_live_dist_to_r2 = _republish_live_dist_to_r2
    mod.tool_result = tool_result
    mod.tool_error = tool_error
    mod.safebox = safebox
    mod.TakyonError = _FakeTakyonError
    mod.InsufficientCreativeCredits = _FakeInsufficientCreativeCredits
    mod.CreativeCreditBudgetExceeded = _FakeCreativeCreditBudgetExceeded
    mod._creative_credit_backend = _creative_credit_backend
    mod._reserve_creative_credits = _reserve_creative_credits
    mod._commit_creative_credits = _commit_creative_credits
    mod._release_creative_credits = _release_creative_credits
    mod._assert_ad_set_budget_authorized = _assert_ad_set_budget_authorized
    mod._business_mode = _business_mode
    mod._resolved_business_slug = _resolved_business_slug
    mod._file_slug = _file_slug
    mod._now = _now
    mod._atomic_write_text = _atomic_write_text
    mod._read_existing_receipt = _read_existing_receipt
    mod._meta_int_metric = _meta_int_metric
    mod._meta_aggregate_insights_rows = _meta_aggregate_insights_rows
    mod._META_PURCHASE_ACTION_TYPES = _real_meta_purchase_action_types

    # Schema/property helpers used by TAKYON_META_ADS_V2_DEFINITIONS.
    def _schema(name, description, properties, required):
        return {
            "name": name,
            "description": description,
            "type": "object",
            "properties": properties,
            "required": required,
        }

    mod._schema = _schema
    mod._BUSINESS_PROP = {"type": "string", "description": "Business slug"}
    mod._IDEMPOTENCY_PROP = {"type": "string", "description": "Idempotency key"}
    mod._REASON_PROP = {"type": "string", "description": "Reason"}
    mod._ACTOR_PROP = {"type": "string", "description": "Actor"}

    # Test-only handles (NOT part of the real core API) for assertions/configuration.
    mod._test_store = store
    mod._test_safebox = safebox
    mod._test_mode_registry = mode_registry
    mod._test_reservations = reservations
    mod._test_commits = commits
    mod._test_releases = releases
    mod._test_budget_authz_calls = budget_authz_calls
    return mod


class _GraphRecorder:
    """Captures every ``meta_graph`` call the handler makes."""

    def __init__(self):
        self.uploads_video: list[dict] = []
        self.uploads_image: list[dict] = []
        self.status_calls: list[dict] = []
        self.budget_calls: list[dict] = []
        self.custom_conversions: list[dict] = []
        # Raw _graph round-trips (insights sync + pixel verify read through this).
        self.graph_calls: list[dict] = []
        self.graph_forward_get_response: dict[str, object] = {"data": []}


def _build_fake_meta_graph(recorder: _GraphRecorder) -> types.ModuleType:
    mod = types.ModuleType("plugins.takyon.meta_graph")

    class MetaGraphError(RuntimeError):
        pass

    def account_path(ad_account_id: str) -> str:
        digits = str(ad_account_id).replace("act_", "")
        return f"act_{digits}"

    def _graph(method, path, data_or_files, *, token, version="v21.0", host=None, files=None, timeout=180.0):
        # The two direct call sites (insights sync, pixel verify) GET a list-bearing
        # envelope; return an empty ``data`` list and record the call. ``version``
        # carries a default so a no-version call site never raises (FIX 1).
        recorder.graph_calls.append(
            {"method": method, "path": path, "params": dict(data_or_files or {}), "token": token, "version": version}
        )
        return {"data": []}

    def upload_video(token, ad_account_id, video_bytes, *, name, version="v21.0", poll=True, timeout=180.0):
        recorder.uploads_video.append(
            {
                "token": token,
                "ad_account_id": ad_account_id,
                "path": f"{account_path(ad_account_id)}/advideos",
                "bytes": video_bytes,
                "name": name,
            }
        )
        return "video-1"

    def upload_image(token, ad_account_id, image_bytes, *, name, version="v21.0", timeout=180.0):
        recorder.uploads_image.append(
            {
                "token": token,
                "ad_account_id": ad_account_id,
                "path": f"{account_path(ad_account_id)}/adimages",
                "bytes": image_bytes,
                "name": name,
            }
        )
        return {"hash": "image-hash-1", "url": "https://scontent.example/img.png"}

    def set_status(token, object_id, status, *, version="v21.0"):
        recorder.status_calls.append({"token": token, "object_id": object_id, "status": status})
        return {"success": True, "id": object_id, "status": status}

    def update_daily_budget(token, object_id, daily_budget_cents, *, version="v21.0"):
        recorder.budget_calls.append(
            {"token": token, "object_id": object_id, "daily_budget_cents": daily_budget_cents}
        )
        return {"success": True, "id": object_id, "daily_budget": daily_budget_cents}

    def ensure_custom_conversion(token, ad_account_id, *, name, rule, custom_event_type,
                                 event_source_id="", version="v21.0"):
        record = {
            "token": token,
            "ad_account_id": ad_account_id,
            "name": name,
            "rule": rule,
            "custom_event_type": custom_event_type,
            "event_source_id": event_source_id,
        }
        recorder.custom_conversions.append(record)
        return {
            "id": "custom-conv-1", "name": name, "existed": False, "verified": True,
            "custom_event_type": str(custom_event_type).upper(),
            "pixel_id": str(event_source_id), "rule": rule,
        }

    mod.MetaGraphError = MetaGraphError
    mod.account_path = account_path
    mod._graph = _graph
    mod.upload_video = upload_video
    mod.upload_image = upload_image
    mod.set_status = set_status
    mod.update_daily_budget = update_daily_budget
    mod.ensure_custom_conversion = ensure_custom_conversion
    return mod


class _MCPRecorder:
    """Captures ``meta_mcp.call_tool`` invocations and returns canned id payloads."""

    def __init__(self):
        self.calls: list[dict] = []
        # Map tool name -> the structuredContent payload returned.
        self.responses = {
            "ads_create_creative": {"creative_id": "creative-1", "id": "creative-1"},
            "ads_create_campaign": {"campaign_id": "campaign-1", "id": "campaign-1"},
            "ads_create_ad_set": {"adset_id": "adset-1", "ad_set_id": "adset-1", "id": "adset-1"},
            "ads_create_ad": {"ad_id": "ad-1", "id": "ad-1"},
        }

    def call_tool(self, tool_name, arguments=None, *, token, endpoint=None, timeout=60.0):
        self.calls.append(
            {"tool": tool_name, "arguments": dict(arguments or {}), "token": token, "endpoint": endpoint}
        )
        payload = self.responses.get(tool_name, {"id": f"{tool_name}-id"})
        # Mirror the real MCP envelope: content text + structuredContent.
        return {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "structuredContent": payload,
            **payload,
        }


def _build_fake_meta_mcp(recorder: _MCPRecorder) -> types.ModuleType:
    mod = types.ModuleType("plugins.takyon.meta_mcp")

    class MetaMCPError(RuntimeError):
        pass

    mod.MetaMCPError = MetaMCPError
    mod.DEFAULT_META_MCP_ENDPOINT = "https://mcp.facebook.com/ads"
    # Safebox alias tuples the handler resolves the MCP token/endpoint through.
    mod.META_MCP_TOKEN_ALIASES = ("META_MCP_OAUTH_TOKEN",)
    mod.META_MCP_ENDPOINT_ALIASES = ("META_MCP_ENDPOINT",)
    mod.call_tool = recorder.call_tool
    return mod


# --------------------------------------------------------------------------- #
# Fixture: install fakes, import the module under test fresh per test.
# --------------------------------------------------------------------------- #
class _Harness:
    def __init__(self, module, core, graph_rec, mcp_rec, root):
        self.module = module
        self.core = core
        self.graph = graph_rec
        self.mcp = mcp_rec
        self.root = Path(root)

    # -- business-mode configuration ------------------------------------- #
    def set_business_mode(self, business: str, mode: str) -> None:
        self.core._test_mode_registry[business] = mode

    # -- workspace asset materialization --------------------------------- #
    def write_business_file(self, business: str, rel: str, data: bytes) -> Path:
        path = self.root / "businesses" / business / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read_business_file(self, business: str, rel: str):
        path = self.root / "businesses" / business / rel
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def business_file_path(self, business: str, rel: str) -> Path:
        return self.root / "businesses" / business / rel


@pytest.fixture
def harness(tmp_path, monkeypatch):
    if not _META_ADS_V2_PATH.is_file():
        pytest.skip(f"module under test not present at {_META_ADS_V2_PATH}")

    graph_rec = _GraphRecorder()
    mcp_rec = _MCPRecorder()

    fake_core = _build_fake_core(tmp_path, graph_rec, mcp_rec)
    fake_graph = _build_fake_meta_graph(graph_rec)
    fake_mcp = _build_fake_meta_mcp(mcp_rec)

    # Inject FRESH parent package objects unconditionally so relative imports resolve to the
    # fakes below. ``from . import core`` prefers ``getattr(package, "core")`` over the
    # sys.modules entry — if another test file in the same process imported the REAL
    # ``plugins.takyon`` (e.g. test_takyon_episode_metrics), the real package object carries a
    # real ``core`` attribute and the module under test would silently bind the real core
    # (real store → Postgres) instead of the fakes. monkeypatch restores the real packages
    # at teardown.
    for pkg_name in ("plugins", "plugins.takyon"):
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = []  # mark as a (namespace-ish) package
        monkeypatch.setitem(sys.modules, pkg_name, pkg)

    monkeypatch.setitem(sys.modules, "plugins.takyon.core", fake_core)
    monkeypatch.setitem(sys.modules, "plugins.takyon.meta_graph", fake_graph)
    monkeypatch.setitem(sys.modules, "plugins.takyon.meta_mcp", fake_mcp)

    # Import the module under test under the fully-qualified name so its
    # ``from . import core, meta_mcp, meta_graph`` binds to the fakes above.
    spec = importlib.util.spec_from_file_location(
        "plugins.takyon.meta_ads_v2", _META_ADS_V2_PATH
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "plugins.takyon.meta_ads_v2", module)
    spec.loader.exec_module(module)

    return _Harness(module, fake_core, graph_rec, mcp_rec, tmp_path)


# --------------------------------------------------------------------------- #
# Argument builders.
# --------------------------------------------------------------------------- #
def _launch_args(**overrides):
    args = {
        "business": "clipbook",
        "slug": "demo-meta",
        "asset_kind": "video",
        "ad_account_id": "123456",
        "page_id": "654321",
        "link_url": "https://demo-meta.coscale.app",
        "message": "Try Clipbook",
        "headline": "Clipbook",
        "call_to_action_type": "LEARN_MORE",
        "objective": "OUTCOME_TRAFFIC",
        "daily_budget_usd": 5.0,
        "mode": "paused",
        "idempotency_key": "clipbook-meta-demo-v1",
    }
    args.update(overrides)
    return args


def _control_args(**overrides):
    args = {
        "business": "clipbook",
        "slug": "demo-meta",
        "operation": "pause",
        "object_id": "campaign-1",
        "idempotency_key": "clipbook-meta-control-v1",
    }
    args.update(overrides)
    return args


def _write_launch_receipt(harness, *, business="clipbook", slug="demo-meta", ids=None):
    payload = {
        "idempotency_key": "launch-receipt-v1",
        "business": business,
        "slug": slug,
        "status": "created_paused",
        "ids": ids
        or {
            "campaign_id": "campaign-1",
            "adset_id": "adset-1",
            "ad_id": "ad-1",
        },
    }
    path = harness.business_file_path(business, f"distribution/meta-ads/{slug}/receipt.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return payload


def _result(raw):
    """Handlers return a JSON string (via ``core.tool_result``); decode it."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _mcp_tools(harness):
    return [c["tool"] for c in harness.mcp.calls]


# --------------------------------------------------------------------------- #
# Launch: video happy-path (paused).
# --------------------------------------------------------------------------- #
def test_launch_video_happy_path_uploads_then_creates_paused(harness):
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file("clipbook", "product/ugc-ads/demo-meta/ad.mp4", b"fake-mp4-bytes")
    # video creatives need a thumbnail; provide one so the handler can stage it.
    harness.write_business_file(
        "clipbook", "product/ugc-ads/demo-meta/thumbnail.png", b"fake-thumb-bytes"
    )

    result = _result(
        harness.module.handle_business_meta_ad_launch(_launch_args(asset_kind="video"))
    )

    assert result["success"] is True
    assert result["status"] == "created_paused"
    # Video uploaded through the Safebox Graph broker, bytes-first.
    assert len(harness.graph.uploads_video) == 1
    upload = harness.graph.uploads_video[0]
    assert upload["token"] == ""
    assert upload["path"].endswith("/advideos")
    assert upload["bytes"] == b"fake-mp4-bytes"
    # A video creative also needs a thumbnail image (Meta requires an image_hash/url
    # alongside the video_id), so exactly one image upload happens — the thumbnail.
    assert len(harness.graph.uploads_image) == 1
    thumb = harness.graph.uploads_image[0]
    assert thumb["token"] == ""
    assert thumb["path"].endswith("/adimages")
    assert thumb["bytes"] == b"fake-thumb-bytes"

    # Then the four MCP object creates, in order, through the Safebox MCP broker.
    tools = _mcp_tools(harness)
    assert tools == [
        "ads_create_creative",
        "ads_create_campaign",
        "ads_create_ad_set",
        "ads_create_ad",
    ]
    for call in harness.mcp.calls:
        assert call["token"] == ""

    # Creative carries the uploaded video id (not an image hash).
    creative_args = harness.mcp.calls[0]["arguments"]
    assert creative_args.get("video_id") == "video-1"
    campaign_args = harness.mcp.calls[1]["arguments"]
    adset_args = harness.mcp.calls[2]["arguments"]
    ad_args = harness.mcp.calls[3]["arguments"]
    assert "status" not in campaign_args
    assert "is_adset_budget_sharing_enabled" not in campaign_args
    assert "status" not in adset_args
    assert "status" not in ad_args

    # Paused mode => no ACTIVE status flips.
    assert not [c for c in harness.graph.status_calls if c["status"] == "ACTIVE"]

    # Truthful receipt written with the real ids.
    receipt = harness.read_business_file(
        "clipbook", "distribution/meta-ads/demo-meta/receipt.json"
    )
    assert receipt is not None
    assert receipt["status"] == "created_paused"
    ids = receipt.get("ids", {})
    assert ids.get("creative_id") == "creative-1"
    assert ids.get("campaign_id") == "campaign-1"
    assert ids.get("adset_id") in ("adset-1",)
    assert ids.get("ad_id") == "ad-1"


# --------------------------------------------------------------------------- #
# Launch: image happy-path (paused).
# --------------------------------------------------------------------------- #
def test_launch_image_happy_path_uploads_adimage_then_creates(harness):
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file(
        "clipbook", "product/static-ads/demo-meta/creative-1.png", b"fake-png-bytes"
    )

    # The handler reads the creative from ``asset_path`` (business-relative); point it
    # at the static image we just staged.
    args = _launch_args(
        asset_kind="image",
        asset_path="product/static-ads/demo-meta/creative-1.png",
        idempotency_key="clipbook-meta-image-v1",
    )
    result = _result(harness.module.handle_business_meta_ad_launch(args))

    assert result["success"] is True
    assert result["status"] == "created_paused"
    # Image uploaded to /adimages through the Safebox broker; no video upload.
    assert len(harness.graph.uploads_image) == 1
    img = harness.graph.uploads_image[0]
    assert img["token"] == ""
    assert img["path"].endswith("/adimages")
    assert img["bytes"] == b"fake-png-bytes"
    assert not harness.graph.uploads_video

    tools = _mcp_tools(harness)
    assert tools == [
        "ads_create_creative",
        "ads_create_campaign",
        "ads_create_ad_set",
        "ads_create_ad",
    ]
    # Creative built from the image hash or url, not a video id.
    creative_args = harness.mcp.calls[0]["arguments"]
    assert creative_args.get("image_hash") == "image-hash-1" or creative_args.get(
        "image_url"
    ) == "https://scontent.example/img.png"
    assert "video_id" not in creative_args

    receipt = harness.read_business_file(
        "clipbook", "distribution/meta-ads/demo-meta/receipt.json"
    )
    assert receipt["ids"]["ad_id"] == "ad-1"


def test_launch_fails_if_receipt_does_not_sync_to_canonical_workspace(harness):
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file(
        "clipbook", "product/static-ads/demo-meta/creative-1.png", b"fake-png-bytes"
    )
    harness.core._test_store.sync_status = "skipped_disallowed"

    args = _launch_args(
        asset_kind="image",
        asset_path="product/static-ads/demo-meta/creative-1.png",
        idempotency_key="clipbook-meta-sync-fail-v1",
    )
    result = _result(harness.module.handle_business_meta_ad_launch(args))

    assert result["success"] is False
    assert "receipt workspace sync failed" in result["error"]
    assert result["ids"]["campaign_id"] == "campaign-1"
    assert result["ids"]["adset_id"] == "adset-1"
    assert result["ids"]["ad_id"] == "ad-1"
    assert harness.core._test_commits
    assert harness.core._test_releases == []


# --------------------------------------------------------------------------- #
# Launch: live mode activates campaign -> adset -> ad.
# --------------------------------------------------------------------------- #
def test_launch_live_mode_activates_three_objects(harness):
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file("clipbook", "product/ugc-ads/demo-meta/ad.mp4", b"fake-mp4-bytes")
    harness.write_business_file(
        "clipbook", "product/ugc-ads/demo-meta/thumbnail.png", b"fake-thumb-bytes"
    )

    result = _result(
        harness.module.handle_business_meta_ad_launch(
            _launch_args(mode="live", idempotency_key="clipbook-meta-live-v1")
        )
    )

    assert result["success"] is True
    assert result["status"] in ("live", "active", "created_active", "activated")

    active = [c for c in harness.graph.status_calls if c["status"] == "ACTIVE"]
    assert len(active) == 3
    # campaign first, then adset, then ad.
    activated_ids = [c["object_id"] for c in active]
    assert activated_ids == ["campaign-1", "adset-1", "ad-1"]
    for call in active:
        assert call["token"] == ""

    receipt = harness.read_business_file(
        "clipbook", "distribution/meta-ads/demo-meta/receipt.json"
    )
    assert receipt["mode"] == "live"


def test_launch_failure_after_credit_reserve_releases_reservation(harness):
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file(
        "clipbook", "product/static-ads/demo-meta/creative-1.png", b"fake-png-bytes"
    )
    harness.mcp.responses["ads_create_campaign"] = {}

    args = _launch_args(
        asset_kind="image",
        asset_path="product/static-ads/demo-meta/creative-1.png",
        idempotency_key="clipbook-meta-fail-v1",
    )
    result = _result(harness.module.handle_business_meta_ad_launch(args))

    assert result["success"] is False
    assert harness.core._test_reservations
    assert harness.core._test_commits == []
    assert [r["reservation_key"] for r in harness.core._test_releases] == [
        "clipbook-meta-fail-v1:creative-credits"
    ]

    receipt = harness.read_business_file(
        "clipbook", "distribution/meta-ads/demo-meta/receipt.json"
    )
    assert receipt["status"] == "partial_failed"
    assert receipt["credits_committed"] is False
    assert receipt["credits_released"] is True
    assert receipt["ids"]["creative_id"] == "creative-1"


# --------------------------------------------------------------------------- #
# Test-mode: write a local receipt, make no external calls.
# --------------------------------------------------------------------------- #
def test_launch_test_mode_writes_local_receipt_no_external_calls(harness):
    harness.set_business_mode("clipbook", "test")
    harness.write_business_file("clipbook", "product/ugc-ads/demo-meta/ad.mp4", b"fake-mp4-bytes")

    result = _result(harness.module.handle_business_meta_ad_launch(_launch_args()))

    assert result["success"] is True
    assert result["status"] in ("test_receipt", "suppressed_test_mode")
    # No external Meta calls whatsoever.
    assert harness.graph.uploads_video == []
    assert harness.graph.uploads_image == []
    assert harness.graph.status_calls == []
    assert harness.mcp.calls == []
    # No real Meta object ids fabricated.
    assert "ids" not in result or not any(
        str(v).startswith(("creative-", "campaign-", "adset-", "ad-"))
        for v in (result.get("ids") or {}).values()
    )

    receipt = harness.read_business_file(
        "clipbook", "distribution/meta-ads/demo-meta/receipt.json"
    )
    assert receipt is not None
    assert receipt["status"] in ("test_receipt", "suppressed_test_mode")


def test_launch_mode_arg_only_accepts_paused_or_live(harness):
    # The ``mode`` arg gates activation (paused|live) only; it is NOT a suppression
    # switch. External-call suppression is driven by the *business* mode, not this arg.
    # An invalid ``mode`` (e.g. "test") is rejected before any external Meta call.
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file("clipbook", "product/ugc-ads/demo-meta/ad.mp4", b"fake-mp4-bytes")

    result = _result(
        harness.module.handle_business_meta_ad_launch(
            _launch_args(mode="test", idempotency_key="clipbook-meta-mode-invalid-v1")
        )
    )

    assert result["success"] is False
    assert "mode" in str(result.get("error") or "").lower()
    # No external Meta calls happened — the invalid mode was rejected up front.
    assert harness.mcp.calls == []
    assert harness.graph.uploads_video == []
    assert harness.graph.uploads_image == []


# --------------------------------------------------------------------------- #
# Idempotency key is required.
# --------------------------------------------------------------------------- #
def test_launch_requires_idempotency_key(harness):
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file("clipbook", "product/ugc-ads/demo-meta/ad.mp4", b"fake-mp4-bytes")
    args = _launch_args()
    args.pop("idempotency_key", None)

    with pytest.raises((harness.core.TakyonError, ValueError, KeyError)):
        raw = harness.module.handle_business_meta_ad_launch(args)
        # If the handler returns a structured error instead of raising, surface it.
        decoded = _result(raw)
        if not decoded.get("success", True):
            raise harness.core.TakyonError(decoded.get("error", "missing idempotency_key"))
        raise AssertionError("expected missing idempotency_key to be rejected")


# --------------------------------------------------------------------------- #
# Launch: one live campaign at a time (hard rail).
# --------------------------------------------------------------------------- #
def _launch_image(harness, slug, key):
    harness.write_business_file("clipbook", f"product/static-ads/{slug}/{slug}.png", b"png-bytes")
    return _result(harness.module.handle_business_meta_ad_launch(_launch_args(
        asset_kind="image", slug=slug, idempotency_key=key,
        asset_path=f"product/static-ads/{slug}/{slug}.png",
    )))


def test_launch_blocked_while_another_campaign_holds_live_slot(harness):
    harness.set_business_mode("clipbook", "live")
    first = _launch_image(harness, "demo-meta", "clipbook-meta-demo-v1")
    assert first["success"] is True  # holds the slot as created_paused

    second = _launch_image(harness, "demo-meta-2", "clipbook-meta-demo-v2")
    assert second["success"] is False
    assert "one live meta campaign at a time" in second["error"]
    assert "demo-meta" in second["error"]
    # Fails closed BEFORE any provider work for the second campaign: only the
    # first campaign's object creates happened.
    assert _mcp_tools(harness).count("ads_create_campaign") == 1


def test_launch_same_slug_retry_not_blocked_by_own_slot(harness):
    harness.set_business_mode("clipbook", "live")
    first = _launch_image(harness, "demo-meta", "clipbook-meta-demo-v1")
    assert first["success"] is True

    retry = _result(harness.module.handle_business_meta_ad_launch(_launch_args(
        asset_kind="image", slug="demo-meta", idempotency_key="clipbook-meta-demo-v1",
        asset_path="product/static-ads/demo-meta/demo-meta.png",
    )))
    assert retry["success"] is True
    assert retry.get("idempotent") is True


def test_pause_frees_the_live_slot_for_the_next_launch(harness):
    harness.set_business_mode("clipbook", "live")
    first = _launch_image(harness, "demo-meta", "clipbook-meta-demo-v1")
    assert first["success"] is True

    paused = _result(harness.module.handle_business_meta_ad_control(
        _control_args(operation="pause", object_id="campaign-1")))
    assert paused["success"] is True

    second = _launch_image(harness, "demo-meta-2", "clipbook-meta-demo-v2")
    assert second["success"] is True
    assert _mcp_tools(harness).count("ads_create_campaign") == 2


def test_activate_blocked_while_another_campaign_holds_live_slot(harness):
    harness.set_business_mode("clipbook", "live")
    assert _launch_image(harness, "demo-meta", "clipbook-meta-demo-v1")["success"] is True
    assert _result(harness.module.handle_business_meta_ad_control(
        _control_args(operation="pause", object_id="campaign-1")))["success"] is True
    assert _launch_image(harness, "demo-meta-2", "clipbook-meta-demo-v2")["success"] is True

    # demo-meta-2 now holds the slot; re-activating demo-meta must refuse.
    reactivate = _result(harness.module.handle_business_meta_ad_control(
        _control_args(operation="activate", object_id="campaign-1", slug="demo-meta",
                      idempotency_key="clipbook-meta-control-v2")))
    assert reactivate["success"] is False
    assert "one live meta campaign at a time" in reactivate["error"]


# --------------------------------------------------------------------------- #
# Control: set_budget / pause / activate.
# --------------------------------------------------------------------------- #
def test_control_set_budget_updates_daily_budget(harness):
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    result = _result(
        harness.module.handle_business_meta_ad_control(
            _control_args(
                operation="set_budget",
                object_id="adset-1",
                daily_budget_usd=12.0,
                idempotency_key="clipbook-meta-budget-v1",
            )
        )
    )

    assert result["success"] is True
    assert len(harness.graph.budget_calls) == 1
    call = harness.graph.budget_calls[0]
    assert call["object_id"] == "adset-1"
    assert call["daily_budget_cents"] == 1200  # $12.00 in cents
    assert call["token"] == ""
    assert harness.core._test_budget_authz_calls == [
        {
            "channel": "meta",
            "business": "clipbook",
            "slug": "demo-meta",
            "target_id": "adset-1",
            "daily_budget_cents": 1200,
            "safety_cap_cents": 0,
        }
    ]

    action = harness.read_business_file(
        "clipbook", "distribution/meta-ads/demo-meta/actions/clipbook-meta-budget-v1.json"
    )
    assert action is not None


def test_control_pause_sets_status_paused(harness):
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    result = _result(
        harness.module.handle_business_meta_ad_control(
            _control_args(operation="pause", object_id="campaign-1")
        )
    )

    assert result["success"] is True
    assert any(
        c["status"] == "PAUSED" and c["object_id"] == "campaign-1"
        for c in harness.graph.status_calls
    )


def test_control_activate_sets_status_active(harness):
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    result = _result(
        harness.module.handle_business_meta_ad_control(
            _control_args(
                operation="activate",
                object_id="ad-1",
                idempotency_key="clipbook-meta-activate-v1",
            )
        )
    )

    assert result["success"] is True
    assert any(
        c["status"] == "ACTIVE" and c["object_id"] == "ad-1"
        for c in harness.graph.status_calls
    )


def test_control_rejects_object_id_from_another_business(harness):
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)

    result = _result(
        harness.module.handle_business_meta_ad_control(
            _control_args(operation="pause", object_id="campaign-other")
        )
    )

    assert result["success"] is False
    assert "does not belong to this business" in result["error"]
    assert harness.graph.status_calls == []


def test_insights_sync_defaults_to_receipt_owned_object(harness):
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    harness.graph.graph_forward_get_response = {
        "data": [
            {
                "ad_id": "ad-1",
                "date_start": "2026-06-24",
                "date_stop": "2026-06-24",
                "impressions": "12",
                "reach": "10",
                "clicks": "3",
                "spend": "4.56",
            }
        ]
    }

    result = _result(
        harness.module.handle_business_meta_ad_insights_sync(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "level": "ad",
                "idempotency_key": "clipbook-meta-insights-v1",
            }
        )
    )

    assert result["success"] is True
    assert harness.graph.graph_calls[-1]["path"] == "ad-1/insights"
    insights_path = harness.business_file_path("clipbook", "metrics/meta-ads/demo-meta/insights.jsonl")
    lines = [json.loads(line) for line in insights_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[0]["object_id"] == "ad-1"


def test_insights_sync_isolates_revenue_to_the_business_custom_conversion(harness):
    # SHARED-PIXEL hardening: once pixel-ensure has minted this business its own custom
    # conversion, the sync receipt's purchases/revenue/ROAS count ONLY that conversion's
    # action type — a generic purchase (someone buying on ANOTHER business's site after
    # clicking this ad) must not pollute the totals. The receipt records the boundary used.
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    harness.write_business_file(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json",
        json.dumps({
            "status": "active",
            "business": "clipbook",
            "custom_conversion_id": "42424242",
            "custom_event_type": "PURCHASE",
            "pixel_id": "PIX-TEST-1",
            "rule": "{\"url\":{\"i_contains\":\"clipbook.coscale.app\"}}",
            "url_match": "clipbook.coscale.app",
            "created_at": "2026-07-01T00:00:00+00:00",
        }).encode("utf-8"),
    )
    harness.graph.graph_forward_get_response = {
        "data": [
            {
                "ad_id": "ad-1",
                "date_start": "2026-06-24",
                "date_stop": "2026-06-24",
                "impressions": "1200",
                "clicks": "40",
                "spend": "20.00",
                "actions": [
                    {"action_type": "purchase", "value": "9"},                        # not ours
                    {"action_type": "offsite_conversion.custom.42424242", "value": "2"},
                ],
                "action_values": [
                    {"action_type": "purchase", "value": "900.00"},                   # not ours
                    {"action_type": "offsite_conversion.custom.42424242", "value": "40.00"},
                ],
            }
        ]
    }

    result = _result(harness.module.handle_business_meta_ad_insights_sync({
        "business": "clipbook", "slug": "demo-meta", "level": "ad",
        "idempotency_key": "clipbook-meta-cc-v1",
    }))

    assert result["success"] is True
    totals = result["value"]["totals"]
    assert totals["purchase_value_usd"] == 40.0   # ours only, not 900
    assert totals["purchase_count"] == 2
    assert totals["roas"] == 2.0
    assert result["value"]["purchase_attribution"] == "custom_conversion:42424242"


_GENERIC_PURCHASE_ROWS = {
    "data": [{
        "ad_id": "ad-1", "date_start": "2026-06-24", "date_stop": "2026-06-24",
        "impressions": "100", "clicks": "10", "spend": "10.00",
        "actions": [{"action_type": "purchase", "value": "1"}],
        "action_values": [{"action_type": "purchase", "value": "30.00"}],
    }]
}


def _sync_clipbook(harness, key):
    return _result(harness.module.handle_business_meta_ad_insights_sync({
        "business": "clipbook", "slug": "demo-meta", "level": "ad",
        "idempotency_key": key,
    }))


def test_insights_sync_missing_attribution_is_unavailable_not_generic(harness):
    # FAIL-CLOSED: no canonical purchase-attribution record -> purchases/revenue/ROAS are
    # UNAVAILABLE (None). Counting the generic purchase actions here would re-admit other
    # businesses' sales on the shared pixel — the exact contamination the boundary prevents.
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    harness.graph.graph_forward_get_response = _GENERIC_PURCHASE_ROWS
    result = _sync_clipbook(harness, "clipbook-meta-unavail-v1")
    assert result["success"] is True
    totals = result["value"]["totals"]
    assert totals["spend_usd"] == 10.0 and totals["clicks"] == 10  # delivery still syncs
    assert totals["purchase_count"] is None
    assert totals["purchase_value_usd"] is None
    assert totals["roas"] is None
    assert result["value"]["purchase_attribution"] == "unavailable"


def test_insights_sync_rejects_lead_attribution_record(harness):
    # A LEAD conversion must never become the purchase/revenue boundary.
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    harness.write_business_file(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json",
        json.dumps({
            "status": "active",
            "business": "clipbook", "custom_conversion_id": "42424242",
            "custom_event_type": "LEAD", "pixel_id": "PIX-TEST-1",
            "rule": "{\"url\":{\"i_contains\":\"clipbook.coscale.app\"}}",
            "url_match": "clipbook.coscale.app",
        }).encode("utf-8"),
    )
    harness.graph.graph_forward_get_response = _GENERIC_PURCHASE_ROWS
    result = _sync_clipbook(harness, "clipbook-meta-lead-v1")
    assert result["value"]["purchase_attribution"] == "unavailable"
    assert result["value"]["totals"]["roas"] is None


def test_insights_sync_rejects_stale_pixel_attribution_record(harness):
    # The record must match the CURRENTLY configured pixel — a record minted against a
    # rotated/old pixel must not masquerade as live attribution.
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    harness.write_business_file(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json",
        json.dumps({
            "status": "active",
            "business": "clipbook", "custom_conversion_id": "42424242",
            "custom_event_type": "PURCHASE", "pixel_id": "PIX-OLD-ROTATED",
            "rule": "{\"url\":{\"i_contains\":\"clipbook.coscale.app\"}}",
            "url_match": "clipbook.coscale.app",
        }).encode("utf-8"),
    )
    harness.graph.graph_forward_get_response = _GENERIC_PURCHASE_ROWS
    result = _sync_clipbook(harness, "clipbook-meta-stale-v1")
    assert result["value"]["purchase_attribution"] == "unavailable"
    assert result["value"]["totals"]["purchase_count"] is None


def test_insights_sync_requests_and_surfaces_meta_attributed_revenue(harness):
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)
    harness.write_business_file(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json",
        json.dumps({
            "status": "active",
            "business": "clipbook", "custom_conversion_id": "555",
            "custom_event_type": "PURCHASE", "pixel_id": "PIX-TEST-1",
            "rule": "{\"url\":{\"i_contains\":\"clipbook.coscale.app\"}}",
            "url_match": "clipbook.coscale.app",
        }).encode("utf-8"),
    )
    harness.graph.graph_forward_get_response = {
        "data": [
            {
                "ad_id": "ad-1",
                "date_start": "2026-06-24",
                "date_stop": "2026-06-24",
                "impressions": "1200",
                "clicks": "40",
                "spend": "20.00",
                "actions": [{"action_type": "offsite_conversion.custom.555", "value": "3"}],
                "action_values": [{"action_type": "offsite_conversion.custom.555", "value": "80.00"}],
            }
        ]
    }

    result = _result(
        harness.module.handle_business_meta_ad_insights_sync(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "level": "ad",
                "idempotency_key": "clipbook-meta-revenue-v1",
            }
        )
    )

    assert result["success"] is True
    # The read request asks Meta for the revenue field, not just spend/counts.
    assert "action_values" in harness.graph.graph_calls[-1]["params"]["fields"]
    # ROAS surfaces on the receipt totals: 80 revenue / 20 spend = 4.0.
    totals = result["value"]["totals"]
    assert totals["purchase_value_usd"] == 80.0
    assert totals["purchase_count"] == 3
    assert totals["roas"] == 4.0
    # action_values is persisted for downstream re-reads.
    insights_path = harness.business_file_path("clipbook", "metrics/meta-ads/demo-meta/insights.jsonl")
    lines = [json.loads(line) for line in insights_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[0]["action_values"] == [{"action_type": "offsite_conversion.custom.555", "value": "80.00"}]


def test_pixel_ensure_live_installs_site_side_and_meta_side(harness):
    harness.set_business_mode("clipbook", "live")
    # A published live site exists (index.html present in the served dist).
    live_index = harness.core._product_live_current_root("clipbook") / "index.html"
    live_index.write_text("<html><head></head><body></body></html>", encoding="utf-8")

    result = _result(
        harness.module.handle_business_meta_pixel_ensure(
            {"business": "clipbook", "idempotency_key": "clipbook-pixel-v1", "ad_account_id": "123456"}
        )
    )

    assert result["success"] is True
    assert result["ok"] is True
    # Meta side: the per-business custom conversion was ensured.
    assert harness.graph.custom_conversions
    # Site side: surface flag flipped, snippet injected into the live dist, edge republished.
    site = result["site"]
    assert site["surface"] == {"enabled": True, "changed": True}
    assert site["live_injected"] is True
    assert site["republish"]["live_build_id"] == "build-test-1"
    rec = harness.core._pixel_site_rec
    assert rec["surface_flips"] == ["clipbook"]
    assert rec["injections"][0]["pixel_id"] == "PIX-TEST-1"
    # The custom conversion is anchored to the shared pixel (Meta requires event_source_id).
    assert harness.graph.custom_conversions[-1]["event_source_id"] == "PIX-TEST-1"
    assert rec["republishes"][0]["business"] == "clipbook"
    # The receipt mirrors the site block so operators can see what actually happened.
    receipt = harness.read_business_file("clipbook", result["receipt"])
    assert receipt["site"]["live_injected"] is True
    # Default event type is LEAD -> the canonical PURCHASE attribution record must NOT exist.
    assert harness.business_file_path(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json").exists() is False


def test_pixel_ensure_purchase_conversion_activates_verified_canonical_record(harness):
    harness.set_business_mode("clipbook", "live")
    # A published live site exists — instrumentation verification reads the served dist.
    live_index = harness.core._product_live_current_root("clipbook") / "index.html"
    live_index.write_text("<html><head></head><body></body></html>", encoding="utf-8")

    result = _result(harness.module.handle_business_meta_pixel_ensure({
        "business": "clipbook", "idempotency_key": "clipbook-pixel-purchase-v1",
        "ad_account_id": "123456", "custom_event_type": "PURCHASE",
    }))
    assert result["success"] is True and result["ok"] is True
    assert result["value"]["provider_verified"] is True
    assert result["value"]["site"]["instrumentation_verified"] is True
    record_path = harness.business_file_path(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json")
    assert record_path.exists()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "active"
    assert record["business"] == "clipbook"
    assert record["custom_event_type"] == "PURCHASE"
    assert record["custom_conversion_id"] == "custom-conv-1"
    assert record["pixel_id"] == "PIX-TEST-1"    # anchored to the CURRENT shared pixel
    # The rule scopes to the AUTHORITATIVE hostname (never a caller-supplied domain).
    assert "clipbook.coscale.app" in record["rule"]
    assert record["url_match"] == "clipbook.coscale.app"
    # And the strict resolver accepts exactly this record.
    types, label = harness.module._business_purchase_attribution(
        harness.core._store(), "clipbook")
    assert types == ("offsite_conversion.custom.custom-conv-1",)
    assert label == "custom_conversion:custom-conv-1"


def test_pixel_ensure_purchase_without_live_instrumentation_blocks_record(harness):
    # Provider verification alone is NOT enough: if the served site does not actually carry
    # the pixel, the conversion can never observe a purchase — the canonical record must not
    # activate, and any previous record must be left invalidated.
    harness.set_business_mode("clipbook", "live")
    harness.write_business_file(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json",
        json.dumps({
            "status": "active", "business": "clipbook", "custom_conversion_id": "OLD-1",
            "custom_event_type": "PURCHASE", "pixel_id": "PIX-TEST-1",
            "rule": "{\"url\":{\"i_contains\":\"clipbook.coscale.app\"}}",
            "url_match": "clipbook.coscale.app",
        }).encode("utf-8"),
    )
    # NO live index.html -> instrumentation verification must fail.
    result = _result(harness.module.handle_business_meta_pixel_ensure({
        "business": "clipbook", "idempotency_key": "clipbook-pixel-noinstr-v1",
        "ad_account_id": "123456", "custom_event_type": "PURCHASE",
    }))
    assert result["success"] is True
    assert result["value"]["site"]["instrumentation_verified"] is False
    assert result["value"]["purchase_attribution_blocked"]["instrumentation_verified"] is False
    # The OLD record was invalidated before the attempt and stays invalidated on failure.
    record = json.loads(harness.business_file_path(
        "clipbook", "metrics/meta-pixel/purchase-attribution.json").read_text(encoding="utf-8"))
    assert record["status"] == "invalidated"
    assert record["previous"]["custom_conversion_id"] == "OLD-1"
    types, label = harness.module._business_purchase_attribution(
        harness.core._store(), "clipbook")
    assert types is None and label == "unavailable"


def test_pixel_ensure_rejects_cross_business_domain(harness):
    harness.set_business_mode("clipbook", "live")
    result = _result(harness.module.handle_business_meta_pixel_ensure({
        "business": "clipbook", "idempotency_key": "clipbook-pixel-xdom-v1",
        "ad_account_id": "123456", "custom_event_type": "PURCHASE",
        "domain": "othersaas.coscale.app",
    }))
    assert result["success"] is False
    assert "cross-business domain rejected" in result["error"]


def test_write_and_patch_tools_refuse_the_canonical_attribution_record():
    # The guard lives in the REAL core (wired into handle_business_write_file and
    # handle_business_patch_file immediately after path resolution).
    from plugins.takyon import core as real_core

    try:
        real_core._refuse_tool_write_to_attribution_record(
            "metrics/meta-pixel/purchase-attribution.json")
    except real_core.TakyonError as exc:
        assert "tool-immutable" in str(exc)
    else:  # pragma: no cover - the guard must raise
        raise AssertionError("attribution record write was not refused")
    # Leading-slash normalization is covered too.
    try:
        real_core._refuse_tool_write_to_attribution_record(
            "/metrics/meta-pixel/purchase-attribution.json")
    except real_core.TakyonError:
        pass
    else:  # pragma: no cover
        raise AssertionError("normalized path was not refused")
    # Any other path passes the guard untouched.
    real_core._refuse_tool_write_to_attribution_record("metrics/summary.md")
    # And both file tools actually invoke the guard.
    import inspect
    assert "_refuse_tool_write_to_attribution_record" in inspect.getsource(
        real_core.handle_business_write_file)
    assert "_refuse_tool_write_to_attribution_record" in inspect.getsource(
        real_core.handle_business_patch_file)


def test_pixel_ensure_unconfigured_pixel_fails_closed_before_provider_call(harness):
    # Meta REQUIRES event_source_id (the pixel) to create a custom conversion, so an
    # unconfigured pixel is a hard fail-closed BEFORE any provider call — not the old
    # "meta side succeeds, site blocker" split (which Meta itself made impossible).
    harness.set_business_mode("clipbook", "live")
    harness.core._pixel_site_rec["config"] = {}  # analytics.meta_pixel unset

    result = _result(
        harness.module.handle_business_meta_pixel_ensure(
            {"business": "clipbook", "idempotency_key": "clipbook-pixel-v2", "ad_account_id": "123456"}
        )
    )

    assert result["success"] is False
    assert "meta_pixel_unconfigured" in result["error"]
    assert not harness.core._pixel_site_rec["injections"]


def test_pixel_ensure_unpublished_site_records_blocker(harness):
    harness.set_business_mode("clipbook", "live")
    # No live index.html -> injection cannot land; ensure reports it instead of pretending.

    result = _result(
        harness.module.handle_business_meta_pixel_ensure(
            {"business": "clipbook", "idempotency_key": "clipbook-pixel-v3", "ad_account_id": "123456"}
        )
    )

    assert result["success"] is True
    site = result["site"]
    assert site["live_injected"] is False
    assert "publish the product first" in site["blocker"]
    assert not harness.core._pixel_site_rec["republishes"]


def test_pixel_ensure_test_mode_suppresses_site_install(harness):
    harness.set_business_mode("clipbook", "test")

    result = _result(
        harness.module.handle_business_meta_pixel_ensure(
            {"business": "clipbook", "idempotency_key": "clipbook-pixel-v4", "ad_account_id": "123456"}
        )
    )

    assert result["success"] is True
    assert result["status"] == "test_receipt"
    rec = harness.core._pixel_site_rec
    assert not rec["surface_flips"] and not rec["injections"] and not rec["republishes"]


def test_insights_sync_rejects_cross_business_object_id(harness):
    harness.set_business_mode("clipbook", "live")
    _write_launch_receipt(harness)

    result = _result(
        harness.module.handle_business_meta_ad_insights_sync(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "level": "ad",
                "object_id": "ad-other",
                "idempotency_key": "clipbook-meta-insights-cross-v1",
            }
        )
    )

    assert result["success"] is False
    assert "does not belong to this business" in result["error"]
    assert harness.graph.graph_calls == []


# --------------------------------------------------------------------------- #
# Evaluate: verdict from a synthetic insights row.
# --------------------------------------------------------------------------- #
def _write_insights(harness, rows, *, business="clipbook", slug="demo-meta"):
    path = harness.business_file_path(
        business, f"metrics/meta-ads/{slug}/insights.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return path


def test_evaluate_poor_ctr_recommends_refresh_creative(harness):
    harness.set_business_mode("clipbook", "live")
    # A poor-CTR, post-learning row: 200 clicks / 50000 impressions => CTR 0.4% (< 1.0%
    # poor); $600 / 200 clicks => CPC $3.0 (> $3.0 boundary is not poor, so CTR drives
    # the verdict). Conversions are carried where the handler actually reads them — the
    # Meta ``actions`` list — with 60 leads so the ad set is past the 50-event learning gate.
    _write_insights(
        harness,
        [
            {
                "level": "adset",
                "object_id": "adset-1",
                "date_start": "2026-06-25",
                "date_stop": "2026-06-25",
                "impressions": 50000,
                "clicks": 200,
                "spend": 600.0,
                "ctr": 0.4,
                "cpc": 3.0,
                "actions": [{"action_type": "lead", "value": "60"}],
                "frequency": 2.0,
            }
        ],
    )

    result = _result(
        harness.module.handle_business_meta_ad_evaluate(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "idempotency_key": "clipbook-meta-eval-v1",
            }
        )
    )

    assert result["success"] is True
    verdict = result.get("verdict") or result.get("value", {}).get("verdict")
    action = result.get("recommended_action") or result.get("action") or result.get(
        "value", {}
    ).get("recommended_action")
    assert verdict in ("bad", "poor")
    assert action == "refresh_creative"

    evaluation = harness.read_business_file(
        "clipbook", "evaluations/clipbook-meta-eval-v1.json"
    ) or harness.read_business_file(
        "clipbook", "metrics/meta-ads/demo-meta/evaluations/clipbook-meta-eval-v1.json"
    )
    assert evaluation is not None


def test_evaluate_still_learning_recommends_wait(harness):
    harness.set_business_mode("clipbook", "live")
    # Fewer than ~50 conversions in 7 days => still Learning => verdict wait, not poor.
    _write_insights(
        harness,
        [
            {
                "level": "adset",
                "object_id": "adset-1",
                "date": "2026-06-25",
                "impressions": 8000,
                "clicks": 40,
                "spend": 120.0,
                "ctr": 0.5,
                "cpc": 3.0,
                "conversions": 5,
                "frequency": 1.2,
            }
        ],
    )

    result = _result(
        harness.module.handle_business_meta_ad_evaluate(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "idempotency_key": "clipbook-meta-eval-learn-v1",
            }
        )
    )

    assert result["success"] is True
    verdict = result.get("verdict") or result.get("value", {}).get("verdict")
    action = result.get("recommended_action") or result.get("action") or result.get(
        "value", {}
    ).get("recommended_action")
    assert verdict == "learning"
    assert action == "wait"


# --------------------------------------------------------------------------- #
# Tool definitions surface.
# --------------------------------------------------------------------------- #
def test_definitions_export_the_seven_tools(harness):
    defs = harness.module.TAKYON_META_ADS_V2_DEFINITIONS
    names = {d["name"] for d in defs}
    expected = {
        "business_meta_ad_launch",
        "business_meta_ad_control",
        "business_meta_ad_insights_sync",
        "business_meta_ad_evaluate",
        "business_meta_pixel_verify",
        "business_meta_pixel_ensure",
    }
    # business_read_business already exists in core, so it is NOT re-declared here.
    assert expected.issubset(names)
    for d in defs:
        assert callable(d["handler"])
        assert "schema" in d
        assert "idempotency_key" in d["schema"]["properties"]
