# One shared pixel, per-business attribution

The model for a single Meta pixel across many `<slug>.coscale.app` sites.

## Architecture
- **One shared `pixel_id`**, installed identically on every business site (same string everywhere). The
  pixel is one shared event firehose; you slice it per business on top.
- **Per-business attribution = a Custom Conversion keyed on a URL rule.** One per business:
  `rule: url i_contains "<slug>.coscale.app/<conversion_path>"` → `custom_event_type` (e.g. `LEAD`,
  `PURCHASE`). It fires only for that site's traffic → a clean per-business conversion to optimize and
  report against off the shared pixel.
- **Campaign mapping:** the ad set's `promoted_object` points at that business's custom conversion
  (`pixel_id` + `custom_conversion_id` / rule + `custom_event_type`). That's how a campaign for one
  business optimizes only toward its own conversion.
- **Standard events carry a per-site discriminator** parameter (e.g. `content_category=<slug>`) so server
  logic and audiences can split sites even where URL rules are coarse.

## The non-negotiable rule
**Optimize toward the per-business custom conversion, never the raw shared base event.** If a campaign
optimizes for the generic `Lead`, it learns from *every* business's leads on the shared pixel
(cross-business signal bleed). Per-campaign isolation exists only when each ad set optimizes toward its
own custom conversion.

## Limits & gotchas
- **100 custom conversions per ad account** — the real scaling cap. ~1 per business → ~100 businesses per
  account; beyond that, split across ad accounts. (This is why creation is **lazy**, first-ad-only.)
- **8-event AEM cap is retired (~2025)** — AEM auto-aggregates eligible standard + custom events; no
  manual ranking. Implementation quality (dedup, consistent schema) is the new bottleneck. Verify current
  state in Events Manager (rollouts vary).
- **Dedup:** if you run Pixel + CAPI, every event needs a shared `event_id` + `event_name` or Meta
  double-counts browser vs server copies.
- **iOS/ATT:** opted-out users are modeled/aggregated only (delayed, lossy); browser-only also loses
  ~15-25% to blockers/ITP, which CAPI recovers.

## One-time manual setup (not programmatic)
- **Create the shared pixel/dataset** (Business settings / Events Manager — not an MCP strength).
- **Verify every domain** (DNS TXT / meta-tag / file) so events attribute and one site can't clobber
  another's config.
- **Traffic Permissions:** lock the shared pixel to recognized domains so a stray site can't pollute the
  shared stream.
