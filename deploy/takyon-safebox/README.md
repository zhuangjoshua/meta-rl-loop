# takyon-safebox deploy config

This directory tracks the dedicated Safebox authority service host.

Deploys should install:

- `deploy/takyon-safebox/takyon-safebox.service`
- `deploy/takyon-safebox/bootstrap-host.sh` on first boot

First boot must copy the current operator runtime plus the real secret backing
file target (`/opt/takyon/secrets/.env`), not only the `.takyon/.env` symlink.

The service exposes only the Safebox HTTP API on private port `8000`. It does
not serve dashboard, app, or product-host traffic.
