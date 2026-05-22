---
name: local-browser
description: "Use Mac Chrome surfaces for local browser tasks without mixing active-browser and managed-profile assumptions."
version: 1.0.0
metadata:
  hermes:
    tags: [local-device, browser, credentialed-web, mac]
    related_skills: [dogfood]
---

# Local Browser

Use this skill when the task depends on the user's real Mac Chrome profile:

- LinkedIn, Gmail, private dashboards, paid accounts, or browser cookies
- "my browser", "this browser", "logged in", "my account"
- private web workflows where VPS Chromium would not have the user's session

Do not export cookies or ask for passwords. Use the narrowest browser surface
that matches the request.

## Browser Surfaces

- `vps_browser`: noninteractive browser on the VPS. Use for public or
  non-credentialed web work. It does not have the user's Mac browser cookies.
- `local_managed_browser_profile`: Argon's managed Chrome/CDP tab using an
  Argon-owned Chrome profile created with `--user-data-dir`. Use
  `local_managed_browser_*` for background/noninteractive browser work after
  the user has logged into that Argon Chrome profile. This surface does not use
  the user's default Chrome profile cookies and does not depend on Chrome's
  "Allow JavaScript from Apple Events" setting.
- `local_active_browser`: the user's visible Google Chrome session on the Mac.
  Use `local_browser_*` only when the user explicitly wants the active visible
  Chrome session, the current visible Chrome tab, or accounts that are only
  logged into the user's default Chrome profile.

If voice/screen context shows Safari or Firefox, treat it as visual context
only. For credentialed browser automation in this alpha, ask the user to open
Chrome and log in there, then retry.

## Tools

The local tools mirror the core Hermes browser workflow, but execute against
the user's real Google Chrome session on the Mac:

```
local_managed_browser_start()
local_managed_browser_navigate(url)
local_managed_browser_snapshot(full=false)
local_managed_browser_click(ref)
local_managed_browser_type(ref, text)
local_managed_browser_scroll(direction)
local_managed_browser_back()
local_managed_browser_screenshot(annotate=false)
local_managed_browser_console(clear=false, expression="")
local_managed_browser_dialog(action, prompt_text="")
local_managed_browser_stop(close_tab=false)

local_browser_navigate(url)
local_browser_snapshot(full=false)
local_browser_click(ref)
local_browser_type(ref, text)
local_browser_scroll(direction)
local_browser_back()
local_browser_press(key)
local_browser_get_images()
local_browser_screenshot(annotate=false)
local_browser_console(clear=false, expression="")
local_browser_dialog(action, prompt_text="", dialog_id="")
local_browser_vision(question, annotate=false)
local_browser_stop()
```

## Workflow

1. For background/noninteractive browser work, start with
   `local_managed_browser_start`, then `local_managed_browser_navigate` or
   `local_managed_browser_snapshot`. If the site needs login, ask the user to
   log in once inside the Argon-managed Chrome profile.
2. `local_browser_navigate` is URL-only for the active visible Chrome tab.
   Call `local_browser_snapshot` separately if the page must be inspected.
3. Use `local_browser_*` only when the user explicitly asked for the active
   visible Chrome tab/session or needs accounts that only exist in their
   default Chrome profile.
4. Use refs from snapshot `elements[].ref` for click and type.
5. Use `local_browser_get_images` when the task depends on page images, product
   images, profile photos, chart screenshots, or image URLs.
6. Use `local_browser_console` for JavaScript errors, failed requests, or DOM
   inspection through a focused expression.
7. Use `local_browser_vision(question, annotate=true)` when layout or visual
   state matters. The returned `annotations` map labels such as `[3]` back to
   refs such as `@e3`.
8. If `local_browser_snapshot.pending_dialogs` is non-empty, call
   `local_browser_dialog(action="accept"|"dismiss", dialog_id=...)`.
   For prompt dialogs, pass `prompt_text`.
9. Stop the local browser session when the workflow is done.

## Failure Handling

If the local browser is busy, offline, missing permissions, or not running, report that directly and do not fall back to VPS browser for credentialed work.

Expected structured failures include:

- `busy`: another Hermes task owns the active-user-browser lease
- `blocked`: the original local job is waiting on `blocked_on` capability
  requirements and may retry once after the Mac reports the capability granted
- `ref_not_found`: refresh `local_browser_snapshot` and retry with a current ref
- `mac.chrome.argon_profile` blocked: Argon's managed Chrome profile did not
  become reachable; report the setup issue directly and do not pretend the
  user's default Chrome profile is controlled.
- missing Chrome: ask the user to open Google Chrome and log in there for credentialed work
- missing Chrome Automation permission: ask the user to grant Argon permission
  in System Settings -> Privacy & Security -> Automation -> Google Chrome
- Chrome JavaScript from Apple Events disabled: active-tab snapshots/clicks/type
  are blocked until the Chrome setting is enabled. Prefer
  `local_managed_browser_*` when background CDP control is acceptable.
- missing Accessibility or Screen Recording permission: ask the user to grant it
