# What Went Wrong In V2

## Main Failure

The generated-app builder was too freeform and too forgiving.

It tried Claude Agent SDK, Claude CLI, Vercel Sandbox, Open Lovable, repair shells, and degraded paths. Some failures became visible as "done enough" outputs, which made the product look live when it was not robust.

## Specific Problems

- Vercel Sandbox did not work reliably for Claude SDK style building.
- Open Lovable required its own server and sandbox path.
- The initial autonomous pipeline could fail even when the fallback expectation was "ship homepage first".
- Fallbacks and `catch {}` paths hid real failures.
- `degraded` and repair-shell output could be persisted as success.
- Internal words like draft/degraded/policy leaked into product surfaces.
- Approval logic was hardcoded in code, not configurable by company/action.
- Cron jobs were partly configurable but still too tied to hardcoded handlers.
- Hermes, direct LLM, deterministic workflows, and builder logic were tangled.
- V2 copied useful generated-app economics but did not produce consistently good generated products.

## Rebuild Rule

Do not port the v2 product-builder strategy as-is. Port the control-plane concepts and generated-app economics, then rebuild the template/builder around deterministic modules and strict gates.

