# Skill HANDOFF Guide

HANDOFF is an authoring guide for future skills. It is not loaded by the Claude Agent SDK, is not
an authority boundary, and does not bind tools, paths, modes, receipts, or completion predicates.

## Native skill shape

Create one directory under `skills/creative/<name>/` or `skills/takyon/<name>/`:

```text
<name>/
├── SKILL.md
├── scripts/       # optional
├── references/    # optional
├── templates/     # optional
└── assets/        # optional
```

`SKILL.md` uses standard Agent Skills frontmatter:

```yaml
---
name: lowercase-hyphenated-name
description: What the skill does. State when Claude should use it and when it should not.
---
```

Claude receives every approved skill's `name` and `description` at session start. The description
is the autonomous routing surface. When a request matches, Claude's native `Skill` tool loads the
complete `SKILL.md`; relative supporting files load only when the skill calls for them.

Keep the complete reusable method in the skill: inputs, procedure, tool usage, quality criteria,
verification, failure behavior, references, scripts, templates, and assets. Do not replace detailed
instructions with summaries during a runtime migration.

## Higher-level release and runtime policy

The skill does not choose where it is installed or which invocation mode can expose side effects:

- `skills/release-skills.yaml` owns the approved skill inventory, source directory, version, and
  complete bundle copied into the immutable production plugin.
- `skills/sdk-runtime-policy.yaml` owns exact mode-level tool allowlists and denied write paths.
- Runtime code owns tenant scope, authority, idempotency, money gates, publication, deterministic
  validators, phase order, and completion state.
- `scripts/build_approved_skills_manifest.py` validates and publishes the flat read-only plugin.
- The Agent SDK loads that plugin with `plugins: [{type: "local", path: ...}]` and `skills: "all"`.

Those layers may limit side effects, but they must not rewrite, summarize, or hide approved skills.

## Adding or changing a skill

1. Start from `../SKILL-TEMPLATE.md`.
2. Put all reusable behavior and explicit routing in the native skill bundle.
3. Add the directory and every bundled file to `../release-skills.yaml`.
4. Add or change a runtime tool only when the skill needs a real capability that does not exist.
5. Change `../sdk-runtime-policy.yaml` only when mode-level authority must change.
6. Regenerate and verify the immutable plugin:

```bash
python3 scripts/build_approved_skills_manifest.py
python3 scripts/build_approved_skills_manifest.py --check
```

Never add a second agent, nested worker, per-business skill installation, direct provider-key path,
stub, mock side effect, or prompt-only substitute for a required tool.
