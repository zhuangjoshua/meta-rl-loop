# Takyon Subuser App Kit

This directory is the shared support kit for customer-facing Takyon product apps.

Use it as a starting point, not a visual cage:

- Keep runtime semantics truthful.
- Redesign pages, flows, copy, and layout freely.
- Put business-specific UI in the main app source, not in this managed kit.

Files:

- `surface-context.js` — generated per business; exports the current surface truth.
- `surface-context.js` also carries `auth` when the app shell has the auth rail, including the
  public Supabase URL + publishable key the browser needs to start Google OAuth.
- `runtime-client.js` — canonical client for `/api/takyon/apps/<slug>/...`, including the server-authoritative immediate `cancelSubscription()` account helper.
- `packs.js` — mode, subscription, and API pack hints for faster composition.
- `ui-primitives.js` — small blocked/pricing/usage/API helper renderers.
- `tokens.css` — neutral shared tokens and state styles.
- Seeded starter source under `src/` — a landing page at `/`, public support pages at `/pricing`, `/privacy`, and `/terms`, a gated product shell at `/app`, an account/subscription page at `/app/profile`, and supporting metadata routes for Open Graph, Twitter, `robots`, and `sitemap`.
- `src/components/subscription-cancellation.tsx` — force-refreshed self-service cancellation rendered on `/app/profile` for active Stripe subscriptions; it ends access immediately and cannot be replaced with contact-support-only copy.

Normal pattern:

1. Import `surface-context.js`.
2. Create one runtime client with `createSubuserRuntimeClient(...)`.
3. Treat only `rail_state=live` as callable; `unknown`, `blocked`, and `broken` stay visibly non-live.
4. Use `packs.js` to choose a shell direction.
5. If the surface is app-like, keep a real `/app` route. Landing-heavy app surfaces still default to `/, /app`; only drop `/app` when the owning surface is intentionally `landing_page_only`.
6. Build the actual product UI around that truth.
7. Replace the seeded page presentation freely, but keep the auth/paywall/account boundaries unless you are intentionally changing the rail logic.

The kit targets the pinned Vite app scaffold only.
