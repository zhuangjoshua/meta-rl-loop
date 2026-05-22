# Takyon Business Plugin

Takyon is a terminal-first CEO operator layer. It adds an isolated business store, concrete guarded business tools, business workflow skills, CEO wake/sleep cron scheduling, and one CLI command.

## Commands

```bash
takyon businesses
takyon shell
takyon create latexflow "Build a LaTeX workflow product for students"
takyon create latexflow --test "Build a LaTeX workflow product for students"
takyon create --test --schedule "every 6h" latexflow "Build this business end to end in test mode."
takyon create --no-auto latexflow "Record only; do not run the CEO bootstrap turn"
takyon files latexflow
takyon read latexflow brain/index.md
takyon campaigns latexflow
takyon show latexflow
takyon show latexflow brain/index.md
takyon commands
takyon app-server 127.0.0.1 8787
takyon registry
takyon registry tools queue p2_growth
takyon registry skills distribution p2_growth
takyon budget set latexflow 100
takyon wake latexflow "every 6h"
takyon pause business:latexflow "operator pause"
takyon resume business:latexflow "operator resume"
takyon kill business:latexflow/workspace:campaigns/finals "stop campaign"
takyon gc 90
takyon gc 90 confirm
takyon "for latexflow, improve the pricing strategy and create the next distribution campaign"
```

In an interactive Takyon session, use the slash command:

```text
/takyon businesses
/takyon registry tools queue p2_growth
/takyon kill business:latexflow/workspace:campaigns/finals "stop campaign"
/takyon market-research compare latex tools for finals week
/takyon skill python-debugpy help debug this failing test
```

If the first `/takyon` argument is an installed Takyon skill command, Takyon queues that skill with the remaining instruction. Otherwise, Takyon treats the text as a CEO operator command.

`takyon shell` opens the scoped Takyon operator shell. The shell is always in either `global` account/root scope or `business:<slug>` scope. Plain text always goes to the CEO for the current scope; `/ceo` only shows/focuses that already-active scoped CEO. Control slash commands come from `plugins/takyon/harness/settings.json`, file-backed skill commands come from `plugins/takyon/harness/commands/`, and Takyon skills/tools come from `plugins/takyon/registry.py`.

Business creation in the shell is intentionally one command:

```text
/create --test --schedule "every 6h" latexflow Build this business end to end in test mode.
```

`/create` starts one CEO bootstrap turn by default. Use `--no-auto` only when you want a record-only create/update.

## Storage

Runtime state lives in the configured Takyon home. In the parent workspace launcher this is `/Users/Zygote/Downloads/takyon/.takyon`.

```text
$TAKYON_HOME/
  state.sqlite3
  businesses/
    <business>/
      brain/
      conversations/
      campaigns/
      product/
      sales/
```

Set `TAKYON_HOME` to override the storage root.

## Design

Takyon keeps the hardcoded surface small. The code hardcodes guardrails; Takyon chooses strategy.

Hardcoded guardrails:

- business isolation
- path containment
- idempotency
- API/env credential gates
- budget caps
- audit events
- pause/resume/kill controls
- conservative GC

Everything else is business memory, skills, or agent judgment.

## Tools

Business tools are concrete powers, not bundled strategies:

```text
business_registry
business_list_businesses
business_read_business
business_read_file
business_list_files
business_upsert_business
business_set_mode
business_create_workspace
business_write_file
business_patch_file
business_record_memory
business_allocate_budget
business_configure_app_budget
business_upsert_app_surface_contract
business_upsert_app_plan
business_upsert_app_customer
business_grant_app_entitlement
business_request_app_magic_link
business_verify_app_magic_link
business_read_app_account
business_create_app_checkout
business_record_stripe_webhook
business_record_app_usage
business_enqueue_job
business_publish_test_outreach
business_claude_agent_task
business_upsert_conversation_thread
business_record_conversation_message
business_record_event
business_record_agent
business_set_control
business_schedule_ceo_wakeup
business_gc
```

