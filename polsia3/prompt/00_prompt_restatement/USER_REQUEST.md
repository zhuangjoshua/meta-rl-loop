# User Request Restatement

The rebuild must not be a single large implementation prompt. It must start with a readable `run/` directory that captures the operating plan, feature ledger, v2 audit, architecture, backend decision, local runners, cron, generated-app template strategy, add-ons, secrets, testing, and implementation order.

The rebuild must preserve the useful v2 and Polsia v1 features while cleaning up the failed v2 architecture.

No product implementation code should be started until the run directory and implementation plan are explicit.

## Current Confirmed Goals

- Build from scratch in `/Users/Zygote/polsia3`.
- Use a private GitHub repo: `tejdiv/polsia3`.
- Deploy the platform through Vercel, intended project path: existing `argon-site`.
- Generated customer app URLs should live on `*.fourmanifold`.
- Use Vercel for the platform and app hosting, but do not use Vercel Sandbox as the builder.
- Use a local Mac worker for v0 long-running jobs and generated-app builds.
- Keep Hermes where v2 used it, but scoped and optional.
- Preserve prompt editability.
- Preserve generated-app subuser auth, payments, and AI limits.
- Replace v2's weak generated sites/products with a strong modular template system.
- Make cron jobs database-configurable and visible.
- Make action approvals configurable by policy.
- X posts should be automatic when configured and allowed.
- Meta Ads v0 should generate and display Sora creative only, with no Meta spend/post/upload.
- Community/Reddit surfaces should show real discovered targets and generated launch copy, but must not post.
- All future implementation changes must update this `run/` directory.
- The operator can edit `prompt/`, the sync folder, and ask the agent to implement the corresponding code. `prompt/` is rough input; `run/` is updated only after implementation/verification, then `prompt/` is synced from `run/`.
