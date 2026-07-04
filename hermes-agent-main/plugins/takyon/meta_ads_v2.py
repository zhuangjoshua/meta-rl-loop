"""takyon-meta-ads-v2 tool handlers — the clean hybrid Meta Ads launch/control/insights skill.

This module declares the 6 guarded ``business_meta_*`` tools the takyon-meta-ads-v2 skill drives
(the 7th tool the skill uses, ``business_read_business``, is pre-existing in ``core``).
It is a thin authority-side orchestrator: it never resolves a secret from ``os.environ`` and never
hardcodes a token, ad-account id, or page id. All secret/config access goes through the Safebox
broker helpers, and all external work is split across two disjoint credentials, each doing what the
other cannot:

  * SYSTEM-USER token (alias ``META_SYSTEM_USER_ACCESS_TOKEN``) — the Graph Marketing API leg
    (``meta_graph``). Uploads the generated creative bytes (video/image) and runs lifecycle
    (activate/pause/budget/insights). Rejected by the MCP.
  * MCP token (alias ``META_MCP_OAUTH_TOKEN``) — the official Meta Ads MCP (``meta_mcp``). Creates the
    ad OBJECTS (creative → campaign → ad set → ad). Rejected by the Graph API.

WHY split: the business's own Meta app runs in DEVELOPMENT mode, so the system-user token cannot
create an ad creative (Meta error 100/1885183 "app in development mode"). The MCP runs on an
APPROVED/Live app, so it creates the creative+ad with no such block. Once the Takyon Ads Server app is
switched to LIVE the MCP leg can be dropped (swap ``ads_create_creative``/``ads_create_ad`` to the
Graph token) — same handlers. The MCP cannot hard-delete; DELETE/ARCHIVE are forced to PAUSED, so the
control handler only pauses.

Creative bytes come from the sibling ugc-video-ad / static-ad-creative-generator skills:
  * video → ``product/ugc-ads/<slug>/ad.mp4`` (video creatives also need a thumbnail image_hash/url)
  * image → ``product/static-ads/<slug>/<creative_id>.png``

Receipts are truthful: every handler writes the real provider ids + status + mode under
``distribution/meta-ads/<slug>/`` (launch/control) or ``metrics/meta-ads/<slug>/`` (sync/evaluate) and
re-reads what it wrote before claiming success. A partial launch writes a repair-able receipt that
records any ids already created.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from . import core, meta_graph


def _dt_now() -> datetime:
    return datetime.now(timezone.utc)


# ── Defaults / constants ──────────────────────────────────────────────────────────────────────────
_DEFAULT_OBJECTIVE = "OUTCOME_TRAFFIC"
_DEFAULT_CALL_TO_ACTION = "LEARN_MORE"
_DEFAULT_OPTIMIZATION_GOAL = "LINK_CLICKS"
_DEFAULT_BILLING_EVENT = "IMPRESSIONS"
_DEFAULT_BID_STRATEGY = "LOWEST_COST_WITHOUT_CAP"
_DEFAULT_DAILY_BUDGET_USD = 5.0
# Default geo target: US-only, Facebook feed (no Instagram required — Facebook-only default).
_DEFAULT_GEO_COUNTRIES = ("US",)
_CREATIVE_ACTION = "meta_ad_launch"
_BUDGET_BUCKET = "meta"

# The ad-object ids a complete live launch must carry, in creation order.
_REQUIRED_IDS = ("creative_id", "campaign_id", "adset_id", "ad_id")


# ── small local utilities (no secret access, no env reads) ──────────────────────────────────────────
def _require_idempotency_key(args: Mapping[str, Any]) -> str:
    key = str(args.get("idempotency_key") or "").strip()
    if not key:
        raise core.TakyonError("idempotency_key is required")
    return key


def _arg(args: Mapping[str, Any], *names: str, default: Any = "") -> Any:
    """First present, non-empty arg among ``names`` (mirrors the launch plan's tolerant arg reads)."""
    for name in names:
        value = args.get(name)
        if value is not None and (not isinstance(value, str) or value.strip()):
            return value
    return default


def _meta_config() -> Mapping[str, Any]:
    try:
        cfg = core.safebox.meta_config()
    except Exception:
        cfg = {}
    return cfg if isinstance(cfg, Mapping) else {}


def _resolve_config_value(args: Mapping[str, Any], arg_names: tuple[str, ...], env_keys: tuple[str, ...]) -> str:
    """Resolve config via explicit args first, else the Safebox Meta config broker."""
    explicit = str(_arg(args, *arg_names) or "").strip()
    if explicit:
        return explicit
    field_map = {
        "META_AD_ACCOUNT_ID": "ad_account_id",
        "META_PAGE_ID": "page_id",
        "META_INSTAGRAM_ID": "instagram_user_id",
        "META_MCP_ENDPOINT": "mcp_endpoint",
        "META_ADS_MCP_ENDPOINT": "mcp_endpoint",
    }
    cfg = _meta_config()
    for key in env_keys:
        field = field_map.get(str(key or "").strip())
        if not field:
            continue
        value = str(cfg.get(field) or "").strip()
        if value:
            return value
    try:
        return str(core.safebox.first_env_backed_value(*env_keys) or "").strip()
    except Exception:
        return ""


def _numeric_account_id(ad_account_id: str) -> str:
    """The MCP wants the numeric id (no ``act_`` prefix); the Graph leg wants ``act_<digits>``."""
    raw = str(ad_account_id or "").strip()
    return raw[4:] if raw.lower().startswith("act_") else raw


def _budget_cents(daily_budget_usd: Any) -> int:
    try:
        cents = int(round(float(daily_budget_usd) * 100))
    except (TypeError, ValueError):
        cents = int(round(_DEFAULT_DAILY_BUDGET_USD * 100))
    # Meta enforces a per-ad-set daily-budget floor; keep a sane minimum.
    return max(cents, 100)


def _targeting_json(args: Mapping[str, Any]) -> str:
    """Geo-only broad targeting, Facebook-only by default (no Instagram required).

    A caller may pass a ready ``targeting`` (dict or JSON string) to override; otherwise we build a
    US-geo + facebook-feed default. Interest targeting is intentionally NOT invented here — geo-only
    broad until verified numeric interest ids are supplied upstream.
    """
    override = args.get("targeting")
    if isinstance(override, str) and override.strip():
        return override.strip()
    if isinstance(override, Mapping):
        return json.dumps(dict(override), ensure_ascii=False)
    countries = args.get("geo_countries")
    if isinstance(countries, (list, tuple)) and countries:
        geo = [str(c).strip().upper() for c in countries if str(c).strip()]
    else:
        geo = list(_DEFAULT_GEO_COUNTRIES)
    targeting = {
        "geo_locations": {"countries": geo},
        "publisher_platforms": ["facebook"],
        "facebook_positions": ["feed"],
    }
    return json.dumps(targeting, ensure_ascii=False)


def _extract_id(payload: Any, *keys: str) -> str:
    """Tolerant id extractor for MCP responses.

    The MCP returns ``{content:[{type:'text',text:'{...json...}'}], structuredContent:{...}}`` and
    ``meta_mcp.call_tool`` already collapses that to a dict (structuredContent preferred, else parsed
    text). We still look across a few shapes: top-level keys, a nested ``result``/``data``/``entity``
    object, and a generic ``id`` fallback.
    """
    candidates: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        candidates.append(payload)
        for nested_key in ("result", "data", "entity", "object", "structuredContent"):
            nested = payload.get(nested_key)
            if isinstance(nested, Mapping):
                candidates.append(nested)
    for candidate in candidates:
        for key in keys:
            value = candidate.get(key)
            if value not in (None, ""):
                return str(value).strip()
    # Generic id fallback only after the specific keys miss.
    for candidate in candidates:
        value = candidate.get("id")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _slugify(value: str, fallback: str = "meta-ad") -> str:
    return core._file_slug(str(value or ""), fallback)


def _read_receipt_if_done(receipt_abs: Path, idempotency_key: str) -> dict[str, Any] | None:
    """Idempotency: return a prior receipt only if it is complete (all required ids present).

    An incomplete prior receipt (a partial launch) returns ``None`` so the caller can re-attempt /
    repair rather than falsely claim done.
    """
    prior = core._read_existing_receipt(receipt_abs, idempotency_key)
    if prior is None:
        return None
    status = str(prior.get("status") or "").strip().lower()
    if status in {"test_receipt", "suppressed_test_mode", "created_paused", "activated", "blocked_insufficient_credits"}:
        ids = prior.get("ids") if isinstance(prior.get("ids"), Mapping) else {}
        # test/blocked receipts never created provider objects → always idempotent-complete.
        if status in {"test_receipt", "suppressed_test_mode", "blocked_insufficient_credits"}:
            return prior
        if all(str(ids.get(key) or "").strip() for key in _REQUIRED_IDS):
            return prior
    return None


def _write_receipt(business: str, receipt_rel: str, receipt: Mapping[str, Any]) -> None:
    store = core._store()
    abs_path = store._resolve_business_file(business, receipt_rel)
    receipt_text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    core._atomic_write_text(abs_path, receipt_text)
    # Re-read what we wrote before any success is claimed (truthful-receipt rule).
    reread = json.loads(abs_path.read_text(encoding="utf-8"))
    if reread.get("idempotency_key") != receipt.get("idempotency_key"):
        raise core.TakyonError(f"receipt write verification failed for {receipt_rel}")
    plan_rel = str(receipt.get("plan_path") or "").strip()
    plan_text = ""
    if plan_rel:
        plan_abs = store._resolve_business_file(business, plan_rel, sync=False)
        if plan_abs.is_file():
            plan_text = plan_abs.read_text(encoding="utf-8")

    def _reassert_written_files(root: Path) -> None:
        if plan_rel and plan_text:
            core._atomic_write_text(root / plan_rel, plan_text)
        core._atomic_write_text(root / receipt_rel, receipt_text)

    sync_status = store._sync_business_workspace_remote(business, before_attempt=_reassert_written_files)
    if sync_status != "synced":
        raise core.TakyonError(
            f"receipt workspace sync failed for {receipt_rel}: {sync_status or 'unknown'}"
        )

    committed_store = core._store()
    committed_abs = committed_store._resolve_business_file(business, receipt_rel)
    committed = json.loads(committed_abs.read_text(encoding="utf-8"))
    if committed.get("idempotency_key") != receipt.get("idempotency_key"):
        raise core.TakyonError(f"committed receipt verification failed for {receipt_rel}")
    if committed.get("status") != receipt.get("status"):
        raise core.TakyonError(f"committed receipt status mismatch for {receipt_rel}")
    if isinstance(receipt.get("ids"), Mapping) and committed.get("ids") != receipt.get("ids"):
        raise core.TakyonError(f"committed receipt ids mismatch for {receipt_rel}")


def _is_test_mode(store: Any, business: str) -> bool:
    # Mirrors how core gates external side effects elsewhere (e.g. magic-link send):
    # business mode == "test" ⇒ suppress all external calls and write a local receipt.
    try:
        return core._business_mode(store, business) == "test"
    except Exception as exc:
        raise core.TakyonError(
            f"could not determine business mode; refusing to launch to avoid unintended spend: {exc}"
        )


def _launch_receipt_rel(slug: str) -> str:
    return f"distribution/meta-ads/{slug}/receipt.json"


def _load_launch_receipt(store: Any, business: str, slug: str) -> Mapping[str, Any]:
    receipt_rel = _launch_receipt_rel(slug)
    receipt_abs = store._resolve_business_file(business, receipt_rel)
    if not receipt_abs.is_file():
        raise core.TakyonError(
            f"Meta launch receipt not found at {receipt_rel}; launch the paused Meta ad first"
        )
    try:
        receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    except Exception as exc:
        raise core.TakyonError(f"Meta launch receipt is unreadable at {receipt_rel}: {exc}") from exc
    if not isinstance(receipt, Mapping):
        raise core.TakyonError(f"Meta launch receipt at {receipt_rel} is not a JSON object")
    return receipt


def _launch_receipt_ids(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    ids = receipt.get("ids")
    if not isinstance(ids, Mapping):
        raise core.TakyonError("Meta launch receipt does not contain launched object ids")
    return ids


def _authorized_meta_object_ids(receipt: Mapping[str, Any]) -> set[str]:
    ids = _launch_receipt_ids(receipt)
    return {
        value
        for value in (
            str(ids.get("campaign_id") or "").strip(),
            str(ids.get("adset_id") or "").strip(),
            str(ids.get("ad_id") or "").strip(),
        )
        if value
    }


def _receipt_object_id_for_level(level: str, receipt: Mapping[str, Any]) -> str:
    key = {
        "campaign": "campaign_id",
        "adset": "adset_id",
        "ad": "ad_id",
    }.get(str(level or "").strip().lower())
    if not key:
        raise core.TakyonError("level must be one of: campaign, adset, ad")
    value = str(_launch_receipt_ids(receipt).get(key) or "").strip()
    if not value:
        raise core.TakyonError(f"Meta launch receipt does not contain {key} for level={level!r}")
    return value


def _require_authorized_meta_object_id(
    store: Any,
    business: str,
    slug: str,
    object_id: str,
) -> Mapping[str, Any]:
    receipt = _load_launch_receipt(store, business, slug)
    if str(object_id or "").strip() not in _authorized_meta_object_ids(receipt):
        raise core.TakyonError("target object does not belong to this business's launched Meta ad")
    return receipt


def _stage_thumbnail_bytes(store: Any, business: str, args: Mapping[str, Any], plan_slug: str) -> bytes | None:
    """Resolve thumbnail bytes for a video creative (Meta requires an image_hash/url for video).

    Accepts an explicit ``thumbnail_path`` (business-relative) or the conventional
    ``product/ugc-ads/<slug>/thumbnail.png``. Returns ``None`` if no thumbnail is available; the launch
    handler then surfaces a clear error rather than creating a thumbnail-less video creative.
    """
    rel = str(_arg(args, "thumbnail_path", "thumbnail_rel") or "").strip()
    candidates = []
    if rel:
        candidates.append(rel)
    candidates.append(f"product/ugc-ads/{plan_slug}/thumbnail.png")
    candidates.append(f"product/ugc-ads/{plan_slug}/thumbnail.jpg")
    for candidate in candidates:
        try:
            abs_path = store._resolve_business_file(business, candidate)
        except Exception:
            continue
        if abs_path.is_file():
            return abs_path.read_bytes()
    return None


# ── 1. LAUNCH ───────────────────────────────────────────────────────────────────────────────────────
def handle_business_meta_ad_launch(args: dict, **_: Any) -> str:
    """Launch a Meta ad end-to-end: upload creative via Graph (system-user), create ad objects via MCP.

    Sequence:
      1. Resolve the launch plan (ad_account/page/instagram, asset kind/path, copy, budget, mode).
      2. Read the generated creative bytes from the workspace store.
      3. Upload to Meta with the SYSTEM-USER token (Graph): video→/advideos (+thumbnail), image→/adimages.
      4. Create ad objects with the MCP token: creative → campaign → ad set → ad (all PAUSED).
      5. Activate (Graph set_status ACTIVE) only when mode=='live' and not test-mode; else stay PAUSED.
      6. Write a truthful receipt with the real ids + status + mode.
    """
    business = ""
    receipt_rel = ""
    plan_rel = ""
    created_ids: dict[str, str] = {}
    reservation_key = ""
    credit_metadata: dict[str, Any] = {}
    credits_committed = False
    credits_released = False
    media_reservation_key = ""
    media_reserved = False
    slug = ""
    try:
        store = core._store()
        business = core._resolved_business_slug(args, required=True)
        idempotency_key = _require_idempotency_key(args)

        # ── 1. Resolve the launch plan ──
        ad_account_id = _resolve_config_value(args, ("ad_account_id",), ("META_AD_ACCOUNT_ID",))
        if not ad_account_id:
            raise core.TakyonError("ad_account_id is required (arg ad_account_id or Safebox META_AD_ACCOUNT_ID)")
        page_id = _resolve_config_value(args, ("page_id",), ("META_PAGE_ID",))
        if not page_id:
            raise core.TakyonError("page_id is required (arg page_id or Safebox META_PAGE_ID)")
        instagram_user_id = _resolve_config_value(args, ("instagram_user_id",), ("META_INSTAGRAM_ID",))

        asset_kind = str(_arg(args, "asset_kind", default="video") or "video").strip().lower()
        if asset_kind not in {"video", "image"}:
            raise core.TakyonError("asset_kind must be 'video' or 'image'")

        slug = _slugify(str(_arg(args, "slug", "name") or business), business or "meta-ad")
        asset_rel = str(_arg(args, "asset_path", "asset_rel") or "").strip()
        if not asset_rel:
            asset_rel = (
                f"product/ugc-ads/{slug}/ad.mp4"
                if asset_kind == "video"
                else f"product/static-ads/{slug}/{slug}.png"
            )

        link_url = str(_arg(args, "link_url", "link") or "").strip()
        if not link_url:
            raise core.TakyonError("link_url is required")
        message = str(_arg(args, "message", "primary_text", "body") or "").strip()
        headline = str(_arg(args, "headline", "title") or "").strip()
        call_to_action_type = str(_arg(args, "call_to_action_type", "cta", default=_DEFAULT_CALL_TO_ACTION)).strip().upper()
        objective = str(_arg(args, "objective", default=_DEFAULT_OBJECTIVE)).strip().upper()
        daily_budget_usd = _arg(args, "daily_budget_usd", "daily_budget", default=_DEFAULT_DAILY_BUDGET_USD)
        daily_budget_cents = _budget_cents(daily_budget_usd)
        mode = str(_arg(args, "mode", default="paused") or "paused").strip().lower()
        if mode not in {"paused", "live"}:
            raise core.TakyonError("mode must be 'paused' or 'live'")

        pub_rel = f"distribution/meta-ads/{slug}"
        plan_rel = f"{pub_rel}/plan.json"
        receipt_rel = f"{pub_rel}/receipt.json"
        receipt_abs = store._resolve_business_file(business, receipt_rel)

        # ── Idempotency: a prior COMPLETE receipt short-circuits ──
        prior = _read_receipt_if_done(receipt_abs, idempotency_key)
        if prior is not None:
            return core.tool_result({
                "success": True,
                "action": "business_meta_ad_launch",
                "business": business,
                "slug": slug,
                "idempotent": True,
                "status": prior.get("status"),
                "receipt": receipt_rel,
                "value": prior,
            })

        plan = {
            "idempotency_key": idempotency_key,
            "business": business,
            "slug": slug,
            "ad_account_id": ad_account_id,
            "page_id": page_id,
            "instagram_user_id": instagram_user_id or None,
            "asset_kind": asset_kind,
            "asset_path": asset_rel,
            "link_url": link_url,
            "message": message,
            "headline": headline,
            "call_to_action_type": call_to_action_type,
            "objective": objective,
            "daily_budget_usd": round(daily_budget_cents / 100.0, 2),
            "daily_budget_cents": daily_budget_cents,
            "mode": mode,
            "created_at": core._now(),
        }
        core._atomic_write_text(
            store._resolve_business_file(business, plan_rel),
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        )

        base_receipt = dict(plan)
        base_receipt["plan_path"] = plan_rel

        # ── Test mode: suppress all external calls, write a truthful local receipt ──
        if _is_test_mode(store, business):
            receipt = {
                **base_receipt,
                "success": True,
                "status": "test_receipt",
                "external_side_effects": "suppressed",
                "ids": {},
                "note": "Test mode recorded the Meta ad launch plan locally; no Meta objects were created.",
            }
            _write_receipt(business, receipt_rel, receipt)
            return core.tool_result({
                "success": True,
                "action": "business_meta_ad_launch",
                "business": business,
                "slug": slug,
                "mode": "test",
                "status": "test_receipt",
                "external_side_effects": "suppressed",
                "receipt": receipt_rel,
                "value": receipt,
            })

        # ── Channel-credit guard (reserve before any external spend-bearing work) ──
        reservation_key = f"{idempotency_key}:creative-credits"
        credit_metadata = {
            "business": business,
            "slug": slug,
            "receipt_path": receipt_rel,
            "asset_kind": asset_kind,
            "mode": mode,
        }
        try:
            core._reserve_creative_credits(
                business,
                action=_CREATIVE_ACTION,
                reservation_key=reservation_key,
                budget_bucket=_BUDGET_BUCKET,
                metadata=credit_metadata,
            )
        except (core._creative_credit_backend().InsufficientCreativeCredits, core.CreativeCreditBudgetExceeded) as exc:
            receipt = {
                **base_receipt,
                "success": False,
                "status": "blocked_insufficient_credits",
                "external_side_effects": "none",
                "ids": {},
                "error": str(exc),
            }
            _write_receipt(business, receipt_rel, receipt)
            return core.tool_result({
                "success": False,
                "action": "business_meta_ad_launch",
                "business": business,
                "slug": slug,
                "status": "blocked_insufficient_credits",
                "receipt": receipt_rel,
                "error": str(exc),
                "value": receipt,
            })

        # ── Media-spend budget authority (parity with the reddit launch rail) ──
        # Live ad delivery must be capped by reserved channel credits BEFORE any provider objects
        # exist: reserve the remaining meta channel credits as the campaign's total budget
        # authority, derive a bounded schedule from them, and register the campaign in the
        # canonical ad-spend policy registry after creation. This is what makes a meta campaign
        # visible to the wake pulse (active_ad_campaigns), eligible for the pre-wake insights
        # refresh, enforceable by the gateway budget gate, and auto-settled/paused at its cap —
        # none of which previously applied to meta (2026-07-04 parity fix).
        budget_snapshot = core._creative_credit_budget_snapshot(business)
        meta_budget = (
            budget_snapshot.get("channels", {}).get("meta", {})
            if isinstance(budget_snapshot.get("channels"), Mapping)
            else {}
        )
        remaining_channel_credits = core._creative_credit_int(meta_budget.get("remaining_credits"))
        media_spend_credits = core._ad_channel_live_media_spend_credits(
            "meta", remaining_channel_credits
        )
        schedule = core._derive_ad_spend_schedule(
            channel="meta",
            reserved_credits=media_spend_credits,
            requested_daily_budget_usd=round(daily_budget_cents / 100.0, 2),
        )
        daily_budget_cents = int(round(float(schedule["daily_budget_usd"]) * 100))
        plan["daily_budget_usd"] = schedule["daily_budget_usd"]
        plan["daily_budget_cents"] = daily_budget_cents
        plan["total_budget_usd"] = schedule["total_budget_usd"]
        plan["start_at"] = schedule["start_at"]
        plan["end_at"] = schedule["end_at"]
        base_receipt.update({
            "daily_budget_usd": schedule["daily_budget_usd"],
            "total_budget_usd": schedule["total_budget_usd"],
            "start_at": schedule["start_at"],
            "end_at": schedule["end_at"],
        })
        media_reservation_key = f"{idempotency_key}:meta-media-spend"
        core._reserve_channel_spend_credits(
            business,
            channel="meta",
            requested_credits=media_spend_credits,
            reservation_key=media_reservation_key,
            metadata={
                "slug": slug,
                "receipt_path": receipt_rel,
                "plan_path": plan_rel,
                "activation_requested": mode == "live",
            },
        )
        media_reserved = True

        # ── 2. Fetch the generated creative bytes from the workspace store ──
        asset_abs = store._resolve_business_file(business, asset_rel)
        if not asset_abs.is_file():
            sibling = "ugc-video-ad" if asset_kind == "video" else "static-ad-creative-generator"
            raise core.TakyonError(
                f"creative not found at {asset_rel}; build it with the {sibling} skill first"
            )
        raw = asset_abs.read_bytes()

        numeric_account = _numeric_account_id(ad_account_id)

        creative_media: dict[str, Any] = {}

        # ── 3. Upload the creative bytes to Meta through the Safebox-held SYSTEM-USER token ──
        if asset_kind == "video":
            video_id = str(core.safebox.meta_graph_upload_video(
                ad_account_id=ad_account_id,
                video_bytes=raw,
                name=f"{slug}-video",
            )
            ).strip()
            created_ids["video_id"] = video_id
            # Video creatives also require a thumbnail image_hash/url.
            thumb_bytes = _stage_thumbnail_bytes(store, business, args, slug)
            if thumb_bytes is None:
                raise core.TakyonError(
                    "video creative requires a thumbnail; provide thumbnail_path or "
                    f"product/ugc-ads/{slug}/thumbnail.png"
                )
            thumb = core.safebox.meta_graph_upload_image(
                ad_account_id=ad_account_id,
                image_bytes=thumb_bytes,
                name=f"{slug}-thumb",
            )
            creative_media["video_id"] = video_id
            if thumb.get("hash"):
                creative_media["image_hash"] = thumb["hash"]
            elif thumb.get("url"):
                creative_media["image_url"] = thumb["url"]
            created_ids["thumbnail_hash"] = str(thumb.get("hash") or "")
        else:
            image = core.safebox.meta_graph_upload_image(
                ad_account_id=ad_account_id,
                image_bytes=raw,
                name=f"{slug}-image",
            )
            if image.get("hash"):
                creative_media["image_hash"] = image["hash"]
                created_ids["image_hash"] = image["hash"]
            elif image.get("url"):
                creative_media["image_url"] = image["url"]
                created_ids["image_url"] = image["url"]
            else:
                raise core.TakyonError("image upload returned neither a hash nor a url")

        # ── 4. Create the ad objects with the official MCP broker (creative → campaign → ad set → ad) ──
        def _mcp(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return core.safebox.meta_mcp_call(
                tool_name=tool_name,
                arguments=arguments,
            )

        creative_args = {
            "ad_account_id": numeric_account,
            "page_id": page_id,
            "link_url": link_url,
            "message": message,
            "name": f"{slug}-creative",
            "headline": headline,
            "call_to_action_type": call_to_action_type,
            **creative_media,
        }
        if instagram_user_id:
            creative_args["instagram_user_id"] = instagram_user_id
        creative_resp = _mcp("ads_create_creative", creative_args)
        creative_id = _extract_id(creative_resp, "creative_id")
        if not creative_id:
            raise core.TakyonError(f"ads_create_creative returned no creative_id: {creative_resp!r}")
        created_ids["creative_id"] = creative_id

        campaign_resp = _mcp("ads_create_campaign", {
            "ad_account_id": numeric_account,
            "campaign_name": f"{slug}-campaign",
            "objective": objective,
            "buying_type": "AUCTION",
            "special_ad_categories": "[]",
        })
        campaign_id = _extract_id(campaign_resp, "campaign_id")
        if not campaign_id:
            raise core.TakyonError(f"ads_create_campaign returned no campaign_id: {campaign_resp!r}")
        created_ids["campaign_id"] = campaign_id

        adset_resp = _mcp("ads_create_ad_set", {
            "ad_account_id": numeric_account,
            "campaign_id": campaign_id,
            "ad_set_name": f"{slug}-adset",
            "daily_budget": daily_budget_cents,
            "billing_event": _DEFAULT_BILLING_EVENT,
            "optimization_goal": _DEFAULT_OPTIMIZATION_GOAL,
            "bid_strategy": _DEFAULT_BID_STRATEGY,
            "targeting": _targeting_json(args),
        })
        adset_id = _extract_id(adset_resp, "adset_id", "ad_set_id")
        if not adset_id:
            raise core.TakyonError(f"ads_create_ad_set returned no adset_id: {adset_resp!r}")
        created_ids["adset_id"] = adset_id

        ad_resp = _mcp("ads_create_ad", {
            "ad_account_id": numeric_account,
            "ad_set_id": adset_id,
            "ad_name": f"{slug}-ad",
            "creative": json.dumps({"creative_id": creative_id}),
        })
        ad_id = _extract_id(ad_resp, "ad_id")
        if not ad_id:
            raise core.TakyonError(f"ads_create_ad returned no ad_id: {ad_resp!r}")
        created_ids["ad_id"] = ad_id

        # ── Register the campaign in the canonical ad-spend policy registry (before activation,
        # mirroring the reddit launch: the budget authority row must exist before delivery can
        # spend). This row is what the wake pulse, pre-wake refresh, budget gate, and
        # settle/auto-pause rails key on.
        core._upsert_ad_spend_policy(
            business,
            channel="meta",
            slug=slug,
            reservation_key=media_reservation_key,
            reserved_credits=media_spend_credits,
            daily_budget_cents=daily_budget_cents,
            total_budget_cents=media_spend_credits,
            start_at=core._parse_iso_datetime(schedule["start_at"]) or _dt_now(),
            end_at=core._parse_iso_datetime(schedule["end_at"]) or (_dt_now() + timedelta(days=1)),
            provider_account_id=str(ad_account_id or "") or None,
            provider_campaign_id=str(campaign_id or "") or None,
            provider_group_id=str(adset_id or "") or None,
            provider_ad_id=str(ad_id or "") or None,
            provider_post_id=str(creative_id or "") or None,
            status="created_paused",
            metadata={
                "receipt_path": receipt_rel,
                "plan_path": plan_rel,
                "activation_requested": mode == "live",
            },
        )

        # ── 5. Activate only when mode=='live' (and not test-mode, already gated) ──
        status = "created_paused"
        activated = False
        if mode == "live":
            # campaign → ad set → ad, all via the Safebox-held Graph system-user token.
            core.safebox.meta_graph_forward(method="POST", path=campaign_id, params={"status": "ACTIVE"})
            core.safebox.meta_graph_forward(method="POST", path=adset_id, params={"status": "ACTIVE"})
            core.safebox.meta_graph_forward(method="POST", path=ad_id, params={"status": "ACTIVE"})
            status = "activated"
            activated = True
            core._update_ad_spend_policy(
                business,
                channel="meta",
                slug=slug,
                status="active",
                metadata_patch={"activated_at": core._now()},
            )

        # ── Commit the reserved credits now that provider objects exist ──
        core._commit_creative_credits(
            reservation_key,
            action=_CREATIVE_ACTION,
            budget_bucket=_BUDGET_BUCKET,
            metadata=credit_metadata,
        )
        credits_committed = True

        # ── 6. Write the truthful receipt with the real ids + status + mode ──
        receipt = {
            **base_receipt,
            "success": True,
            "status": status,
            "activated": activated,
            "paused": not activated,
            "external_side_effects": "created",
            "ids": {
                "creative_id": creative_id,
                "campaign_id": campaign_id,
                "adset_id": adset_id,
                "ad_id": ad_id,
                **({"video_id": created_ids["video_id"]} if "video_id" in created_ids else {}),
                **({"image_hash": created_ids["image_hash"]} if "image_hash" in created_ids else {}),
            },
            "credits_committed": True,
        }
        _write_receipt(business, receipt_rel, receipt)
        return core.tool_result({
            "success": True,
            "action": "business_meta_ad_launch",
            "business": business,
            "slug": slug,
            "mode": mode,
            "status": status,
            "paused": not activated,
            "receipt": receipt_rel,
            "ids": receipt["ids"],
            "value": receipt,
        })

    except Exception as exc:
        if reservation_key and not credits_committed:
            try:
                core._release_creative_credits(
                    reservation_key,
                    action=_CREATIVE_ACTION,
                    budget_bucket=_BUDGET_BUCKET,
                    metadata=credit_metadata,
                )
                credits_released = True
            except Exception:
                pass
        # Release the media-spend budget hold on failure (parity with reddit): a launch that did
        # not activate must not keep the channel's credits reserved. If provider objects were
        # created before the failure, keep the policy row truthful as partial_failed so a later
        # repair/cleanup can find the real ids.
        if media_reserved:
            try:
                core._release_channel_spend_credits(
                    media_reservation_key,
                    business=business,
                    channel="meta",
                    metadata={"slug": slug, "status": "launch_failed", "error": str(exc)[:300]},
                )
            except Exception:
                pass
        if created_ids and business and slug:
            try:
                core._update_ad_spend_policy(
                    business,
                    channel="meta",
                    slug=slug,
                    status="partial_failed",
                    metadata_patch={"error": str(exc)[:300], "ids": dict(created_ids)},
                )
            except Exception:
                pass
        # A partial launch must leave a repair-able receipt recording any ids already created so a
        # retry can finish (or a human can clean up) — never claim success, never drop the ids.
        if business and receipt_rel and created_ids:
            try:
                store = core._store()
                receipt_abs = store._resolve_business_file(business, receipt_rel)
                base = core._read_existing_receipt(receipt_abs, str(args.get("idempotency_key") or "")) or {}
                partial = {
                    **base,
                    "idempotency_key": str(args.get("idempotency_key") or ""),
                    "business": business,
                    "slug": str(args.get("slug") or base.get("slug") or ""),
                    "plan_path": plan_rel or base.get("plan_path"),
                    "success": False,
                    "status": "partial_failed",
                    "external_side_effects": "partial",
                    "ids": created_ids,
                    "error": str(exc),
                    "credits_committed": credits_committed,
                    "credits_released": credits_released,
                    "updated_at": core._now(),
                }
                _write_receipt(business, receipt_rel, partial)
            except Exception:
                pass
        return core.tool_error(str(exc), success=False, ids=created_ids or None)


# ── 2. CONTROL (set_budget | pause | activate) ────────────────────────────────────────────────────
def handle_business_meta_ad_control(args: dict, **_: Any) -> str:
    """Lifecycle control via the Graph system-user token.

    Operations: ``set_budget`` (meta_graph.update_daily_budget on the ad set),
    ``pause`` / ``activate`` (meta_graph.set_status). The MCP cannot hard-delete; pause is the floor.
    Writes ``distribution/meta-ads/<slug>/actions/<idempotency_key>.json``.
    """
    try:
        store = core._store()
        business = core._resolved_business_slug(args, required=True)
        idempotency_key = _require_idempotency_key(args)

        operation = str(_arg(args, "operation", "action_type") or "").strip().lower()
        if operation not in {"set_budget", "pause", "activate"}:
            raise core.TakyonError("operation must be one of: set_budget, pause, activate")
        object_id = str(_arg(args, "object_id", "ad_id", "adset_id", "campaign_id") or "").strip()
        if not object_id:
            raise core.TakyonError("object_id is required")

        slug = _slugify(str(_arg(args, "slug", "name") or business), business or "meta-ad")
        action_rel = f"distribution/meta-ads/{slug}/actions/{_slugify(idempotency_key, idempotency_key)}.json"
        action_abs = store._resolve_business_file(business, action_rel)

        prior = core._read_existing_receipt(action_abs, idempotency_key)
        if prior is not None and prior.get("success"):
            return core.tool_result({
                "success": True,
                "action": "business_meta_ad_control",
                "business": business,
                "operation": operation,
                "idempotent": True,
                "receipt": action_rel,
                "value": prior,
            })

        base_action = {
            "idempotency_key": idempotency_key,
            "business": business,
            "slug": slug,
            "operation": operation,
            "object_id": object_id,
            "created_at": core._now(),
        }

        if _is_test_mode(store, business):
            action = {
                **base_action,
                "success": True,
                "status": "test_receipt",
                "external_side_effects": "suppressed",
            }
            _write_receipt(business, action_rel, action)
            return core.tool_result({
                "success": True,
                "action": "business_meta_ad_control",
                "business": business,
                "operation": operation,
                "status": "test_receipt",
                "receipt": action_rel,
                "value": action,
            })

        if operation == "set_budget":
            receipt = _require_authorized_meta_object_id(store, business, slug, object_id)
            daily_budget_cents = _budget_cents(
                _arg(args, "daily_budget_usd", "daily_budget", default=_DEFAULT_DAILY_BUDGET_USD)
            )
            expected_adset_id = _receipt_object_id_for_level("adset", receipt)
            if object_id != expected_adset_id:
                raise core.TakyonError("set_budget target must be the launched ad set for this business")
            core._assert_ad_set_budget_authorized(
                channel="meta",
                business=business,
                slug=slug,
                target_id=object_id,
                daily_budget_cents=daily_budget_cents,
            )
            result = core.safebox.meta_graph_forward(
                method="POST",
                path=object_id,
                params={"daily_budget": daily_budget_cents},
            )
            applied = {"daily_budget_cents": daily_budget_cents}
        elif operation == "pause":
            _require_authorized_meta_object_id(store, business, slug, object_id)
            result = core.safebox.meta_graph_forward(
                method="POST",
                path=object_id,
                params={"status": "PAUSED"},
            )
            applied = {"status": "PAUSED"}
        else:  # activate
            _require_authorized_meta_object_id(store, business, slug, object_id)
            result = core.safebox.meta_graph_forward(
                method="POST",
                path=object_id,
                params={"status": "ACTIVE"},
            )
            applied = {"status": "ACTIVE"}

        action = {
            **base_action,
            "success": True,
            "status": "applied",
            "external_side_effects": "applied",
            "applied": applied,
            "provider_response": result,
        }
        _write_receipt(business, action_rel, action)
        return core.tool_result({
            "success": True,
            "action": "business_meta_ad_control",
            "business": business,
            "operation": operation,
            "object_id": object_id,
            "status": "applied",
            "applied": applied,
            "receipt": action_rel,
            "value": action,
        })
    except Exception as exc:
        return core.tool_error(str(exc), success=False)


# ── 3. INSIGHTS SYNC ──────────────────────────────────────────────────────────────────────────────
def handle_business_meta_ad_insights_sync(args: dict, **_: Any) -> str:
    """Pull Graph insights (system-user token) and append deduped rows to insights.jsonl.

    GETs ``act_<id>/insights`` (or per-object insights when an object_id is supplied) and appends
    rows keyed by ``level + object_id + date_start`` to
    ``metrics/meta-ads/<slug>/insights.jsonl`` (dedup on that key), plus a sync receipt under
    ``metrics/meta-ads/<slug>/syncs/<idempotency_key>.json``.
    """
    try:
        store = core._store()
        business = core._resolved_business_slug(args, required=True)
        idempotency_key = _require_idempotency_key(args)

        slug = _slugify(str(_arg(args, "slug", "name") or business), business or "meta-ad")
        level = str(_arg(args, "level", default="ad") or "ad").strip().lower()
        object_id = str(_arg(args, "object_id", "ad_id", "adset_id", "campaign_id") or "").strip()
        date_preset = str(_arg(args, "date_preset", default="last_7d") or "last_7d").strip()

        insights_rel = f"metrics/meta-ads/{slug}/insights.jsonl"
        sync_rel = f"metrics/meta-ads/{slug}/syncs/{_slugify(idempotency_key, idempotency_key)}.json"
        sync_abs = store._resolve_business_file(business, sync_rel)

        prior = core._read_existing_receipt(sync_abs, idempotency_key)
        if prior is not None and prior.get("success"):
            return core.tool_result({
                "success": True,
                "action": "business_meta_ad_insights_sync",
                "business": business,
                "idempotent": True,
                "receipt": sync_rel,
                "value": prior,
            })

        base_sync = {
            "idempotency_key": idempotency_key,
            "business": business,
            "slug": slug,
            "level": level,
            "object_id": object_id or None,
            "date_preset": date_preset,
            "created_at": core._now(),
        }

        if _is_test_mode(store, business):
            sync = {**base_sync, "success": True, "status": "test_receipt", "rows_appended": 0, "external_side_effects": "suppressed"}
            _write_receipt(business, sync_rel, sync)
            return core.tool_result({
                "success": True,
                "action": "business_meta_ad_insights_sync",
                "business": business,
                "status": "test_receipt",
                "receipt": sync_rel,
                "value": sync,
            })

        fields = "impressions,reach,clicks,spend,cpc,cpm,ctr,frequency,actions,date_start,date_stop"
        receipt = _load_launch_receipt(store, business, slug)
        resolved_object_id = object_id
        if resolved_object_id:
            _require_authorized_meta_object_id(store, business, slug, resolved_object_id)
        else:
            resolved_object_id = _receipt_object_id_for_level(level, receipt)
        base_sync["object_id"] = resolved_object_id
        path = f"{resolved_object_id}/insights"
        params = {"level": level, "fields": fields, "date_preset": date_preset}

        resp = core.safebox.meta_graph_forward(method="GET", path=path, params=params)
        rows = resp.get("data") if isinstance(resp, Mapping) else None
        rows = rows if isinstance(rows, list) else []

        # Dedup on level+object_id+date_start against existing rows.
        insights_abs = store._resolve_business_file(business, insights_rel)
        existing_keys: set[str] = set()
        existing_lines: list[str] = []
        if insights_abs.is_file():
            for line in insights_abs.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                existing_lines.append(line)
                try:
                    obj = json.loads(line)
                    existing_keys.add(f"{obj.get('level')}|{obj.get('object_id')}|{obj.get('date_start')}")
                except Exception:
                    continue

        appended = 0
        new_lines: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            record = {
                "level": level,
                "object_id": resolved_object_id or str(row.get("ad_id") or row.get("adset_id") or row.get("campaign_id") or ""),
                "date_start": row.get("date_start"),
                "date_stop": row.get("date_stop"),
                "impressions": core._meta_int_metric(row.get("impressions")),
                "reach": core._meta_int_metric(row.get("reach")),
                "clicks": core._meta_int_metric(row.get("clicks")),
                "spend": str(row.get("spend") or "0"),
                "cpc": row.get("cpc"),
                "cpm": row.get("cpm"),
                "ctr": row.get("ctr"),
                "frequency": row.get("frequency"),
                "actions": row.get("actions"),
                "synced_at": core._now(),
            }
            dedup_key = f"{record['level']}|{record['object_id']}|{record['date_start']}"
            if dedup_key in existing_keys:
                continue
            existing_keys.add(dedup_key)
            new_lines.append(json.dumps(record, ensure_ascii=False))
            appended += 1

        if new_lines:
            content = "\n".join(existing_lines + new_lines) + "\n"
            core._atomic_write_text(insights_abs, content)

        # ── Stamp + settle the canonical ad-spend policy (parity with the reddit sync) ──
        # Every sync stamps insights_synced_at (truthful staleness for the wake pulse /
        # pre-wake refresh) and the running spend; when spend reaches the reserved cap or the
        # window ends, settle the channel credits and pause the ad set (the D9 stop-at-cap
        # belt). Best-effort by design: a stamping failure must never fail the metrics sync,
        # and campaigns launched before the policy registry existed simply have no row.
        settlement: dict[str, Any] | None = None
        try:
            policy = core._load_ad_spend_policy(business, channel="meta", slug=slug)
        except Exception:
            policy = None
        if policy is not None:
            try:
                totals = core._meta_aggregate_insights_rows(
                    [dict(r) for r in rows if isinstance(r, Mapping)]
                )
                synced_spend_cents = max(
                    int(policy.last_synced_spend_cents or 0),
                    int(totals.get("spend_cents") or 0),
                )
                terminal = synced_spend_cents >= int(policy.total_budget_cents or 0)
                if isinstance(policy.end_at, datetime) and policy.end_at <= _dt_now():
                    terminal = True
                if terminal and int(policy.settled_credits or 0) < int(policy.reserved_credits or 0):
                    settled_credits = min(int(policy.reserved_credits or 0), synced_spend_cents)
                    balances = core._settle_channel_spend_credits(
                        policy.reservation_key,
                        business=business,
                        channel="meta",
                        actual_credits=settled_credits,
                        metadata={"slug": slug, "status": "settled"},
                    )
                    core._update_ad_spend_policy(
                        business,
                        channel="meta",
                        slug=slug,
                        status="completed",
                        last_synced_spend_cents=synced_spend_cents,
                        settled_credits=settled_credits,
                        metadata_patch={"settled_at": core._now(), "insights_synced_at": core._now()},
                    )
                    auto_pause: dict[str, Any]
                    try:
                        if policy.provider_group_id:
                            core.safebox.meta_graph_forward(
                                method="POST",
                                path=str(policy.provider_group_id),
                                params={"status": "PAUSED"},
                            )
                        auto_pause = {"success": True, "paused_adset_id": policy.provider_group_id}
                    except Exception as pause_exc:  # noqa: BLE001 - belt, not the primary gate
                        auto_pause = {"success": False, "error": str(pause_exc)[:200]}
                    settlement = {
                        "settled_credits": settled_credits,
                        "balance_credits": balances.get("balance_credits"),
                        "reserved_credits": balances.get("reserved_credits"),
                        "auto_pause": auto_pause,
                    }
                else:
                    core._update_ad_spend_policy(
                        business,
                        channel="meta",
                        slug=slug,
                        last_synced_spend_cents=synced_spend_cents,
                        metadata_patch={"insights_synced_at": core._now()},
                    )
            except Exception:
                pass

        sync = {
            **base_sync,
            "success": True,
            "status": "synced",
            "rows_returned": len(rows),
            "rows_appended": appended,
            "insights_path": insights_rel,
            "external_side_effects": "read",
        }
        if settlement:
            sync["credit_settlement"] = settlement
        _write_receipt(business, sync_rel, sync)
        return core.tool_result({
            "success": True,
            "action": "business_meta_ad_insights_sync",
            "business": business,
            "slug": slug,
            "status": "synced",
            "rows_appended": appended,
            "insights_path": insights_rel,
            "receipt": sync_rel,
            "value": sync,
        })
    except Exception as exc:
        return core.tool_error(str(exc), success=False)


# ── 4. EVALUATE ───────────────────────────────────────────────────────────────────────────────────
def _evaluate_rows(rows: list[Mapping[str, Any]], *, cpa_baseline_usd: float) -> dict[str, Any]:
    """Apply references/benchmarks.md thresholds to aggregated insights → verdict + recommended action."""
    totals = core._meta_aggregate_insights_rows([dict(r) for r in rows])
    ctr = totals.get("ctr")  # percent
    cpc = totals.get("cpc")
    cpm = totals.get("cpm")
    clicks = totals.get("clicks") or 0

    # Learning phase: ad set exits learning after ~50 optimization events in 7d; below that, do not
    # score "poor" on spend alone → verdict 'learning', action 'wait'.
    conversions = 0
    for row in rows:
        actions = row.get("actions")
        if isinstance(actions, list):
            for entry in actions:
                if isinstance(entry, Mapping) and "lead" in str(entry.get("action_type") or "").lower():
                    try:
                        conversions += int(float(entry.get("value") or 0))
                    except (TypeError, ValueError):
                        continue
    if conversions < 50 and clicks > 0:
        return {
            "verdict": "learning",
            "recommended_action": "wait",
            "reason": "Ad set still in Learning (<50 optimization events in 7d); not scored on spend alone.",
            "totals": totals,
            "conversions": conversions,
        }

    # CTR/CPC heuristic thresholds (benchmarks.md "Thresholds").
    poor_signals: list[str] = []
    good_signals: list[str] = []
    if ctr is not None:
        if ctr < 1.0:
            poor_signals.append("ctr")
        elif ctr > 1.5:
            good_signals.append("ctr")
    if cpc is not None:
        if cpc > 3.0:
            poor_signals.append("cpc")
        elif cpc < 2.0:
            good_signals.append("cpc")
    if cpm is not None and cpm > 20.0:
        poor_signals.append("cpm")

    if poor_signals and not good_signals:
        verdict = "bad"
        recommended_action = "refresh_creative"
        reason = f"Poor on {', '.join(poor_signals)} vs heuristic thresholds; refresh the creative."
    elif good_signals and not poor_signals:
        verdict = "good"
        recommended_action = "scale"
        reason = f"Good on {', '.join(good_signals)} past learning; scale budget gradually."
    else:
        verdict = "watch"
        recommended_action = "hold"
        reason = "Mixed signals; hold and re-evaluate after more data."

    return {
        "verdict": verdict,
        "recommended_action": recommended_action,
        "reason": reason,
        "totals": totals,
        "conversions": conversions,
        "good_signals": good_signals,
        "poor_signals": poor_signals,
        "cpa_baseline_usd": cpa_baseline_usd,
    }


def handle_business_meta_ad_evaluate(args: dict, **_: Any) -> str:
    """Read insights.jsonl, apply benchmarks.md thresholds, write evaluations/<idempotency_key>.json."""
    try:
        store = core._store()
        business = core._resolved_business_slug(args, required=True)
        idempotency_key = _require_idempotency_key(args)

        slug = _slugify(str(_arg(args, "slug", "name") or business), business or "meta-ad")
        cpa_baseline_usd = float(_arg(args, "cpa_baseline_usd", default=40.0) or 40.0)

        insights_rel = f"metrics/meta-ads/{slug}/insights.jsonl"
        eval_rel = f"metrics/meta-ads/{slug}/evaluations/{_slugify(idempotency_key, idempotency_key)}.json"
        eval_abs = store._resolve_business_file(business, eval_rel)

        prior = core._read_existing_receipt(eval_abs, idempotency_key)
        if prior is not None and prior.get("success"):
            return core.tool_result({
                "success": True,
                "action": "business_meta_ad_evaluate",
                "business": business,
                "idempotent": True,
                "receipt": eval_rel,
                "value": prior,
            })

        insights_abs = store._resolve_business_file(business, insights_rel)
        rows: list[Mapping[str, Any]] = []
        if insights_abs.is_file():
            for line in insights_abs.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue

        if not rows:
            evaluation = {
                "idempotency_key": idempotency_key,
                "business": business,
                "slug": slug,
                "success": True,
                "status": "no_data",
                "verdict": "no_data",
                "recommended_action": "wait",
                "reason": "No insights rows yet; run business_meta_ad_insights_sync first.",
                "created_at": core._now(),
            }
        else:
            result = _evaluate_rows(rows, cpa_baseline_usd=cpa_baseline_usd)
            evaluation = {
                "idempotency_key": idempotency_key,
                "business": business,
                "slug": slug,
                "success": True,
                "status": "evaluated",
                "rows_evaluated": len(rows),
                "created_at": core._now(),
                **result,
            }
        _write_receipt(business, eval_rel, evaluation)
        return core.tool_result({
            "success": True,
            "action": "business_meta_ad_evaluate",
            "business": business,
            "slug": slug,
            "verdict": evaluation.get("verdict"),
            "recommended_action": evaluation.get("recommended_action"),
            "receipt": eval_rel,
            "value": evaluation,
        })
    except Exception as exc:
        return core.tool_error(str(exc), success=False)


# ── 5. PIXEL VERIFY ───────────────────────────────────────────────────────────────────────────────
def handle_business_meta_pixel_verify(args: dict, **_: Any) -> str:
    """Verify the shared pixel for a business: snippet served + per-business custom conversion present.

    Truthful degradation: ``ok`` is true only when both proofs pass; otherwise report what is
    configured without simulating a healthy pixel. Writes
    ``metrics/meta-pixel/<slug>/preflight.json``.
    """
    try:
        store = core._store()
        business = core._resolved_business_slug(args, required=True)
        idempotency_key = _require_idempotency_key(args)
        slug = _slugify(str(_arg(args, "slug", "name") or business), business or "meta-ad")

        verify_rel = f"metrics/meta-pixel/{slug}/preflight.json"
        verify_abs = store._resolve_business_file(business, verify_rel)
        prior = core._read_existing_receipt(verify_abs, idempotency_key)
        if prior is not None and prior.get("success"):
            return core.tool_result({
                "success": True,
                "action": "business_meta_pixel_verify",
                "business": business,
                "idempotent": True,
                "receipt": verify_rel,
                "value": prior,
            })

        ad_account_id = _resolve_config_value(args, ("ad_account_id",), ("META_AD_ACCOUNT_ID",))
        pixel_id = str(_arg(args, "pixel_id") or "").strip()
        custom_conversion_id = str(_arg(args, "custom_conversion_id") or "").strip()

        base = {
            "idempotency_key": idempotency_key,
            "business": business,
            "slug": slug,
            "ad_account_id": ad_account_id or None,
            "pixel_id": pixel_id or None,
            "created_at": core._now(),
        }

        if _is_test_mode(store, business):
            verify = {**base, "success": True, "status": "test_receipt", "ok": False, "external_side_effects": "suppressed"}
            _write_receipt(business, verify_rel, verify)
            return core.tool_result({
                "success": True,
                "action": "business_meta_pixel_verify",
                "business": business,
                "status": "test_receipt",
                "ok": False,
                "receipt": verify_rel,
                "value": verify,
            })

        # Proof B (Meta side): the per-business custom conversion is registered.
        custom_conversion_ok = False
        last_event = None
        if ad_account_id:
            try:
                resp = core.safebox.meta_graph_forward(
                    method="GET",
                    path=f"{meta_graph.account_path(ad_account_id)}/customconversions",
                    params={"fields": "id,name,custom_event_type,rule,last_fired_time"},
                )
                conversions = resp.get("data") if isinstance(resp, Mapping) else None
                conversions = conversions if isinstance(conversions, list) else []
                for entry in conversions:
                    if not isinstance(entry, Mapping):
                        continue
                    if custom_conversion_id and str(entry.get("id") or "") == custom_conversion_id:
                        custom_conversion_ok = True
                        last_event = entry.get("last_fired_time")
                        break
                    if not custom_conversion_id and slug in str(entry.get("name") or "").lower():
                        custom_conversion_ok = True
                        custom_conversion_id = str(entry.get("id") or "")
                        last_event = entry.get("last_fired_time")
                        break
            except Exception:
                custom_conversion_ok = False

        # Proof A (snippet installed) is a live-fetch concern owned by the publish reachability posture;
        # this verify reports it as caller-supplied evidence (or unknown) rather than simulating it.
        installed = args.get("snippet_installed")
        installed_ok = bool(installed) if installed is not None else None

        ok = bool(custom_conversion_ok) and bool(installed_ok)
        verify = {
            **base,
            "success": True,
            "status": "verified" if ok else "degraded",
            "ok": ok,
            "installed": installed_ok,
            "custom_conversion_ok": custom_conversion_ok,
            "custom_conversion_id": custom_conversion_id or None,
            "last_event": last_event,
            "note": "ok is true only when both the served snippet and the per-business custom conversion are confirmed.",
            "external_side_effects": "read",
        }
        _write_receipt(business, verify_rel, verify)
        return core.tool_result({
            "success": True,
            "action": "business_meta_pixel_verify",
            "business": business,
            "slug": slug,
            "ok": ok,
            "custom_conversion_ok": custom_conversion_ok,
            "receipt": verify_rel,
            "value": verify,
        })
    except Exception as exc:
        return core.tool_error(str(exc), success=False)


# ── 6. PIXEL ENSURE ───────────────────────────────────────────────────────────────────────────────
def handle_business_meta_pixel_ensure(args: dict, **_: Any) -> str:
    """Lazily ensure the per-business custom conversion exists on the ONE shared pixel.

    The shared pixel/dataset itself is created once manually (Events Manager — not an MCP strength).
    Per-business attribution is a URL-rule custom conversion created via
    ``meta_graph.ensure_custom_conversion``. Writes ``metrics/meta-pixel/<slug>/ensure.json``.
    """
    try:
        store = core._store()
        business = core._resolved_business_slug(args, required=True)
        idempotency_key = _require_idempotency_key(args)
        slug = _slugify(str(_arg(args, "slug", "name") or business), business or "meta-ad")

        ensure_rel = f"metrics/meta-pixel/{slug}/ensure.json"
        ensure_abs = store._resolve_business_file(business, ensure_rel)
        prior = core._read_existing_receipt(ensure_abs, idempotency_key)
        if prior is not None and prior.get("success"):
            return core.tool_result({
                "success": True,
                "action": "business_meta_pixel_ensure",
                "business": business,
                "idempotent": True,
                "receipt": ensure_rel,
                "value": prior,
            })

        ad_account_id = _resolve_config_value(args, ("ad_account_id",), ("META_AD_ACCOUNT_ID",))
        if not ad_account_id:
            raise core.TakyonError("ad_account_id is required (arg ad_account_id or Safebox META_AD_ACCOUNT_ID)")
        custom_event_type = str(_arg(args, "custom_event_type", default="LEAD") or "LEAD").strip().upper()
        conversion_path = str(_arg(args, "conversion_path", "conversion_url") or "").strip()
        domain = str(_arg(args, "domain") or f"{slug}.coscale.app").strip()
        url_match = conversion_path or f"{domain}"
        name = str(_arg(args, "conversion_name") or f"{slug}-{custom_event_type.lower()}").strip()
        # URL rule: fire only for this business's traffic (per-business isolation on the shared pixel).
        rule = json.dumps({"url": {"i_contains": url_match}}, ensure_ascii=False)

        base = {
            "idempotency_key": idempotency_key,
            "business": business,
            "slug": slug,
            "ad_account_id": ad_account_id,
            "custom_event_type": custom_event_type,
            "rule": rule,
            "name": name,
            "created_at": core._now(),
        }

        if _is_test_mode(store, business):
            ensure = {**base, "success": True, "status": "test_receipt", "ok": False, "external_side_effects": "suppressed"}
            _write_receipt(business, ensure_rel, ensure)
            return core.tool_result({
                "success": True,
                "action": "business_meta_pixel_ensure",
                "business": business,
                "status": "test_receipt",
                "ok": False,
                "receipt": ensure_rel,
                "value": ensure,
            })

        result = core.safebox.meta_graph_ensure_custom_conversion(
            ad_account_id=ad_account_id,
            name=name,
            rule=rule,
            custom_event_type=custom_event_type,
        )
        custom_conversion_id = str(
            (result or {}).get("id")
            or (result or {}).get("custom_conversion_id")
            or ""
        ).strip()
        ok = bool(custom_conversion_id)
        ensure = {
            **base,
            "success": True,
            "status": "ensured" if ok else "degraded",
            "ok": ok,
            "custom_conversion_id": custom_conversion_id or None,
            "provider_response": result,
            "external_side_effects": "ensured",
        }
        _write_receipt(business, ensure_rel, ensure)
        return core.tool_result({
            "success": True,
            "action": "business_meta_pixel_ensure",
            "business": business,
            "slug": slug,
            "ok": ok,
            "custom_conversion_id": custom_conversion_id or None,
            "receipt": ensure_rel,
            "value": ensure,
        })
    except Exception as exc:
        return core.tool_error(str(exc), success=False)


# ── Tool definitions (core.py .extend()s this onto TAKYON_TOOL_DEFINITIONS) ─────────────────────────
_SLUG_PROP = {"type": "string", "description": "Publication slug under distribution/meta-ads/<slug> (defaults to the business slug)"}
_OBJECT_ID_PROP = {"type": "string", "description": "Meta object id (ad, ad set, or campaign) to act on"}

TAKYON_META_ADS_V2_DEFINITIONS = [
    {
        "name": "business_meta_ad_launch",
        "description": (
            "Launch a Meta ad end-to-end: upload the generated creative (video/image) via the Graph "
            "system-user token, create the ad objects (creative→campaign→ad set→ad) via the official "
            "Meta Ads MCP, and leave PAUSED (mode='paused') or activate (mode='live'). Writes a truthful "
            "receipt with the real provider ids."
        ),
        "handler": handle_business_meta_ad_launch,
        "schema": core._schema(
            "business_meta_ad_launch",
            "Launch a Meta ad from a generated UGC video or static image creative.",
            {
                "business": core._BUSINESS_PROP,
                "idempotency_key": core._IDEMPOTENCY_PROP,
                "slug": _SLUG_PROP,
                "asset_kind": {"type": "string", "enum": ["video", "image"], "description": "Creative kind; default video"},
                "asset_path": {"type": "string", "description": "Business-relative path to the creative bytes; defaults by asset_kind"},
                "thumbnail_path": {"type": "string", "description": "Business-relative thumbnail for a video creative (required for video)"},
                "ad_account_id": {"type": "string", "description": "Meta ad account id; defaults to Safebox META_AD_ACCOUNT_ID"},
                "page_id": {"type": "string", "description": "Facebook page id; defaults to Safebox META_PAGE_ID"},
                "instagram_user_id": {"type": "string", "description": "Optional Instagram user id; defaults to Safebox META_INSTAGRAM_ID"},
                "link_url": {"type": "string", "description": "Destination URL for the ad"},
                "message": {"type": "string", "description": "Primary text / body of the ad"},
                "headline": {"type": "string", "description": "Ad headline"},
                "call_to_action_type": {"type": "string", "description": "CTA enum, default LEARN_MORE"},
                "objective": {"type": "string", "description": "Campaign objective, default OUTCOME_TRAFFIC"},
                "daily_budget_usd": {"type": "number", "description": "Ad set daily budget in USD; default 5"},
                "geo_countries": {"type": "array", "items": {"type": "string"}, "description": "ISO country codes for geo targeting; default ['US']"},
                "targeting": {"type": "string", "description": "Optional ready targeting JSON to override the geo-only default"},
                "mode": {"type": "string", "enum": ["paused", "live"], "description": "paused (default) leaves PAUSED; live activates"},
                "reason": core._REASON_PROP,
                "actor": core._ACTOR_PROP,
            },
            ["business", "idempotency_key", "link_url"],
        ),
    },
    {
        "name": "business_meta_ad_control",
        "description": (
            "Lifecycle control for a Meta ad via the Graph system-user token: set_budget (ad set daily "
            "budget), pause, or activate. The MCP cannot hard-delete — pause is the floor. Writes an "
            "action receipt."
        ),
        "handler": handle_business_meta_ad_control,
        "schema": core._schema(
            "business_meta_ad_control",
            "Set budget, pause, or activate a Meta ad object.",
            {
                "business": core._BUSINESS_PROP,
                "idempotency_key": core._IDEMPOTENCY_PROP,
                "slug": _SLUG_PROP,
                "operation": {"type": "string", "enum": ["set_budget", "pause", "activate"], "description": "Control operation"},
                "object_id": _OBJECT_ID_PROP,
                "daily_budget_usd": {"type": "number", "description": "New daily budget in USD (set_budget only)"},
                "reason": core._REASON_PROP,
                "actor": core._ACTOR_PROP,
            },
            ["business", "idempotency_key", "operation", "object_id"],
        ),
    },
    {
        "name": "business_meta_ad_insights_sync",
        "description": (
            "Pull Graph insights (system-user token) for an account or object and append deduped rows "
            "to metrics/meta-ads/<slug>/insights.jsonl keyed by level+object_id+date."
        ),
        "handler": handle_business_meta_ad_insights_sync,
        "schema": core._schema(
            "business_meta_ad_insights_sync",
            "Sync Meta ad insights into the metrics store.",
            {
                "business": core._BUSINESS_PROP,
                "idempotency_key": core._IDEMPOTENCY_PROP,
                "slug": _SLUG_PROP,
                "level": {"type": "string", "enum": ["ad", "adset", "campaign", "account"], "description": "Insights level; default ad"},
                "object_id": {"type": "string", "description": "Object id to scope insights to; omit to use the ad account"},
                "ad_account_id": {"type": "string", "description": "Ad account id when no object_id; defaults to Safebox META_AD_ACCOUNT_ID"},
                "date_preset": {"type": "string", "description": "Meta date_preset, e.g. last_7d (default)"},
                "reason": core._REASON_PROP,
                "actor": core._ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_ad_evaluate",
        "description": (
            "Read insights.jsonl, apply references/benchmarks.md thresholds (CTR/CPC/CPM + learning "
            "phase), and write a verdict + recommended action to metrics/meta-ads/<slug>/evaluations/<id>.json."
        ),
        "handler": handle_business_meta_ad_evaluate,
        "schema": core._schema(
            "business_meta_ad_evaluate",
            "Evaluate synced Meta ad insights against benchmarks and recommend an action.",
            {
                "business": core._BUSINESS_PROP,
                "idempotency_key": core._IDEMPOTENCY_PROP,
                "slug": _SLUG_PROP,
                "cpa_baseline_usd": {"type": "number", "description": "Per-business CPA baseline in USD; default 40"},
                "reason": core._REASON_PROP,
                "actor": core._ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_pixel_verify",
        "description": (
            "Verify the shared Meta pixel for a business: the served snippet (caller-supplied evidence) "
            "and the per-business custom conversion (read via Graph). ok is true only when both pass."
        ),
        "handler": handle_business_meta_pixel_verify,
        "schema": core._schema(
            "business_meta_pixel_verify",
            "Verify the shared pixel snippet + per-business custom conversion.",
            {
                "business": core._BUSINESS_PROP,
                "idempotency_key": core._IDEMPOTENCY_PROP,
                "slug": _SLUG_PROP,
                "ad_account_id": {"type": "string", "description": "Ad account id; defaults to Safebox META_AD_ACCOUNT_ID"},
                "pixel_id": {"type": "string", "description": "The shared pixel id (informational)"},
                "custom_conversion_id": {"type": "string", "description": "Expected per-business custom conversion id"},
                "snippet_installed": {"type": "boolean", "description": "Live-fetch evidence that the snippet is served (from the publish reachability check)"},
                "reason": core._REASON_PROP,
                "actor": core._ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_pixel_ensure",
        "description": (
            "Lazily ensure the per-business URL-rule custom conversion exists on the ONE shared pixel via "
            "meta_graph.ensure_custom_conversion. The shared pixel/dataset is created once manually."
        ),
        "handler": handle_business_meta_pixel_ensure,
        "schema": core._schema(
            "business_meta_pixel_ensure",
            "Ensure the per-business custom conversion on the shared pixel.",
            {
                "business": core._BUSINESS_PROP,
                "idempotency_key": core._IDEMPOTENCY_PROP,
                "slug": _SLUG_PROP,
                "ad_account_id": {"type": "string", "description": "Ad account id; defaults to Safebox META_AD_ACCOUNT_ID"},
                "custom_event_type": {"type": "string", "description": "Standard event the conversion maps to, e.g. LEAD (default) or PURCHASE"},
                "conversion_path": {"type": "string", "description": "URL substring the rule matches; defaults to the business domain"},
                "domain": {"type": "string", "description": "Business domain; default <slug>.coscale.app"},
                "conversion_name": {"type": "string", "description": "Custom conversion name; defaults to <slug>-<event>"},
                "reason": core._REASON_PROP,
                "actor": core._ACTOR_PROP,
            },
            ["business", "idempotency_key"],
        ),
    },
]
