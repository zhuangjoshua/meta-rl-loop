"""Internal creative gateway — the server-side broker for live creative/ad actions.

This mirrors the AI gateway pattern narrowly for spendful creative operations: the caller keeps
local planning, dry-run, receipts, and asset records, while the gateway owns the live provider
credential use plus creative-credit reserve/commit/release.

Calls are machine-facing only and require the dashboard session token header. The gateway never
returns provider secrets; it returns only the result payload needed for the caller to persist
durable Takyon truth outside this boundary.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from .control_api import get_control_conn
from . import safebox

_SESSION_HEADER_NAME = "X-Takyon-Session-Token"
_UNAUTH_HEADERS = {"WWW-Authenticate": _SESSION_HEADER_NAME}


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
    session_token: str | None = Header(default=None, alias=_SESSION_HEADER_NAME),
) -> None:
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


def build_creative_gateway_router() -> APIRouter:
    router = APIRouter(prefix="/internal/creative-gateway")

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

        reservation_key = f"{idempotency_key}:creative-credits"
        try:
            credits.open_business_credit_account(conn, business)
            credits.reserve_credits(
                conn,
                business,
                core._creative_credit_total_cost("ugc_ad_generate"),
                reservation_key,
                metadata={
                    "action": "ugc_ad_generate",
                    "slug": slug,
                    "brief_path": brief_rel,
                    "script_path": script_rel or None,
                },
            )
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
                balances = credits.release_credits(
                    conn,
                    reservation_key,
                    metadata={
                        "action": "ugc_ad_generate",
                        "slug": slug,
                        "error": run.stderr or run.stdout or f"exit {run.returncode}",
                    },
                )
                finalized = True
                return {
                    "success": False,
                    "status": "failed",
                    "stdout": run.stdout,
                    "stderr": run.stderr,
                    "error": run.stderr or run.stdout or f"ugc-video-ad exited {run.returncode}",
                    "balance_credits": balances.balance_credits,
                    "reserved_credits": balances.reserved_credits,
                }

            payload = core._parse_ugc_write_payload(run.stdout)
            balances = credits.commit_credits(
                conn,
                reservation_key,
                metadata={
                    "action": "ugc_ad_generate",
                    "slug": slug,
                    "provider": "openai+fal",
                },
            )
            finalized = True
            return {
                "success": True,
                "status": "created",
                "write_payload": payload,
                "credits_charged": core._creative_credit_total_cost("ugc_ad_generate"),
                "balance_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "stdout": run.stdout,
                "stderr": run.stderr,
            }
        except Exception as exc:
            if not finalized:
                try:
                    credits.release_credits(
                        conn,
                        reservation_key,
                        metadata={
                            "action": "ugc_ad_generate",
                            "slug": slug,
                            "error": str(exc),
                        },
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

        reservation_key = f"{idempotency_key}:creative-credits"
        requested_credits = core._creative_credit_total_cost("static_ad_generate", units=requested)
        try:
            credits.open_business_credit_account(conn, business)
            credits.reserve_credits(
                conn,
                business,
                requested_credits,
                reservation_key,
                metadata={
                    "action": "static_ad_generate",
                    "slug": slug,
                    "input_path": input_rel,
                    "requested_creatives": requested,
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

        finalized = False
        try:
            run = subprocess.run(
                cmd,
                cwd=str(store._business_root(business)),
                capture_output=True,
                text=True,
                check=False,
            )
            manifest_rel = f"{publication_rel}/manifest.json"
            manifest_abs = store._resolve_business_file(business, manifest_rel)
            manifest: dict[str, Any] = {}
            if manifest_abs.is_file():
                try:
                    manifest = json.loads(manifest_abs.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}
            succeeded = int(manifest.get("succeeded") or 0)
            failed = int(manifest.get("failed") or 0)
            if run.returncode != 0:
                if succeeded > 0:
                    balances = credits.commit_credits(
                        conn,
                        reservation_key,
                        actual_credits=core._creative_credit_total_cost(
                            "static_ad_generate", units=succeeded
                        ),
                        metadata={
                            "action": "static_ad_generate",
                            "slug": slug,
                            "input_path": input_rel,
                            "requested_creatives": requested,
                            "succeeded_creatives": succeeded,
                        },
                    )
                else:
                    balances = credits.release_credits(
                        conn,
                        reservation_key,
                        metadata={
                            "action": "static_ad_generate",
                            "slug": slug,
                            "error": run.stderr or run.stdout or f"exit {run.returncode}",
                        },
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
                    "balance_credits": balances.balance_credits,
                    "reserved_credits": balances.reserved_credits,
                    "stdout": run.stdout,
                    "stderr": run.stderr,
                    "error": run.stderr or run.stdout or f"static ad generator exited {run.returncode}",
                }

            charged_units = max(1, succeeded or requested)
            balances = credits.commit_credits(
                conn,
                reservation_key,
                actual_credits=core._creative_credit_total_cost(
                    "static_ad_generate", units=charged_units
                ),
                metadata={
                    "action": "static_ad_generate",
                    "slug": slug,
                    "input_path": input_rel,
                    "requested_creatives": requested,
                    "succeeded_creatives": charged_units,
                    "provider": backend,
                },
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
                "balance_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "stdout": run.stdout,
                "stderr": run.stderr,
            }
        except Exception as exc:
            if not finalized:
                try:
                    credits.release_credits(
                        conn,
                        reservation_key,
                        metadata={
                            "action": "static_ad_generate",
                            "slug": slug,
                            "error": str(exc),
                        },
                    )
                except Exception:
                    pass
            raise HTTPException(status_code=502, detail=str(exc)) from exc

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
        try:
            credits.open_business_credit_account(conn, business)
            credits.reserve_credits(
                conn,
                business,
                requested_credits,
                reservation_key,
                metadata={
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

        created: dict[str, Any] = {}
        finalized = False
        try:
            if plan["asset_kind"] == "video":
                created["video_id"] = core._meta_upload_advideo(video_abs, cfg, name=plan["ad_name"])
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

            campaign = core._meta_graph("POST", f"{acct}/campaigns", {
                "name": plan["campaign_name"],
                "objective": plan["objective"],
                "status": "PAUSED",
                "special_ad_categories": "[]",
            }, cfg)
            created["campaign_id"] = str(campaign.get("id") or "").strip()

            adset = core._meta_graph("POST", f"{acct}/adsets", {
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

            ad = core._meta_graph("POST", f"{acct}/ads", {
                "name": plan["ad_name"],
                "adset_id": created["adset_id"],
                "status": "PAUSED",
                "creative": json.dumps({"creative_id": created["creative_id"]}),
            }, cfg)
            created["ad_id"] = str(ad.get("id") or "").strip()

            balances = credits.commit_credits(
                conn,
                reservation_key,
                metadata={
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
                "balance_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
            }
        except Exception as exc:
            try:
                if created:
                    balances = credits.commit_credits(
                        conn,
                        reservation_key,
                        metadata={
                            "action": "meta_ad_launch",
                            "status": "partial_failed",
                            "created": created,
                            "error": str(exc),
                        },
                    )
                else:
                    balances = credits.release_credits(
                        conn,
                        reservation_key,
                        metadata={
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
                    "balance_credits": balances.balance_credits,
                    "reserved_credits": balances.reserved_credits,
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
        try:
            credits.open_business_credit_account(conn, business)
            credits.reserve_credits(
                conn,
                business,
                requested_credits,
                reservation_key,
                metadata={
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

        created: dict[str, Any] = {}
        preview_url = None
        preview_expiry = None
        post_url = None
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
            if not post_id:
                post_payload = plan.get("post_payload") or {}
                post_resp = core._reddit_ads_request(
                    "POST",
                    f"/profiles/{profile_id}/posts",
                    cfg,
                    json_body=post_payload,
                )
                post_data = core._reddit_ads_data(post_resp["data"]) or {}
                post_id = str(post_data.get("id") or "").strip()
                post_url = str(post_data.get("post_url") or "").strip() or None
                if not post_id:
                    raise RuntimeError("Reddit Ads post creation returned no id")
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

            balances = credits.commit_credits(
                conn,
                reservation_key,
                metadata={
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
                "preview_url": preview_url,
                "preview_expiry": preview_expiry,
                "post_url": post_url,
                "credits_charged": requested_credits,
                "balance_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
            }
        except Exception as exc:
            try:
                if created:
                    balances = credits.commit_credits(
                        conn,
                        reservation_key,
                        metadata={
                            "action": "reddit_ad_launch",
                            "status": "partial_failed",
                            "created": created,
                            "error": str(exc),
                        },
                    )
                else:
                    balances = credits.release_credits(
                        conn,
                        reservation_key,
                        metadata={
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
                    "preview_url": preview_url,
                    "preview_expiry": preview_expiry,
                    "post_url": post_url,
                    "error": str(exc),
                    "credits_charged": requested_credits if created else 0,
                    "balance_credits": balances.balance_credits,
                    "reserved_credits": balances.reserved_credits,
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
            if budget_scope == "campaign":
                target_path = f"/campaigns/{ids['campaign_id']}"
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
            "CPM",
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
