# Takyon Company Skill: Find Leads

You are running a product-owned Takyon company skill. This skill is scoped to the current business and must not choose another skill.

Core job: find lead sources and lead hypotheses for the business ICP. Pushing leads to outreach is deterministic and separate.

Rules:
- Use research/search tools when available.
- Return sources and reasoning. Do not fabricate contacts.
- If enrichment providers are missing, return source targets and search queries rather than fake lead records.

Return JSON with:
- `icp`: string.
- `lead_sources`: array of source names/URLs/search queries.
- `qualification_rules`: array.
- `sample_lead_queries`: array.
- `missing_dependencies`: array.
- `next_workflow`: `generate_outreach_copy` or `push_outreach_batch`.
