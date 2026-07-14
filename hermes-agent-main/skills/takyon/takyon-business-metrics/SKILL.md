---
name: takyon-business-metrics
description: >-
  Compute, summarize, and track canonical business metrics, wake history, and unresolved inbound
  state. Use when running every wake, establishing the first creation baseline, or when users,
  revenue, usage, or replies
  materially affect the next move. Do not use to invent strategy from missing measurements.
---

# Business Metrics

Turn current business measurements and unresolved inbound state into a compact operating summary.
This method describes what to calculate and how to interpret it; storage and data-access bindings are
declared separately.

## Method

1. Read canonical business state and the latest available analytics.
2. Separate measured values from unavailable values and prior estimates.
3. Compute the current pulse: users, revenue, usage, conversion signals, unresolved conversations,
   and the deltas that materially changed since the previous run.
4. Append one wake-history entry with timestamp, trigger, measured deltas, decision relevance, and
   outstanding blockers.
5. Produce both a concise human summary and a machine-readable summary from the same facts.
6. Recommend a next move only when the measurements support it; otherwise state the missing evidence.

## Verification

- Human and machine-readable summaries agree on every reported value.
- Unknown values remain unknown; zero is used only when zero was actually measured.
- Wake history is append-only and names the source period for each delta.
- Unresolved inbound items remain visible until their status changes through an authoritative action.
- No strategic claim is presented as measurement.
