# takyon-safebox deploy config

This directory tracks the dedicated Safebox authority service host.

Deploys should install:

- `deploy/takyon-safebox/takyon-safebox.service`
- `deploy/takyon-safebox/bootstrap-host.sh` on first boot

First boot must copy the current operator runtime plus the real secret backing
file target (`/opt/takyon/secrets/.env`), not only the `.takyon/.env` symlink.

The service exposes only the Safebox HTTP API on private port `8000`. It does
not serve dashboard, app, or product-host traffic.

The tracked Safebox env contract now also owns the shared Supabase app-auth values:

- `SUPABASE_URL`
- one of `SUPABASE_PUBLISHABLE_KEY` or `SUPABASE_ANON_KEY`
- optional `SUPABASE_JWT_SECRET` for legacy HS256 fallback only

Use `deploy/shared/supabase-auth-env.sh upsert-file ...` against the outside-git env file target to provision or rotate them without inventing a second secret path.

## Managed secret-manager cutover

The Safebox can optionally resolve selected sensitive env keys from a managed
secret manager CLI instead of `/opt/takyon/secrets/.env`. Runtime planes still
call the Safebox API; only the Safebox host runs the CLI.

Configure the Safebox service environment with:

- `TAKYON_MANAGED_SECRET_KEYS`: comma/space-separated env key names that have
  been migrated, for example `STRIPE_SECRET_KEY,STRIPE_WEBHOOK_SECRET`.
- `TAKYON_MANAGED_SECRET_COMMAND`: command template that prints the secret value
  to stdout. `{key}` is replaced with the requested env key; if omitted, the key
  is appended as the final argument. The command runs without a shell.
- optional `TAKYON_MANAGED_SECRET_TIMEOUT_SECONDS` (default `8`).
- optional `TAKYON_MANAGED_SECRET_CACHE_SECONDS` (default `60`, `0` disables).

Examples after the operator has signed the provider CLI in on the Safebox host:

```sh
# Doppler
TAKYON_MANAGED_SECRET_COMMAND='doppler secrets get {key} --plain --project takyon --config prd'

# Infisical
TAKYON_MANAGED_SECRET_COMMAND='infisical secrets get {key} --plain --projectId <project-id> --env prod'

# 1Password
TAKYON_MANAGED_SECRET_COMMAND='op read op://Takyon/production/{key}'

# Google Secret Manager
TAKYON_MANAGED_SECRET_COMMAND='gcloud secrets versions access latest --secret {key}'
```

For a key listed in `TAKYON_MANAGED_SECRET_KEYS`, the managed secret manager is
authoritative. `takyon secret set KEY VALUE` refuses to write that key back to
the env file; update it in the provider instead. For gradual cutover, always set
`TAKYON_MANAGED_SECRET_KEYS`; if the command is configured without a manifest,
the command owns every sensitive key.
