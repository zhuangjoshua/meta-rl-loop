"""AUTHORITATIVE — INVARIANT 2: No user-mutable live pricing.

GOAL_RULES.md §3 invariant 2:

    "Economic terms on a `plan_key` with >=1 active/trialing subscriber are frozen;
     no override reachable by any caller."

This is the money-safety suite's authoritative pin for the pricing-freeze invariant.
It asserts the *structure* of the guard (so the invariant cannot be silently weakened
in source) AND the observable behavior on a real Postgres rig.

Grounding (re-confirmed by reading the source before writing):
  plugins/takyon/app_entitlements.py
    * upsert_plan_policy            (~234) — the only plan-catalog mutator; runs the guard
    * GrandfatheredPlanFrozen       (~84)  — the typed refusal raised when frozen terms change
    * count_active_entitlements_for_plan (~396) — counts active/trialing locks on a plan_key
    * _ACTIVE_STATUSES = ("active", "trialing")  — what "active subscriber" means here

The economic terms the guard freezes (read from the `current_terms`/`incoming_terms`
comparison in upsert_plan_policy, ~291-307):
    tier, price_cents, currency, billing_interval,
    included_ai_budget_microusd, included_action_quota

The invariant of the task names four economic fields explicitly
(tier / price_cents / billing_interval / included_ai_budget_microusd); the source
additionally freezes currency and included_action_quota. We assert the four named
fields are a SUBSET of the frozen set (the contract), and that no NON-economic field
(notes, metadata, stripe linkage) is in the frozen set (those stay editable).

Most assertions need NO credentials/network — they import the real symbol and assert
its signature + source structure. The behavioral end-to-end checks use the repo's
`pg_conn` fixture and skip when psycopg / TAKYON_TEST_PG_DSN are absent.
"""

from __future__ import annotations

import inspect
import io
import re
import tokenize
import uuid

import pytest

from plugins.takyon import app_entitlements as ae
from plugins.takyon.app_entitlements import (
    GrandfatheredPlanFrozen,
    count_active_entitlements_for_plan,
    upsert_plan_policy,
)

# The four economic fields the task names explicitly. The guard must freeze AT LEAST these.
_NAMED_ECONOMIC_FIELDS = frozenset(
    {"tier", "price_cents", "billing_interval", "included_ai_budget_microusd"}
)

# Fields that are explicitly NON-economic and must stay editable on a live plan.
_NON_ECONOMIC_FIELDS = frozenset(
    {"notes", "metadata", "stripe_product_id", "stripe_price_id", "source"}
)

# Any kwarg name that would look like an escape hatch / override for the freeze.
# If a future edit adds one of these, this invariant test must FAIL.
_FORBIDDEN_OVERRIDE_TOKENS = (
    "override",
    "force",
    "bypass",
    "allow_reprice",
    "allow_repricing",
    "ignore_grandfather",
    "ignore_freeze",
    "unfreeze",
    "skip_grandfather",
    "skip_freeze",
    "regrandfather",
)


_SOURCE = inspect.getsource(upsert_plan_policy)


def _executable_code_only(src: str) -> str:
    """Return `src` with comments and string literals (incl. the docstring) removed, so token
    scans see only executable logic. The guard's own docstring legitimately contains the word
    "override" ("There is no override flag"); we must not false-positive on prose."""
    out: list[str] = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        # tolerate a truncated dedent at the tail; we still captured the body tokens
        pass
    return " ".join(out)


_SOURCE_CODE_ONLY = _executable_code_only(_SOURCE)


# ── 1. signature: no override / bypass kwarg exists on the only mutator ───────────────


def test_upsert_plan_policy_signature_has_no_override_kwarg():
    """The sole plan-catalog mutator exposes NO caller-supplied escape hatch. "No override
    reachable by any caller" first means: the override cannot even be NAMED at the call site."""
    params = list(inspect.signature(upsert_plan_policy).parameters.keys())
    lowered = {p.lower() for p in params}
    leaked = {
        p
        for p in params
        if any(tok in p.lower() for tok in _FORBIDDEN_OVERRIDE_TOKENS)
    }
    assert not leaked, f"plan mutator grew an override-shaped kwarg: {sorted(leaked)}"
    # And positively pin the exact accepted parameter set so a sneaky rename is caught too.
    assert lowered == {
        "conn",
        "business_slug",
        "plan_key",
        "tier",
        "price_cents",
        "currency",
        "billing_interval",
        "included_ai_budget_microusd",
        "included_action_quota",
        "stripe_product_id",
        "stripe_price_id",
        "source",
        "notes",
        "metadata",
    }, f"unexpected parameter set: {sorted(lowered)}"


