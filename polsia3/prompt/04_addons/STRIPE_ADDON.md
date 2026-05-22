# Stripe Add-On

Port generated-app commerce from v2 closely.

Responsibilities:
- platform billing if needed
- generated app payment links
- checkout sessions
- checkout intents
- webhook ingestion
- subscription updates
- generated-app entitlements
- revenue events
- creator/platform fee metadata where applicable

Stripe webhooks must update entitlements only from verified webhook events.

No generated app may fake payment state from browser state.

