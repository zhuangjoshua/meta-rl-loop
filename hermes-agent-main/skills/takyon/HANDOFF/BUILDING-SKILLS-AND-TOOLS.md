# Takyon Skill And Tool Templates

`SKILL-TEMPLATE.md` and this file are the authoritative Takyon authoring docs. If a future skill or tool needs a different structure or return format, update these documents in the same change before authoring the new skill or tool.

## Skill Template

Return a skill folder in this shape:

```text
hermes-agent-main/skills/takyon/<new-skill>/
  SKILL.md
  references/
  templates/
  scripts/
  assets/
```

Only `SKILL.md` is required. The other folders are optional.

- `references/` = extra docs
- `templates/` = starter files
- `scripts/` = helper programs
- `assets/` = supporting files

Use the real template here:

- [SKILL-TEMPLATE.md](/Users/Zygote/Downloads/takyon/hermes-agent-main/skills/takyon/SKILL-TEMPLATE.md)

When filling that template in, keep `How to Run`, `Procedure`, and `Verification Checklist` operational:

- name the exact tool names used
- say what file or business state to inspect first
- say what to do if the needed state is missing
- say what file, tool result, or receipt proves success
- if the skill needs a new canonical mutation, add the `business_*` tool in the same change; skill authors are also tool authors when necessary

Every Takyon skill should also declare a compact routing contract in frontmatter under `metadata.hermes.routing`:

- `owns` = one sentence naming the business method or truth surface this skill owns
- `when_to_use` = 1-3 concrete trigger bullets
- `do_not_use_for` = nearby work that should route elsewhere

That routing metadata is the source of truth for the dynamic ownership summary injected into Takyon CEO/skills prompts. Do not re-hardcode skill ownership lists in `ceo.md` when the metadata can express it.

## Minimal Guidance

- Start from canonical state. Skills should usually begin with `business_read_business`, then use `business_read_file` or `business_list_files` for the specific business roots they touch instead of guessing from stale artifacts or chat history.
- Keep `metadata.hermes.routing` aligned with the body `## When to Use` section. If they disagree, fix the metadata in the same change.
- If the skill only reads, drafts, or summarizes, it does not need a new tool.
- If the skill changes canonical business or provider state, it must call an existing `business_*` tool or add a new one if none exists.
- If the skill changes evidence behind a cached projection, the normal path is still "use the canonical write/verify tools and receipts." Future skills only need extra reconciliation logic when they introduce a new projection/evidence pair or bypass canonical write tools.
- Only mention test mode when business mode changes a real external side effect.
- Keep the durable outputs in the canonical business roots and prove success with a file, tool result, or receipt.

After editing a skill, start a fresh `./takyon` run or relaunch the shell so bundled Takyon skills sync automatically and Hermes rebuilds the runtime skills index.

## Tool Template

Return:

- one Python handler function for `hermes-agent-main/plugins/takyon/core.py`
- one matching `TAKYON_TOOL_DEFINITIONS` entry in the same file
- focused tests for `hermes-agent-main/tests/plugins/test_takyon_plugin.py`

If a skill needs a new canonical mutation, add the `business_*` tool in the same change. Start by copying the handler template below and keep the `return _commit_tool(...)` line unless the tool truly needs custom logic.

Handler template:

```python
def handle_business_example_write(args: dict, **_: Any) -> str:
    operation = {
        "action": "example.write",
        "business": args.get("business"),
        "scope": args.get("scope") or _business_scope(args),
        "value": args.get("value"),
    }
    return _commit_tool(args, operation, scope=operation["scope"])
```

Definition template:

```python
{
    "name": "business_example_write",
    "description": "Short truthful description of what the tool does.",
    "handler": handle_business_example_write,
    "schema": _schema(
        "business_example_write",
        "One-sentence schema description.",
        {
            "business": _BUSINESS_PROP,
            "value": {"type": "string"},
            "idempotency_key": _IDEMPOTENCY_PROP,
            "reason": _REASON_PROP,
            "actor": _ACTOR_PROP,
        },
        ["business", "value", "idempotency_key"],
    ),
}
```

If a skill uses the tool, mention the tool by name in `## Prerequisites`, `## How to Run`, or `## Procedure`.

Mutating tools should follow these rules:

- Usually copy the handler template shape and keep the `return _commit_tool(...)` line.
- Keep the tool business-scoped and idempotent.
- Add focused tests for the normal path and any real blocked/test variant.
- Prefer extending the core reconciliation rails over inventing skill-local freshness rules. If your new tool writes evidence for an existing projection, make the core rail notice that write; only teach future skills new rules when you create a brand-new projection/evidence pair.
