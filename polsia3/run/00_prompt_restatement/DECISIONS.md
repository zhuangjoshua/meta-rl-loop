# Confirmed Decisions

## Backend

V0 backend is not Vercel Sandbox and not Open Lovable.

The backend split is:
- Vercel: Takyon UI, API routes, generated app proxy/session/checkout/AI gateway
- Supabase/Postgres: mandatory source of truth
- local Mac worker: long jobs, generated-app build/deploy, local runner/Hermes bridge
- GitHub: private source/versioning

## UI And Templates

Use a platform-owned generated-app template system, not an Open Lovable dependency.

The template system should use a stable Next.js app shell, design tokens, block registry, generated-app module registry, and typed config. LLMs may fill structured slots and create bounded modules, but should not freely invent the entire app each time.

## Hermes

Hermes code existed in v2. It was used for non-deterministic agent/skill workflows, not for deterministic side effects or the generated-app builder.

V0 keeps Hermes where v2 used Hermes. Current v3 reality: the full v2 Hermes runtime code has not been copied yet. V3 currently has only a thin HTTP adapter to a local Hermes-style gateway. The v2 vendored runtime, setup/start scripts, skill sync behavior, runtime session/reconciler logic, and workflow envelopes still need to be ported and verified with a real local run receipt. This is local infrastructure, not a VPS or external SaaS API.

## Claude / Builder Runtime

Avoid Claude CLI as a core dependency. If a Claude/agent integration is needed, prefer SDK/library integration and the local worker, but verify the SDK does not secretly require a Claude Code executable. V2's `@anthropic-ai/claude-agent-sdk` call passed `pathToClaudeCodeExecutable`, so this must be checked before relying on it.

Generated-app robustness should come from modular templates, typed configs, build gates, and smoke tests.

## X

X posting should be automatic when configured, policy-allowed, and rate-limit-allowed. It must return a real X receipt.

## Meta

V0 Meta Ads must not call Meta campaign/ad upload/spend APIs.

It may generate Sora video/ad creative and display it in the UI.

Verified update: the v0 media lane now uses OpenAI Sora through `OPENAI_API_KEY`; Atlas/Seedance is not required for the current local worker path.

## Community/Reddit

Community surfaces should look product-complete. They should show real targets/leads and real generated copy. They should not expose policy language in the main UI and should not post.

## Generated App Auth/Payments/AI

Port v2's generated-app subuser logic closely:
- magic-link app users
- generated app sessions
- entitlements
- Stripe checkout
- plan policies
- project AI wallet
- project-scoped AI proxy key
- per-subuser usage events and budgets

Do not port the weak v2 product-builder output quality.
