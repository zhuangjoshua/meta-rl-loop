# Local Vs Preview

Local tests are for speed.

Preview deployments are for real hosted verification.

Required before Vercel preview:
- typecheck
- build
- route smoke tests
- generated-app health route
- no missing required env for selected feature

Required before saving generated app URL:
- deployment succeeded
- health check passed
- expected page renders
- protected/proxy behavior works
- checkout/session/AI gateway endpoints are wired if enabled

