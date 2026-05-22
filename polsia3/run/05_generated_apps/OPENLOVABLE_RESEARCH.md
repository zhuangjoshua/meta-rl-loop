# OpenLovable Research

Research date: 2026-05-19.

## What OpenLovable Actually Does

OpenLovable is a Next.js control app that builds a generated Vite React app inside a cloud sandbox.

Core loop:
1. Create Vercel/E2B sandbox.
2. Initialize a minimal Vite + React + Tailwind app.
3. Optionally scrape a URL with Firecrawl, including markdown/html/screenshot.
4. Ask an LLM to output XML-tagged files.
5. Parse `<file path="...">...</file>` blocks.
6. Detect missing imports/packages and install them.
7. Write files into the sandbox.
8. Restart Vite when packages change.
9. Check whether the preview is rendering and whether Vite error overlays appear.
10. For edits, build a file manifest and try to target only the needed files.

## Useful Ideas To Borrow

- strict file-output protocol instead of freeform code blobs
- manifest of generated files, imports, exports, components, and routes
- targeted edit mode that selects primary files and context files
- package/import detection before build
- automatic build/preview validation
- screenshot/DOM smoke checks before claiming success
- conversation/project memory, but bounded and summarized
- hard file-count limits for small edits

## Ideas Not To Copy

- do not use Vercel Sandbox as the v3 production builder
- do not depend on E2B for v0
- do not start from a nearly empty Vite page and hope LLM taste is enough
- do not use mock scrape fallbacks as success
- do not let generated code invent auth, payments, AI budgets, or vendor integrations
- do not use arbitrary package installation as the default path for every app

## Implication For Polsia V3

The generated-app template must be curated before the LLM touches it.

V3 should use:
- a hand-reviewed Next.js generated-app base, not a blank Vite starter
- shared design tokens
- shadcn/Radix-style primitives
- typed homepage block registry
- typed product module registry
- typed app config
- local Mac worker build/preview loop
- Playwright screenshot/viewport checks
- package allowlist or review gate
- file manifest and targeted edit workflow

The LLM may select and configure blocks, generate bounded product-module code, and write copy. It must not own the whole app architecture.

## Template Quality Gate

The base template is not considered good because it exists. It is good only after it passes acceptance:
- desktop and mobile screenshots render professionally
- typography, spacing, and density are appropriate for SaaS/product tools
- no fake data claims are visible
- auth/payments/AI budget states are wired or clearly blocked in backend state
- homepage and app shell use reusable blocks
- generated app builds locally
- deployed health check passes before URL is saved
- design tokens can create distinct brand skins without arbitrary CSS rewrites
