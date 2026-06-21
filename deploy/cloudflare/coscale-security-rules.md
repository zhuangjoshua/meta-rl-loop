# coscale.app Cloudflare security rules

Date applied: 2026-06-21

Zone: `coscale.app`
Account: `4ced5158c2ca7e509a8ad06cb3d74e1c`
Plan observed after upgrade: Pro

This file records the Cloudflare dashboard state that protects the shared product sub-user plane.
It is not a replacement for Cloudflare API/Terraform state, but it is the tracked operator note for
the manual Pro/WAF step in `subuser-security-hardening-plan-for-codex.md`.

## Managed WAF

Dashboard path:

`Security` -> `Security rules` -> `Managed rules`

Enabled managed rulesets:

- `Cloudflare Managed Ruleset`
  - Scope: all incoming requests to `coscale.app`
  - Action: execute
  - Status: active
- `Cloudflare OWASP Core Ruleset`
  - Scope: all incoming requests to `coscale.app`
  - OWASP paranoia level: `PL1`
  - OWASP anomaly threshold: `Medium - 40 and higher`
  - OWASP action: `Block`
  - Action: execute
  - Status: active

## Rate limiting

Dashboard path:

`Security` -> `Security rules` -> `Rate limiting rules`

Enabled rule:

- Name: `Limit product magic-link auth requests`
- Expression:

```text
(http.host eq "coscale.app" or ends_with(http.host, ".coscale.app")) and starts_with(http.request.uri.path, "/api/takyon/apps/") and ends_with(http.request.uri.path, "/auth/request")
```

- Characteristics: `IP`
- Threshold: `10` requests per `1 minute`
- Action: `Managed Challenge`
- Status: active

This complements the tracked Caddy auth-request limiter in `deploy/takyon-subuser/Caddyfile`.
Cloudflare catches edge abuse before origin; Caddy remains the origin-side backstop; app-layer SQL
limits remain the per-sub-user authority.

## Bot / crawler settings

Dashboard path:

`Security` -> `Settings` -> `Bot traffic`

Observed state:

- `Block AI bots`: enabled, scope `Block on all pages`
- `AI Labyrinth`: left disabled
- `Super Bot Fight Mode`: not exposed in the current Cloudflare dashboard for this zone after Pro

Do not enable page-altering or challenge-heavy bot controls without re-running app auth, checkout,
generate, search, and action smoke checks on a fresh product business.

## Smoke checks run after applying

```text
https://wandr.coscale.app/ -> 200 text/html
https://wandr.coscale.app/app -> 200 text/html
https://wandr.coscale.app/api/takyon/apps/wandr/account -> 200 application/json
```
