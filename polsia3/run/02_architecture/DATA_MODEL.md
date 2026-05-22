# Data Model

## Trunk Tables

- profiles
- companies
- company_memberships
- company_sites
- tasks
- workflow_jobs
- agent_runs
- agent_run_steps
- events
- business_documents
- approvals
- agent_actions
- prompts
- prompt_versions
- cron_jobs
- addons
- company_addons
- action_policies
- company_action_policies

## Generated App Tables

- generated_app_users
- generated_app_magic_links
- generated_app_sessions
- generated_app_entitlements
- generated_app_plan_policies
- company_payment_links
- company_checkout_intents
- company_checkout_sessions
- company_revenue_events

## Project AI Tables

- project_ai_wallets
- project_ai_wallet_events
- project_ai_proxy_keys
- project_ai_model_policies
- project_ai_usage_events

## Worker/Build Tables

- generated_app_builds
- generated_app_build_steps
- generated_app_deployments
- generated_app_runtime_manifests

## Simplification From V2

Avoid overlapping run tables. Use:

```text
task -> workflow_job -> agent_run -> agent_run_step
```

Runtime sessions, if Hermes is used, attach to `agent_run`.

