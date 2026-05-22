# Non-Negotiables

- No feature loss without explicitly listing the removed feature and why.
- No Vercel Sandbox builder.
- No Open Lovable dependency for v0.
- No silent `catch {}` behavior.
- No repair shell or degraded build marked as success.
- No fake deployments or fake preview URLs.
- No fake metrics, customers, payments, emails, API calls, posts, or vendor receipts.
- No generated app receives raw OpenAI, Anthropic, Meta, X, Stripe, or other provider keys.
- No hardcoded generated-app AI limits in env vars.
- No platform posting to Reddit/community surfaces in v0.
- No Meta spend or Meta campaign object creation in v0.
- Every long-running job has durable status and evidence.
- Every vendor side effect records a real receipt or error.
- Every prompt-bearing workflow is registered and editable.
- Every future implementation change updates `run/`.
- Supabase/Postgres is mandatory core infrastructure, not optional.
- Avoid Claude CLI as a core dependency; prefer SDK/library/local-worker paths.