def test_no_other_public_mutator_can_set_plan_economics():
    """`upsert_plan_policy` must be the ONLY public callable in the module that writes the
    plan-catalog economic columns. A second writer would be an override reachable by a caller
    even if `upsert_plan_policy` itself is locked down."""
    econ_columns = (
        "price_cents",
        "included_ai_budget_microusd",
        "billing_interval",
    )
    offenders = []
    for name, fn in vars(ae).items():
        if name.startswith("_") or not callable(fn):
            continue
        if getattr(fn, "__module__", None) != ae.__name__:
            continue
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        # A public function that issues an UPDATE/INSERT writing an economic column,
        # other than the sanctioned mutator, is a leak.
        writes_economics = any(
            re.search(rf"\b{col}\b\s*=", src) and ("update app_plan_policies" in src or "insert into app_plan_policies" in src)
            for col in econ_columns
        )
        if writes_economics and name != "upsert_plan_policy":
            offenders.append(name)
    assert offenders == [], f"unexpected plan-economics writers: {offenders}"


# ── 2. source structure: the guard is wired, unconditional, and not toggle-gated ──────


def test_guard_reads_active_subscriber_count_and_raises_frozen():
    """The mutator must call `count_active_entitlements_for_plan` and raise
    `GrandfatheredPlanFrozen` when economic terms changed with active>0. This pins that the
    refusal path actually exists in the code path (not just the exception class)."""
    assert "count_active_entitlements_for_plan(" in _SOURCE
    assert "GrandfatheredPlanFrozen(" in _SOURCE
    # the refusal is gated on the live-subscriber count being positive
    assert re.search(r"if\s+active\s*>\s*0\s*:", _SOURCE), "freeze must require active>0"


def test_guard_is_not_disabled_by_any_flag_or_env():
    """"No override reachable by any caller" also means no in-body backdoor: the guard must
    not be guarded by an env var, a config lookup, a kwarg flag, or a test-mode branch.

    We scan EXECUTABLE CODE ONLY (comments + the docstring are stripped) — the guard's own
    docstring legitimately says "There is no override flag", which is prose, not a branch."""
    code = _SOURCE_CODE_ONLY.lower()
    assert "os.environ" not in code and "getenv" not in code, (
        "freeze path must not consult the environment"
    )
    assert "test_mode" not in code and "mode ==" not in code, (
        "freeze must not have a test/live-mode escape branch"
    )
    # No override-shaped identifier appears in the executable logic of the guard.
    for tok in _FORBIDDEN_OVERRIDE_TOKENS:
        assert tok not in code, f"freeze logic references an override token: {tok!r}"


def test_grandfathered_plan_frozen_is_an_exception_not_a_returnable_flag():
    """The refusal is a raised typed error (fail-closed), not a boolean a caller can ignore."""
    assert isinstance(GrandfatheredPlanFrozen, type)
    assert issubclass(GrandfatheredPlanFrozen, Exception)
    assert issubclass(GrandfatheredPlanFrozen, ae.EntitlementError)


# ── 3. the frozen term-set is exactly the economic columns (named ⊆ frozen) ───────────


def _frozen_term_keys_from_source() -> set[str]:
    """Extract the keys compared in the `current_terms = { ... }` dict the guard diffs.
    Those keys ARE the frozen economic-term set."""
    m = re.search(r"current_terms\s*=\s*\{(.*?)\}", _SOURCE, re.DOTALL)
    assert m, "could not locate the current_terms comparison dict in the guard"
    return set(re.findall(r'"([a-z_]+)"\s*:', m.group(1)))


def test_named_economic_fields_are_all_frozen():
    """Every economic field the task names must be in the guard's compared term-set."""
    frozen = _frozen_term_keys_from_source()
    missing = _NAMED_ECONOMIC_FIELDS - frozen
    assert not missing, f"named economic fields NOT frozen by the guard: {sorted(missing)}"


def test_non_economic_fields_are_not_frozen():
    """Non-economic fields (notes, metadata, Stripe linkage, source) must NOT be in the frozen
    set — they stay editable on a live plan; only economic terms are frozen."""
    frozen = _frozen_term_keys_from_source()
    leaked = frozen & _NON_ECONOMIC_FIELDS
    assert not leaked, f"non-economic fields wrongly frozen: {sorted(leaked)}"


# ── 4. count_active_entitlements_for_plan defines "active" as active|trialing ──────────


def test_active_means_active_or_trialing():
    """The freeze trigger counts exactly active+trialing grants. A grant in any other status
    (cancelled, past_due, …) must NOT freeze the plan, or a re-price could be blocked forever
    by dead subscriptions, and trialing users must be protected like active ones."""
    assert set(ae._ACTIVE_STATUSES) == {"active", "trialing"}
    count_src = inspect.getsource(count_active_entitlements_for_plan)
    assert "status in (" in count_src, "active count must filter on subscription status"
    assert "_ACTIVE_STATUSES" in count_src


# ── 5. PG behavioral end-to-end: the guard actually refuses a live re-price ────────────
#
# These exercise the real engine on real Postgres via the repo `pg_conn` fixture. They skip
# automatically when psycopg is missing or TAKYON_TEST_PG_DSN is unset (the fixture skips).

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402
from plugins.takyon.app_entitlements import FakeBillingRejected  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402

