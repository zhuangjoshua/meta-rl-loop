# Takyon Product Scaffold

Canonical Vite + React + TypeScript + Tailwind static SPA that generated Takyon products start from.

- **Placeholder tokens rule:** `src/tokens.css` ships deliberately ugly values (magenta/lime/Comic Sans). Every product MUST replace them from its design brief before publish; shipping them as-is is a visible refresh finding.
- **No server code rule:** static SPA only — no API routes, no express, no next.config. `npm run build` -> `vite build` -> `dist/`.
- **Kit boundary:** app code imports the runtime kit ONLY via `_takyon/runtime-client.js` and context via `_takyon/surface-context.js` (alias `@takyon/*`); the platform overwrites `_takyon/` wholesale in real products.
- **Record references:** `saveRecord(...)`, `listRecords(...)`, and `readRecord(...)` return each record with a canonical opaque `record.ref`. Pass that exact ref to `readRecord(ref)` (or `getRecord(ref)`); never derive a second ID from a title, route slug, or form value. Positional record reads are not part of the generated-app SDK.
- **Seeding:** products are seeded by copying everything EXCEPT `_takyon/`, which the platform materializes per business.
- **Health gate:** `npm ci && npm run build && npm run typecheck` must stay green at all times. The typecheck uses separate browser and action environments; actions get an explicit server-global allowlist, never DOM or WebWorker globals, and use `TakyonActionContext` instead of an untyped `ctx`.
