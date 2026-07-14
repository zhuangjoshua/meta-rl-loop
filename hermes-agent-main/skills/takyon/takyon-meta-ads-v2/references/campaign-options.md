# Campaign options (selectable values + defaults)

Values the agent passes through the bound launch capability. Defaults are pinned; for any fuller enum,
query the live MCP schema (`ads_get_field_context`) — server-side compatibility is enforced. Copy keys:
primary text = `message`; CTA = `call_to_action_type`.

## Default — use this unless the business goal requires otherwise
**Traffic → Website clicks:** `objective=OUTCOME_TRAFFIC`, `optimization_goal=LINK_CLICKS`,
`billing_event=IMPRESSIONS`, `destination_type=WEBSITE`, budget mode **CBO**,
`bid_strategy=LOWEST_COST_WITHOUT_CAP`, Advantage+ placements & audience, geo-only broad targeting,
staged paused unless launch intent is live. Deviate to Sales/Leads/etc. only when the goal clearly needs
it **and** the conversion plumbing (pixel / `promoted_object`) exists.

## Campaign (`ads_create_campaign`)
- **objective**: `OUTCOME_TRAFFIC`, `OUTCOME_SALES`, `OUTCOME_LEADS`, `OUTCOME_ENGAGEMENT`,
  `OUTCOME_AWARENESS`, `OUTCOME_APP_PROMOTION`. (Legacy objectives are rejected.)
- **budget mode**: **CBO** (default — Meta allocates across ad sets): `campaign_daily_budget` OR
  `campaign_lifetime_budget` (cents). **ABO**: leave campaign budgets unset; set the ad-set budget.
- **campaign_spend_cap** (optional, cents): hard lifetime cap.
- **special_ad_categories**: `[]`; or `HOUSING`/`CREDIT`/`EMPLOYMENT`/`ISSUES_ELECTIONS_POLITICS`
  (+ `special_ad_category_country`).
- **schedule** (optional): `campaign_start_time` / `campaign_stop_time` (ISO 8601).

## Ad set (`ads_create_ad_set`)
- **billing_event**: `IMPRESSIONS` (default), `LINK_CLICKS`, `POST_ENGAGEMENT`, `VIDEO_VIEWS`.
- **optimization_goal** — default per objective; full valid set per objective via live schema:
  `OUTCOME_TRAFFIC`→`LINK_CLICKS`; `OUTCOME_SALES`→`OFFSITE_CONVERSIONS` (needs
  `promoted_object.pixel_id`); `OUTCOME_LEADS`→`OFFSITE_CONVERSIONS` (or `LEAD_GENERATION`, page ToS
  required); `OUTCOME_ENGAGEMENT`→`THRUPLAY`; `OUTCOME_AWARENESS`→`REACH`;
  `OUTCOME_APP_PROMOTION`→`APP_INSTALLS`.
- **bid_strategy**: `LOWEST_COST_WITHOUT_CAP` (default, autobid). Other strategies
  (`LOWEST_COST_WITH_BID_CAP`, `COST_CAP` → require `bid_amount`; `LOWEST_COST_WITH_MIN_ROAS` → requires
  `bid_constraints.roas_average_floor`) per live schema. (ABO sets this on the ad set; CBO on the
  campaign.)
- **budget** (ABO): `daily_budget`/`lifetime_budget` (cents). Respect `min_daily_budget_cents` from
  `ads_get_ad_accounts` (USD floor = 100). `lifetime_budget` requires `end_time`.
- **targeting** (JSON): default geo-only broad, e.g. `{"geo_locations":{"countries":["US"]}}`. Interest
  targeting needs **verified numeric IDs** (never invented) — see the targeting note in
  `meta-mcp-tool-map.md`. Advantage+ Audience is on by default (age becomes a suggestion; set
  `targeting_automation.advantage_audience=0` for a hard age cap).
- **destination_type**: `WEBSITE` (default). Other values (`APP`, `MESSENGER`/`WHATSAPP`/
  `INSTAGRAM_DIRECT` — need `promoted_object.page_id`, `ON_POST`, …) per live schema.
- **placement**: omit → Advantage+ placements. For manual, pass a full Meta placement object, e.g.
  `{"publisher_platforms":["facebook","instagram"],"facebook_positions":["feed"]}` — do not partially
  specify.
- **schedule**: `start_time` / `end_time` (ISO 8601).
- **promoted_object**: required for conversion/value/lead/app goals, e.g. `{"pixel_id":"…"}`.
- **EU/DSA**: targeting EU countries requires `dsa_beneficiary` + `dsa_payor` (auto-filled from business
  name if omitted).

## Ad / creative (`ads_create_creative` → `ads_create_ad`)
- **page_id** (required), **instagram_user_id** (for IG delivery), **link_url** (destination).
- **call_to_action_type**: `LEARN_MORE` (default); other values (`SHOP_NOW`, `SIGN_UP`, `GET_OFFER`,
  `BOOK_NOW`, `DOWNLOAD`, `CONTACT_US`, …) — verify exact enum via `ads_get_field_context` before use.
- **copy** (from the ad-copy skill): `message`, `headline`, `description`.
- **asset**: image → `image_url`; video → `video_id` (+ thumbnail `image_url`).

## Recommended defaults for an autonomous launch
CBO + `LOWEST_COST_WITHOUT_CAP`; objective from the business goal; Advantage+ placements & audience;
geo-only broad targeting unless research specifies; daily budget derived from remaining channel credits;
staged paused unless launch intent is explicitly live.