# NB: the repo registers only `integration` / `real_concurrent_gate` markers and its PG tests
# skip purely via the `pg_conn` fixture (no custom mark). We follow that convention — the
# `pg_conn` fixture itself pytest.skip()s when psycopg / TAKYON_TEST_PG_DSN are absent.


def _owner(conn) -> str:
    uid, _, _ = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    return uid


def _business(conn, owner_id) -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", owner_id),
    )
    return slug


def _subscribe(conn, slug, plan_key, *, status="active", email="cust@example.com") -> str:
    """A real active/trialing subscriber that locks `plan_key` (with Stripe evidence so the
    money-truth guard does not reject the grant)."""
    user_id = app_identity.upsert_app_user(conn, slug, email).id
    app_entitlements_grant = ae.grant_entitlement(
        conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        status=status,
        source="stripe",
        stripe_subscription_id=f"sub_{uuid.uuid4().hex[:8]}",
        plan_key=plan_key,
    )
    assert app_entitlements_grant  # grant succeeded
    return user_id


def test_pg_each_named_economic_change_is_refused_with_active_subscriber(pg_conn):
    """For EACH named economic field, changing it on a plan with an active subscriber raises
    GrandfatheredPlanFrozen, and the live plan row is left untouched."""
    slug = _business(pg_conn, _owner(pg_conn))
    upsert_plan_policy(
        pg_conn,
        slug,
        "pro",
        tier="paid",
        price_cents=2000,
        billing_interval="month",
        included_ai_budget_microusd=1_000_000,
    )
    _subscribe(pg_conn, slug, "pro")

    # one mutation per named economic field; each must be refused
    changes = {
        "tier": dict(tier="pro", price_cents=2000, billing_interval="month",
                     included_ai_budget_microusd=1_000_000),
        "price_cents": dict(tier="paid", price_cents=3000, billing_interval="month",
                            included_ai_budget_microusd=1_000_000),
        "billing_interval": dict(tier="paid", price_cents=2000, billing_interval="year",
                                 included_ai_budget_microusd=1_000_000),
        "included_ai_budget_microusd": dict(tier="paid", price_cents=2000,
                                            billing_interval="month",
                                            included_ai_budget_microusd=500_000),
    }
    for field, kwargs in changes.items():
        with pytest.raises(GrandfatheredPlanFrozen) as exc:
            upsert_plan_policy(pg_conn, slug, "pro", **kwargs)
        assert field in str(exc.value), f"refusal for {field} did not name it"

    # live row never moved
    live = ae.get_plan_policy(pg_conn, slug, "pro")
    assert live.tier == "paid"
    assert live.price_cents == 2000
    assert live.billing_interval == "month"
    assert live.included_ai_budget_microusd == 1_000_000


def test_pg_non_economic_edit_passes_on_live_plan(pg_conn):
    """Editing notes / Stripe linkage while re-passing identical economic terms must SUCCEED on
    a live plan — the freeze is scoped to economics only."""
    slug = _business(pg_conn, _owner(pg_conn))
    upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    _subscribe(pg_conn, slug, "pro")
    plan = upsert_plan_policy(
        pg_conn,
        slug,
        "pro",
        tier="paid",
        price_cents=2000,
        notes="clarified copy",
        stripe_price_id="price_live",
    )
    assert plan.notes == "clarified copy"
    assert plan.stripe_price_id == "price_live"


def test_pg_reprice_allowed_without_active_subscriber(pg_conn):
    """The freeze only bites with an active/trialing subscriber. With zero live subscribers a
    re-price in place is allowed (so the guard is not an unconditional lock)."""
    slug = _business(pg_conn, _owner(pg_conn))
    upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=1000)
    plan = upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    assert plan.price_cents == 2000


def test_pg_cancelled_subscriber_does_not_freeze(pg_conn):
    """A cancelled grant is not active/trialing, so it must NOT freeze the plan — otherwise dead
    subscriptions would lock pricing forever."""
    slug = _business(pg_conn, _owner(pg_conn))
    upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    _subscribe(pg_conn, slug, "pro", status="canceled")
    assert count_active_entitlements_for_plan(pg_conn, slug, "pro") == 0
    plan = upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=4000)
    assert plan.price_cents == 4000


def test_pg_new_plan_key_is_the_only_sanctioned_reprice_path(pg_conn):
    """The sanctioned way to change pricing with a live subscriber is to mint a NEW plan_key;
    the old subscriber's frozen plan_key row is untouched."""
    slug = _business(pg_conn, _owner(pg_conn))
    upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    _subscribe(pg_conn, slug, "pro")
    new_plan = upsert_plan_policy(pg_conn, slug, "pro-2", tier="paid", price_cents=3000)
    assert new_plan.plan_key == "pro-2"
    assert new_plan.price_cents == 3000
    assert ae.get_plan_policy(pg_conn, slug, "pro").price_cents == 2000  # frozen row intact
