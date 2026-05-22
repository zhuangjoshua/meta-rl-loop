# Takyon Business Skill: Measurement Plan

Source material copied and adapted from OpenClaw analytics tracking and A/B test setup skills plus Meta Ads Kit Pixel/CAPI audit patterns. Real CAPI event sending is excluded.

Purpose: define what the business must measure before claiming growth, conversion lift, or paid-media performance.

Rules:
- Do not send CAPI events, install pixels, edit vendor settings, or claim tracking is live without receipts.
- Do not fabricate metrics.
- Use current Takyon receipts to separate implemented tracking from proposed tracking.
- Keep the plan small enough for the CEO and product runner to act on.

Return Markdown with these sections:
- `# Business Measurement Plan`
- `## Measurement North Star`: one primary outcome and supporting indicators.
- `## Event Taxonomy`: events, properties, trigger point, owner, and receipt needed.
- `## Funnel Metrics`: visit, activation, signup, checkout, payment, retention, and referral evidence.
- `## Experiment Metrics`: success, guardrail, sample/evidence requirements, and stop rules.
- `## UTM And Attribution`: channel naming and campaign conventions.
- `## Pixel/CAPI Audit`: Meta pixel/CAPI status from evidence, gaps, and future gated actions.
- `## Implementation Candidates`: Takyon workflows or files that should be updated next.
