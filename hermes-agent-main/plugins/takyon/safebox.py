"""Read-only Safebox accessors for env-backed secret authority.

The backing store for deployed Takyon secrets may still be process env
(e.g. Vercel/runtime injection) or ``TAKYON_HOME/.env``. The authority
boundary lives here: callers should route secret and funding-sensitive
reads through Safebox instead of touching ``os.environ`` or parsing the
env file directly.

This module is intentionally read-only. It exposes no setters and no
generic "read any key" helper beyond a narrow allowlist for the current
dashboard/auth/billing surfaces.
"""

from __future__ import annotations

import os

from takyon_cli.config import load_env

SAFEBOX_ENV_KEYS = frozenset(
    {
        "AUTH0_CLIENT_SECRET",
        "AUTH0_SECRET",
        "DATABASE_URL",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL",
        "STRIPE_BILLING_WEBHOOK_SECRET",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
    }
)


def _require_allowed(key: str) -> str:
    name = str(key or "").strip()
    if name not in SAFEBOX_ENV_KEYS:
        raise KeyError(f"safebox does not expose env key: {name}")
    return name


def read_env_backed_value(key: str) -> str:
    """Read one allowlisted secret-backed value from env or TAKYON_HOME/.env."""
    name = _require_allowed(key)
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    return str(load_env().get(name) or "").strip()


def first_env_backed_value(*keys: str) -> str:
    """Return the first non-empty allowlisted value across env-backed aliases."""
    for key in keys:
        value = read_env_backed_value(key)
        if value:
            return value
    return ""
