---
name: takyon-failure-recovery
description: Recover from failed, stale, blocked, or contradictory business work and turn it into learning.
---

# Takyon Failure Recovery

Use this skill when jobs fail, campaigns go stale, agents drift, APIs are missing, assumptions break, or cleanup is needed.

## Practice

- Read recent events, jobs, controls, relevant workspace files, and brain failure notes.
- Determine whether to retry, simplify, pause, kill, archive, or change strategy.
- Use `business_set_control` for pause/resume/kill at global, business, workspace, job, or agent scope.
- Use `business_gc` only for conservative row cleanup. It must not delete files, ledgers, controls, budgets, businesses, workspaces, or idempotency records.
- Use `business_delete_business` only for explicit operator deletion; preview first unless the operator has already confirmed permanent cleanup.
- Record what failed, why if known, what changed, and what the CEO should avoid next time.

Recovery is part of learning. A clean failure note is better than repeating the same move.
