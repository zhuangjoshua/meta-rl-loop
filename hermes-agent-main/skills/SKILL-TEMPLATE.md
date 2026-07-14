---
name: verb-object
description: >-
  State the method's outcome. Use when concrete trigger conditions apply. Do not use for the nearest
  adjacent tasks owned by another method.
---

# Verb Object

## Inputs

- Name the information required to apply the method.
- State assumptions that materially affect the result.

## Method

1. Inspect the relevant inputs.
2. Apply the domain method in bounded steps.
3. Use the named scoped tools and bundled resources needed to produce the real result.

## Verification

- Define evidence that proves the method's output is correct.
- Separate observations, inferences, and unavailable evidence.

## Failure Conditions

- Stop when required evidence or capability is unavailable.
- Report the exact unmet condition without fabricating a result.

Keep the reusable method complete: name the tools, relative resources, procedure, verification, and
failure behavior Claude needs. Put installation destinations and bundle membership in
`release-skills.yaml`, mode-level tool/write restrictions in `sdk-runtime-policy.yaml`, and keep
tenant scope, authority, spend, publication, receipts, validators, and completion gates enforced in
runtime code. HANDOFF is an authoring guide only.
