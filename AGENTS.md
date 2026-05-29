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

## Deployment Notes

The current Takyon VPS target is `137.184.75.57` (`argon-alpha-14`). Use the local Codex deploy key at `~/.ssh/takyon_argon_alpha14` for root SSH when deployment work needs direct VPS access.

The public dashboard hostname is `app.fourmanifold.com`. DNS is managed outside this repo and should currently resolve to `137.184.75.57`; if it does not, treat that as DNS drift and fix the record outside this repo rather than papering over it in code.

When the operator asks to push or deploy Takyon, keep the three rails distinct:

1. Git push uses the outer workspace repo at `/Users/Zygote/Downloads/takyon`, not the nested `hermes-agent-main` git metadata. Stage only the intended hunks, commit in the outer repo, and push `origin main` unless the operator asked for a branch.
2. VPS deploy updates the active runtime. The VPS runtime is `/opt/takyon/hermes-agent-main` and may not be a git checkout, so deploy changed runtime files with `rsync`/`ssh` using `~/.ssh/takyon_argon_alpha14`, then compile touched Python files and restart `takyon-dashboard.service`. Verify with `systemctl is-active takyon-dashboard.service` and source checks on the VPS.
3. VPS Caddy config is tracked at `deploy/argon-alpha-14/Caddyfile`; apply it with `deploy/argon-alpha-14/apply-caddyfile.sh`. This is the repeatable source for `app.fourmanifold.com` and shared `slug.fourmanifold.com` product routing. Do not hand-add new per-business Caddy blocks for normal businesses.
4. Vercel deploy is the `app` project frontdoor only. It is not the canonical Takyon runtime and successful Vercel deploys do not prove prompt, skill, registry, or backend changes reached the VPS. Do not run `vercel deploy` from the workspace root; that uploads the wrong artifact. Use `vercel redeploy` against the current known-good `app` frontdoor production deployment, or deploy an equivalent tiny frontdoor artifact, then verify `vercel inspect app.fourmanifold.com` is Ready and aliased. Treat Vercel alias state separately from DNS: `app.fourmanifold.com` may still resolve to the VPS and return Caddy/uvicorn headers even when Vercel has a Ready alias.

