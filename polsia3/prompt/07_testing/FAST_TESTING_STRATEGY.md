# Fast Testing Strategy

The previous loop felt slow because every change leaned on Vercel.

V0 testing should use tiers:

1. local unit/type checks for fast feedback
2. local worker build checks
3. local smoke tests against built app
4. Vercel preview deploy only after local gates pass
5. production deploy only after preview health passes

The user does not want to rely on local-only product validation, but local checks should prevent wasting 20 minutes on bad preview deploys.

