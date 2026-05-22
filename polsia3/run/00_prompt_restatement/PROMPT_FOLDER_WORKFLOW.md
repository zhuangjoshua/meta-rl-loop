# Sync Folder Workflow

The repo has two readable folders:

```text
prompt/  operator-editable rough intent/spec input
run/     agent-owned truth ledger
```

`prompt/` is what the user edits.

`run/` is what must remain true.

The source-of-truth chain is:

```text
verified reality -> run/ -> prompt/
```

`verified reality` is implemented code, tested behavior, observed connection state, actual failures, actual blockers, and explicit decisions. `run/` records that truth. `prompt/` is refreshed from `run/` after the truth ledger is updated.

## Status Language

`run/` may include both aspirational scope and completed work, but it must label them clearly.

Use these status labels:
- `verified`: implemented and backed by a named check, observed behavior, receipt, or DB state.
- `partial`: some code exists, but one or more required gates are still missing.
- `planned`: intended scope from the user/spec, not implemented yet.
- `blocked`: cannot proceed until a missing secret, permission, vendor state, budget, policy decision, or operator action is resolved.
- `failed`: attempted and errored; the error must be recorded.
- `forbidden`: intentionally not allowed by product policy, such as Meta spend in v0.

Rules:
- A feature listed without a status is only a requirement, not a claim of completion.
- Any feature that touches money, external posting, deployment, auth, AI provider calls, or persistence must be `verified` only after a real check/receipt exists.
- `prompt/` receives the same status labels when synced from `run/`.

## Intended User Workflow

1. The operator edits files under `prompt/`.
2. The operator says something like:

```text
I edited prompt/04_addons/X_ADDON.md. Clean it up and implement the corresponding change.
```

3. The agent reads the changed files.
4. The agent restates the requested change.
5. The agent implements the code.
6. The agent verifies the implementation.
7. The agent updates `run/` with:
   - final behavior
   - changed code paths
   - acceptance checks
   - blockers or missing secrets, if any
   - explicit status labels for aspirational vs completed work
8. The agent syncs `prompt/` from `run/`.

## Sync Direction

After implementation, sync direction is always:

```text
run/ -> prompt/
```

Do not sync rough `prompt/` notes into `run/` as truth before implementation.

If `prompt/` and `run/` disagree, treat `prompt/` as a requested change. The agent must implement or block it, then update `run/` to the actual outcome, then sync `run/ -> prompt/`.

## Rule

Future code changes must update `run/` after implementation and verification. Future prompt/spec edits in `prompt/` are input, not truth.
