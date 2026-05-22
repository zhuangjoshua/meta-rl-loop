# Langfuse Observability Plugin

This plugin ships bundled with Takyon but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

```bash
pip install langfuse
takyon plugins enable observability/langfuse
```

Or check the box in the interactive `takyon plugins` UI.

## Required credentials

Set these in `~/.takyon/.env`:

```bash
TAKYON_LANGFUSE_PUBLIC_KEY=pk-lf-...
TAKYON_LANGFUSE_SECRET_KEY=sk-lf-...
TAKYON_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
takyon plugins list                 # observability/langfuse should show "enabled"
takyon chat -q "hello"              # then check Langfuse for a "Takyon turn" trace
```

## Optional tuning

```bash
TAKYON_LANGFUSE_ENV=production       # environment tag
TAKYON_LANGFUSE_RELEASE=v1.0.0       # release tag
TAKYON_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
TAKYON_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
TAKYON_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
takyon plugins disable observability/langfuse
```
