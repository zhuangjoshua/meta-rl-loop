# Generated App AI Gateway And Limits

Generated app server routes call:

```text
POST /api/ai-gateway/messages
Authorization: Bearer ARGON_PROJECT_AI_KEY
```

Request includes:
- purpose
- route
- messages
- appUserTier
- appUserKey

Gateway steps:
- authenticate project key
- resolve model policy
- estimate max cost
- check wallet
- reserve usage
- apply paid-user/free-user rules
- call provider
- record actual usage/cost
- complete or fail reservation

No generated app receives raw provider keys.

If budget/config is missing, return a real blocked/setup state and record the attempt where possible.

