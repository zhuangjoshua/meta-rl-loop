# Takyon Business Plugin

Takyon is a terminal-first CEO operator layer. It adds an isolated business store, concrete guarded business tools, business workflow skills, CEO wake/sleep cron scheduling, and one CLI command.

## Commands

```bash
takyon businesses
takyon init latexflow "Build a LaTeX workflow product for students"
takyon campaigns latexflow
takyon show latexflow
takyon show latexflow brain/index.md
takyon registry
takyon registry tools queue p2_growth
takyon registry skills distribution p2_growth
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

## Storage

Runtime state lives outside the repo by default:

```text
$TAKYON_HOME/takyon/
  state.sqlite3
  businesses/
    <business>/
      brain/
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
business_create_workspace
business_write_file
business_patch_file
business_record_memory
business_allocate_budget
business_enqueue_job
business_record_event
business_record_agent
business_set_control
business_schedule_ceo_wakeup
business_gc
```

Web search comes from Takyon's web toolset. Ad posting, deploys, checkout changes, vendor calls, media generation, and other side effects are requested through `business_enqueue_job` with `requires_api` or `requires_env`, then executed by a deterministic runner.

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
```

Cron is not a skill. Cron wakes the CEO. The CEO then reads business state, chooses whatever skills fit, uses concrete tools for durable changes, and schedules the next wake when useful.
