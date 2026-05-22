# Generated Secrets

Generate:
- CRON_SECRET
- generated app project AI proxy keys
- generated app platform proxy secrets if needed
- magic link tokens/session tokens

## Generated During Setup

- `CRON_SECRET` was generated locally for v3 on 2026-05-19.
- The same local value was pushed to Vercel `argon-site` for production, preview, and development without printing it.

Do not generate:
- provider API keys
- Stripe keys
- X tokens
- Meta tokens
- Atlas keys
- Vercel token

Missing vendor secrets produce blocked/config-required state.
