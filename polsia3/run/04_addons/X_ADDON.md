# X Add-On

## Port From V2

- OAuth client config.
- Platform access/refresh token storage.
- Token refresh.
- `POST /2/tweets` deterministic publish.
- X current user verification.
- Daily rate limit.

## V3 Policy

X publish policy is configurable:

```text
automatic | approval_required | disabled
```

Default requested for v0:

```text
publish_x_post = automatic
```

Automatic still requires:
- configured X credentials
- DB rate limit allowance
- real API receipt

No DMs, scraping spam, or aggressive polling.

