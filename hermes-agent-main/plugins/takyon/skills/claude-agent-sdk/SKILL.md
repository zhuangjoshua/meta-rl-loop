---
name: takyon-claude-agent-sdk
description: Use a bounded Claude Agent SDK worker for general business-scoped tasks, not just websites.
---

# Takyon Claude Agent SDK

Use this skill when a business needs a focused agentic worker to inspect or edit files inside a business workspace: product specs, customer assets, pricing drafts, onboarding flows, research synthesis, support/playbook edits, code-adjacent artifacts, or app/page work.

This is not a website lane and not a deterministic runner. It is a scoped Claude Agent SDK worker.

## Practice

- Read the business first and choose a narrow workspace.
- Prefer `business_claude_agent_task` for bounded file work that benefits from a separate Claude SDK pass.
- Keep the task inside one business workspace. The SDK worker is path-contained and cannot use Bash.
- Use a small budget reservation. The business must have a numeric budget cap before the tool can spend.
- Do not use this skill for vendor side effects, posting, payment changes, deploys, or credential work.
- After the SDK worker returns, inspect its summary and record any durable lesson in the business brain if it should guide future CEO decisions.

If the task is pure strategy or simple writing, use normal Takyon business tools directly. The SDK worker is for work where a bounded file-editing agent is actually useful.
