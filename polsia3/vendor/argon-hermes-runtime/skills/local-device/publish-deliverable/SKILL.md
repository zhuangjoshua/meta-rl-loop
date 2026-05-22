---
name: publish-deliverable
description: "Deliver VPS-created user files to the Mac, keep a signed artifact link, and clean scratch workspace."
version: 1.0.0
metadata:
  hermes:
    tags: [deliverables, artifacts, cleanup, vps]
    related_skills: []
---

# Publish Deliverable

Use this skill when a task creates user-facing files on the VPS:

- apps or generated project folders
- reports, archives, images, datasets, documents
- anything the user should download, open, or keep

## Storage Contract

Use these roots:

```
/opt/data/workspace/<run_id>/...   ephemeral scratch
/opt/data/artifacts/...            published deliverables with TTL
/opt/data/memory                   persistent memory
/opt/data/cron                     persistent schedules/output
/opt/data/repos                    intentionally retained repos
```

Do not put persistent memory, schedules, credentials, or retained repos under workspace.

## Workflow

1. Build scratch output under the run workspace from run metadata.
2. Prefer `deliver_artifact_to_mac(path, cleanup_source=true)` for normal user-facing deliverables.
3. Include the returned `local_path` and `artifact_url` in the final response.
4. Do not tell the user to use `scp` or raw `/opt/data` paths except for debug recovery.

`deliver_artifact_to_mac` calls `publish_artifact`, downloads the artifact to
`~/Downloads/Argon/Deliverables` on the user's Mac, verifies size/SHA-256, and
returns both the Mac path and signed artifact URL.

If Mac delivery returns `status: "blocked"`, report the `blocked_on`
capability requirement. The local tool queue keeps the original download job
blocked and can retry it once after the Mac reports the capability as granted.

Use `publish_artifact` directly only when the user explicitly wants link-only
delivery or Mac delivery is unavailable and you need to report the failure.
`publish_artifact` refuses paths outside `/opt/data/workspace`, zips the
deliverable, signs a download URL, and deletes the source when cleanup succeeds.
