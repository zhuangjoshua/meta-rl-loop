# Skill HANDOFF Policy

Skills own reusable domain methods: routing, inputs, procedure, quality criteria, verification, and
failure conditions. A skill may declare semantic capabilities and outputs in `contract.yaml`.

HANDOFF owns every Takyon-specific binding: exact tools, workspace roots, artifact paths, invocation
modes, authority, publication, idempotency-key classes, required receipt kinds, and validator IDs.
Runtime code owns phase order, key derivation, atomic effects, validator implementations, and
completion transitions; prompt compliance and editable HANDOFF data are not authority boundaries.

All approved skills are installed and discoverable in every session. `allowed_modes` controls native
selection, while `mode_tool_policy` controls authority independently: the generated manifest contains
an exact `allowed_tools` allowlist for each mode, compiled from a reviewed baseline plus the semantic
capabilities required by skills allowed in that mode. Runtime must fail closed if a listed tool is
missing, the model-tool inventory digest drifts, or a schema outside the allowlist would be exposed.
Generic file adapters must also enforce canonical `denied_write_paths`; normalize `.` and repeated
slashes and reject absolute paths, `..`, and backslashes before checking prefixes.

To add or change a production skill:

1. Start from `../SKILL-TEMPLATE.md` and keep the skill provider- and deployment-agnostic.
2. Add `contract.yaml` with semantic `requires` and `produces` identifiers.
3. Add exact capability, artifact, and mode bindings to `bindings.yaml`.
4. Add the reviewed source and an exact non-executable `publish_files` allowlist to
   `../release-skills.yaml`.
5. Run `python3 scripts/build_approved_skills_manifest.py`, then run it again with `--check`.

The SDK never loads the writable source tree as its production plugin. Activation publishes an
immutable flat copy outside the repository:

```text
python3 scripts/build_approved_skills_manifest.py --check \
  --publish-root "$TAKYON_HOME/runtime/claude-agent-sdk/releases/<release-id>/skills"
```

The destination is the plugin root passed to the SDK. It contains
`.claude-plugin/plugin.json`, `approved-skills.json`, and exactly
the per-skill files listed in each manifest entry's plugin-relative `publish_files`. The publisher
rejects executable resources, provider-key/runtime bindings in published text, extra files or skills,
and digest drift; it atomically activates a new destination and sets files to `0444` and directories
to `0555`. Legacy helper scripts that can read keys or call providers directly are not copied into the
SDK plugin; live work remains behind bound guarded capabilities. A caller must use a versioned new
destination; an existing destination is accepted only when its manifest, files, digests, and
permissions already match exactly.

Changing a tool, path, provider, authority rail, or publication target changes HANDOFF, not the
skill's method.