Conversation threads and messages are first-class business state. They are stored in `state.sqlite3` as structured rows and mirrored into each business filesystem under `conversations/` as Markdown for CEO review.

Business product apps use canonical Hermes app rails for product subusers/customers, magic-link auth, app sessions, plan policies, entitlements, Stripe checkout intents/sessions, subscription lifecycle reconciliation, revenue events, and usage budget caps. This state is stored in `state.sqlite3` and mirrored under each business filesystem at `app/`.

The app runtime does not own the product's look. Each business records its own surface contract in `app/surface.md`, pointing at its design brief, frontend source path, routes, theme source, and constraints. Use `business_upsert_app_surface_contract` to keep that contract visible to the CEO and skills.

`takyon app-server` exposes the product runtime API over HTTP:

```text
POST /api/takyon/apps/<business>/auth/request
GET  /api/takyon/apps/<business>/auth/verify
GET  /api/takyon/apps/<business>/session
GET  /api/takyon/apps/<business>/account
POST /api/takyon/apps/<business>/checkout
POST /api/takyon/apps/<business>/usage
POST /api/webhooks/stripe
```

The legacy `/api/generated-apps/<business>/...` route is accepted only as a compatibility alias.

Web search comes from Takyon's web toolset. Ad posting, deploys, vendor calls, media generation, and other external side effects are represented as guarded business requests or receipts through `business_enqueue_job` with `requires_api` or `requires_env`. Checkout/subscription work should use the canonical app tools. Takyon must not claim outside-world execution happened unless a concrete receipt exists.

## Test Mode

`businesses.mode` is the source of truth for live/test behavior. `business_set_mode` and `takyon test <business> on|off|status` switch one business only. Test mode keeps CEO wakeups, local planning, drafts, app rails, conversations, and follow-up review active, but suppresses outbound delivery and spend.

In test mode, guarded `business_enqueue_job` requests with missing provider credentials are still recorded with `external_side_effects=suppressed` and the missing credentials listed in the job payload. Use `business_publish_test_outreach` to publish outreach locally under `outreach/local-published/`, write a receipt under `receipts/outreach/`, and mirror the outbound message into `conversations/` without sending externally. Stripe checkout and Postmark magic-link sends create local suppressed receipts in test mode instead of calling providers.

General agentic workspace work can use `business_claude_agent_task`, which runs Claude Agent SDK inside one business workspace with path containment, Anthropic credential checks, budget allocation, no Bash, and an agent-run audit record.

## Registry

The canonical registry lives in `plugins/takyon/registry.py` and is readable through `business_registry`.

Priority bands:

```text
p0_control      control, kill switches, budget/API failures, cleanup safety
p1_ceo          manual CEO commands, wakeups, strategy, recovery
p2_growth       product, distribution, pricing, conversion, revenue
p3_learning     research, creative, outreach assets, evidence, memory
p4_maintenance  status, organization, conservative GC
```

Tools and skills each have a category and allowed priority bands. The registry is descriptive; the hard safeguards still live in the tools.

The CEO can create arbitrary brain files and workspace trees. The code only hardcodes safety primitives: business scope, path containment, idempotency, API credential gates, budget caps, audit events, and pause/kill controls.

`SOUL.md` remains identity. Business learning lives under each business brain.

GC is deliberately conservative. It can prune old events, terminal jobs, and agent-run rows, but it does not delete business files, ledgers, control states, budgets, workspaces, or idempotency records.

## Skills

Skills are business operating methods. They are intentionally separate from tools:

```text
takyon:ceo
takyon:business-learning
takyon:build-product
takyon:market-research
takyon:pricing-strategy
takyon:distribution-campaign
takyon:ad-creative
takyon:outreach
takyon:conversion-review
takyon:failure-recovery
takyon:claude-agent-sdk
takyon:app-runtime
```

Cron is not a skill. Cron wakes the CEO. The CEO then reads business state, chooses whatever skills fit, uses concrete tools for durable changes, and schedules the next wake when useful.
