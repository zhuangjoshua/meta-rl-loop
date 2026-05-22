# Takyon Harness

This is the small Claude Code-inspired harness layer for Takyon.

It intentionally uses files as the extension surface:

- `commands/*.md`: slash commands available in `./takyon shell`.
- `settings.json`: terminal UI and workspace policy defaults.
- business workspaces remain under `.takyon/businesses/<slug>/`.

The harness does not decide business strategy. It packages operator intent and guardrails so the CEO can inspect the business filesystem and choose work from evidence.
