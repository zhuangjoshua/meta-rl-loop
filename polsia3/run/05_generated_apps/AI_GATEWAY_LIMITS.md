# Generated App AI Gateway And Limits

Generated app server routes call:

```text
POST /api/ai-gateway/messages
Authorization: Bearer ARGON_PROJECT_AI_KEY
```

Request includes:
- purpose
- route
- messages
- appUserTier
- appUserKey

Gateway steps:
- authenticate project key
- resolve model policy
- estimate max cost
- check wallet
- reserve usage
- apply paid-user/free-user rules
- call provider
- record actual usage/cost
- complete or fail reservation

No generated app receives raw provider keys.

If budget/config is missing, return a real blocked/setup state and record the attempt where possible.

## Abuse And Observability Controls

Public and costly generated-app routes now write hashed request receipts to `platform_request_logs` and enforce sliding-window buckets in `platform_rate_limit_buckets`. Raw IPs and emails are not stored; buckets use the app secret to hash user/IP/email values.

Default limits:
- `TAKYON_AI_GATEWAY_PER_KEY_MINUTE`: 60
- `TAKYON_AI_GATEWAY_PER_APP_USER_HOUR`: 120
- `TAKYON_PRODUCT_RUNS_PER_EMAIL_HOUR`: 20
- `TAKYON_PRODUCT_RUNS_PER_IP_HOUR`: 60
- `TAKYON_PRODUCT_RUNS_PER_BUSINESS_HOUR`: 200
- `TAKYON_MAGIC_LINKS_PER_EMAIL_HOUR`: 5
- `TAKYON_MAGIC_LINKS_PER_IP_HOUR`: 30
- `TAKYON_MAGIC_VERIFY_PER_IP_HOUR`: 60
- `TAKYON_CHECKOUTS_PER_IP_HOUR`: 20

Set a limit to `0` to disable that specific limiter during local debugging.

## Business Memory Learning

Campaign and customer learning is stored per business in `business_memory_records`, not as a separate skill. The worker observation lane writes:
- `campaign_learning/latest-campaign-learning`
- `customer_learning/latest-customer-learning`

The receipt layer behind those memories is:
- `campaign_metric_snapshots` for channel/campaign metrics and revenue snapshots.
- `customer_response_signals` for inbound replies, lead states, purchases, product runs, and support/customer messages.

Attribution is nullable but first-class: campaign IDs can now be attached to social posts, Sora creative rows, outreach/email rows, leads, product runs, checkout intents/sessions, revenue events, and growth variants. Meta launch/spend is still disabled; learning currently uses stored Takyon receipts and any future provider metric fetcher can append snapshots into the same tables.
