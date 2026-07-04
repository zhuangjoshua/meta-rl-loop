# Capability Manifest — concrete spec (deltas 1–2 of general-apps-plan.md)

Short implementation spec for the manifest + composition checker. Parsimony ruling up front:
**the manifest is a VIEW over canonical sources that already exist, not a new store.** Nothing
here duplicates `runtime_features`, `money_shape`, or plan rows into a second table.

## 1. The manifest

A business's capability manifest is the assembled union of its canonical declaration surfaces:

| Namespace key | Canonical source (today) | Write path |
|---|---|---|
| `bindings.rails` (= runtime_features) | surface contract `runtime_features` | `business_upsert_app_surface_contract` |
| `money.shape` | `businesses.money_shape` (migration 0062) | `business_set_mode`-style approval via `set_money_shape` |
| `money.plans` | `app_plan_policies` rows + metadata | `upsert_plan_policy` (freehand + composed) |
| `archetype` | business metadata (archetypes plan) | archetype preset application |
| future keys (`connections.*`, `compute.triggers`, `communication.*`, …) | added to the surface contract as namespaced keys WHEN their rail lands | same surface-contract tool |

`capability_manifest.assemble(conn, business_slug) -> dict` reads these sources and returns the
manifest. It is a read model; there is no manifest table. An archetype preset is a dict literal of
manifest keys applied through the same write paths (never a bypass).

## 2. The checker

New leaf `plugins/takyon/capability_manifest.py`:

```python
def validate_manifest_write(conn, business_slug, namespace: str, proposed: dict) -> None:
    """Raise ManifestViolation subclasses; called from each canonical write path
    BEFORE it persists. Enforces exactly three constraint families:
    1. dependency closure  — generalized rails DAG (core.py:510): every value's
       declared requires_* must resolve to a registered, non-gate entry.
    2. obligation discharge — registry entries carry policy_obligations (native
       fields, per-registry); an undischarged obligation refuses with its gate
       error (e.g. moderation_required:<surface>, tax_posture_required).
    3. money soundness — DELEGATED: calls money_shape.assert_write_matches_shape
       and (for plan writes) compose_plan. The checker performs NO money arithmetic.
    """
```

Wiring: `business_upsert_app_surface_contract` calls it for `runtime_features`/new namespaces;
`upsert_plan_policy` already runs the money family and additionally calls the checker for
obligation edges on plan writes (e.g. order shape ⇒ tax posture). One choke point per write path,
same pattern as `assert_write_matches_shape` today.

Constraint enforcement is over each registry's **native typed fields** — no uniform row-schema
retrofit. The one generalized invariant: `cost_gate`-iff-priced — any registry entry naming a
priced operation must carry its money gate (generalize `CreativeProviderSpec.MissingMoneyGate`'s
unconstructable pattern to `ChannelPublisher` and spendful job kinds/routes as those registries
are next touched). Principal scope derives from `auth_tier` (extend `APP_AUTH_*`), never a
parallel `subject` field.

## 3. Gates

A gate is a **row in the per-concern registry**, not a parallel gate registry: an entry whose
implementation is `gate("<name>")` — e.g. the channel registry holds
`sms → gate("sms_unconfigured")`; the money namespace holds
`payouts → gate("payouts_unsupported")`. Declaring a gated value refuses composition with the
gate's exact error naming its prerequisites. The refused list in `general-apps-plan.md` §3 ships
as deny entries on the brief-time capability screen (one deterministic deny rail). The refused/
deferred catalog derives from registry rows; nothing is policy-in-prose.

## 4. Sequencing (parsimony: no speculative machinery)

The checker module lands **with the first NEW namespace key** (Connections is the first delta
that adds one) — building it before any new registry key exists would validate nothing. Money
Phase A does NOT wait for it: Phase A rides the existing choke points (`upsert_plan_policy`,
`compose_plan`, `money_shape`) which already ARE the money constraint family. Obligation edges
that Phase A introduces (declared exhaustion policy validation) are implemented as plain
validations inside `upsert_plan_policy`, to be lifted into the checker when it lands.

## 5. Reconciliation with takyon-modularization-plan.md (authoritative)

No conflict; complementary layers. The mod-plan owns the COMPUTE plane (WorkerPool/ClaimScope/
RuntimeContext/NodeRegistry, autoscale, dev=prod-mirror) and Stage 6's BuildStep/publish_adapter
seam; the capability manifest owns the PRODUCT capability plane (what a business may declare).
Where they touch: (a) Distribution namespace values enter through the mod-plan's Stage 6
publish_adapter seam — the manifest declares, Stage 6 dispatches; (b) UC4 compositional pricing
IS the money constraint family (`compose_plan` CostBasis) — one implementation, referenced by
both; (c) monthly-only subuser plans (mod-plan ruling) stands — `interval_unsupported` is the
gate. Where they conflict, the mod-plan wins on compute topology; general-apps-plan.md wins on
product capability semantics; `subuser-billing-plan.md` wins on money mechanics.
