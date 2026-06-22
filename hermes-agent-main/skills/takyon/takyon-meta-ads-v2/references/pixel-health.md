# What proves a pixel is functional

Two independent proofs; require both before claiming "functional".

## A. Snippet installed (runtime, no Meta call)
- Fetch the business's live URL (`https://<slug>.coscale.app/`) and confirm the pixel `<script>` /
  `fbq('init', …)` is in the served HTML `<head>`.
- **Check BOTH serving paths** — the VPS path injects at serve time, but the Cloudflare R2 edge serves
  static bytes with no origin fallback, so it only carries the snippet if it's baked into the build
  source. A pass on one path ≠ a pass on the other.
- Reuse the publish reachability posture (a live fetch + assert), not a source read.

## B. Dataset receiving events (Meta side, via MCP)
- `ads_get_datasets` / `ads_get_dataset_details` — the shared dataset exists and is active.
- `ads_get_dataset_stats` — **event volume + recency**: a currently-firing pixel with no recent cliff.
  A sudden drop for an event (or domain) is the primary "broken" signal. `ads_get_errors` surfaces
  dropped events.
- `ads_get_dataset_quality` — **Event Match Quality (EMQ, 0-10)**: healthy floor ~6; realistic by stage
  (PageView 4.5-7, Purchase 7.5-9). Below ~6 leaks 20-40% of attribution.
- **Coverage**: expected standard events present across the funnel (PageView → Lead/Purchase), not just
  top-of-funnel. High EMQ on incomplete coverage is worse than moderate EMQ with full coverage.
- `ads_get_customconversions` — the per-business custom conversion is registered (and, ideally, firing).
- If CAPI is on: **Additional Conversions Reported (ACR)** > 0 (server leg is adding signal) and dedup is
  matching (`event_id`).

## Verdict
Write `metrics/meta-pixel/<slug>/{ensure,preflight}.json` with: `installed_vps`, `installed_r2`,
`dataset_ok`, `custom_conversion_ok`, `dataset_id`, `custom_conversion_id`, `emq`, `last_event`, `ok`.
`ok` is true only when **both** proofs pass. Degrade truthfully (`{configured, ok}`) — never simulate a
healthy pixel from source alone.
