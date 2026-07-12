---
name: claude-design-brutalist
description: Optional industrial-brutalist design reference for dev tools, security/infra, terminal-adjacent, and experimental technical brands; use only when the caller explicitly selects it.
license: MIT
---

# Claude Design Brutalist

Use this optional reference only when the caller explicitly selects `claude-design-brutalist`;
never auto-add it beneath Taste or Claude Design. For a dense product continuation,
`product/site/DESIGN.md`, existing tokens, and existing assets are authoritative: this reference may
reinforce an already-selected direction but must not override or reinterpret the established brand.
When explicitly selected for a new compatible context, apply its raw, mechanical, precise, and
unapologetically technical vocabulary without copying it verbatim.

## When To Use

- developer tools and CLI-adjacent products
- security, infra, and observability surfaces
- data-heavy dashboards and telemetry consoles
- engineering-brand marketing that must feel like a spec sheet, not a brochure
- experimental technical or editorial brands that reject consumer softness

## Visual Direction

- pick ONE mode per business and commit: Swiss Industrial Print (light, newsprint substrate, carbon ink) or Tactical Telemetry (dark, deactivated-CRT ground, phosphor text) — never mix substrates in one interface
- typography IS the design infrastructure; imagery is secondary
- rigid blueprint grids with visible compartmentalization: real borders, full-width rules, elements anchored to tracks, nothing floats
- bimodal density: tightly packed monospace metadata clusters against vast calculated negative space framing oversized display type
- simulated analog texture in restraint: subtle grain, scanlines (dark mode only), halftone treatment on the rare image

## Typography

- macro type: heavy neo-grotesque (Archivo Black, Inter Black, Space Grotesk Bold), fluid `clamp()` scales, tight negative tracking (`-0.03em` to `-0.06em`), compressed leading (`0.85`–`0.95`), uppercase for structural headers
- micro type: monospace (JetBrains Mono, IBM Plex Mono, Space Mono) at fixed small sizes (`10px`–`14px`), generous tracking (`0.05em`–`0.1em`), uppercase for metadata, IDs, coordinates, nav labels
- serif only as rare textural disruption, degraded (halftone/dither), never for body or UI
- numerics tabular (`font-variant-numeric: tabular-nums`) everywhere data appears

## Color and Tokens

- Swiss Print mode: `#F4F4F0`/`#EAE8E3` paper ground, `#050505`–`#111111` carbon ink foreground
- Telemetry mode: `#0A0A0A`/`#121212` ground (never pure black), `#EAEAEA` phosphor foreground
- ONE accent in both modes: aviation/hazard red (`#E61919`/`#FF2A2A`) for strike-throughs, structural rules, and vital highlights only
- terminal green (`#4AF626`) optional, one semantic element max (a live status readout), never general text
- gradients, soft drop shadows, translucency/glassmorphism: prohibited
- paste `tokens.css` `:root` block verbatim and reference values via `var(--name)`

## Components

- zero `border-radius` anywhere; all corners 90 degrees
- razor-thin dividers via `display: grid; gap: 1px` with contrasting parent background, or `1px`/`2px` solid borders
- ASCII framing devices used sparingly and consistently: `[ SECTION ]`, `>>>`, `///`, crosshair `+` marks at grid intersections
- `®` `©` `™` deployed as structural geometric marks
- semantic technical DOM: `<data>`, `<samp>`, `<kbd>`, `<output>`, `<dl>` for telemetry content
- buttons and inputs are rectangular, bordered, uppercase-labeled, with instant sharp hover states (background inversion beats soft transitions)

## Hard Rules

- never mix Swiss Print and Telemetry substrates in one interface
- no border-radius, no gradients, no soft shadows, no glassmorphism
- no consumer SaaS warmth: no pastel accents, no rounded pills, no floating cards
- one red accent; hazard red is not a brand rainbow
- decorative ASCII/technical markers must not outnumber real content; telemetry framing on every element reads as costume, not engineering
- degradation effects (scanlines, grain, dither) stay subtle; the content must remain crisply legible and WCAG-readable
- data density is welcome, illegibility is not: monospace metadata still needs hierarchy and grouping

## Local Sources

- `DESIGN.md`
- `tokens.css`

Adapted from taste-skill `brutalist-skill` (https://github.com/Leonxlnx/taste-skill), MIT License, Copyright (c) Leonxlnx. See `LICENSE` in this folder.