The deployment workflow is tracked at `.github/workflows/deploy.yml`: pushing `main` should run the dashboard web build, compile Python, redeploy the current Vercel `app.fourmanifold.com` frontdoor when `VERCEL_TOKEN` is configured, rsync the active runtime to the VPS, restart `takyon-dashboard.service`, apply tracked Caddy only when `deploy/argon-alpha-14/Caddyfile` changed, and verify the public dashboard host. Required GitHub secrets are `TAKYON_VPS_HOST`, `TAKYON_VPS_USER`, `TAKYON_VPS_SSH_KEY`, and optional Vercel metadata secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`. After pushing, verify the workflow with `gh run list` / `gh run watch`; if it fails or has not run, do not assume production updated.

Fast path for ordinary code/docs/UI changes:

1. Run only the focused local checks needed for the touched surface, plus `git diff --check`.
2. Stage only intended files, commit in the outer repo, and `git push origin main`.
3. Immediately run `gh run list --repo tejdiv/takyon-workspace --branch main --limit 5`, then `gh run watch <run-id> --repo tejdiv/takyon-workspace --exit-status`.
4. If the workflow passes, report the run id and any direct smoke checks. Do not also do manual VPS/Vercel deploys.
5. If the workflow fails, inspect `gh run view <run-id> --log-failed`, patch the failing tracked rail, push again, and watch the new run. Use manual SSH/rsync only for emergency rollback or when the operator explicitly asks.

## User Terms

Do not conflate Takyon users with product subusers.

- A Takyon user is the top-level operator/account that owns a Takyon agent and may create businesses.
- A product subuser/app customer is a customer of a business/product created by that Takyon user.
- Top-level user-scoped APIs, credentials, budgets, and identity are different from business/product customer auth, entitlements, usage, billing, and scoped API keys.

## Product App Surfaces

The shared Hermes app runtime owns backend rails only: auth/session protocol, payment/webhook reconciliation, entitlement policy, app usage budget accounting, state mirrors, and safety gates.

Do not hardcode the final product's look, layout, copy, theme, or information architecture in the runtime. Store that per business with the `business_upsert_app_surface_contract` tool, mirrored at `product/surface.md`, and point it at the business design brief/source path that the CEO and skills should inspect.

When implementing custom backend or own-runtime product capability later, update the authoritative Takyon discovery surfaces the CEO can actually see: the stable CEO prompt at `hermes-agent-main/plugins/takyon/prompts/ceo.md`, the owning Hermes skill under `hermes-agent-main/skills/takyon/`, and the guarded product verification/deploy tool behavior. Do not add a hidden backend path that the CEO cannot see through skill frontmatter/body, tool schemas/results, or business receipts.

## Operating Model

Takyon should be a skill-based Hermes CEO system, not a fixed workflow cockpit.

Practice parsimony and no slop. Parsimony means the system works through the smallest Hermes-native surface that genuinely handles the job for every business and every relevant mode. Do not create a second path for test mode, a one-off shell workaround, a special-case business bootstrap, or a fake/demo product behavior when the same skill, business-scoped tool, receipt, or runtime rail can express the work with the right gates. Mode differences should be explicit guardrails on the same path: in test mode suppress or stub external side effects with receipts; in live mode require the real provider, budget, permission, and receipt. Prefer one clear operator affordance with flags over multiple overlapping commands, aliases, or workflow shortcuts. If a shell action is part of creating a business, put it on `/create` as an explicit flag such as `--test`, `--schedule`, or `--no-auto`; do not add separate slash commands that duplicate the creation path.

Skill-based does not mean every fix belongs in skill prose. If the real missing piece is tool registration, provider config, harness metadata, a guarded runtime rail, or a UI renderer, fix that canonical surface and let the existing skills use it; edit or add a skill only when the business-facing method, judgment, or routing behavior genuinely changes.

When behavior is wrong, find the upstream cause before adding a downstream rule, label, report field, UI shim, or prompt reminder. A fix that merely records, hides, or explains a bad decision is a band-aid unless the canonical surface that enabled the bad decision has been corrected. First identify what source of truth, tool schema, receipt, capability read, guardrail, or runtime rail was missing, misleading, or unenforced; then make the smallest Hermes-native change at that surface. Add downstream visibility only after the upstream behavior is truthful.

Do not add deterministic business-action routers, fixed if/then workflow funnels, or forced artifact shortcuts to make the CEO pick a business move. Encourage the CEO through the Hermes skills index, skill guidance, tool schemas, capability/read tools, exact gate errors, and regression tests that catch bad substitutions. Deterministic code is appropriate only for durable safety and integrity rails such as scope isolation, path containment, idempotency, credential checks, budget caps, audit receipts, kill/pause controls, and UI rendering mechanics.

Use canonical sources of truth wherever they exist. Runtime config belongs in `$TAKYON_HOME/config.yaml`; the CEO runtime prompt belongs in `plugins/takyon/prompts/ceo.md`; Takyon skill metadata belongs in Hermes `SKILL.md` frontmatter under `skills/takyon/`; shell/harness command metadata belongs in `plugins/takyon/harness/settings.json` and `plugins/takyon/harness/commands/*.md`; business facts belong in `state.sqlite3` and the per-business filesystem. Do not duplicate those facts in prompts or UI code when they can be read from the canonical source.

Before adding a new store, command, prompt rule, metric file, or workflow, check whether the active Takyon/Hermes trunk already has a canonical source that does the job. If it does, tell the operator and use or point to that source instead of creating a duplicate.

Do not answer diagnosis with artifact edits. If the operator asks why something happened, whether behavior is correct, or how to improve it, inspect the relevant source of truth and answer. Do not patch a generated business website, outreach file, app copy, or local artifact unless the operator explicitly asks to change that artifact. If the fix is systemic, make the smallest Hermes-native change in the relevant skill, tool, harness metadata, or AGENTS instruction.

`AGENTS.md` is not a CEO runtime prompt. Do not put business strategy, wake-loop policy, product judgment, outreach policy, or per-business operating instructions here. Runtime behavior belongs in the CEO prompt, Takyon skills, guarded business tools, harness command metadata, cron prompts, and per-business `research/`, `product/`, `distribution/`, and `metrics/` state.

Hardcode only durable rails and safety primitives: business isolation, path containment, idempotency, credential gates, budget caps, audit events, pause/resume/kill controls, conservative cleanup, and the shared Hermes app runtime APIs. Strategy, prioritization, product direction, outreach motion, design, and learning belong in per-business `research/`, `product/`, `distribution/`, and `metrics/` state plus active Takyon skills.

When changing CEO/runtime behavior, edit the stable CEO prompt at `hermes-agent-main/plugins/takyon/prompts/ceo.md`, the active Takyon skills under `hermes-agent-main/skills/takyon/`, and the relevant tool/cron/harness source. In particular:

- `plugins/takyon/prompts/ceo.md` is the stable state-aware router prompt.
- `takyon-claude-agent-sdk` / `business_claude_agent_task` is the bounded Claude Agent SDK path for business-scoped workspace edits.
- Do not reintroduce generic Hermes `delegate_task` into normal Takyon CEO runs; the coding worker lane above is the one special worker path.
- `takyon-app-runtime` and the canonical app tools own product customer auth, magic links, sessions, app customers/subusers, entitlements, plan policy, checkout, Stripe webhook reconciliation, revenue, and usage budgets.
- `takyon-business-metrics`, `takyon-market-research`, `takyon-build-product`, `takyon-distribution`, `takyon-x`, `takyon-reddit`, and `takyon-conversation-followup` own the remaining active business methods.

When the operator asks to add a normal new Takyon feature or skill, always use the parsimonious addition path:

1. Add `hermes-agent-main/skills/takyon/<new-feature>/SKILL.md`.
2. Start from `hermes-agent-main/skills/takyon/SKILL-TEMPLATE.md` and keep the canonical section order unless there is a strong reason not to.
   New skill folder shape:
   `hermes-agent-main/skills/takyon/<new-feature>/SKILL.md` plus optional `references/`, `templates/`, `scripts/`, and `assets/` inside that same skill directory.
   `hermes-agent-main/skills/takyon/SKILL-TEMPLATE.md` and `hermes-agent-main/skills/takyon/BUILDING-SKILLS-AND-TOOLS.md` are authoritative. If a future skill or tool needs a different authoring shape, update those documents in the same change before adding the divergent skill or tool.
3. Put canonical routing and readiness metadata in the skill frontmatter, including `metadata.hermes.*` plus any `metadata.hermes.requires_toolsets` / `requires_tools` gating, and `metadata.takyon.allowed_roots` / `output_root` / `publication`. Frontmatter must be valid YAML; there is no Takyon-specific fallback parser.
4. Add `references/`, `templates/`, `scripts/`, or `assets/` only when the skill truly needs them.
5. Add or modify a `business_*` tool only if the feature needs a new canonical state change, guarded side effect, provider call, receipt, budget gate, or durable runtime rail. The skill should name the tool in its body, but the tool must exist in code. If no existing tool fits, create the new tool in the same format documented in `hermes-agent-main/skills/takyon/BUILDING-SKILLS-AND-TOOLS.md`.
6. Add a harness command only if the operator needs `/new-feature` as a shell affordance; do not create slash commands for ordinary CEO-choosable business methods.
7. Start a fresh `./takyon` run or relaunch the shell so bundled Takyon skills sync automatically and Hermes rebuilds the skills index from the updated skill tree.
8. Do not edit the CEO prompt unless the CEO's general policy, routing rule, or safety contract changes.

For every new feature, skill, or tool, first understand when it should be used and verify that the existing Takyon piping will let it be used in those places without hardcoding a deterministic workflow. Check the relevant routing and discovery surfaces: the initial/bootstrap prompt, `plugins/takyon/prompts/ceo.md`, related skills' `SKILL.md` files, the runtime Hermes skills index, shell/harness metadata, and any canonical tools or runtime rails. Update only the surfaces that genuinely need to know about the feature; do not duplicate routing rules across prompts, skills, or UI code.

Takyon skills must keep rich normal-Hermes operational detail. Do not collapse `How to Run`, `Procedure`, or `Verification Checklist` into generic prompt prose. Those sections should explicitly name the exact tool names used, the files or state checked first, the expected outputs, the branch points for test/live or present/missing state, and the receipts or file paths that prove success. Converting a good Hermes skill into a Takyon skill means adding `metadata.takyon.*` and `## Publication`, not removing the concrete operational detail.

Before adding a feature, skill, tool, store, command, metric, or prompt rule, perform a redundancy and conflict check against the active Takyon/Hermes trunk. Report to the operator whether the proposal is new, partially redundant, or better implemented by extending an existing skill/tool/rail; name the overlapping surfaces and explain the chosen canonical home. Also check whether the change conflicts with initial bootstrap, scheduled wakes, manual CEO turns, work focus, test/live mode, business isolation, app-runtime rails, cron behavior, or slash-command policy. If the feature is valid, describe the non-deterministic call points where the CEO or shell can discover and use it through the Hermes skills index, tool schemas, metrics evidence, business state, or related skill guidance.

Before implementing a new feature, prove or ask whether it needs new provider access, API keys, OAuth apps, paid services, network scraping, external posting/sending, deploy credentials, model/video/image generation access, billing rails, or budget authority. Report the exact required environment variables, provider accounts, expected side effects, test-mode behavior, and live-mode gates. If the feature can work locally without new credentials, say that explicitly. If live functionality would be degraded or blocked without credentials, implement the local/guarded path only and record the missing provider gate instead of pretending it works.

External side-effect semantics belong in guarded business tools and the relevant skills. Do not duplicate them in `AGENTS.md`, shell prose, or one-off prompts.

Archived `polsia3` reference files may mention fixed workflow IDs or old workflow catalogs. Treat those as historical source material only. Do not copy that architecture back into the active Takyon/Hermes system.

## Test Mode

Test-mode semantics belong in the Takyon tools, skills, and harness metadata, not in `AGENTS.md`. The canonical state is `businesses.mode` in `.takyon/state.sqlite3`, changed through `business_set_mode` or the shell command `/test on|off|status`. Do not add parallel per-channel test flags or duplicate test-mode behavior in prompts.

## Hermes-Style Takyon Work

Implement Takyon behavior the Hermes way: skills describe operating modes, tools provide guarded state changes/side effects, and the business filesystem records durable context. Do not add local deterministic business flows, one-off worker stages, hardcoded startup sequences, or UI-only command lists when a skill, harness command file, or business-scoped tool can express the behavior.

When modifying artifact/reporting surfaces, keep business deliverables visible through tool results, shell progress, or concise reports. Paths should come from canonical tool results and filesystem roots, not invented shell text.

Default rule: everything business-facing should be a Takyon skill discoverable through the Hermes skills index or a business-scoped tool visible through tool schemas/results. The clear exceptions are shared safety/control rails: path containment, idempotency, credential checks, budget gates, pause/kill controls, cron wake scheduling, auth/session/payment/webhook protocol, and UI rendering mechanics. Even those exceptions should publish their user-facing command metadata through a single source of truth rather than separate UI hardcoding.

Slash-command UI must be source-of-truth driven. Shell palettes/help should derive from `hermes-agent-main/plugins/takyon/harness/settings.json` for controls, `hermes-agent-main/plugins/takyon/harness/commands/*.md` for harness skill commands, and Hermes skill discovery for Takyon skills. If a future command or skill is added, updating that canonical source must be enough for shell discovery; do not duplicate slash command names/descriptions in the UI.

## Takyon Shell Model

The Takyon shell is always in a scope. `global` is the top-level account/root scope; it is not the CEO. A business scope is `business:<slug>`.

The CEO is the scoped operator agent role. Plain text in the shell should route to the CEO for the current scope, except obvious shell self-help questions such as "how do I create a business", which should answer locally and tersely. The shell must preserve recent in-session turns so follow-ups can resolve against recent context; tune that through `harness/settings.json`, not hardcoded prompts. `/ceo` is only a focus/status affordance because the CEO is already the default interface.

Slash commands are narrow shell controls only: scope navigation, local status/inspection, setup/config, debug/doctor, server start/stop, and exit. Product-building behavior belongs in skills, tools, and per-business state.

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

When discussing a repo, package, website, downloaded artifact, or generated output, only describe contents you actually inspected. If local files are not present or you have not opened the relevant source, say that plainly. Do not guess what is inside, do not imply direct inspection when you only inferred from names/docs/marketing copy, and do not gaslight the operator about what you actually verified.

When in doubt:

1. Use the root `./takyon`.
2. Treat Hermes as CEO/runtime/cron owner.
3. Use archived reference material only from `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.
4. Keep business state in the canonical Takyon/Hermes store and per-business filesystem.
