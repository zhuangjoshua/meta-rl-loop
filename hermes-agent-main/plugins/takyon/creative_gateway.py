"""Internal creative gateway — the server-side broker for live creative/ad actions.

This mirrors the AI gateway pattern narrowly for spendful creative operations: the caller keeps
local planning, receipts, and asset records, while the gateway owns the live provider
credential use plus creative-credit reserve/commit/release.

Calls are machine-facing only and require the dashboard session token header. The gateway never
returns provider secrets; it returns only the result payload needed for the caller to persist
durable Takyon truth outside this boundary.
"""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from .control_api import get_control_conn
from . import safebox

_SESSION_HEADER_NAME = "X-Takyon-Session-Token"
_UNAUTH_HEADERS = {"WWW-Authenticate": _SESSION_HEADER_NAME}

# Brand logo image generation (Nano Banana / Gemini). The model id and its
# aliases for resolving the Safebox-backed key live here so the authority route
# is the single place the live provider credential is touched.
_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
_GEMINI_KEY_ALIASES = ("TAKYON_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def _core():
    from . import core

    return core


def _expected_session_token() -> str:
    token = safebox.read_env_backed_value("TAKYON_DASHBOARD_SESSION_TOKEN")
    if token:
        return token
    home = Path(os.getenv("TAKYON_HOME") or (Path.home() / ".takyon"))
    try:
        return (home / "dashboard_session_token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _require_internal_session(
    request: Request,
    session_token: str | None = Header(default=None, alias=_SESSION_HEADER_NAME),
) -> None:
    # Localhost-only, server-to-server boundary. The runtime calls these endpoints over 127.0.0.1
    # with no proxy headers; any request that transited the public reverse proxy carries
    # X-Forwarded-* (Caddy sets them). Reject those so the creative / ad-spend gateway cannot be
    # reached from app.fourmanifold.com even with the shared dashboard token. (The edge Caddy block
    # on /internal/creative-gateway/* is the belt-and-suspenders.)
    if request.headers.get("x-forwarded-host") or request.headers.get("x-forwarded-for"):
        raise HTTPException(status_code=404, detail="not_found")
    expected = _expected_session_token()
    if not expected:
        raise HTTPException(status_code=503, detail="dashboard_session_token_unavailable")
    supplied = str(session_token or "").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="invalid_dashboard_session_token",
            headers=_UNAUTH_HEADERS,
        )


def _meta_campaign_create_payload(plan: dict[str, Any]) -> dict[str, Any]:
    # Meta now requires an explicit ad-set budget sharing toggle when the
    # campaign itself is not budget-managed. This launch rail always stages
    # a non-CBO campaign and assigns budget at the ad set level.
    payload = {
        "name": plan["campaign_name"],
        "objective": plan["objective"],
        "status": "PAUSED",
        "special_ad_categories": "[]",
        "is_adset_budget_sharing_enabled": False,
    }
    if plan.get("campaign_start_time"):
        payload["start_time"] = plan["campaign_start_time"]
    if plan.get("campaign_end_time"):
        payload["stop_time"] = plan["campaign_end_time"]
    return payload


def _resolve_gemini_image_key() -> str:
    """Resolve the Gemini image key from Safebox-backed env aliases.

    Only the authority route calls this; the business runtime never reads the
    raw key from ``os.environ``. Returns "" when no alias is provisioned so the
    caller can fail closed with a 503 before any provider work.
    """
    try:
        value = safebox.first_env_backed_value(*_GEMINI_KEY_ALIASES)
    except Exception:
        value = ""
    return str(value or "").strip()


def _gemini_logo_prompt(business_context: dict[str, Any]) -> str:
    """Build the brand-logo prompt from concrete business context.

    Brand brief is operator-owned (GOAL_RULES §7): flat vector, icon-only, no
    text. Gemini (gemini-*-flash-image / Nano Banana) ignores a "transparent"
    instruction and bakes an opaque/checkerboard background, so the prompt asks
    for a SOLID PURE-WHITE backdrop instead; ``_gemini_generate_logo_png`` then
    keys that white out to a real alpha channel after generation. The business
    name / category / tone are read from the passed context and steer the icon
    concept; they are never invented here.
    """
    name = str(business_context.get("name") or business_context.get("slug") or "").strip()
    category = str(
        business_context.get("category")
        or business_context.get("industry")
        or business_context.get("vertical")
        or ""
    ).strip()
    tone = str(
        business_context.get("tone")
        or business_context.get("brand_tone")
        or business_context.get("voice")
        or ""
    ).strip()
    lines = [
        "Design a single brand logo icon.",
        "Style: flat vector, minimal, icon-only — NO text, NO letters, NO wordmark.",
        "Render the icon CENTERED on a SOLID PURE-WHITE (#FFFFFF) background with "
        "generous padding around it.",
        "Clean flat-vector opaque art: NO drop shadow, NO gradient, NO checkerboard, "
        "NO photographic texture.",
        "Output one centered icon mark suitable as a scalable brand symbol.",
    ]
    if name:
        lines.append(f"Brand: {name}.")
    if category:
        lines.append(f"Business category: {category} — let the icon evoke this domain.")
    if tone:
        lines.append(f"Brand tone: {tone} — reflect this feeling in form and color.")
    return " ".join(lines)


def _key_white_background_to_alpha(png_bytes: bytes) -> bytes:
    """Turn the solid-white logo backdrop into a REAL alpha channel.

    Gemini bakes an opaque white background (it ignores "transparent"), so the
    prompt asks for a pure-white backdrop and this step keys that white out to
    real transparency. The ramp converts near-white pixels to alpha over a band
    (min-channel ``<=230`` stays fully opaque, ``>=250`` becomes fully
    transparent, linear in between) so anti-aliased edges fade smoothly instead
    of leaving a hard white halo, then crops to the alpha bounding box for tight
    framing.

    FAIL-SAFE: if Pillow/numpy are unavailable or post-processing raises, the
    ORIGINAL bytes are returned untouched — a logo with a baked white background
    is still a usable logo, and this must never crash the already-charged render.
    """
    if not png_bytes:
        return png_bytes
    try:
        from tools.lazy_deps import ensure as _lazy_ensure

        _lazy_ensure("image.logo_postprocess", prompt=False)
    except ImportError:
        pass
    except Exception:  # lazy_deps surfaces install hints; fall through to import
        pass

    try:
        import io

        import numpy as np
        from PIL import Image
    except Exception:
        # Pillow/numpy not importable — return the opaque-white PNG unchanged.
        return png_bytes

    try:
        with Image.open(io.BytesIO(png_bytes)) as im:
            rgba = im.convert("RGBA")
        arr = np.asarray(rgba).astype(np.float32)
        rgb = arr[..., :3]
        alpha = arr[..., 3]
        # Per-pixel "whiteness" = the darkest channel. A pure-white pixel has
        # min-channel 255; a saturated/dark icon pixel has a low min-channel.
        min_channel = rgb.min(axis=2)
        lo, hi = 230.0, 250.0
        # Linear ramp: keyed alpha 255 at/below lo, 0 at/above hi.
        keyed = np.clip((hi - min_channel) / (hi - lo), 0.0, 1.0) * 255.0
        new_alpha = np.minimum(alpha, keyed)
        out = arr.copy()
        out[..., 3] = new_alpha
        out_img = Image.fromarray(out.astype(np.uint8), mode="RGBA")
        # Tight framing: crop to the non-transparent bounding box when present.
        bbox = out_img.getbbox()
        if bbox:
            out_img = out_img.crop(bbox)
        buf = io.BytesIO()
        out_img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Any post-processing failure must not lose the rendered (charged) asset.
        return png_bytes


