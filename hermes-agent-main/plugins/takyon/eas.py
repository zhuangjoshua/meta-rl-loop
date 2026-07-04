"""EAS (Expo Application Services) — the build-lane credential gate + lane/pricing map.

The fail-closed foundation for the App Store build lane (readmodular §2.4/§6.3/§7). Like ``asc.py``
this is a pure, custody-agnostic leaf: it never reads ``os.environ`` and never runs eas-cli itself
(the actual build runs in the jailed docker-sandbox builder driven by the job handler + safebox
mint). What it owns is the SECRET BOUNDARY + MONEY GATE contract:

  * resolve the Expo token ONLY through the safebox authority route (``first_env_backed_value``),
    never from the business runtime env — same pattern as ``creative_gateway._resolve_gemini_image_key``;
  * FAIL CLOSED with ``eas_unconfigured`` before any credit reserve or provider call when the token
    is absent (it is not yet deposited in the safebox — custody is a deliberate later step), so the
    build lane can never spend or half-run without its credential;
  * map each build lane to its usage_pricing key so a credited build records the EXACT provider cost
    (unpriced ⇒ refused upstream).

Apple-only per operator ruling: iOS build + OTA update + hosted Maestro run. No Android lane.
"""

from __future__ import annotations

# Safebox alias set for the Expo robot token (kept in sync with core._API_ENV_ALIASES["expo"]).
EXPO_TOKEN_ALIASES = ("TAKYON_EXPO_TOKEN", "EXPO_TOKEN")

# Build/verify lanes → (provider, op) pricing key in agent/usage_pricing.py. A lane whose pricing
# key is absent from the SSOT is refused (fail closed) — a lane can never spend unpriced.
LANE_PRICING = {
    "build_ios": ("eas", "build_ios"),      # EAS cloud iOS build (creative-credit action)
    "update": ("eas", "update"),            # expo-updates OTA publish (usage-metered, cheap)
    "maestro_ios": ("eas", "maestro_ios"),  # hosted iOS device-flow E2E run
}
BUILD_LANES = ("preview", "production")     # eas.json build profiles (internal / store)


class EasError(Exception):
    """Base for EAS build-lane errors."""


class EasUnconfigured(EasError):
    """The Expo token is not resolvable via the safebox — the build lane cannot run. Carries the
    exact ``eas_unconfigured`` gate token (the CEO's discovery surface). This is the expected state
    until the operator deposits EXPO_TOKEN into the safebox (Doppler); it fails CLOSED, never
    fabricates a build or spends credits."""


def resolve_expo_token(first_env_backed_value) -> str:
    """Resolve the Expo robot token via the safebox authority route ONLY.

    ``first_env_backed_value`` is ``safebox.first_env_backed_value`` (injected so this stays a pure,
    testable leaf and never imports the safebox or reads os.environ). Returns "" when absent — the
    business runtime never sees the raw token; the safebox uses it server-side."""
    try:
        value = first_env_backed_value(*EXPO_TOKEN_ALIASES)
    except Exception:
        return ""
    return str(value or "").strip()


def assert_configured(first_env_backed_value) -> str:
    """Return the Expo token if the safebox holds it, else raise ``EasUnconfigured`` — the
    fail-closed gate every build/submit path calls BEFORE reserving credits or invoking a provider.
    """
    token = resolve_expo_token(first_env_backed_value)
    if not token:
        raise EasUnconfigured(
            "eas_unconfigured: the Expo (EAS) token is not configured in the safebox, so the App "
            "Store build lane cannot run. Deposit EXPO_TOKEN into the safebox (Doppler on the "
            "safebox host); the build is refused until then — no credits are reserved and no "
            "provider is called."
        )
    return token


def pricing_key_for_lane(lane: str) -> tuple[str, str]:
    """The (provider, op) usage_pricing key for a lane. Unknown lane raises — a build lane must map
    to a priced action or it cannot be metered (fail closed)."""
    key = LANE_PRICING.get(str(lane or "").strip())
    if key is None:
        raise EasError(
            f"unknown EAS lane {lane!r}; must be one of {', '.join(LANE_PRICING)} "
            "(each maps to a priced ('eas', op) action)"
        )
    return key
