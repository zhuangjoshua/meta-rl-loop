# Takyon Subuser App Kit

This directory is the shared support kit for customer-facing Takyon product apps.

Use it as a starting point, not a visual cage:

- Keep runtime semantics truthful.
- Redesign pages, flows, copy, and layout freely.
- Put business-specific UI in the main app source, not in this managed kit.

Files:

- `surface-context.js` — generated per business; exports the current surface truth.
- `runtime-client.js` — same-origin product-host client with prefixed fallback.
- `packs.js` — mode, subscription, and API pack hints for faster composition.
- `ui-primitives.js` — small blocked/pricing/usage/API helper renderers.
- `tokens.css` — neutral shared tokens and state styles.
- Seeded starter source under `src/` — a canonical landing page at `/`, a gated product shell at `/app`, and an account/subscription page at `/app/profile`.

Normal pattern:

1. Import `surface-context.js`.
2. Create one runtime client with `createSubuserRuntimeClient(...)`.
3. Treat only `rail_state=live` as callable; `unknown`, `blocked`, and `broken` stay visibly non-live.
4. Use `packs.js` to choose a shell direction.
5. If the surface is app-like, keep a real `/app` route. Landing-heavy app surfaces still default to `/, /app`; only drop `/app` when the owning surface is intentionally `landing_page_only`.
6. Build the actual product UI around that truth.
7. Redesign the seeded starter shells freely, but keep their auth/paywall/account boundaries unless you are intentionally changing the rail logic.

The kit is intentionally framework-agnostic so static HTML, vanilla JS, React, or Next source can all use the same runtime boundary.
