# takyon-safebox deploy config

This directory tracks the dedicated Safebox authority service host.

Deploys should install:

- `deploy/takyon-safebox/takyon-safebox.service`

The service exposes only the Safebox HTTP API on private port `8000`. It does
not serve dashboard, app, or product-host traffic.