def _gemini_generate_logo_png(*, api_key: str, prompt: str) -> bytes:
    """Call Gemini image generation and return PNG bytes with a real alpha channel.

    Imports the provider SDK lazily and passes the key as an explicit
    ``genai.Client(api_key=…)`` argument — never via ``os.environ``. Raises on
    any provider/SDK failure so the caller releases the credit reservation.

    Gemini bakes an opaque/checkerboard background even when asked for
    transparency, so the prompt requests a solid pure-white backdrop and the raw
    PNG is post-processed (``_key_white_background_to_alpha``) into real alpha
    before returning. Post-processing fails safe to the original bytes.
    """
    try:
        from tools.lazy_deps import ensure as _lazy_ensure

        _lazy_ensure("image.gemini", prompt=False)
    except ImportError:
        pass
    except Exception:  # lazy_deps surfaces install hints; fall through to import
        pass

    try:
        from google import genai
        from google.genai import types as genai_types
    except Exception as exc:  # provider SDK not installed
        raise RuntimeError(
            "google-genai is not installed; cannot render brand logo"
        ) from exc

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=_GEMINI_IMAGE_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if not data:
                continue
            raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
            return _key_white_background_to_alpha(raw)
    raise RuntimeError("Gemini image generation returned no image data")


def build_creative_gateway_router() -> APIRouter:
    router = APIRouter(prefix="/internal/creative-gateway")

    @router.post("/logo-render")
    def logo_render(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        credits = core._creative_credit_backend()
        store = core._store()
        business = core._resolved_business_slug(body, required=True)
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="idempotency_key is required")

        # Fail closed BEFORE reserving credits or touching the provider when the
        # key is not provisioned: 503 gemini_image_unconfigured.
        api_key = _resolve_gemini_image_key()
        if not api_key:
            raise HTTPException(status_code=503, detail="gemini_image_unconfigured")

        business_context = (
            body.get("business_context")
            if isinstance(body.get("business_context"), dict)
            else {}
        )
        business_context = {**business_context, "slug": business}
        slug = core._file_slug(str(body.get("slug") or business or "logo"), "logo")
        publication_rel = f"product/brand/logos/{slug}"
        asset_rel = f"{publication_rel}/logo.png"
        # Resolve once with the default sync=True to materialize the local cache mirror up front and
        # warm the store's workspace-sync cache, so the publish/commit path below (which resolves with
        # sync=False) operates on a materialized tree. The brand asset itself is (re)written into the
        # mirror by the commit's before_attempt callback, not from this path.
        store._resolve_business_file(business, asset_rel)
        prompt = _gemini_logo_prompt(business_context)

        # Brand-level creative: no channel bucket (budget_bucket="").
        budget_bucket = ""
        reservation_key = f"{idempotency_key}:creative-credits"
        requested_credits = core._creative_credit_total_cost("logo_generate")
        try:
            reservation = core._reserve_creative_credits(
                business,
                action="logo_generate",
                reservation_key=reservation_key,
                budget_bucket=budget_bucket,
                metadata={
                    "business": business,
                    "action": "logo_generate",
                    "slug": slug,
                    "asset_path": asset_rel,
                },
            )
        except credits.InsufficientCreativeCredits as exc:
            balances = credits.get_business_credit_balances(conn, business)
            return {
                "success": False,
                "status": "blocked_insufficient_creative_credits",
                "requested_credits": requested_credits,
                "available_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "error": str(exc),
            }

        provider_cost_usd = core._logo_provider_cost_usd()
        finalized = False
        published_to_site = False
        publish_skipped_reason = ""
        site_logo_url = ""
        try:
            png_bytes = _gemini_generate_logo_png(api_key=api_key, prompt=prompt)
            if not png_bytes:
                raise RuntimeError("Gemini image generation returned empty image")

            source_path = core._canonical_product_surface_source_path("product/site")
            asset_rel_for_root = asset_rel
            # Re-assert the rendered asset AND the published site files into the local cache mirror
            # right before EACH canonical-commit attempt. This runs once normally, and again on a
            # retry after a concurrent ``_business_root(sync=True)`` from another store re-materialized
            # the mirror with ``delete_local=True`` and unlinked our not-yet-committed files between
            # the commit's digest read and its (unguarded) CAS-upload read — the prod bootstrap-logo
            # race that surfaced as ``502 No such file or directory: .../public/brand-logo.png`` and
            # left every fresh business on the monogram favicon. Writing here, then committing in the
            # same retry-protected call, makes the brand asset AND the published site files (public/
            # brand-logo.png + the repointed favicon <link>) durable in one revision. ``workspace_root``
            # is resolved by the caller with ``sync=False`` so this callback never triggers a wipe of
            # the very files it is (re)writing.
            publish_state: dict[str, Any] = {
                "published_to_site": False,
                "site_logo_url": "",
                "publish_skipped_reason": "",
            }

            def _reassert_logo_files(workspace_root: Path) -> None:
                # The asset lives under product/brand/logos/<slug>/logo.png — re-resolve against the
                # (possibly re-materialized) root so the path is always valid for this attempt.
                core._atomic_write_bytes(workspace_root / asset_rel_for_root, png_bytes)
                site_root = workspace_root / source_path
                try:
                    published = bool(
                        core._publish_brand_logo_to_site(site_root, png_bytes=png_bytes)
                    )
                except Exception as publish_exc:
                    # Best-effort publish: never crash the already-rendered (about-to-be-charged) logo.
                    publish_state["published_to_site"] = False
                    publish_state["site_logo_url"] = ""
                    publish_state["publish_skipped_reason"] = f"publish_failed: {publish_exc}"
                    return
                publish_state["published_to_site"] = published
                publish_state["site_logo_url"] = "/brand-logo.png" if published else ""
                publish_state["publish_skipped_reason"] = (
                    "" if published else "publish_returned_false"
                )

            # Persist the rendered logo + published site files to canonical remote storage IMMEDIATELY,
            # before committing the credit. The render wrote the PNG only into the LOCAL cache mirror; a
            # concurrent ``_business_root(sync=True)`` re-materializes that mirror with
            # ``delete_local=True`` and would wipe the logo before any later sync — charging credits for
            # an asset that then vanishes (observed: success+2 credits, but logo.png gone on the next
            # sync). The before_attempt callback re-asserts the files into the mirror immediately before
            # each commit attempt and the commit retries the transient mirror-wipe, so the asset AND the
            # published site files are durable at the point of creation.
            store._sync_business_workspace_remote(business, before_attempt=_reassert_logo_files)
            published_to_site = bool(publish_state["published_to_site"])
            site_logo_url = str(publish_state["site_logo_url"] or "")
            publish_skipped_reason = str(publish_state["publish_skipped_reason"] or "")
            balances = core._commit_creative_credits(
                reservation_key,
                action="logo_generate",
                budget_bucket=budget_bucket,
                metadata={
                    "business": business,
                    "action": "logo_generate",
                    "slug": slug,
                    "provider": "google",
                    "model": _GEMINI_IMAGE_MODEL,
                    "provider_cost_usd": provider_cost_usd,
                },
            )
            finalized = True
            return {
                "success": True,
                "status": "created",
                "asset_path": asset_rel,
                "publication_dir": publication_rel,
                "prompt": prompt,
                "provider": "google",
                "model": _GEMINI_IMAGE_MODEL,
                "provider_cost_usd": provider_cost_usd,
                "credits_charged": requested_credits,
                "balance_credits": balances["balance_credits"],
                "reserved_credits": balances["reserved_credits"],
                "budget_bucket": reservation.get("budget_bucket") if isinstance(reservation, dict) else "",
                "published_to_site": published_to_site,
                "site_logo_url": site_logo_url,
                "publish_skipped_reason": publish_skipped_reason or None,
            }
        except Exception as exc:
            if not finalized:
                try:
                    core._release_creative_credits(
                        reservation_key,
                        action="logo_generate",
                        budget_bucket=budget_bucket,
                        metadata={
                            "business": business,
                            "action": "logo_generate",
                            "slug": slug,
                            "error": str(exc),
                        },
                    )
                except Exception:
                    pass
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    def _create_reddit_structured_post(
        core: Any,
        cfg: dict[str, Any],
        *,
        profile_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, str | None]:
        create_resp = core._reddit_ads_request(
            "POST",
            f"/profiles/{profile_id}/structured_posts/jobs",
            cfg,
            json_body=payload,
        )
        create_data = core._reddit_ads_data(create_resp["data"]) or {}
        job_id = str(create_data.get("id") or "").strip()
        if not job_id:
            raise RuntimeError("Reddit structured post creation returned no job id")

        deadline = time.time() + 120.0
        last_status = ""
        last_error = ""
        post_id = ""
        while time.time() < deadline:
            job_resp = core._reddit_ads_request("GET", f"/structured_posts/jobs/{job_id}", cfg)
            job_data = core._reddit_ads_data(job_resp["data"]) or {}
            last_status = str(job_data.get("status") or "").strip().upper()
            last_error = str(job_data.get("error_message") or "").strip()
            post_id = str(job_data.get("post_id") or "").strip()
            if last_status == "SUCCESS":
                break
            if last_status in {"CLIENT_ERROR", "SERVER_ERROR"}:
                raise RuntimeError(
                    "Reddit structured post creation failed"
                    + (f": {last_error}" if last_error else "")
                )
            time.sleep(1.0)
        else:
            raise RuntimeError(
                "Reddit structured post creation timed out"
                + (f" after last status {last_status}" if last_status else "")
            )

        if not post_id:
            raise RuntimeError("Reddit structured post creation succeeded but returned no post id")

        post_url = None
        try:
            post_resp = core._reddit_ads_request("GET", f"/structured_posts/{post_id}", cfg)
            post_data = core._reddit_ads_data(post_resp["data"]) or {}
            post_url = str(post_data.get("url") or "").strip() or None
        except Exception:
            post_url = None
        return post_id, post_url

    def _create_reddit_legacy_post(
        core: Any,
        cfg: dict[str, Any],
        *,
        profile_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, str | None]:
        post_resp = core._reddit_ads_request(
            "POST",
            f"/profiles/{profile_id}/posts",
            cfg,
            json_body=payload,
        )
        post_data = core._reddit_ads_data(post_resp["data"]) or {}
        post_id = str(post_data.get("id") or "").strip()
        post_url = str(post_data.get("post_url") or post_data.get("url") or "").strip() or None
        if not post_id:
            raise RuntimeError("Reddit Ads post creation returned no id")
        return post_id, post_url

    @router.post("/ugc-render")
    def ugc_render(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        credits = core._creative_credit_backend()
        store = core._store()
        business = core._resolved_business_slug(body, required=True)
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="idempotency_key is required")

        brief_rel = core._safe_relpath(str(body.get("brief_path") or "").strip(), field="brief_path").as_posix()
        script_raw = str(body.get("script_path") or "").strip()
        script_rel = core._safe_relpath(script_raw, field="script_path").as_posix() if script_raw else ""
        slug = core._file_slug(
            str(body.get("slug") or Path(script_rel or brief_rel).stem or "ugc-ad"),
            "ugc-ad",
        )
        brief_abs = store._resolve_business_file(business, brief_rel)
        if not brief_abs.is_file():
            raise HTTPException(status_code=400, detail=f"brief file not found: {brief_rel}")
        if script_rel:
            script_abs = store._resolve_business_file(business, script_rel)
            if not script_abs.is_file():
                raise HTTPException(status_code=400, detail=f"script file not found: {script_rel}")

        script_path = (
            Path(__file__).resolve().parents[2]
            / "skills"
            / "takyon"
            / "ugc-video-ad"
            / "scripts"
            / "build_ad.py"
        )
        cmd = [
            sys.executable,
            str(script_path),
            "--brief",
            brief_rel,
            "--out-root",
            "product",
            "--slug",
            slug,
            "--transition-mode",
            str(body.get("transition_mode") or "continuity"),
            "--env-file",
            str(body.get("env_file") or ".env"),
        ]
        if script_rel:
            cmd.extend(["--script", script_rel])
        if core._boolish(body.get("jumpcuts"), default=False):
            cmd.append("--jumpcuts")
        if core._boolish(body.get("skip_post"), default=False):
            cmd.append("--skip-post")
        if body.get("workdir"):
            cmd.extend(["--workdir", str(body.get("workdir"))])

        ad_metadata = body.get("ad_metadata") if isinstance(body.get("ad_metadata"), dict) else {}
        budget_bucket = core._normalize_creative_credit_bucket(
            body.get("budget_bucket") or ad_metadata.get("channel") or body.get("channel")
        )
        if not budget_bucket:
            raise HTTPException(status_code=400, detail="budget_bucket or ad_metadata.channel is required")
        reservation_key = f"{idempotency_key}:creative-credits"
        try:
            reservation = core._reserve_creative_credits(
                business,
                action="ugc_ad_generate",
                reservation_key=reservation_key,
                budget_bucket=budget_bucket,
                metadata={
                    "business": business,
                    "action": "ugc_ad_generate",
                    "slug": slug,
                    "brief_path": brief_rel,
                    "script_path": script_rel or None,
                },
                ad_metadata=ad_metadata,
            )
        except core.CreativeCreditBudgetExceeded as exc:
            balances = credits.get_business_credit_balances(conn, business)
            return {
                "success": False,
                "status": "blocked_channel_budget_exhausted",
                "requested_credits": core._creative_credit_total_cost("ugc_ad_generate"),
                "available_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "budget_bucket": exc.bucket,
                "channel_budget": {
                    "allocated_credits": exc.allocated_credits,
                    "used_credits": exc.used_credits,
                    "reserved_credits": exc.reserved_credits,
                    "remaining_credits": exc.remaining_credits,
                },
                "error": str(exc),
            }
        except credits.InsufficientCreativeCredits as exc:
            balances = credits.get_business_credit_balances(conn, business)
            return {
                "success": False,
                "status": "blocked_insufficient_creative_credits",
                "requested_credits": core._creative_credit_total_cost("ugc_ad_generate"),
                "available_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "error": str(exc),
            }

        finalized = False
        try:
            run = subprocess.run(
                cmd,
                cwd=str(store._business_root(business)),
                capture_output=True,
                text=True,
                check=False,
            )
            if run.returncode != 0:
                balances = core._release_creative_credits(
                    reservation_key,
                    action="ugc_ad_generate",
                    budget_bucket=budget_bucket,
                    metadata={
                        "business": business,
                        "action": "ugc_ad_generate",
                        "slug": slug,
                        "error": run.stderr or run.stdout or f"exit {run.returncode}",
                    },
                    ad_metadata=ad_metadata,
                )
                finalized = True
                return {
                    "success": False,
                    "status": "failed",
                    "stdout": run.stdout,
                    "stderr": run.stderr,
                    "error": run.stderr or run.stdout or f"ugc-video-ad exited {run.returncode}",
                    "balance_credits": balances["balance_credits"],
                    "reserved_credits": balances["reserved_credits"],
                    "channel_budget": balances.get("channel_budget"),
                }

            payload = core._parse_ugc_write_payload(run.stdout)
            balances = core._commit_creative_credits(
                reservation_key,
                action="ugc_ad_generate",
                budget_bucket=budget_bucket,
                metadata={
                    "business": business,
                    "action": "ugc_ad_generate",
                    "slug": slug,
                    "provider": "openai+fal",
                },
                ad_metadata=ad_metadata,
            )
            finalized = True
            return {
                "success": True,
                "status": "created",
                "write_payload": payload,
                "credits_charged": core._creative_credit_total_cost("ugc_ad_generate"),
                "balance_credits": balances["balance_credits"],
                "reserved_credits": balances["reserved_credits"],
                "budget_bucket": reservation.get("budget_bucket"),
                "channel_budget": balances.get("channel_budget"),
                "stdout": run.stdout,
                "stderr": run.stderr,
            }
        except Exception as exc:
            if not finalized:
                try:
                    core._release_creative_credits(
                        reservation_key,
                        action="ugc_ad_generate",
                        budget_bucket=budget_bucket,
                        metadata={
                            "business": business,
                            "action": "ugc_ad_generate",
                            "slug": slug,
                            "error": str(exc),
                        },
                        ad_metadata=ad_metadata,
                    )
                except Exception:
                    pass
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/static-render")
    def static_render(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        credits = core._creative_credit_backend()
        store = core._store()
        business = core._resolved_business_slug(body, required=True)
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="idempotency_key is required")

        input_rel = core._safe_relpath(str(body.get("input_path") or "").strip(), field="input_path").as_posix()
        input_abs = store._resolve_business_file(business, input_rel)
        if not input_abs.exists():
            raise HTTPException(status_code=400, detail=f"static ad input not found: {input_rel}")

        slug = core._file_slug(str(body.get("slug") or Path(input_rel).stem or "static-ad"), "static-ad")
        publication_rel = f"product/static-ads/{slug}"
        requested = max(1, core._count_static_specs(input_abs))

        script_path = (
            Path(__file__).resolve().parents[2]
            / "skills"
            / "takyon"
            / "static-ad-creative-generator"
            / "scripts"
            / "batch_generate.py"
        )
        backend = str(body.get("backend") or "openai")
        cmd = [
            sys.executable,
            str(script_path),
            input_rel,
            "-o",
            publication_rel,
            "--backend",
            backend,
            "--quality",
            str(body.get("quality") or "high"),
        ]
        if core._boolish(body.get("crop"), default=False):
            cmd.append("--crop")
        if core._boolish(body.get("strict"), default=False):
            cmd.append("--strict")
        if core._boolish(body.get("stop_on_error"), default=False):
            cmd.append("--stop-on-error")
        if body.get("aspect_ratio"):
            cmd.extend(["--aspect-ratio", str(body.get("aspect_ratio"))])
        if body.get("max"):
            cmd.extend(["--max", str(body.get("max"))])

        ad_metadata = body.get("ad_metadata") if isinstance(body.get("ad_metadata"), dict) else {}
        budget_bucket = core._normalize_creative_credit_bucket(
            body.get("budget_bucket") or ad_metadata.get("channel") or body.get("channel")
        )
        if not budget_bucket:
            raise HTTPException(status_code=400, detail="budget_bucket or ad_metadata.channel is required")
        reservation_key = f"{idempotency_key}:creative-credits"
        requested_credits = core._creative_credit_total_cost("static_ad_generate", units=requested)
        try:
            reservation = core._reserve_creative_credits(
                business,
                action="static_ad_generate",
                reservation_key=reservation_key,
                units=requested,
                budget_bucket=budget_bucket,
                metadata={
                    "business": business,
                    "action": "static_ad_generate",
                    "slug": slug,
                    "input_path": input_rel,
                    "requested_creatives": requested,
                },
                ad_metadata=ad_metadata,
            )
        except core.CreativeCreditBudgetExceeded as exc:
            balances = credits.get_business_credit_balances(conn, business)
            return {
                "success": False,
                "status": "blocked_channel_budget_exhausted",
                "requested_credits": requested_credits,
                "available_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "budget_bucket": exc.bucket,
                "channel_budget": {
                    "allocated_credits": exc.allocated_credits,
                    "used_credits": exc.used_credits,
                    "reserved_credits": exc.reserved_credits,
                    "remaining_credits": exc.remaining_credits,
                },
                "error": str(exc),
            }
        except credits.InsufficientCreativeCredits as exc:
            balances = credits.get_business_credit_balances(conn, business)
            return {
                "success": False,
                "status": "blocked_insufficient_creative_credits",
                "requested_credits": requested_credits,
                "available_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "error": str(exc),
            }

        finalized = False
        # Hold the business mirror lock across the ENTIRE render → manifest-read →
        # remote-push critical section. The render subprocess writes its PNG(s) +
        # manifest.json only into the local cache mirror; without the lock a concurrent
        # ``_business_root(sync=True)`` from another store (the embedded business session,
        # a worker drain thread, the dashboard render path) re-materializes that mirror with
        # ``delete_local=True`` and deletes the freshly rendered files mid-flight — which
        # surfaced as "manifest.json: No such file or directory" even though the render
        # itself succeeded, and could charge a credit for an image that then vanished.
        # The mirror lock is now per-thread reentrant (see ``_business_mirror_lock``), so the
        # nested ``_resolve_business_file`` calls below do not self-deadlock against it. The
        # lock is entered via ExitStack to avoid re-indenting the whole block; it is released
        # in the ``finally`` together with any credit release on error.
        from contextlib import ExitStack as _ExitStack

        _render_lock_stack = _ExitStack()
        try:
            # The business mirror flock that used to wrap this critical section deadlocked the
            # worker and provided no real safety, so it is gone (see core._business_mirror_lock).
            # The render output is protected instead by an IMMEDIATE remote-storage push below and
            # by ``sync=False`` reads off the just-written local tree. Resolve the workspace root
            # ONCE up front (this is the only sync); the render subprocess writes into this exact
            # local cache tree. If a concurrent re-materialize wipes it, the remote push has
            # already durably captured the asset and the manifest read falls through cleanly.
            business_root = store._business_root(business)
            run = subprocess.run(
                cmd,
                cwd=str(business_root),
                capture_output=True,
                text=True,
                check=False,
            )
            manifest_rel = f"{publication_rel}/manifest.json"
            # Read the manifest the subprocess just wrote WITHOUT re-syncing (``sync=False``):
            # the files live in ``business_root`` already and the lock guarantees they are
            # still present.
            manifest_abs = store._resolve_business_file(business, manifest_rel, sync=False)
            manifest: dict[str, Any] = {}
            if manifest_abs.is_file():
                try:
                    manifest = json.loads(manifest_abs.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}
            succeeded = int(manifest.get("succeeded") or 0)
            failed = int(manifest.get("failed") or 0)
            # Persist the freshly rendered image bytes to canonical remote storage
            # IMMEDIATELY, before returning. The render subprocess wrote the PNG(s) only
            # into the local cache mirror; any concurrent ``_business_root(sync=True)`` from
            # another store re-materializes that mirror with ``delete_local=True`` and would
            # wipe the in-flight render before the caller's own remote sync runs. Pushing
            # here (while the files exist) makes the render durable at the point of creation
            # so the credit commit below is never charged for an image that then vanishes.
            if succeeded > 0:
                try:
                    store._sync_business_workspace_remote(business)
                except Exception:
                    # A failed remote push must not silently drop a charged render; surface
                    # it so the outer handler releases credits instead of committing.
                    raise
            if run.returncode != 0:
                if succeeded > 0:
                    balances = core._commit_creative_credits(
                        reservation_key,
                        action="static_ad_generate",
                        actual_units=succeeded,
                        budget_bucket=budget_bucket,
                        metadata={
                            "business": business,
                            "action": "static_ad_generate",
                            "slug": slug,
                            "input_path": input_rel,
                            "requested_creatives": requested,
                            "succeeded_creatives": succeeded,
                        },
                        ad_metadata=ad_metadata,
                    )
                else:
                    balances = core._release_creative_credits(
                        reservation_key,
                        action="static_ad_generate",
                        budget_bucket=budget_bucket,
                        metadata={
                            "business": business,
                            "action": "static_ad_generate",
                            "slug": slug,
                            "error": run.stderr or run.stdout or f"exit {run.returncode}",
                        },
                        ad_metadata=ad_metadata,
                    )
                finalized = True
                return {
                    "success": False,
                    "status": "partial_failed" if succeeded > 0 else "failed",
                    "manifest": manifest_rel if manifest_abs.is_file() else None,
                    "succeeded": succeeded,
                    "failed": failed,
                    "requested_credits": requested_credits,
                    "credits_charged": core._creative_credit_total_cost(
                        "static_ad_generate", units=succeeded
                    ),
                    "balance_credits": balances["balance_credits"],
                    "reserved_credits": balances["reserved_credits"],
                    "budget_bucket": reservation.get("budget_bucket"),
                    "channel_budget": balances.get("channel_budget"),
                    "stdout": run.stdout,
                    "stderr": run.stderr,
                    "error": run.stderr or run.stdout or f"static ad generator exited {run.returncode}",
                }

            charged_units = max(1, succeeded or requested)
            balances = core._commit_creative_credits(
                reservation_key,
                action="static_ad_generate",
                actual_units=charged_units,
                budget_bucket=budget_bucket,
                metadata={
                    "business": business,
                    "action": "static_ad_generate",
                    "slug": slug,
                    "input_path": input_rel,
                    "requested_creatives": requested,
                    "succeeded_creatives": charged_units,
                    "provider": backend,
                },
                ad_metadata=ad_metadata,
            )
            finalized = True
            return {
                "success": True,
                "status": "created",
                "manifest": manifest_rel if manifest_abs.is_file() else None,
                "succeeded": succeeded or requested,
                "failed": failed,
                "credits_charged": core._creative_credit_total_cost(
                    "static_ad_generate", units=charged_units
                ),
                "balance_credits": balances["balance_credits"],
                "reserved_credits": balances["reserved_credits"],
                "budget_bucket": reservation.get("budget_bucket"),
                "channel_budget": balances.get("channel_budget"),
                "stdout": run.stdout,
                "stderr": run.stderr,
            }
        except Exception as exc:
            if not finalized:
                try:
                    core._release_creative_credits(
                        reservation_key,
                        action="static_ad_generate",
                        budget_bucket=budget_bucket,
                        metadata={
                            "business": business,
                            "action": "static_ad_generate",
                            "slug": slug,
                            "error": str(exc),
                        },
                        ad_metadata=ad_metadata,
                    )
                except Exception:
                    pass
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            # The mirror flock is gone (deadlock); this stack is now empty but kept so the
            # render critical section retains a single structural exit point.
            _render_lock_stack.close()

    @router.post("/meta-launch")
    def meta_launch(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        credits = core._creative_credit_backend()
        store = core._store()
        business = core._resolved_business_slug(body, required=True)
        mode = str(body.get("mode") or "launch").strip().lower()
        cfg = core._meta_config(require_token=True)
        if mode == "preflight":
            identity = core._meta_graph("GET", "me", {"fields": "id,name"}, cfg)
            accounts = core._meta_graph(
                "GET",
                "me/adaccounts",
                {"fields": "id,account_id,name,account_status,currency,is_prepay_account"},
                cfg,
            )
            return {
                "success": True,
                "mode": "preflight",
                "read_only": True,
                "business": business,
                "graph_version": cfg["version"],
                "identity": identity,
                "ad_accounts": accounts.get("data") if isinstance(accounts, dict) else None,
                "default_ad_account_id": cfg.get("ad_account_id") or None,
                "default_page_id": cfg.get("page_id") or None,
            }

        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="idempotency_key is required")
        plan = core._meta_launch_plan(body, cfg)
        if not plan["ad_account_id"]:
            raise HTTPException(status_code=400, detail="live launch requires META_AD_ACCOUNT_ID or ad_account_id")
        if not plan["page_id"]:
            raise HTTPException(status_code=400, detail="live launch requires META_PAGE_ID or ad.page_id")

        video_abs: Path | None = None
        image_abs: Path | None = None
        if plan["asset_kind"] == "video":
            video_abs = store._resolve_business_file(business, plan["ad_video_path"])
            if not video_abs.is_file():
                raise HTTPException(status_code=400, detail=f"ad video not found at {plan['ad_video_path']}")
        else:
            image_abs = store._resolve_business_file(business, str(plan["ad_image_path"] or ""))
            if not image_abs.is_file():
                raise HTTPException(status_code=400, detail=f"ad image not found at {plan['ad_image_path']}")

        cfg["ad_account_id"] = plan["ad_account_id"]
        acct = core._meta_account_path(plan["ad_account_id"])
        reservation_key = f"{idempotency_key}:creative-credits"
        requested_credits = core._creative_credit_total_cost("meta_ad_launch")
        reservation: dict[str, Any] | None = None
        try:
            reservation = core._reserve_creative_credits(
                business,
                action="meta_ad_launch",
                reservation_key=reservation_key,
                budget_bucket="meta",
                metadata={
                    "business": business,
                    "action": "meta_ad_launch",
                    "slug": plan["slug"],
                    "asset_kind": plan["asset_kind"],
                    "ad_video_path": plan["ad_video_path"],
                    "ad_image_path": plan.get("ad_image_path"),
                },
            )
        except credits.InsufficientCreativeCredits as exc:
            balances = credits.get_business_credit_balances(conn, business)
            return {
                "success": False,
                "status": "blocked_insufficient_creative_credits",
                "requested_credits": requested_credits,
                "available_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "error": str(exc),
            }
        except core.CreativeCreditBudgetExceeded as exc:
            return {
                "success": False,
                "status": "blocked_channel_budget_exhausted",
                "requested_credits": requested_credits,
                "budget_bucket": exc.bucket,
                "channel_budget": exc.channel_budget,
                "error": str(exc),
            }

        created: dict[str, Any] = {}
        finalized = False
        try:
            if plan["asset_kind"] == "video":
                created["video_id"] = core._meta_upload_advideo(
                    video_abs,
                    cfg,
                    name=plan["ad_name"],
                    business=business,
                    video_rel=plan["ad_video_path"],
                )
                image_url = plan["image_url"] or core._meta_video_thumbnail(created["video_id"], cfg)
                if not image_url:
                    raise RuntimeError(
                        "Meta requires a thumbnail for a video creative but none was ready yet; "
                        "pass ad.image_url or retry shortly after the video finishes processing"
                    )
                story_spec = {
                    "page_id": plan["page_id"],
                    "video_data": {
                        "video_id": created["video_id"],
                        "message": plan["message"],
                        "image_url": image_url,
                        "call_to_action": {
                            "type": plan["call_to_action"],
                            "value": {"link": plan["link"]},
                        },
                    },
                }
            else:
                uploaded = core._meta_upload_adimage(image_abs, cfg)
                created["image_hash"] = uploaded["hash"]
                image_url = plan["image_url"] or uploaded.get("url")
                story_spec = {
                    "page_id": plan["page_id"],
                    "link_data": {
                        "link": plan["link"],
                        "message": plan["message"],
                        "image_hash": uploaded["hash"],
                        "call_to_action": {
                            "type": plan["call_to_action"],
                            "value": {"link": plan["link"]},
                        },
                    },
                }

            creative = core._meta_graph("POST", f"{acct}/adcreatives", {
                "name": f"{plan['ad_name']} creative",
                "object_story_spec": json.dumps(story_spec),
            }, cfg)
            created["creative_id"] = str(creative.get("id") or "").strip()

            campaign = core._meta_graph(
                "POST",
                f"{acct}/campaigns",
                _meta_campaign_create_payload(plan),
                cfg,
            )
            created["campaign_id"] = str(campaign.get("id") or "").strip()

            adset = core._meta_graph("POST", f"{acct}/adsets", {
                "name": plan["adset_name"],
                "campaign_id": created["campaign_id"],
                "status": "PAUSED",
                "daily_budget": plan["daily_budget_cents"],
                "billing_event": plan["billing_event"],
                "optimization_goal": plan["optimization_goal"],
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "start_time": plan.get("adset_start_time"),
                "end_time": plan.get("adset_end_time"),
                "targeting": json.dumps(plan["targeting"]),
            }, cfg)
            created["adset_id"] = str(adset.get("id") or "").strip()

            ad = core._meta_graph("POST", f"{acct}/ads", {
                "name": plan["ad_name"],
                "adset_id": created["adset_id"],
                "status": "PAUSED",
                "creative": json.dumps({"creative_id": created["creative_id"]}),
            }, cfg)
            created["ad_id"] = str(ad.get("id") or "").strip()

            balances = core._commit_creative_credits(
                reservation_key,
                action="meta_ad_launch",
                budget_bucket="meta",
                metadata={
                    "business": business,
                    "action": "meta_ad_launch",
                    "slug": plan["slug"],
                    "asset_kind": plan["asset_kind"],
                    "provider": "meta",
                    "ids": created,
                },
            )
            finalized = True
            return {
                "success": True,
                "status": "created_paused",
                "paused": True,
                "ids": created,
                "thumbnail_url": image_url,
                "graph_version": cfg["version"],
                "ad_account_id": acct,
                "page_id": plan["page_id"],
                "credits_charged": requested_credits,
                "balance_credits": balances["balance_credits"],
                "reserved_credits": balances["reserved_credits"],
                "budget_bucket": reservation.get("budget_bucket") if isinstance(reservation, dict) else "meta",
                "channel_budget": balances.get("channel_budget", {}),
            }
        except Exception as exc:
            try:
                if created:
                    balances = core._commit_creative_credits(
                        reservation_key,
                        action="meta_ad_launch",
                        budget_bucket="meta",
                        metadata={
                            "business": business,
                            "action": "meta_ad_launch",
                            "status": "partial_failed",
                            "created": created,
                            "error": str(exc),
                        },
                    )
                else:
                    balances = core._release_creative_credits(
                        reservation_key,
                        action="meta_ad_launch",
                        budget_bucket="meta",
                        metadata={
                            "business": business,
                            "action": "meta_ad_launch",
                            "status": "failed",
                            "error": str(exc),
                        },
                    )
                finalized = True
                return {
                    "success": False,
                    "status": "partial_failed" if created else "failed",
                    "paused": True,
                    "ids": created or None,
                    "error": str(exc),
                    "credits_charged": requested_credits if created else 0,
                    "balance_credits": balances["balance_credits"],
                    "reserved_credits": balances["reserved_credits"],
                    "budget_bucket": reservation.get("budget_bucket") if isinstance(reservation, dict) else "meta",
                    "channel_budget": balances.get("channel_budget", {}),
                }
            except Exception as release_exc:
                if not finalized:
                    raise HTTPException(
                        status_code=502,
                        detail=f"{exc} (credit finalization also failed: {release_exc})",
                    ) from exc
                raise

    @router.post("/meta-control")
    def meta_control(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        cfg = core._meta_config(require_token=True)
        operation = str(body.get("operation") or "").strip().lower()
        if operation not in {"activate", "pause", "set_budget"}:
            raise HTTPException(status_code=400, detail="operation must be activate, pause, or set_budget")

        ids = {
            "campaign_id": str(body.get("campaign_id") or "").strip(),
            "adset_id": str(body.get("adset_id") or "").strip(),
            "ad_id": str(body.get("ad_id") or "").strip(),
        }
        if not ids["campaign_id"] or not ids["adset_id"] or not ids["ad_id"]:
            raise HTTPException(status_code=400, detail="campaign_id, adset_id, and ad_id are required")

        if operation == "set_budget":
            try:
                daily_budget_cents = int(body.get("daily_budget_cents"))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="daily_budget_cents is required for set_budget")
            if daily_budget_cents <= 0:
                raise HTTPException(status_code=400, detail="daily_budget_cents must be positive")
            try:
                core._assert_ad_set_budget_authorized(
                    channel="meta",
                    business=str(body.get("business") or "").strip(),
                    slug=str(body.get("slug") or "").strip(),
                    target_id=ids["adset_id"],
                    daily_budget_cents=daily_budget_cents,
                    safety_cap_cents=int(round(core._meta_daily_budget_cap() * 100)),
                )
            except core.TakyonError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            try:
                core._meta_graph(
                    "POST",
                    ids["adset_id"],
                    {"daily_budget": daily_budget_cents},
                    cfg,
                )
            except Exception as exc:
                return {
                    "success": False,
                    "status": "failed",
                    "graph_version": cfg["version"],
                    "error": str(exc),
                    "ids": ids,
                }
            return {
                "success": True,
                "status": "budget_updated",
                "graph_version": cfg["version"],
                "ids": ids,
                "daily_budget_cents": daily_budget_cents,
                "daily_budget_usd": body.get("daily_budget_usd"),
                "applied": [{"object": "adset", "id": ids["adset_id"], "daily_budget_cents": daily_budget_cents}],
            }

        target_status = "ACTIVE" if operation == "activate" else "PAUSED"
        ordered_ids = [
            ("campaign", ids["campaign_id"]),
            ("adset", ids["adset_id"]),
            ("ad", ids["ad_id"]),
        ]
        if operation == "pause":
            ordered_ids = list(reversed(ordered_ids))
        applied: list[dict[str, Any]] = []
        try:
            for kind, object_id in ordered_ids:
                core._meta_graph("POST", object_id, {"status": target_status}, cfg)
                applied.append({"object": kind, "id": object_id, "status": target_status})
            return {
                "success": True,
                "status": "activated" if operation == "activate" else "paused",
                "graph_version": cfg["version"],
                "ids": ids,
                "applied": applied,
            }
        except Exception as exc:
            return {
                "success": False,
                "status": "partial_failed" if applied else "failed",
                "graph_version": cfg["version"],
                "ids": ids,
                "applied": applied or None,
                "error": str(exc),
            }

    @router.post("/meta-insights")
    def meta_insights(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        cfg = core._meta_config(require_token=True)
        level = str(body.get("level") or "campaign").strip().lower()
        if level not in {"campaign", "adset", "ad"}:
            raise HTTPException(status_code=400, detail="level must be campaign, adset, or ad")

        object_id = str(body.get(f"{level}_id") or "").strip()
        if not object_id:
            raise HTTPException(status_code=400, detail=f"{level}_id is required")

        params: dict[str, Any] = {
            "fields": ",".join([
                "account_currency",
                "campaign_id",
                "campaign_name",
                "adset_id",
                "adset_name",
                "ad_id",
                "ad_name",
                "date_start",
                "date_stop",
                "impressions",
                "reach",
                "clicks",
                "spend",
                "cpc",
                "cpm",
                "ctr",
            ]),
        }
        time_range = body.get("time_range") if isinstance(body.get("time_range"), dict) else None
        if time_range:
            params["time_range"] = json.dumps(time_range)
        else:
            params["date_preset"] = str(body.get("date_preset") or "today").strip().lower() or "today"

        try:
            result = core._meta_graph("GET", f"{object_id}/insights", params, cfg)
        except Exception as exc:
            return {
                "success": False,
                "status": "failed",
                "graph_version": cfg["version"],
                "level": level,
                "object_id": object_id,
                "error": str(exc),
            }

        rows = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), list) else []
        return {
            "success": True,
            "status": "synced",
            "graph_version": cfg["version"],
            "level": level,
            "object_id": object_id,
            "rows": rows,
        }

    @router.post("/reddit-launch")
    def reddit_launch(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        credits = core._creative_credit_backend()
        business = core._resolved_business_slug(body, required=True)
        mode = str(body.get("mode") or "launch").strip().lower()
        cfg = core._reddit_ads_config(require_auth=True)

        if mode == "preflight":
            return core._reddit_ads_preflight(cfg)

        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="idempotency_key is required")
        plan = body.get("plan") if isinstance(body.get("plan"), dict) else core._reddit_launch_plan(body, cfg)
        preflight = core._reddit_ads_preflight(cfg)
        defaults = preflight.get("defaults") if isinstance(preflight.get("defaults"), dict) else {}

        business_id = str(plan.get("business_id") or defaults.get("business_id") or cfg.get("business_id") or "").strip()
        ad_account_id = str(plan.get("ad_account_id") or defaults.get("ad_account_id") or cfg.get("ad_account_id") or "").strip()
        profile_id = str(plan.get("profile_id") or defaults.get("profile_id") or cfg.get("profile_id") or "").strip()
        funding_instrument_id = str(
            plan.get("funding_instrument_id")
            or defaults.get("funding_instrument_id")
            or cfg.get("funding_instrument_id")
            or ""
        ).strip()
        pixel_id = str(plan.get("pixel_id") or defaults.get("pixel_id") or cfg.get("pixel_id") or "").strip()

        if not business_id:
            raise HTTPException(status_code=400, detail="Reddit Ads launch requires a business id")
        if not ad_account_id:
            raise HTTPException(status_code=400, detail="Reddit Ads launch requires an ad account id")
        if not funding_instrument_id:
            raise HTTPException(
                status_code=400,
                detail="Reddit Ads launch requires a funding instrument id; add REDDIT_ADS_FUNDING_INSTRUMENT_ID or preflight an account with a funding instrument",
            )
        if not pixel_id:
            raise HTTPException(
                status_code=400,
                detail="Reddit Ads launch requires a conversion pixel id; add REDDIT_ADS_PIXEL_ID or preflight an account with a pixel",
            )
        if plan.get("asset_kind") != "existing_post" and not profile_id:
            raise HTTPException(
                status_code=400,
                detail="Reddit Ads launch requires a profile id when creating a new promoted post",
            )

        reservation_key = f"{idempotency_key}:creative-credits"
        requested_credits = core._creative_credit_total_cost("reddit_ad_launch")
        reservation: dict[str, Any] | None = None
        try:
            reservation = core._reserve_creative_credits(
                business,
                action="reddit_ad_launch",
                reservation_key=reservation_key,
                budget_bucket="reddit",
                metadata={
                    "business": business,
                    "action": "reddit_ad_launch",
                    "slug": plan.get("slug"),
                    "asset_kind": plan.get("asset_kind"),
                    "objective": plan.get("objective"),
                    "ad_account_id": ad_account_id,
                },
            )
        except credits.InsufficientCreativeCredits as exc:
            balances = credits.get_business_credit_balances(conn, business)
            return {
                "success": False,
                "status": "blocked_insufficient_creative_credits",
                "requested_credits": requested_credits,
                "available_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "error": str(exc),
            }
        except core.CreativeCreditBudgetExceeded as exc:
            return {
                "success": False,
                "status": "blocked_channel_budget_exhausted",
                "requested_credits": requested_credits,
                "budget_bucket": exc.bucket,
                "channel_budget": exc.channel_budget,
                "error": str(exc),
            }

        created: dict[str, Any] = {}
        preview_url = None
        preview_expiry = None
        post_url = None
        post_creation_mode = ""
        finalized = False
        try:
            campaign_payload = json.loads(json.dumps(plan.get("campaign_payload") or {}))
            campaign_payload.setdefault("data", {})
            campaign_payload["data"]["funding_instrument_id"] = funding_instrument_id
            campaign_resp = core._reddit_ads_request(
                "POST",
                f"/ad_accounts/{ad_account_id}/campaigns",
                cfg,
                json_body=campaign_payload,
            )
            campaign_data = core._reddit_ads_data(campaign_resp["data"]) or {}
            created["campaign_id"] = str(campaign_data.get("id") or "").strip()
            if not created["campaign_id"]:
                raise RuntimeError("Reddit Ads campaign creation returned no id")

            ad_group_payload = json.loads(json.dumps(plan.get("ad_group_payload") or {}))
            ad_group_payload.setdefault("data", {})
            ad_group_payload["data"]["campaign_id"] = created["campaign_id"]
            ad_group_payload["data"]["conversion_pixel_id"] = pixel_id
            ad_group_resp = core._reddit_ads_request(
                "POST",
                f"/ad_accounts/{ad_account_id}/ad_groups",
                cfg,
                json_body=ad_group_payload,
            )
            ad_group_data = core._reddit_ads_data(ad_group_resp["data"]) or {}
            created["ad_group_id"] = str(ad_group_data.get("id") or "").strip()
            if not created["ad_group_id"]:
                raise RuntimeError("Reddit Ads ad group creation returned no id")

            post_id = str(plan.get("post_id") or "").strip()
            post_creation_mode = "existing_post" if post_id else ""
            if not post_id:
                structured_payload = plan.get("structured_post_payload") if isinstance(plan.get("structured_post_payload"), dict) else None
                legacy_payload = plan.get("legacy_post_payload") if isinstance(plan.get("legacy_post_payload"), dict) else None
                if structured_payload:
                    try:
                        post_id, post_url = _create_reddit_structured_post(
                            core,
                            cfg,
                            profile_id=profile_id,
                            payload=structured_payload,
                        )
                        post_creation_mode = "structured_post_job"
                    except Exception:
                        if not legacy_payload:
                            raise
                        post_id, post_url = _create_reddit_legacy_post(
                            core,
                            cfg,
                            profile_id=profile_id,
                            payload=legacy_payload,
                        )
                        post_creation_mode = "legacy_post_fallback"
                else:
                    if not legacy_payload:
                        raise RuntimeError("Reddit launch plan did not include a post creation payload")
                    post_id, post_url = _create_reddit_legacy_post(
                        core,
                        cfg,
                        profile_id=profile_id,
                        payload=legacy_payload,
                    )
                    post_creation_mode = "legacy_post"
            created["post_id"] = post_id

            ad_payload = json.loads(json.dumps(plan.get("ad_payload") or {}))
            ad_payload.setdefault("data", {})
            ad_payload["data"]["ad_group_id"] = created["ad_group_id"]
            ad_payload["data"]["post_id"] = post_id
            ad_resp = core._reddit_ads_request(
                "POST",
                f"/ad_accounts/{ad_account_id}/ads",
                cfg,
                json_body=ad_payload,
            )
            ad_data = core._reddit_ads_data(ad_resp["data"]) or {}
            created["ad_id"] = str(ad_data.get("id") or "").strip()
            if not created["ad_id"]:
                raise RuntimeError("Reddit Ads ad creation returned no id")
            preview_url = str(ad_data.get("preview_url") or "").strip() or None
            preview_expiry = str(ad_data.get("preview_expiry") or "").strip() or None
            post_url = post_url or (str(ad_data.get("post_url") or "").strip() or None)

            balances = core._commit_creative_credits(
                reservation_key,
                action="reddit_ad_launch",
                budget_bucket="reddit",
                metadata={
                    "business": business,
                    "action": "reddit_ad_launch",
                    "slug": plan.get("slug"),
                    "asset_kind": plan.get("asset_kind"),
                    "provider": "reddit",
                    "ids": created,
                },
            )
            finalized = True
            return {
                "success": True,
                "status": "created_paused",
                "paused": True,
                "business_id": business_id,
                "ad_account_id": ad_account_id,
                "profile_id": profile_id or None,
                "funding_instrument_id": funding_instrument_id,
                "pixel_id": pixel_id,
                "ids": created,
                "post_creation_mode": post_creation_mode or ("existing_post" if post_id else None),
                "preview_url": preview_url,
                "preview_expiry": preview_expiry,
                "post_url": post_url,
                "credits_charged": requested_credits,
                "balance_credits": balances["balance_credits"],
                "reserved_credits": balances["reserved_credits"],
                "budget_bucket": reservation.get("budget_bucket") if isinstance(reservation, dict) else "reddit",
                "channel_budget": balances.get("channel_budget", {}),
            }
        except Exception as exc:
            try:
                if created:
                    balances = core._commit_creative_credits(
                        reservation_key,
                        action="reddit_ad_launch",
                        budget_bucket="reddit",
                        metadata={
                            "business": business,
                            "action": "reddit_ad_launch",
                            "status": "partial_failed",
                            "created": created,
                            "error": str(exc),
                        },
                    )
                else:
                    balances = core._release_creative_credits(
                        reservation_key,
                        action="reddit_ad_launch",
                        budget_bucket="reddit",
                        metadata={
                            "business": business,
                            "action": "reddit_ad_launch",
                            "status": "failed",
                            "error": str(exc),
                        },
                    )
                finalized = True
                return {
                    "success": False,
                    "status": "partial_failed" if created else "failed",
                    "paused": True,
                    "business_id": business_id or None,
                    "ad_account_id": ad_account_id or None,
                    "profile_id": profile_id or None,
                    "funding_instrument_id": funding_instrument_id or None,
                    "pixel_id": pixel_id or None,
                    "ids": created or None,
                    "post_creation_mode": post_creation_mode or None,
                    "preview_url": preview_url,
                    "preview_expiry": preview_expiry,
                    "post_url": post_url,
                    "error": str(exc),
                    "credits_charged": requested_credits if created else 0,
                    "balance_credits": balances["balance_credits"],
                    "reserved_credits": balances["reserved_credits"],
                    "budget_bucket": reservation.get("budget_bucket") if isinstance(reservation, dict) else "reddit",
                    "channel_budget": balances.get("channel_budget", {}),
                }
            except Exception as release_exc:
                if not finalized:
                    raise HTTPException(
                        status_code=502,
                        detail=f"{exc} (credit finalization also failed: {release_exc})",
                    ) from exc
                raise

    @router.post("/reddit-control")
    def reddit_control(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        cfg = core._reddit_ads_config(require_auth=True)
        operation = str(body.get("operation") or "").strip().lower()
        if operation not in {"activate", "pause", "set_budget"}:
            raise HTTPException(status_code=400, detail="operation must be activate, pause, or set_budget")

        ids = {
            "campaign_id": str(body.get("campaign_id") or "").strip(),
            "ad_group_id": str(body.get("ad_group_id") or "").strip(),
            "ad_id": str(body.get("ad_id") or "").strip(),
        }
        if not ids["campaign_id"] or not ids["ad_group_id"] or not ids["ad_id"]:
            raise HTTPException(status_code=400, detail="campaign_id, ad_group_id, and ad_id are required")

        if operation == "set_budget":
            try:
                daily_budget_micros = int(body.get("daily_budget_micros"))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="daily_budget_micros is required for set_budget")
            if daily_budget_micros <= 0:
                raise HTTPException(status_code=400, detail="daily_budget_micros must be positive")
            budget_scope = str(body.get("budget_scope") or "ad_group").strip().lower() or "ad_group"
            target_path = f"/ad_groups/{ids['ad_group_id']}"
            budget_target_id = ids["ad_group_id"]
            if budget_scope == "campaign":
                target_path = f"/campaigns/{ids['campaign_id']}"
                budget_target_id = ids["campaign_id"]
            try:
                core._assert_ad_set_budget_authorized(
                    channel="reddit",
                    business=str(body.get("business") or "").strip(),
                    slug=str(body.get("slug") or "").strip(),
                    target_id=budget_target_id,
                    daily_budget_cents=int(daily_budget_micros) // 10000,
                    safety_cap_cents=int(round(core._reddit_daily_budget_cap() * 100)),
                )
            except core.TakyonError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            try:
                core._reddit_ads_request(
                    "PATCH",
                    target_path,
                    cfg,
                    json_body={"data": {"goal_type": "DAILY_SPEND", "goal_value": daily_budget_micros}},
                )
            except Exception as exc:
                return {
                    "success": False,
                    "status": "failed",
                    "error": str(exc),
                    "ids": ids,
                }
            return {
                "success": True,
                "status": "budget_updated",
                "ids": ids,
                "daily_budget_micros": daily_budget_micros,
                "daily_budget_usd": body.get("daily_budget_usd"),
                "applied": [
                    {
                        "object": budget_scope,
                        "id": ids["campaign_id"] if budget_scope == "campaign" else ids["ad_group_id"],
                        "daily_budget_micros": daily_budget_micros,
                    }
                ],
            }

        target_status = "ACTIVE" if operation == "activate" else "PAUSED"
        ordered = [
            ("campaign", ids["campaign_id"], f"/campaigns/{ids['campaign_id']}"),
            ("ad_group", ids["ad_group_id"], f"/ad_groups/{ids['ad_group_id']}"),
            ("ad", ids["ad_id"], f"/ads/{ids['ad_id']}"),
        ]
        if operation == "pause":
            ordered = list(reversed(ordered))
        applied: list[dict[str, Any]] = []
        try:
            for kind, object_id, path in ordered:
                core._reddit_ads_request(
                    "PATCH",
                    path,
                    cfg,
                    json_body={"data": {"configured_status": target_status}},
                )
                applied.append({"object": kind, "id": object_id, "configured_status": target_status})
            return {
                "success": True,
                "status": "activated" if operation == "activate" else "paused",
                "ids": ids,
                "applied": applied,
            }
        except Exception as exc:
            return {
                "success": False,
                "status": "partial_failed" if applied else "failed",
                "ids": ids,
                "applied": applied or None,
                "error": str(exc),
            }

    @router.post("/reddit-insights")
    def reddit_insights(
        body: dict | None = Body(default=None),
        _: None = Depends(_require_internal_session),
    ) -> dict[str, Any]:
        body = body or {}
        core = _core()
        cfg = core._reddit_ads_config(require_auth=True)
        ad_account_id = str(body.get("ad_account_id") or cfg.get("ad_account_id") or "").strip()
        if not ad_account_id:
            raise HTTPException(status_code=400, detail="ad_account_id is required")
        level = str(body.get("level") or "campaign").strip().lower()
        if level not in {"campaign", "ad_group", "ad"}:
            raise HTTPException(status_code=400, detail="level must be campaign, ad_group, or ad")

        object_id_key = "ad_group_id" if level == "ad_group" else f"{level}_id"
        object_id = str(body.get(object_id_key) or "").strip()
        if not object_id:
            raise HTTPException(status_code=400, detail=f"{object_id_key} is required")

        starts_at, ends_at = core._reddit_report_window(body)
        fields = body.get("fields") if isinstance(body.get("fields"), list) else [
            "SPEND",
            "IMPRESSIONS",
            "CLICKS",
            "CTR",
            "CPC",
            "ECPM",
        ]
        breakdowns = body.get("breakdowns") if isinstance(body.get("breakdowns"), list) else ["DATE"]
        filter_value = str(body.get("filter") or f"{level}:id=={object_id}").strip()
        report_body = {
            "data": {
                "fields": fields,
                "breakdowns": breakdowns,
                "filter": filter_value,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "time_zone_id": str(body.get("time_zone_id") or "UTC").strip() or "UTC",
            }
        }

        try:
            report = core._reddit_ads_request(
                "POST",
                f"/ad_accounts/{ad_account_id}/reports",
                cfg,
                json_body=report_body,
                timeout=120,
            )
        except Exception as exc:
            return {
                "success": False,
                "status": "failed",
                "level": level,
                "object_id": object_id,
                "error": str(exc),
            }

        rows = core._reddit_ads_list(report["data"])
        return {
            "success": True,
            "status": "synced",
            "level": level,
            "object_id": object_id,
            "rows": rows,
        }

    return router
