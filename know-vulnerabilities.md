# Known Vulnerabilities

## Shared host cache can still persist backend-mediated writes

- Status: open
- Severity: high
- Affected surface: `app.fourmanifold.com` operator/business flows that can trigger backend file mutations

### What is true

A normal browser user on `app.fourmanifold.com` cannot directly browse or edit host files such as:

```text
/opt/takyon/.takyon/cache/businesses/<slug>/...
```

### What is still vulnerable

The backend Takyon write path still writes into the shared host-side business cache before syncing to the durable object store. So if an attacker can find a backend authorization bug, tool exposure bug, path validation bug, or other server-side write primitive reachable from the app, they may be able to cause writes that persist indirectly through the backend.

Current write path examples:

- `workspace.upsert` writes locally, then syncs remote in [core.py](/Users/Zygote/Downloads/takyon/hermes-agent-main/plugins/takyon/core.py:12225)
- `artifact.write` / `memory.write` write locally, then sync remote in [core.py](/Users/Zygote/Downloads/takyon/hermes-agent-main/plugins/takyon/core.py:12249)
- per-run isolated scratch is ephemeral and removed on exit in [storage.py](/Users/Zygote/Downloads/takyon/hermes-agent-main/plugins/takyon/storage.py:547)

### Security implication

This is not a direct browser-to-filesystem vulnerability.

It is an indirect persistence risk:

- direct host-cache editing from the app: no
- persistence via a backend write vulnerability: yes, potentially

### Recommended hardening

Remove shared host-cache mutation as a normal backend write path:

1. Route all business mutations through isolated scratch workspaces only.
2. Sync from isolated scratch to the durable object store.
3. Delete scratch after the run.
4. Keep the shared host cache read-only or read-side only.

That would make backend compromise materially less able to persist through the shared cache layer.
