# Failure And Retry Policy

## Blocked

Use `blocked` for:
- missing secret
- missing integration
- policy disabled
- approval required
- budget unavailable
- rate limit reached
- operator input required

## Failed

Use `failed` for:
- code exception
- vendor error
- timeout
- invalid response
- build failure
- smoke test failure

## Retry

Retries must include:
- attempt count
- max attempts
- last error
- whether retry is safe
- next suggested action

Do not hide failures behind generic success messages.

