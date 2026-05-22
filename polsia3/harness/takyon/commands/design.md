---
description: Create or apply a business-owned web/app design brief
requires-business: true
priority-band: product
allowed-tools: [read, workspace, queue, memory]
---

Design the webpage and product surface for `$BUSINESS`.

Operator arguments:

`$ARGUMENTS`

Use explicit business evidence only. First inspect:

- `ceo/map.md`
- `state/current.md`
- `product/design-brief.md`
- `memory/product-marketing-context.md`
- `product/conversion-review.md`
- `website/deployments.jsonl`

If `product/design-brief.md` is missing or stale, queue `business_product_design` before asking for generated-app edits.

If the design brief is fresh and the requested change needs source edits, queue `website_build_deploy` for public website work or `product_ui` for in-app workflow work. Do not run Open Design, start daemons, call MCP tools, spawn coding-agent CLIs, or write outside this business workspace.
