# Design System Inspired by Industrial Brutalism & Tactical Telemetry

Raw mechanical interfaces synthesizing mid-century Swiss typographic print, industrial manufacturing manuals, and retro-futuristic aerospace/military terminal displays. The objective: digital environments that project raw functionality, mechanical precision, and high data density, deliberately discarding conventional consumer UI patterns.

## 1. Visual Theme & Atmosphere

Two archetypes share one system. **Pick ONE per project and commit — never alternate or blend substrates within the same interface.**

**Swiss Industrial Print (light).** 1960s corporate identity systems and heavy-machinery blueprints. Matte unbleached-paper grounds, monolithic heavy sans headers, unforgiving structural grids outlined by visible dividing lines, aggressive asymmetric negative space punctured by viewport-bleeding numerals or letterforms. Primary red as the only alert/accent color.

**Tactical Telemetry (dark).** Classified military databases, legacy mainframes, aerospace HUDs. Dark-mode exclusive: deactivated-CRT grounds, white-phosphor text, absolute monospace dominance for data, technical framing devices (ASCII brackets, crosshairs), simulated hardware limitations (subtle phosphor glow, scanlines, low bit-depth texture).

The layout must appear mathematically engineered: elements anchored to grid tracks and intersections, never floating. Density is bimodal — tightly packed monospace metadata clusters juxtaposed against vast calculated negative space framing macro type.

## 2. Typography Rules

Typography is the primary structural AND decorative infrastructure; imagery is secondary.

**Macro (structural headers).** Heavy neo-grotesque: Archivo Black, Inter Extra Bold/Black, Space Grotesk Bold. Deployed at massive fluid scales — `clamp(3rem, 9vw, 11rem)` class. Tracking tight to negative (`-0.03em` to `-0.06em`) so glyphs form solid architectural blocks. Leading compressed (`0.85`–`0.95`). Uppercase exclusively for structural impact.

**Micro (data & telemetry).** Monospace: JetBrains Mono, IBM Plex Mono, Space Mono. Fixed small scale (`10px`–`14px`). Generous tracking (`0.05em`–`0.1em`) simulating typewriter/terminal matrices. Leading `1.2`–`1.4`. Uppercase for all metadata, navigation, unit IDs, coordinates. Tabular numerics always.

**Textural serif (rare disruption).** Playfair Display or EB Garamond, used exceedingly sparingly and degraded (halftone filter, 1-bit dither) so vector perfection breaks against the clean sans. Never body text, never UI chrome.

## 3. Spacing & Layout

- Strict CSS Grid architectures — the Blueprint Grid. Anchor to tracks and intersections; no free-floating elements.
- Visible compartmentalization: `1px`/`2px` solid borders delineate information zones; full-container-width horizontal rules segregate operational units.
- The razor-line trick: `display: grid; gap: 1px;` with contrasting parent/child backgrounds yields mathematically perfect thin dividers without border declarations.
- Bimodal density rhythm: a dense monospace data block earns a following expanse of negative space with one oversized numeral or headline.
- Absolute rejection of `border-radius`. Every corner is 90 degrees.
- Section padding is architectural, not cozy: large vertical bands (`96px+` desktop) between operational zones, tight internal packing (`8px`–`16px`) inside data clusters.
- Macro type may bleed the viewport edge deliberately; data tables never do.
- Crosshair `+` marks at major grid intersections and sparse `[ BRACKETED ]` section labels are the permitted ornament — rationed, aligned to the grid, never scattered.

## 4. Do's and Don'ts

### Do
- Commit to one substrate (print-light or telemetry-dark) for the entire surface
- Use one hazard-red accent for rules, strike-throughs, and vital data highlights
- Set all data in monospace with tabular figures, grouped and labeled like an instrument panel
- Use semantic technical DOM (`<data>`, `<samp>`, `<kbd>`, `<output>`, `<dl>`) for telemetry content
- Keep degradation textures (grain, scanlines, halftone) subtle enough that text stays crisply legible
- Let oversized uppercase numerals and headers do the decorative work

### Don't
- Mix light and dark substrates in one interface
- Use border-radius, gradients, soft drop shadows, translucency, or glassmorphism
- Add pastel or secondary accent colors; red is the only voice
- Use terminal green as general text color (one semantic readout element max)
- Deploy ASCII framing on every element — rationed ornament or it reads as costume
- Use pure `#000000` backgrounds (deactivated CRT is `#0A0A0A`/`#121212`)
- Let data density collapse into illegibility; clusters still need hierarchy, grouping, and WCAG-passing contrast

## Attribution

Adapted from taste-skill `brutalist-skill` (https://github.com/Leonxlnx/taste-skill), MIT License, Copyright (c) Leonxlnx. Reworked for the shared Takyon design-pack schema; see `LICENSE`.
