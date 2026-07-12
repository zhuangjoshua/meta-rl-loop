"""Typed, machine-readable invariants for the Takyon product app runtime.

This module owns platform facts only.  Product layout, visual design, and copy remain worker-owned;
SDKs and AppKit consume these facts so they never have to guess backend behavior.
"""

from __future__ import annotations

from typing import Literal, TypedDict

APP_SESSION_COOKIE = "takyon_app_session"


class SubscriptionCancellationPolicy(TypedDict):
    version: Literal[1]
    effective_timing: Literal["immediate"]
    refund_policy: Literal["none"]


class RecordIdentityPolicy(TypedDict):
    identifier: Literal["opaque_ref"]


class SubscriptionPolicy(TypedDict):
    cancellation: SubscriptionCancellationPolicy


class ProductRuntimeContract(TypedDict):
    version: Literal[1]
    subscription: SubscriptionPolicy
    records: RecordIdentityPolicy


def product_runtime_contract() -> ProductRuntimeContract:
    """Return a fresh copy of the small contract shared by runtime consumers.

    Returning new nested objects prevents one request from mutating process-wide policy.  The
    contract intentionally contains no customer-refund operation and no presentation choices.
    """

    return {
        "version": 1,
        "subscription": {
            "cancellation": {
                "version": 1,
                "effective_timing": "immediate",
                "refund_policy": "none",
            }
        },
        "records": {"identifier": "opaque_ref"},
    }


def subscription_cancellation_policy() -> SubscriptionCancellationPolicy:
    """Machine-readable product cancellation truth shared by producer and consumers.

    Keep customer copy out of this contract. AppKit renders copy from these fields, and the
    product worker receives this exact object in its build contract, so neither can infer timing
    from an entitlement renewal date. Product cancellation never creates or offers a refund.
    """

    return product_runtime_contract()["subscription"]["cancellation"]
