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
3. Produce the semantic result declared by `contract.yaml`.

## Verification

- Define evidence that proves the method's output is correct.
- Separate observations, inferences, and unavailable evidence.

## Failure Conditions

- Stop when required evidence or capability is unavailable.
- Report the exact unmet condition without fabricating a result.

Keep provider names, runtime names, exact tools, filesystem roots, publication targets, authority,
spend, and receipts out of the skill; declare only semantic capabilities and outputs in
`contract.yaml`, then bind them in `HANDOFF/bindings.yaml`.
