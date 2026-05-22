---
name: local-files
description: "Inspect and organize user-local Mac folders with move-only local file tools."
version: 1.0.0
metadata:
  hermes:
    tags: [local-device, files, desktop, downloads, documents, mac]
    related_skills: [local-computer]
---

# Local Files

Use this skill when the user asks about simple Mac-local file inspection or
move-only organization:

- "my Desktop"
- "my Downloads"
- "my Documents"
- "my files"
- "clean my Desktop"
- "what files are in my Downloads"
- "tell me the folders in my Documents"
- "move these files into a folder"

Use the dedicated local file tools for simple list/move work. Use the
`local-computer` skill and `local_shell_exec` / `local_fs_*` primitives when the
task needs a concrete workdir, shell commands, building, app packaging,
Finder/app workflows, or multi-file code edits.

## Tools

```
local_list_files(root="desktop", relative_path="", max_entries=200)
local_move_files(root="desktop", sources=[...], destination_root="desktop", destination_relative_path="Folder", apply=false, collision_policy="fail")
```

Supported roots are `desktop`, `downloads`, `documents`, and `home`.
Paths must be relative under those roots. `local_list_files` returns
`entries[].is_directory`; for folder-listing requests, filter that result
instead of invoking broader shell/filesystem tools.

`local_move_files` accepts many `sources` in one call. Prefer one bulk move
job over moving files one at a time.

## Rules

- These tools run on the user's Mac through the Argon Local Executor.
- Do not use VPS filesystem tools for the user's local Mac files.
- Do not delete, trash, unlink, shred, or permanently remove Mac-local files.
- For cleanup, move items into a clearly named folder.
- Run `local_list_files` before moving unless the exact relative paths are already known.
- Use `apply=false` for dry runs when the user has not clearly authorized the move.
- If the tool returns `status: "blocked"` with `blocked_on`, report the required Mac permission and wait for the user/app to grant it. Do not silently fall back to VPS files.
