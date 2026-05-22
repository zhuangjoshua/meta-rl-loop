# Vercel And Git Deployment

## Confirmed Repo

GitHub target:

```text
tejdiv/polsia3
```

It should be private.

Verified setup status:
- `tejdiv/polsia3` now exists and is private.
- `/Users/Zygote/polsia3` is initialized as a git repo on branch `codex/rebuild-v3`.
- `origin` points to `https://github.com/tejdiv/polsia3.git`.
- Repo-local git identity is configured as `tejdiv <203025654+tejdiv@users.noreply.github.com>`.

## Confirmed Vercel Intent

The platform should be pushed to the existing Vercel project intended as:

```text
argon-site
```

The user explicitly confirmed that the current `argon-site` Vercel deployment may be overwritten by this rebuild.

Verified setup status:
- `/Users/Zygote/polsia3/.vercel/project.json` is linked to `argon-site`.
- The local Vercel project id and team id match the copied local env values.
- `CRON_SECRET` is present in Vercel production, preview, and development environments.

## Platform URLs

Expected:
- platform production: existing Four Manifold/Takyon platform domain
- generated apps: `https://{slug}.fourmanifold`
- fallback/proxy route: `/c/{slug}`

Exact domain mapping must be verified during implementation before finalizing.

## Git Rules

- Main source lives in GitHub.
- Generated app template source lives in the platform repo.
- Generated app build outputs/source snapshots must be saved with build records.
- Do not push secrets.
