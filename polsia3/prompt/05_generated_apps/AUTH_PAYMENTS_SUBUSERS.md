# Generated App Auth, Payments, And Subusers

Port v2 semantics closely.

## Subuser Model

Generated app visitors become `generated_app_users`.

They authenticate with magic links and receive generated app sessions.

## Entitlements

Entitlements come from:
- free/default plan
- Stripe checkout/webhook
- admin/operator grant if added later

Browser state cannot decide paid/free state.

## Checkout

Generated apps link to platform checkout:

```text
/api/generated-apps/[slug]/checkout?plan=starter
```

Stripe webhook updates generated-app entitlements.

## Important Keying Detail

Generated apps do not get provider API keys per subuser.

They use:
- project-scoped AI proxy key for the generated app
- app user id/key/tier for per-subuser metering

Generated apps must never receive raw OpenAI, Anthropic, Stripe, X, Meta, or other provider keys.

## TODO - Not Complete Yet

Do not describe these as complete until implemented and verified:

- User-supplied API keys as a selectable funding source for generated-app AI.
- Cross-app allocation of an owner/operator API key or wallet budget across all generated apps created by that user.
- Full paid-user reserve and free-user leftover budget enforcement from the v2 generated-app economics model.
- Correct generated-app browser auth/session wiring from generated app domains, including same-origin or CORS-safe magic-link request, verify, session read, cookie scope, and credentials behavior.
- Stripe webhook entitlement E2E proving a real checkout/session event updates generated-app paid entitlements.
