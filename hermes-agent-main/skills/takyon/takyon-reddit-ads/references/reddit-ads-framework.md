# Reddit Ads Framework

## Access Model

The practical launch gates are an authorized developer application and session, access to the target
business and ad account, a usable profile and funding instrument, and any pixel required by the
objective. Run read-only preflight before choosing defaults; never guess account identifiers.

## Object Model

- Campaign: high-level objective and total bounded intent.
- Ad group: targeting, schedule, optimization, daily pace, and conversion pixel when applicable.
- Post: an existing promoted post or a new post backed by publicly reachable media.
- Ad: the binding between ad group and post.

Keep the first launch deliberately small: one campaign, one ad group, one post, and one ad under one
authorized budget. This makes retries, control, and measurement legible.

## Launch Sequence

1. Complete read-only preflight and resolve real account objects.
2. Reuse a promoted post or stage one real creative asset onto an authorized public target.
3. Verify media reachability from outside the private workspace.
4. Define objective, audience, destination, schedule, daily pace, post, and copy.
5. Launch or stage paused with a stable idempotency key.
6. Read authoritative completion state.
7. Activate, pause, or change budget only through the bound control capability.
8. Synchronize delivery metrics with an explicit date range and object level.

## Budget Semantics

Reddit expresses ad-group daily spend in microcurrency. The binding layer owns currency conversion,
provider minimums, maximum daily pace, total authorization, and reservation. A present credential is
not spend authority.

## Metrics Truth

Platform delivery can establish spend, impressions, clicks, CTR, CPC, and CPM. It cannot by itself
establish CAC, ROAS, pipeline, purchases, or attributed revenue; those require a separate truthful
join.

Rate limits vary by operation group. Keep launches sequential and bounded, respect retry guidance,
and do not turn an ambiguous provider response into a duplicate write.
