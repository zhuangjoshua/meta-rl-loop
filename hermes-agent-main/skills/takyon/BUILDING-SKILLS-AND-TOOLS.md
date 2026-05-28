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
- say what file, mirror, or receipt proves success

After editing a skill:

```bash
./takyon skills-index
```

## Tool Template

Return:

- one Python handler function for `hermes-agent-main/plugins/takyon/core.py`
- one matching `TAKYON_TOOL_DEFINITIONS` entry in the same file
- focused tests for `hermes-agent-main/tests/plugins/test_takyon_plugin.py`

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
