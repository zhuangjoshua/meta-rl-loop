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
