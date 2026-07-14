---
name: surface-refresh-audit
description: >-
  Audit and improve an existing product surface while preserving its established design direction
  and functionality. Use when a shipped interface needs a focused refresh, redesign, or anti-slop
  pass. Do not use for a first build or for backend behavior changes.
---

# Surface Refresh Audit

Use this method to improve an existing interface without replacing its product logic or visual
identity. The approved design contract, existing tokens, assets, and working behavior are the source
of truth.

## Method

1. Read the brief, design contract, current source, and visible states before editing.
2. Inventory the styling system, reusable components, responsive behavior, and interaction states.
3. Diagnose specific weaknesses using the audit below; do not begin from a replacement template.
4. Make the smallest coherent set of changes that fixes the diagnosed problems.
5. Exercise the affected flows at representative viewport sizes and compare them with the original
   requirements.

## Audit

- Typography: purposeful display hierarchy, readable measure, balanced headings, and tabular figures
  where numeric comparison matters.
- Color and surfaces: intentional contrast, restrained accents, visible hierarchy, and no decorative
  effects that weaken comprehension.
- Layout: varied but coherent composition, strong grouping, clear density, and no repeated-card
  monoculture.
- Interaction: complete hover, focus, active, disabled, loading, empty, success, and error states.
- Responsive behavior: no clipped content, hidden primary actions, or desktop assumptions on small
  screens.
- Authenticity: concrete product language and real assets; no invented claims or generic filler.

## Boundaries

- Preserve functional behavior unless the brief explicitly changes it.
- Preserve the established design direction; a refresh is not a silent rebrand.
- Do not rebuild from scratch merely because a different implementation would be easier.
- Do not claim the surface is improved until the affected flows have been exercised.

## Verification

- The diagnosed weaknesses are visibly resolved.
- Existing behavior still works and affected states remain reachable.
- The result is consistent with the approved design contract and no longer reads as generic output.

