"""Product email rail: guarded, metered sends over the platform's transactional provider.

This leaf owns recipient/limit/test-mode policy; metering and receipts reuse the
app_actions dual-backend helpers so there is exactly one reserve/settle implementation
per plugin. The provider is Postmark today (same credentials as platform magic links);
the rail boundary keeps it swappable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

try:
    from . import app_actions as _app_actions
except Exception:  # pragma: no cover - import shape depends on caller
    from plugins.takyon import app_actions as _app_actions

is_service_email = _app_actions.is_service_email

_SUBJECT_MAX_CHARS = 200
_TEXT_MAX_CHARS = 50_000
_PURPOSE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DEFAULT_DAILY_CAP = 200
_DEFAULT_SEND_PRICE_MICROUSD = 1500


class AppEmailError(RuntimeError):
    pass


class EmailBudgetExceeded(AppEmailError):
    pass


class EmailDailyCapExceeded(AppEmailError):
    pass


def _daily_send_cap() -> int:
    raw = str(os.getenv("TAKYON_APP_EMAIL_DAILY_CAP") or "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_DAILY_CAP
    except ValueError:
        value = _DEFAULT_DAILY_CAP
    return max(1, value)


def _send_price_microusd() -> int:
    raw = str(os.getenv("TAKYON_APP_EMAIL_SEND_PRICE_MICROUSD") or "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_SEND_PRICE_MICROUSD
    except ValueError:
        value = _DEFAULT_SEND_PRICE_MICROUSD
    return max(0, value)


def _resolve_recipient(
    store: Any,
    business_slug: str,
    app_user_id: str,
    *,
    service_session_token: str | None = None,
) -> dict[str, Any]:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            leaves = store._app_leaves()
            with store._leaf_conn(conn) as leaf:
                if service_session_token:
                    with store._pg_app_scope(conn, business_slug, session_token=service_session_token):
                        row = leaf.execute(
                            "select app_user_id, business_slug, email, name, status, tier "
                            "from takyon_app_service_email_recipient(%s, %s, %s)",
                            (
                                business_slug,
                                leaves["identity"]._hash_token(service_session_token),
                                app_user_id,
                            ),
                        ).fetchone()
                    user = None if row is None else leaves["identity"]._app_user_from_row(row)
                else:
                    row = leaf.execute(
                        "select id, business_slug, email, name, status, tier "
                        "from app_users where business_slug = %s and id = %s",
                        (business_slug, app_user_id),
                    ).fetchone()
                    user = None if row is None else leaves["identity"]._app_user_from_row(row)
            if user is None or str(getattr(user, "status", "") or "") != "active":
                raise AppEmailError("recipient app user not found")
            return {"id": user.id, "email": user.email, "tier": getattr(user, "tier", "") or ""}
        row = conn.execute(
            "SELECT id, email, tier, status FROM app_users WHERE business_slug = ? AND id = ?",
            (business_slug, app_user_id),
        ).fetchone()
        user = store._row_to_dict(row) if row is not None else None
        if not user or str(user.get("status") or "active") != "active":
            raise AppEmailError("recipient app user not found")
        return {
            "id": str(user.get("id") or ""),
            "email": str(user.get("email") or ""),
            "tier": str(user.get("tier") or ""),
        }


def _sends_today(store: Any, business_slug: str, *, service_session_token: str | None = None) -> int:
    try:
        from .core import _PGConn
    except Exception:
        from plugins.takyon.core import _PGConn

    with store._connect() as conn:
        if isinstance(conn, _PGConn):
            leaves = store._app_leaves()
            with store._leaf_conn(conn) as leaf:
                if service_session_token:
                    with store._pg_app_scope(conn, business_slug, session_token=service_session_token):
                        row = leaf.execute(
                            "select takyon_app_service_email_sends_today(%s, %s)",
                            (business_slug, leaves["identity"]._hash_token(service_session_token)),
                        ).fetchone()
                    if row is None:
                        raise AppEmailError("service app session not authorized for product email")
                else:
                    row = leaf.execute(
                        "select count(*) from app_usage_events "
                        "where business_slug = %s and purpose = 'email_send' "
                        "and created_at >= date_trunc('day', now() at time zone 'utc')",
                        (business_slug,),
                    ).fetchone()
            return int(row[0]) if row else 0
        row = conn.execute(
            "SELECT COUNT(*) FROM app_usage_events "
            "WHERE business_slug = ? AND purpose = 'email_send' "
            "AND substr(created_at, 1, 10) = substr(?, 1, 10)",
            (business_slug, _utc_now_text()),
        ).fetchone()
        return int(row[0]) if row else 0


def _utc_now_text() -> str:
    try:
        from .core import _now
    except Exception:
        from plugins.takyon.core import _now
    return str(_now())


def _send_postmark(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None,
) -> str | None:
    try:
        from . import safebox
    except Exception:
        from plugins.takyon import safebox

    try:
        body = safebox.send_postmark_email(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            message_stream=str(os.getenv("TAKYON_APP_EMAIL_MESSAGE_STREAM") or "").strip() or None,
        )
        return body.get("message_id")
    except Exception as exc:
        if "postmark_unconfigured" in str(exc):
            raise AppEmailError(
                "product email requires POSTMARK_SERVER_TOKEN and POSTMARK_FROM_EMAIL"
            ) from exc
        raise AppEmailError(f"product email provider failed: {exc}") from exc


def _send_postmark_broker(
    *,
    business_slug: str,
    session_token: str,
    recipient_app_user_id: str,
    subject: str,
    text_body: str,
    html_body: str | None,
    estimate_microusd: int,
) -> str | None:
    try:
        from . import safebox
    except Exception:
        from plugins.takyon import safebox

    if not str(session_token or "").strip():
        raise AppEmailError("product email broker requires a service app session")
    try:
        body = safebox.broker_provider_call(
            "postmark",
            "send",
            {
                "recipient_app_user_id": recipient_app_user_id,
                "subject": subject,
                "text_body": text_body,
                "html_body": html_body,
                "message_stream": str(os.getenv("TAKYON_APP_EMAIL_MESSAGE_STREAM") or "").strip() or None,
            },
            estimate_microusd=int(estimate_microusd),
            business=business_slug,
            action="postmark.send",
            session_token=session_token,
        )
        return body.get("message_id")
    except Exception as exc:
        if "postmark_unconfigured" in str(exc):
            raise AppEmailError(
                "product email requires POSTMARK_SERVER_TOKEN and POSTMARK_FROM_EMAIL"
            ) from exc
        raise AppEmailError(f"product email provider failed: {exc}") from exc


def _receipt_relpath(business_slug: str, purpose: str, reservation_key: str) -> str:
    digest = hashlib.sha256(f"{business_slug}:{purpose}:{reservation_key}".encode("utf-8")).hexdigest()[:16]
    return f"metrics/receipts/app-email/{purpose}-{digest}.json"


def send_app_email(
    store: Any,
    *,
    business_slug: str,
    recipient_app_user_id: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    purpose: str = "product_email",
    idempotency_key: str,
    test_mode: bool,
    principal: Mapping[str, Any],
    service_session_token: str | None = None,
) -> dict[str, Any]:
    subject = str(subject or "").strip()
    text_body = str(text_body or "")
    purpose = str(purpose or "product_email").strip().lower()
    if not subject or len(subject) > _SUBJECT_MAX_CHARS:
        raise AppEmailError(f"subject is required and must be at most {_SUBJECT_MAX_CHARS} characters")
    if not text_body.strip() or len(text_body) > _TEXT_MAX_CHARS:
        raise AppEmailError(f"text body is required and must be at most {_TEXT_MAX_CHARS} characters")
    if not _PURPOSE_PATTERN.match(purpose):
        raise AppEmailError("purpose must be a lowercase slug (a-z, 0-9, -, _)")

    recipient = _resolve_recipient(
        store,
        business_slug,
        str(recipient_app_user_id or "").strip(),
        service_session_token=service_session_token,
    )
    if is_service_email(recipient["email"]):
        raise AppEmailError("service identities cannot receive product email")

    cap = _daily_send_cap()
    sent_today = _sends_today(store, business_slug, service_session_token=service_session_token)
    if sent_today >= cap:
        raise EmailDailyCapExceeded(f"daily send cap reached: {sent_today}/{cap}")

    price = _send_price_microusd()
    metadata = {
        "purpose": purpose,
        "recipient": recipient["id"],
        "principal": str(principal.get("kind") or "unknown"),
    }
    suppressed = bool(test_mode)
    brokered = False
    provider_message_id: str | None
    try:
        from . import safebox
    except Exception:
        from plugins.takyon import safebox

    if not suppressed and service_session_token:
        if not safebox.provider_broker_enabled():
            raise AppEmailError("live service email requires the Safebox provider broker")
        provider_message_id = _send_postmark_broker(
            business_slug=business_slug,
            session_token=service_session_token,
            recipient_app_user_id=recipient["id"],
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            estimate_microusd=price,
        )
        brokered = True
    else:
        try:
            _app_actions._reserve_usage(
                store,
                business_slug,
                reservation_key=idempotency_key,
                app_user_id=recipient["id"],
                app_user_tier=recipient["tier"] or None,
                estimate_microusd=price,
                route="email",
                metadata=dict(metadata),
                purpose="email_send",
            )
        except Exception as exc:
            message = str(exc)
            if "budget" in message.lower():
                raise EmailBudgetExceeded(message) from exc
            raise

        try:
            if suppressed:
                provider_message_id = f"test-mode-suppressed:{uuid.uuid4().hex}"
            else:
                provider_message_id = _send_postmark(recipient["email"], subject, text_body, html_body)
        except Exception as exc:
            _app_actions._release_usage(
                store,
                business_slug,
                reservation_key=idempotency_key,
                error=str(exc)[:300],
                metadata=dict(metadata),
            )
            raise

        settle_metadata = {
            **metadata,
            "external_side_effects": "suppressed" if suppressed else "sent",
            "provider_message_id": provider_message_id,
        }
        _app_actions._settle_usage(
            store,
            business_slug,
            reservation_key=idempotency_key,
            actual_microusd=price,
            metadata=settle_metadata,
        )

    receipt_rel = _receipt_relpath(business_slug, purpose, idempotency_key)
    receipt_abs = Path(store._business_root(business_slug)) / receipt_rel
    recipient_domain = recipient["email"].rsplit("@", 1)[-1] if "@" in recipient["email"] else ""
    _app_actions._write_receipt(
        receipt_abs,
        {
            "kind": "app_email_send",
            "business": business_slug,
            "purpose": purpose,
            "recipient_app_user_id": recipient["id"],
            "recipient_domain": recipient_domain,
            "subject": subject[:120],
            "principal": str(principal.get("kind") or "unknown"),
            "suppressed": suppressed,
            "brokered": brokered,
            "provider_message_id": provider_message_id,
            "cost_microusd": price,
            "created_at": _utc_now_text(),
        },
    )
    return {
        "provider_message_id": provider_message_id,
        "suppressed": suppressed,
        "brokered": brokered,
        "receipt_path": receipt_rel,
        "purpose": purpose,
    }
