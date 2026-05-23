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
- The chosen workspace is the worker's current directory. Tell the worker to write paths relative to that workspace, not to prefix the workspace path again.
- Use a small budget reservation. The business must have a numeric budget cap before the tool can spend.
- If the worker or its helper appears blocked by local runtimes or package managers, check `business_check_runtime_capabilities` before reporting the blocker. Repair or provision through the guarded capability path when available; otherwise record the exact missing capability. Do not hardcode one executable as the business-level problem.
- Do not use this skill for vendor side effects, posting, payment changes, deploys, or credential work.
- Do not let the worker fake business reality in product source. Auth, sessions, users, entitlements, billing, checkout, provider calls, metrics, outreach, and deploys must be wired to canonical Hermes/Takyon rails or shown as visible `DEBUG`/blocked states. Browser localStorage is acceptable for local product data such as draft notes or device-local logs, but not for auth/session/account/subscription state.
- Worker contract: you are not allowed to invent backend behavior. For auth, billing, entitlements, usage, or outreach, use the existing Hermes runtime API contract. If no browser endpoint exists, build the screen as unavailable/blocking, not fake.
- Product workspaces are verified after worker edits when possible. A failed verification receipt is business evidence for repair or blocker reporting; it is not a successful build.
- After the SDK worker returns, inspect its summary and record any durable lesson in the business brain if it should guide future CEO decisions.

If the task is pure strategy or simple writing, use normal Takyon business tools directly. The SDK worker is for work where a bounded file-editing agent is actually useful.
