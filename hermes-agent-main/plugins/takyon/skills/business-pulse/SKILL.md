---
name: takyon-business-pulse
description: Interpret deterministic business pulse metrics against the current business model.
---

# Takyon Business Pulse

Use this skill on scheduled CEO wakeups, initial bootstrap baseline, `/pulse` refresh requests, and business decisions where current metrics matter.

## Sources

- Start with `business_calculate_pulse`. It is the deterministic, read-only metric calculator.
- Read `brain/business-model.md` when present. Create it only when the business needs a current model file.
- Read `brain/pulse.md` when present for the previous human-readable pulse.
- Do not scan the whole business filesystem for pulse work.

## Canonical Files

- `brain/business-model.md` stores the current business model: philosophy, ICP, urgent customer, promise, wedge, pricing hypothesis, distribution theory, key assumptions, and what evidence would change them.
- `brain/pulse.md` stores the latest human-readable pulse: generated-at, source windows, metric deltas, missing metrics, alerts, semantic changes, and the next CEO question.

Raw metrics remain in canonical SQLite/app/conversation/job/ledger/event stores. Pulse snapshots over time should be recorded as `business.pulse.snapshot` events. Do not make Markdown the raw metrics database.

## Practice

- Treat metric definitions as fluid and archivable.
- Never delete old metric data, pulse events, wake journals, app records, conversation records, ledger entries, jobs, or raw events during a wake cycle.
- If a metric stops mattering, mark it inactive, missing, or not applicable in the current pulse instead of deleting history.
- The snapshot stores evidence and deltas, not a verdict.
- Missing or unavailable metrics are evidence gaps, not fatal errors. Preserve the gap in the pulse and let the CEO decide whether to instrument, ignore, or change strategy.
- Think semantically against the business model only when evidence moved or important evidence is missing.
- Update `brain/business-model.md` only when the pulse or customer evidence materially changes the model.
- Write `brain/pulse.md` compactly enough that the CEO can read it on every wake.
- Record the machine-readable pulse with `business_record_event(event_type="business.pulse.snapshot")` after the readable pulse is written.

## Delegation

Deterministic pulse math is not delegated. Use `business_calculate_pulse`.

Customer response review remains the existing conversation path. If replies, comments, support messages, or outreach results are too large or noisy to inspect cheaply, use `business_conversation_agent_task` to summarize qualitative evidence before the CEO decides.

The CEO decides. The pulse skill interprets. The calculator computes.
