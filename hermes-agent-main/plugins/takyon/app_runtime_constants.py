"""Shared constants for the Takyon product app runtime."""

from __future__ import annotations

from typing import Any

APP_SESSION_COOKIE = "takyon_app_session"


def subscription_cancellation_policy() -> dict[str, Any]:
    """Machine-readable product cancellation truth shared by producer and consumers.

    Keep customer copy out of this contract. AppKit renders copy from these fields, and the
    product worker receives this exact object in its build contract, so neither can infer timing
    from an entitlement renewal date. Product cancellation never creates or offers a refund.
    """

    return {
        "version": 1,
        "effective_timing": "immediate",
        "refund_policy": "none",
    }
