# Supabase Auth cutover for product sub-users

Status: design spike for Sprint 3. Do not delete the legacy magic-link front door until the
operator approves this cutover and a fresh-business browser proof is green.

## Goal

Move product sub-user login to Supabase Auth as the only public login front door while keeping
Takyon's existing app identity/session store as the backend authority for product runtime rails.

Operators stay on Auth0. This document only covers product sub-users on `*.coscale.app`.

## What gets removed at cutover

Remove these public front-door paths and tool registrations only after the live Supabase path is
verified:

- `POST /api/takyon/apps/{business}/auth/request`
- `GET /api/takyon/apps/{business}/auth/verify`
- `business_request_app_magic_link`
- `business_verify_app_magic_link`
- `TakyonStore.handle_business_request_app_magic_link`
- `TakyonStore.handle_business_verify_app_magic_link`
- `app_identity.create_magic_link`
- `app_identity.verify_magic_link`
- writes to `app_magic_links`
- Postmark magic-link send logic for product sub-user login

After removal, drop the `app_magic_links` table in a follow-up migration once any remaining
unexpired links are impossible to redeem.

## What stays

Keep these surfaces. They are the canonical app identity/session store and are not magic-link
specific:

- `app_users`
- `app_sessions`
- `app_identity.validate_session`
- `app_identity.start_session`
- `app_identity.revoke_session`
- `app_identity.get_app_user`
- `app_identity.upsert_app_user_by_supabase_id`
- `business_supabase_login`
- `POST /api/takyon/apps/{business}/auth/session`
- account, entitlement, usage, records, media, directory, actions, generate, and search rails that
  resolve the app user from `app_sessions`

The runtime continues to present a Takyon app session token/cookie to backend rails. Supabase is the
login credential source; `app_sessions` remains the local runtime session authority.

## Existing user migration

1. Keep legacy magic-link sessions valid until their normal expiry during the migration window.
2. On Supabase login, call `upsert_app_user_by_supabase_id`.
3. If an `app_users` row already has the same `supabase_user_id`, reuse it.
4. Otherwise adopt exactly one legacy row for `(business_slug, email)` where `supabase_user_id is null`.
5. Preserve the existing `app_users.id`, tier, entitlements, usage history, profile, records, media, and
   connections for that adopted user.
6. If there are duplicate legacy rows for one business/email, block adoption and report the blocker
   instead of guessing.
7. After a measured migration window, disable the magic-link request/verify routes, then later drop
   `app_magic_links`.

## Verification requirements

The Supabase verifier must fail closed on:

- missing access token
- bad signature
- expired token
- missing `exp`
- missing `sub`
- wrong audience
- missing email
- unverified email

Current pre-cutover code enforces the standard Supabase audience `authenticated` and now requires a
verified email. When the JWT does not carry an explicit verified-email claim, the verifier confirms
the token with Supabase Auth and requires `email_confirmed_at`, `confirmed_at`, or an equivalent
positive verification claim from the user payload.

## Session lifetime and refresh

Current Takyon app sessions are 30-day bearer sessions. The cutover should keep this unchanged for the
first Supabase-only release, then tighten in a separate deploy once login reliability is proven:

- access session TTL target: 7 days
- idle timeout target: 24 hours
- refresh route: authenticated by current app session plus a still-valid Supabase session where the
  browser can provide it
- optional device binding: store a hashed device id in `app_sessions.metadata`, never a raw fingerprint

Do not shorten sessions in the same deploy that removes magic links. That would mix auth migration risk
with session-expiry risk.

## GET-token leak closure

The current legacy magic-link path redeems a raw token through a `GET` query string. The web server
already strips live-mode tokens from the unauthenticated request response, but the emailed redemption
URL still contains `?token=...`.

When Supabase becomes the only login front door, this disappears because redemption happens inside
Supabase Auth. Takyon receives only the Supabase access token via `POST /auth/session` and then mints
the app session server-side.

## Cutover checklist

1. Verify Supabase redirect URLs include `https://*.coscale.app/**`.
2. Create a fresh business.
3. Sign in via Supabase on the fresh product host.
4. Confirm one `app_users` row with `supabase_user_id`.
5. Confirm one active `app_sessions` row.
6. Confirm account, records, generate, search, checkout, entitlement, and sign-out still work.
7. Disable magic-link request/verify routes behind one tracked code change.
8. Re-run the fresh-business browser gate.
9. After legacy links age out, drop `app_magic_links`.
