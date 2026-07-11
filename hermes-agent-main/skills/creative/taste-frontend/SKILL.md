---
name: taste-frontend
description: Top-level, brief-driven art direction and anti-template quality control for every Takyon product/site worker pass. Layer above claude-design and an optional claude-design-* visual system for landing pages, public pages, product UI, and later iterations while preserving the injected App Kit behavior contract.
license: MIT
---

# Taste Frontend for Takyon

Apply this to every `product/site` task as the top design layer. The App Kit contract owns behavior;
Taste interprets the brief and controls art direction; `claude-design` supplies the implementation
method; one optional `claude-design-*` skill supplies a concrete visual system. Adapt that system to
the business instead of replacing it or copying its house aesthetic verbatim. Never change routes,
destinations, auth, checkout, entitlements, account semantics, action files, analytics hooks, legal
copy, or the click graph merely to improve appearance.

## Design Read

Before editing, infer one direction from the business brief, audience, offer, brand assets, existing
source, references, accessibility constraints, and whether the target is public marketing or product
UI. State internally:

`Reading this as: <surface> for <audience>, with a <vibe> language, leaning toward <visual family>.`

Set three internal dials and let them govern the whole surface:

- `DESIGN_VARIANCE`: 1 symmetric/systematic to 10 experimental/asymmetric.
- `MOTION_INTENSITY`: 1 static to 10 cinematic/physics-led.
- `VISUAL_DENSITY`: 1 gallery-airy to 10 cockpit-dense.

Infer them from the brief. Marketing pages normally allow more variance and motion; operational
product screens normally require more density and less theatrical motion. Do not force one aesthetic
across unrelated businesses.

## Fluid Composition

- The landing and public pages may use any composition that serves the brief: asymmetric split,
  editorial manifesto, image-as-canvas, horizontal narrative, sticky stack, kinetic typography,
  full-bleed media, restrained centered launch, or conventional product framing.
- Do not default to centered dark mesh heroes, left-copy/right-card heroes, three equal feature cards,
  bento grids, glass panels, or purple gradients.
- Vary section families and visual anchors. Repetition must establish rhythm, not reveal a template.
- Keep the hero legible and conversion-ready in the initial viewport. Make mobile collapse explicit.
- Use real product views or generated/real imagery. Never fabricate product behavior in decorative
  `<div>` mockups or invent proof.

## Design System

- Apply the layers in order: App Kit behavior, Taste art direction, Claude Design method, then one
  optional concrete visual system. Resolve conflicts upward in that order.
- Treat a `claude-design-*` system as a vocabulary of typography, spacing, color, and components,
  not a template. Taste may transform its expression to fit the brief while preserving its craft.
- Establish one coherent token language in `src/tokens.css`: typography, palette, surfaces, spacing,
  radii, shadows, focus, and motion.
- Public and product pages share that language without sharing the same composition. A cinematic
  landing can lead to a dense, operational `/app` that still unmistakably belongs to the same brand.
- Preserve existing committed brand assets. Introduce no new dependency without checking the pinned
  package manifest first.
- Use one accent strategy, one neutral family, one radius grammar, and deliberate type hierarchy.
- Cards communicate grouping or elevation; they are not the default container for every idea.

## Product UI

- Preserve the injected workflow, route, and runtime contracts exactly.
- Design the real states: loading, empty, success, error, disabled, focus, entitlement, and recovery.
- Use the available application viewport. Do not turn `/app` into a narrow marketing card.
- Optimize hierarchy, scanability, and task completion before decorative novelty.

## Assets

- Reuse truthful brand and product assets first.
- When the outer Takyon run provides generated assets, read its manifest and use the supplied local
  paths. The coding worker never reads provider keys or calls a paid image provider directly.
- If the surface needs imagery and none exists, report a bounded asset request to the owning Takyon
  workflow; do not substitute hotlinked stock, placeholders, or fake screenshots.
- Generated imagery must match the section's actual aspect ratio and job, not act as generic filler.

## Anti-Slop

- No default Inter/system-font monoculture, gradient display text, repeated eyebrow kickers, numbered
  section scaffolding, fake metrics, generic testimonials, em-dash-heavy aphorisms, glow-on-black AI
  palettes, nested cards, or icon-tile grids.
- No stock-template prose such as “Elevate”, “Seamless”, “Unleash”, or “Revolutionize”.
- Avoid category reflexes: premium consumer does not automatically mean beige serif; AI does not
  automatically mean purple dark mode; developer tools do not automatically mean terminal cosplay.
- Verify contrast, overflow, touch targets, keyboard focus, reduced motion, responsive behavior, and
  every visible string before finishing.

## Preflight

- One brief-derived visual thesis is visible across the touched pages.
- The target is not recognizably a generic AI template.
- App Kit routes and behavior are unchanged.
- Public composition is fluid; product composition remains task-appropriate.
- Generated or real assets are truthful and locally published.
- Build and typecheck pass; the real surface is inspected at mobile and desktop widths.

## Provenance

Adapted from Taste Skill v2 at commit `b17742737e796305d829b3ad39eda3add0d79060`:
https://github.com/Leonxlnx/taste-skill/blob/b17742737e796305d829b3ad39eda3add0d79060/skills/taste-skill/SKILL.md
