"""Per-business ARCHETYPE registry — the manifest key #3 (after ``runtime_features`` and
``money_shape``) and the ``app | shopify | saas`` operator toggle.

Design contract (general-apps-plan.md §1, operator-ratified): **an archetype is a named,
versioned manifest PRESET in the same grammar as the rest of the surface contract — never a
type, never a code path.** A preset declares DEFAULTS for other manifest keys (money shape,
extra rails, publish adapter, verification gate, compliance gates, approval gates, required
provider aliases). Generic machinery READS the preset; there is deliberately no
``if archetype == 'mobile_app'`` branch anywhere. Adding an archetype is a registry entry, and
the (future, general-apps-plan) composition checker validates the manifest at the single choke
point where ``money_shape.assert_write_matches_shape`` already validates plan writes.

Why this file exists now (readmodular.md §1): the App Store rail needs the archetype spine, and
``general-apps-plan.md`` already treats ``archetype`` as an existing manifest key. This leaf is
that substrate — the ``businesses.archetype`` column's read/write/validate logic — built to be
consumed by the general-apps composition checker rather than to pre-empt it.

House style mirrors ``money_shape.py`` exactly (its header even predicts this file: "the
archetype registry subsumes ``money_shape`` later, when ``money_shape`` becomes a derived
attribute of ``businesses.archetype``"): a pure leaf that takes a psycopg connection, imports no
psycopg, opens its own ``conn.transaction()`` per mutating op, uses ``%s`` placeholders, and
raises typed errors. It has NO authority of its own — it declares and validates the archetype
and never mints a plan, reserves credits, publishes, or touches a ledger. Changing a business's
archetype reuses the SAME single-consume operator-approval affordance ``money_shape.py`` built
(``operator_approvals`` table) — it does not build a second table.

SUBUSER-SECURITY INVARIANT (identical to money_shape's): ``archetype`` is an operator/CEO-plane
declaration. The migration adds it to ``businesses`` but does NOT expose it on the app-runtime
business view, so the subuser plane cannot read it — the App Store pipeline has zero subuser
surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The money-shape leaf owns the operator-approval affordance (request/decide/consume). Archetype
# changes reuse it verbatim — one approval table, per modular.md's golden rule.
try:  # pragma: no cover - alternate load path as a top-level package
    from . import money_shape as _money_shape
except ImportError:  # pragma: no cover
    from plugins.takyon import money_shape as _money_shape


# ── the known archetypes (kept in sync with the businesses_archetype_chk migration) ──────

WEB_SAAS = "web_saas"
MOBILE_APP = "mobile_app"
SHOPIFY_COMMERCE = "shopify_commerce"
ARCHETYPES = (WEB_SAAS, MOBILE_APP, SHOPIFY_COMMERCE)

# The default for a business that has not declared one (archetype NULL/absent). ``web_saas`` is
# today's behavior byte-for-byte — so every existing/undeclared business keeps behaving exactly
# as today with no backfill and no destructive migration.
DEFAULT_ARCHETYPE = WEB_SAAS

# The action_kind for the archetype-change operator approval (mirrors money_shape's
# ``money_shape_change``). Changing a business's archetype is a shape change and is gated.
ARCHETYPE_CHANGE_ACTION_KIND = "archetype_change"


class ArchetypeError(Exception):
    """Base for archetype errors."""


class InvalidArchetype(ArchetypeError):
    """A supplied archetype is not one of the known ARCHETYPES."""


class ArchetypeNotAvailable(ArchetypeError):
    """A known but not-yet-enabled archetype was selected. Its pipeline (publish adapter,
    compliance gates, provider credentials) is not shipped/proven yet, so selecting it is refused
    fail-closed with the exact gate token ``archetype_unavailable:<key>`` — the CEO's discovery
    surface (readmodular §5 rollout: new archetypes flag-gated until their fresh-business E2E
    passes). This is a "gate as a row" per general-apps-plan §1: availability is data on the
    preset, not an if/then in a caller."""


# ── the preset (a named, versioned bundle of manifest-key defaults) ──────────────────────


@dataclass(frozen=True)
class ArchetypePreset:
    """One archetype = a versioned preset over other manifest keys. Every field is a DEFAULT the
    generic machinery reads; none is a branch. ``publish_adapter`` / ``compliance_gates`` /
    ``requires_api`` may name capabilities whose handlers are not shipped yet — that is the
    "gate as a row" pattern: the preset is valid data, and selecting it is gated by ``enabled``
    until the pipeline is proven."""

    key: str
    version: int
    # Human-facing toggle label (dashboard/CLI derive the selector from these, never a hardcoded
    # UI list).
    label: str
    description: str
    # Whether the archetype is selectable at create today. web_saas = True (today's behavior);
    # mobile_app / shopify_commerce start False and flip to True when their pipeline lands and its
    # fresh-business E2E passes (readmodular §5). A create attempt on a disabled archetype fails
    # closed with ``archetype_unavailable:<key>``.
    enabled: bool
    # DEFAULT money shape for this archetype (a manifest key it presets). money_shape.py remains
    # the authority for the value + the gate; this is only the create-time default.
    default_money_shape: str
    # Pinned scaffold path (build kind reads it). web_saas = today's Vite scaffold.
    scaffold: str
    build_kind: str
    # Distribution publish adapter (general-apps §2.7 BuildStep/publish_adapter dispatch).
    publish_adapter: str
    # The pre-publish verification gate name (readmodular §1.7).
    verify_gate: str
    # Extra RUNTIME_RAILS keys this archetype adds beyond the shared set (rails stay in the ONE
    # RUNTIME_RAILS registry; this only records which extras the archetype implies).
    extra_rails: tuple[str, ...] = ()
    # action_kinds that require an operator-approval receipt for this archetype (the supervision
    # dial — readmodular §5.4). ADDITIVE over the always-global gates (money_shape/archetype
    # change), never the exhaustive list.
    approval_gates: tuple[str, ...] = ()
    # Compliance gates run pre-publish (greenlight, oss-license, differentiation, quota).
    compliance_gates: tuple[str, ...] = ()
    # Provider-key aliases (resolved ONLY via the safebox authority route, never os.environ) this
    # archetype's tools declare via ``requires_api``.
    requires_api: tuple[str, ...] = ()


# The registry — the ONLY enumeration of archetype capability. Tool schemas, skill readiness,
# the bootstrap ladder, and the create toggle all read it; nothing re-lists archetypes elsewhere.
BUSINESS_ARCHETYPES: dict[str, ArchetypePreset] = {
    WEB_SAAS: ArchetypePreset(
        key=WEB_SAAS,
        version=1,
        label="SaaS / website",
        description="A web product on the R2 edge — today's behavior, byte-for-byte the identity case.",
        enabled=True,
        default_money_shape=_money_shape.SUBSCRIPTION,
        scaffold="subuser_app_kit/scaffold",
        build_kind="node_build",  # vite + tsc + runtime-authority scanner, unchanged
        publish_adapter="pointer_static",  # R2 immutable build id + current pointer, unchanged
        verify_gate="browser_e2e",
        extra_rails=(),
        approval_gates=(),  # fully autonomous, as today
        compliance_gates=(),
        requires_api=(),
    ),
    MOBILE_APP: ArchetypePreset(
        key=MOBILE_APP,
        version=1,
        label="Mobile app (App Store)",
        description=(
            "An iOS app built with Expo/EAS over the same subuser rails, published to the Apple "
            "App Store (readmodular.md). Bootstrap seeds product/app and "
            "business_publish_mobile_release triggers the store-signed EAS build."
        ),
        # Operator ruling 2026-07-08 (god-mode enablement): the store-signed build lane is
        # live-proven (pocketgarden) and the builder is host-independent via the safebox
        # build-credentials route, so the create toggle is ON. App Store SUBMISSION remains a
        # separate, still-gated lane (approval_gates below).
        enabled=True,
        default_money_shape=_money_shape.SUBSCRIPTION,  # IAP is the same shape, a different provider (M3)
        scaffold="mobile_app_kit/scaffold",
        build_kind="expo_build",
        publish_adapter="store_release",
        verify_gate="sim_flows+store_compliance",
        extra_rails=("push", "deep_links", "store_iap"),
        approval_gates=("store_production_submission",),
        compliance_gates=(
            "greenlight_preflight",
            "oss_license_policy",
            "differentiation_gate",
            "submission_quota",
        ),
        requires_api=("expo", "app_store_connect"),  # Apple only — operator ruling, no Google Play
    ),
    SHOPIFY_COMMERCE: ArchetypePreset(
        key=SHOPIFY_COMMERCE,
        version=1,
        label="Shopify store",
        description=(
            "A brand/marketing site on the R2 edge plus a Composio-connected Shopify store "
            "(rides the shipped business_connect_shopify). NOT yet enabled — S1 slice."
        ),
        enabled=False,
        default_money_shape=_money_shape.COGS_PASSTHROUGH,
        scaffold="subuser_app_kit/scaffold",
        build_kind="node_build",
        publish_adapter="pointer_static",  # + a required live shopify connection receipt pre-live
        verify_gate="browser_e2e+shopify_connected",
        extra_rails=(),
        approval_gates=(),  # money-shape change is globally gated; no archetype-specific extra
        compliance_gates=(),
        requires_api=("composio",),
    ),
}


# ── normalization + validation (pure) ────────────────────────────────────────────────────


_ALIASES = {
    "web_saas": WEB_SAAS,
    "websaas": WEB_SAAS,
    "saas": WEB_SAAS,
    "web": WEB_SAAS,
    "website": WEB_SAAS,
    "site": WEB_SAAS,
    "mobile_app": MOBILE_APP,
    "mobile": MOBILE_APP,
    "app": MOBILE_APP,
    "ios": MOBILE_APP,
    "iphone": MOBILE_APP,
    "appstore": MOBILE_APP,
    "app_store": MOBILE_APP,
    "shopify_commerce": SHOPIFY_COMMERCE,
    "shopify": SHOPIFY_COMMERCE,
    "store": SHOPIFY_COMMERCE,
    "commerce": SHOPIFY_COMMERCE,
    "ecommerce": SHOPIFY_COMMERCE,
}


def normalize_archetype(value, *, allow_empty: bool = True) -> str:
    """Normalize/validate an archetype string. Empty/None → the default (web_saas) when
    ``allow_empty``, else raise. An unknown value always raises ``InvalidArchetype``."""
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        if allow_empty:
            return DEFAULT_ARCHETYPE
        raise InvalidArchetype("archetype is required")
    resolved = _ALIASES.get(raw)
    if resolved is None:
        raise InvalidArchetype(
            f"unknown archetype {value!r}; must be one of {', '.join(ARCHETYPES)} "
            f"(aliases: app→mobile_app, shopify→shopify_commerce, saas→web_saas)"
        )
    return resolved


def preset_for(archetype: str) -> ArchetypePreset:
    """The preset for a (normalized) archetype. Raises ``InvalidArchetype`` on an unknown key."""
    key = normalize_archetype(archetype, allow_empty=False)
    return BUSINESS_ARCHETYPES[key]


def default_money_shape_for(archetype) -> str:
    """The create-time default money shape for an archetype (a preset field). money_shape.py stays
    the authority for validation/gating — this only picks the default at create."""
    try:
        return preset_for(archetype).default_money_shape
    except InvalidArchetype:
        return _money_shape.DEFAULT_MONEY_SHAPE


PREVIEW_ENV = "TAKYON_ARCHETYPE_PREVIEW"


def _preview_enabled_keys() -> frozenset[str]:
    """Registry keys enabled by the operator's PREVIEW override for THIS process only.

    ``TAKYON_ARCHETYPE_PREVIEW=mobile_app`` (comma-separated keys) treats a registered-but-disabled
    preset as selectable on the plane that sets it — the seam that lets the acceptance E2E for an
    archetype run on the operator's own lane while every other plane (prod dashboard, VPS workers)
    stays fail-closed. It can only widen selection to keys that already exist in the registry; it
    is not read from any business/customer input and unknown keys are ignored."""
    import os

    raw = str(os.environ.get(PREVIEW_ENV) or "").strip()
    if not raw:
        return frozenset()
    keys = set()
    for part in raw.split(","):
        try:
            keys.add(normalize_archetype(part, allow_empty=False))
        except InvalidArchetype:
            continue
    return frozenset(keys)


def is_enabled(archetype) -> bool:
    """Whether the archetype is selectable at create today (rollout flag, plus the process-local
    PREVIEW override for acceptance E2E runs)."""
    try:
        key = normalize_archetype(archetype, allow_empty=False)
    except InvalidArchetype:
        return False
    return BUSINESS_ARCHETYPES[key].enabled or key in _preview_enabled_keys()


def assert_selectable(archetype: str) -> str:
    """Return the normalized archetype if it is a known, ENABLED archetype; else fail closed. A
    disabled (not-yet-shipped) archetype raises ``ArchetypeNotAvailable`` carrying the
    ``archetype_unavailable:<key>`` gate token. Called at the create/set choke point so a picker
    can never select a pipeline that isn't proven. The PREVIEW override (see
    ``_preview_enabled_keys``) widens this only on the plane that explicitly sets it."""
    key = normalize_archetype(archetype, allow_empty=False)
    if not BUSINESS_ARCHETYPES[key].enabled and key not in _preview_enabled_keys():
        raise ArchetypeNotAvailable(
            f"archetype_unavailable:{key}: the '{key}' archetype is registered but not yet enabled "
            f"(its build/publish pipeline is not shipped and E2E-proven). Selecting it is refused. "
            f"Available archetypes: {', '.join(k for k, p in BUSINESS_ARCHETYPES.items() if p.enabled)}."
        )
    return key


def selectable_archetypes() -> tuple[ArchetypePreset, ...]:
    """The presets a create toggle should offer today (enabled only). The dashboard/CLI derive
    the selector from this — never a hardcoded UI list."""
    return tuple(p for p in BUSINESS_ARCHETYPES.values() if p.enabled)


def create_toggle_options() -> list[dict]:
    """The full create-toggle option list for the dashboard/CLI — the SINGLE source of truth for the
    app|shopify|saas selector. Includes not-yet-enabled presets so the UI can render them as
    "coming soon" (disabled) rather than a hardcoded roadmap. ``default`` marks the one the create
    funnel picks when nothing is chosen. Order: default first, then the rest in registry order."""
    def _row(p: ArchetypePreset) -> dict:
        return {
            "key": p.key,
            "label": p.label,
            "description": p.description,
            "enabled": bool(p.enabled),
            "default": p.key == DEFAULT_ARCHETYPE,
        }

    rows = [_row(p) for p in BUSINESS_ARCHETYPES.values()]
    rows.sort(key=lambda r: (not r["default"], not r["enabled"]))
    return rows


# ── the per-business archetype record (mirrors money_shape.get/set) ───────────────────────


def get_archetype(conn, business_slug: str) -> str:
    """Read a business's declared archetype, or the default (web_saas) when undeclared/unknown.

    Pure read. Unknown business returns the default rather than inventing a business (a real write
    against a phantom business trips the FK elsewhere)."""
    row = conn.execute(
        "select archetype from businesses where slug = %s",
        (business_slug,),
    ).fetchone()
    if row is None:
        return DEFAULT_ARCHETYPE
    try:
        return normalize_archetype(row[0], allow_empty=True)
    except InvalidArchetype:
        return DEFAULT_ARCHETYPE


def set_archetype(
    conn,
    business_slug: str,
    archetype: str,
    *,
    require_approval: bool = True,
    actor: str = "operator",
) -> str:
    """Declare (or change) a business's archetype. Validates the archetype AND that it is enabled.
    When ``require_approval`` (the default for a CHANGE away from the current archetype), an
    approved ``archetype_change`` operator approval for THIS target must be present and is CONSUMED
    atomically — a hallucinated "switch to mobile_app" cannot flip the record silently (identical
    posture to money_shape.set_money_shape).

    Setting the archetype to the value it already holds is a no-op that never requires approval.
    Unknown business fails loud. Returns the persisted archetype."""
    target = assert_selectable(archetype)
    exists = conn.execute(
        "select 1 from businesses where slug = %s", (business_slug,)
    ).fetchone()
    if exists is None:
        raise ArchetypeError(f"unknown business {business_slug!r}; cannot declare an archetype")
    current = get_archetype(conn, business_slug)
    if target == current:
        with conn.transaction():
            conn.execute(
                "update businesses set archetype = %s, updated_at = now() where slug = %s",
                (target, business_slug),
            )
        return target
    with conn.transaction():
        if require_approval:
            _money_shape.consume_approval(
                conn,
                business_slug,
                ARCHETYPE_CHANGE_ACTION_KIND,
                {"from": current, "to": target},
            )
        conn.execute(
            "update businesses set archetype = %s, updated_at = now() where slug = %s",
            (target, business_slug),
        )
    return target
