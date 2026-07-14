# Reddit Ads Framework

This skill mirrors the Takyon Meta ads shape, but Reddit’s live creative path differs in one important way:

- Meta can upload local media bytes directly on launch.
- Reddit’s live launch path in this skill uses either:
  - an existing `post_id`, or
  - public creative URLs when creating a promoted post.

Takyon now bridges that gap by staging local business files under `product/` onto the business
publish target first, then using the resulting public URL in the Reddit promoted-post payload.
That still depends on the publish target being truly reachable in live mode.

## Access Model

Reddit's current Ads API docs say the Ads API is **open to all developers** and does **not**
require allowlisting or approval from Reddit for access. The practical gates are:

- OAuth2 developer application + tokens
- permission on the target business / ad account
- a valid profile, funding instrument, and pixel
- a unique User-Agent string to avoid generic-client throttling

For Takyon, that means "verification" should not be treated as a separate API approval queue unless
Reddit changes the official docs. Debug auth, permissions, and account setup first.

## Object Model

- `Campaign`
  - bounded by the reserved spend policy
  - carries the high-level objective
  - must point at a funding instrument in live mode
- `Ad Group`
  - bounded by the reserved spend policy
  - carries targeting + daily spend (`goal_type: DAILY_SPEND`, `goal_value` in microcurrency)
  - carries the conversion pixel id for the staged launch path
- `Post`
  - optional if you already have `post_id`
  - otherwise created from public image/video/carousel URLs
- `Ad`
  - binds the ad group to the post

## Launch Shape

Takyon’s first Reddit launch rail is intentionally small:

- one campaign
- one ad group
- one post (optional if reusing `post_id`)
- one ad
- everything created under one reserved total spend cap

This keeps the control surface parallel to Meta:

- `launch` = reserve credits, create provider objects, and activate unless explicitly paused
- `control activate|pause|set_budget` = mutate explicitly inside the same reservation
- `insights_sync` = record platform metrics only

## Best Programmatic Path

The normal Reddit API launch shape is:

1. create developer application
2. complete OAuth2 and store a refresh token
3. preflight business, ad account, profile, funding instrument, and pixel
4. reuse an existing `post_id`, or create a new promoted post from public media URLs
5. create one bounded campaign, ad group, and ad under the reserved cap
6. pause or re-activate later only if the operator explicitly asks

Takyon already owns steps 3 through 6, and the launch tool now also stages local business media
into canonical `product/public-assets/<slug>/` receipts before step 4 when needed. In live mode,
the truthful blocker is no longer "Reddit needs verification" but "the publish target was not
publicly reachable yet."

## Live Requirements

- Reddit Ads OAuth client id + secret
- refresh token or valid access token
- ad account id
- funding instrument id
- profile id for new promoted-post creation
- conversion pixel id

Preflight is the safe way to discover these. The guarded tool returns:

- identity
- businesses
- ad accounts
- profiles
- funding instruments
- pixels
- the single discovered defaults when there is only one choice

## Budget Semantics

- The staged launch uses **daily spend** on the ad group.
- Values sent to Reddit are in **microcurrency**.
- Takyon accepts USD in the tool surface and converts internally.
- The same safety cap pattern as Meta applies through `TAKYON_REDDIT_MAX_DAILY_BUDGET_USD`.
- Live launch also fails closed when the remaining reserved Reddit credits are below the live minimum
  (`TAKYON_REDDIT_MIN_LIVE_BUDGET_USD`, default 5 USD).

## Current Takyon Rails

- `business_reddit_ad_launch`
  - `mode: preflight` is read-only
- `mode: launch` reserves credits, creates provider objects, and activates unless explicitly paused
- `business_reddit_ad_control`
  - `activate`
  - `pause`
  - `set_budget`
- `business_reddit_ad_insights_sync`
  - queries report metrics and stores local receipts / JSONL snapshots

## Metrics Truth

This skill records only Reddit ad-platform delivery metrics such as:

- spend
- impressions
- clicks
- CTR
- CPC
- CPM

It does not invent:

- CAC
- ROAS
- pipeline
- purchases
- attributed revenue

Those require separate truthful joins.

## Rate Limits

The official Reddit Ads API docs currently group limits roughly as:

- business manager: `100 / 60s`
- campaign management read: `400 / 60s`
- campaign management write: `200 / 60s`
- creative management: `200 / 60s`
- reporting: `60 / 60s`

The gateway uses one request flow per staged step, so normal Takyon launches stay comfortably below these ceilings.
