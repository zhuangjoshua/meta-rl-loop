---
name: local-computer
description: "Use the user's Mac as a local execution surface through Argon Local Executor primitives."
version: 1.0.0
metadata:
  hermes:
    tags: [local-device, mac, filesystem, shell, browser]
    related_skills: [local-browser, local-files]
---

# Local Computer

Use this skill for multi-step tasks that mention the user's Mac or local
surfaces:

- "my computer", "my Mac", "my Desktop", "Downloads", "Documents", "my files"
- local apps, Finder, screenshots, clicking, typing, or visible windows
- logged-in/private browser sessions such as LinkedIn, Gmail, private
  dashboards, paid accounts, or pages that depend on the user's browser cookies
- local builds, packaging, git, scripts, app folders, or filesystem workflows

Hermes runs on the VPS. Do not use VPS filesystem or VPS terminal tools for
user-local Mac files. Use local tools for the local surface and VPS tools for
cloud/backend/repo work.

## Tool Surfaces

Use these local primitives directly. They execute on the Mac through the Argon
Local Executor; Hermes still owns reasoning, planning, and tool choice.

```
local_shell_exec(root, workdir_relative_path="", command)

local_fs_list(root, workdir_relative_path="", path="")
local_fs_read(root, workdir_relative_path="", path)
local_fs_write(root, workdir_relative_path="", path, content|content_base64)
local_fs_move(root, workdir_relative_path="", source_path, destination_path)
local_fs_delete(root, workdir_relative_path="", path, recursive=false)
```

For browser tasks, use `local-browser` rules:

```
local_managed_browser_*
local_browser_*
```

Supported `root` values are `desktop`, `downloads`, `documents`, and `home`.
For simple root-scoped organization, `local_list_files` and `local_move_files`
are still available. Prefer `local_fs_*` or `local_shell_exec` when the task
needs full shell behavior.

## Workdir

For common Mac folders, use `root` plus an optional `workdir_relative_path`.
Do not invent `/Users/<name>` paths. The Argon Local Executor resolves the
actual active user's Desktop, Downloads, Documents, and home paths on the Mac.

Use absolute `workdir` only when the user explicitly gives an absolute path or
a prior local tool result returned that exact path. If the user points at a
repo/folder or names a local folder and the exact relative path is not known,
inspect or ask.

All `local_fs_*` paths are relative to the resolved workdir. Use
`local_shell_exec` with the resolved workdir as the command current directory.
Successful `local_fs_*` results include resolved Mac paths such as
`absolute_path`, `absolute_source_path`, or `absolute_destination_path`; use
those returned values for final reporting instead of reconstructing a
`/Users/<name>` path.

## Browser Choice

- Public web, no user login needed: use VPS browser tools.
- Credentialed/private web where background work is acceptable: use
  `local_managed_browser_*` after ensuring the user is logged into the
  Argon-managed Chrome profile.
- The user's currently visible Chrome session: use `local_browser_*` only when
  explicitly needed.

Do not claim Safari/Firefox automation parity. If the task needs a logged-in
local browser session in this alpha, ask the user to use Chrome.

## Failure Handling

If a local tool returns `blocked`, `busy`, `failed`, `timed_out`, or a non-zero
`exit_code`, report that directly. Do not mark local work as done unless the
local tool result proves it.

If macOS blocks access, surface the blocked capability and wait for the user to
grant permission or retry. Do not silently fall back to VPS files for local Mac
work.
