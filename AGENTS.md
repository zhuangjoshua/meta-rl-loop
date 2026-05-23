# Takyon Workspace Instructions

## Source Of Truth

This workspace has one runnable Takyon trunk:

```text
/Users/Zygote/Downloads/takyon/takyon
  -> /Users/Zygote/Downloads/takyon/hermes-agent-main
```

`./takyon` at the workspace root is the canonical operator entrypoint. It launches the Hermes/Takyon runtime with `TAKYON_HOME` rooted at `/Users/Zygote/Downloads/takyon/.takyon`.

`hermes-agent-main` is the active runtime, CEO, skill, cron, and plugin trunk.

The old `polsia3` app tree was removed. Archived source material salvaged from it lives under `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.

Do not recreate `polsia3`, do not restore `polsia3/takyon`, and do not describe `polsia3` as "the active Next/Takyon app", "the main trunk", or "one of two trunks".

## User Terms

Do not conflate Takyon users with product subusers.

- A Takyon user is the top-level operator/account that owns a Takyon agent and may create businesses.
- A product subuser/app customer is a customer of a business/product created by that Takyon user.
- Top-level user-scoped APIs, credentials, budgets, and identity are different from business/product customer auth, entitlements, usage, billing, and scoped API keys.

## Product App Surfaces

The shared Hermes app runtime owns backend rails only: auth/session protocol, payment/webhook reconciliation, entitlement policy, app usage budget accounting, state mirrors, and safety gates.

Do not hardcode the final product's look, layout, copy, theme, or information architecture in the runtime. Store that per business with the `business_upsert_app_surface_contract` tool, mirrored at `app/surface.md`, and point it at the business design brief/source path that the CEO and skills should inspect.

## Operating Model

Takyon should be a skill-based Hermes CEO system, not a fixed workflow cockpit.

Practice parsimony and no slop. Parsimony means the system works through the smallest Hermes-native surface that genuinely handles the job for every business and every relevant mode. Do not create a second path for test mode, a one-off shell workaround, a special-case business bootstrap, or a fake/demo product behavior when the same skill, business-scoped tool, receipt, or runtime rail can express the work with the right gates. Mode differences should be explicit guardrails on the same path: in test mode suppress or stub external side effects with receipts; in live mode require the real provider, budget, permission, and receipt. Prefer one clear operator affordance with flags over multiple overlapping commands, aliases, or workflow shortcuts. If a shell action is part of creating a business, put it on `/create` as an explicit flag such as `--test`, `--schedule`, or `--no-auto`; do not add separate slash commands that duplicate the creation path.

Use canonical sources of truth wherever they exist. Runtime config belongs in `$TAKYON_HOME/config.yaml`; tool and skill metadata belong in `plugins/takyon/registry.py`; shell/harness command metadata belongs in `plugins/takyon/harness/settings.json` and `plugins/takyon/harness/commands/*.md`; business facts belong in `state.sqlite3` and the per-business filesystem. Do not duplicate those facts in prompts or UI code when they can be read from the canonical source.

Before adding a new store, command, prompt rule, metric file, or workflow, check whether the active Takyon/Hermes trunk already has a canonical source that does the job. If it does, tell the operator and use or point to that source instead of creating a duplicate.

Do not answer diagnosis with artifact edits. If the operator asks why something happened, whether behavior is correct, or how to improve it, inspect the relevant source of truth and answer. Do not patch a generated business website, outreach file, app copy, or local artifact unless the operator explicitly asks to change that artifact. If the fix is systemic, make the smallest Hermes-native change in the relevant skill, tool, registry, harness metadata, or AGENTS instruction.

`AGENTS.md` is not a CEO runtime prompt. Do not put business strategy, wake-loop policy, product judgment, outreach policy, or per-business operating instructions here. Runtime behavior belongs in Takyon skills, guarded business tools, harness command metadata, cron prompts, and per-business brain/workspace state.

Hardcode only durable rails and safety primitives: business isolation, path containment, idempotency, credential gates, budget caps, audit events, pause/resume/kill controls, conservative cleanup, and the shared Hermes app runtime APIs. Strategy, prioritization, product direction, outreach motion, design, and learning belong in per-business brain/workspace state plus active Takyon skills.

When changing CEO/runtime behavior, edit the active skills under `hermes-agent-main/plugins/takyon/skills/` and the relevant tool/cron/harness source. In particular:

- `takyon:ceo` is the state-aware router.
- `takyon:claude-agent-sdk` / `business_claude_agent_task` is the bounded Claude Agent SDK path for business-scoped workspace edits.
- `takyon:app-runtime` and the canonical app tools own product customer auth, magic links, sessions, app customers/subusers, entitlements, plan policy, checkout, Stripe webhook reconciliation, revenue, and usage budgets.
- `takyon:business-pulse`, `takyon:outreach`, `takyon:conversation-response`, `takyon:distribution-campaign`, `takyon:ad-creative`, `takyon:market-research`, `takyon:pricing-strategy`, `takyon:conversion-review`, `takyon:business-learning`, and `takyon:failure-recovery` own their respective business methods.

External side-effect semantics belong in guarded business tools and the relevant skills. Do not duplicate them in `AGENTS.md`, shell prose, or one-off prompts.

Archived `polsia3` reference files may mention fixed workflow IDs or old workflow catalogs. Treat those as historical source material only. Do not copy that architecture back into the active Takyon/Hermes system.

## Test Mode

Test-mode semantics belong in the Takyon tools, skills, and harness metadata, not in `AGENTS.md`. The canonical state is `businesses.mode` in `.takyon/state.sqlite3`, changed through `business_set_mode` or the registry-driven shell command `/test on|off|status`. Do not add parallel per-channel test flags or duplicate test-mode behavior in prompts.

## Hermes-Style Takyon Work

Implement Takyon behavior the Hermes way: skills describe operating modes, tools provide guarded state changes/side effects, and the business filesystem records durable context. Do not add local deterministic business flows, one-off worker stages, hardcoded startup sequences, or UI-only command lists when a skill, registry entry, harness command file, or business-scoped tool can express the behavior.

When modifying artifact/reporting surfaces, keep business deliverables visible through tool results, shell progress, or concise reports. Paths should come from canonical tool results and filesystem roots, not invented shell text.

Default rule: everything business-facing should be a Takyon skill or a business-scoped tool visible in `business_registry`. The clear exceptions are shared safety/control rails: path containment, idempotency, credential checks, budget gates, pause/kill controls, cron wake scheduling, auth/session/payment/webhook protocol, and UI rendering mechanics. Even those exceptions should publish their user-facing command metadata through a single source of truth rather than separate UI hardcoding.

Slash-command UI must be registry-driven. Shell palettes/help should derive from `hermes-agent-main/plugins/takyon/harness/settings.json` for controls, `hermes-agent-main/plugins/takyon/harness/commands/*.md` for harness skill commands, and `hermes-agent-main/plugins/takyon/registry.py` for Takyon skills/tools. If a future command or skill is added, updating that canonical source must be enough for shell discovery; do not duplicate slash command names/descriptions in the UI.

## Takyon Shell Model

The Takyon shell is always in a scope. `global` is the top-level account/root scope; it is not the CEO. A business scope is `business:<slug>`.

The CEO is the scoped operator agent role. Plain text in the shell should route to the CEO for the current scope, except obvious shell self-help questions such as "how do I create a business", which should answer locally and tersely. The shell must preserve recent in-session turns so follow-ups can resolve against recent context; tune that through `harness/settings.json`, not hardcoded prompts. `/ceo` is only a focus/status affordance because the CEO is already the default interface.

Slash commands are narrow shell controls only: scope navigation, local status/inspection, setup/config, debug/doctor, server start/stop, and exit. Product-building behavior belongs in skills, tools, registry metadata, and per-business state.

Business creation belongs to one shell command: `/create`. Creation-time choices such as test mode, immediate CEO start, and wake cadence should be flags on `/create`, not separate slash commands.

When the CEO is working, the shell must leave a visible operator lane. Do not use spinners, carriage-return redraws, explanatory status text, or progress output that occupies or clears the input row. The active-run indicator should be minimal and visual: use a blinking `*` only when it can be cleared on completion, and let streaming tool progress stand alone when progress lines are visible. If true mid-turn injection is not available, document that in help/status surfaces rather than cluttering the active input area.

## E2E Testing

For operator experience, test through the real shell path. Direct commands such as `./takyon create ...` are useful unit/smoke checks, but they do not exercise the interactive shell parser, slash command handling, scoped CEO routing, shell history, visible progress, input-lane behavior, or operator follow-up flow.

For isolated E2E tests, use a temporary workspace-local `TAKYON_HOME`, copy only the config needed to run the model, and launch `./takyon shell`. Run `/create` and follow-up inspection commands from inside the shell. Do not test by writing directly into `.takyon/state.sqlite3` or by bypassing the shell when the bug is about shell UX, progress, slash commands, scope, or operator conversation.

Use `/status`, `/pulse`, `/files`, `/read`, `/cron list`, and `/cron tick` inside the shell to verify state, receipts, product surface, pulse, filesystem visibility, and scheduled wake behavior. Keep test businesses in test mode unless the operator explicitly wants live side effects.

When the operator wants live monitoring, run the shell in a terminal visible to Codex or ask them to paste output. Codex can inspect the shared terminal output, but cannot see an unrelated external Terminal window unless its output is provided.

## Agent Behavior

Do not stop at analysis because the workspace is dirty. There may be existing user or previous-agent edits. Read the relevant files, preserve unrelated changes, and make the requested change in the canonical location.

Do not begin with a verbose prompt restatement. If the request is actionable, inspect and act. If context is ambiguous, ask one concise question or state the assumption you are using.

When in doubt:

1. Use the root `./takyon`.
2. Treat Hermes as CEO/runtime/cron owner.
3. Use archived reference material only from `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.
4. Keep business state in the canonical Takyon/Hermes store and per-business filesystem.
