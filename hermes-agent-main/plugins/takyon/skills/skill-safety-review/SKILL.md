---
name: takyon-skill-safety-review
description: Review third-party skills, scripts, plugins, and workflow packs before adoption.
---

# Takyon Skill Safety Review

Use this skill before importing, adapting, installing, or trusting third-party skills, scripts, plugins, MCP servers, workflow packs, or automation snippets.

## Practice

- Review statically first. Do not execute unknown code, installers, shell snippets, or package scripts just to inspect them.
- Check license, provenance, binaries, generated files, dependency installers, network calls, external posting/sending, provider credentials, secret handling, prompt injection risk, tool scope, filesystem scope, and destructive commands.
- Scan for likely secrets and suspicious patterns. If local scanners are unavailable, say which checks were not run instead of implying a clean bill of health.
- Identify required environment variables, provider accounts, budget needs, live side effects, and test-mode behavior before adoption.
- Decide whether to adopt, adapt selected prose, quarantine, or reject. Prefer adapting small method guidance into active Takyon skills over bulk-importing foreign routers or CLIs.
- Record the review in a business or operator-visible file when it affects a business decision, and record important findings with `business_record_event` when scoped to a business.

## Fit

This is an audit/control method. It does not replace `takyon:failure-recovery`; use recovery when already-running business work failed or drifted.
