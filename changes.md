# Changes Log

## 2026-06-15

### Session goal
- Run the real operator UI path on `https://app.fourmanifold.com` through the in-app browser for a fresh `Longer` business using the prompt:
  - `Longer - an AI mens sexual penis extension coach. Be BOLD not SHY about penis`
- Monitor bootstrap and product build live.
- If a failure appears, fix it at the generation/runtime source, deploy canonically, and note every code/deploy change here for Claude review.

### Activity log
- Started session and initialized this tracking file.
- Submitted a fresh operator-path business build from the in-app browser and created business `longer-0615152814`.
- Monitored the live VPS `agent.log` / `errors.log` and product-surface receipt path during bootstrap.
- Observed an early runtime verification error during bootstrap: `business_invoke_app_action` returned `app account not found`.
- Observed a long delegated `business_claude_agent_task` run with no new operator-side log lines for several minutes while the Docker worker remained alive and editing only its mounted workspace.
- Confirmed the delegated worker eventually synced canonical source edits but returned `business_claude_agent_task blocked on the worker plane`.
- Confirmed the canonical generated source still shipped starter-shaped output:
  - `src/screens/support.tsx` kept `data-takyon-scaffold="support"` and bundled starter legal/article copy.
  - `src/screens/app-layout.tsx` kept `data-takyon-scaffold="app-layout"`.
  - `src/screens/app-home.tsx` still promised the real dashboard was `being built` / `coming soon`.
- Root cause found: the shared shell-build/product-worker guidance did not explicitly require rewriting the support-route bundle or clearing scaffold sentinels / future-promissory placeholder copy before returning, even though the refresh/publish rail treats those starter leftovers as unfinished.
- Patched the shared generation guidance in:
  - `hermes-agent-main/plugins/takyon/core.py`
  - `hermes-agent-main/skills/takyon/takyon-build-product/SKILL.md`
  - `hermes-agent-main/skills/takyon/takyon-product-workflow/SKILL.md`
- Added a focused contract regression in:
  - `hermes-agent-main/tests/plugins/test_takyon_customer_experience_shape.py`
- Deployed that guidance fix canonically to production and restarted the operator runtime through the tracked deploy rail.
- Started a fresh business rerun `longer-0615155707` after deploy and monitored the live worker temp workspace, logs, and browser activity.
- Confirmed the new guidance text was live on the VPS, but the delegated worker still generated starter support/legal/article content and left `data-takyon-scaffold` markers in `landing.tsx`, `app-layout.tsx`, `app-home.tsx`, `profile.tsx`, and `support.tsx`.
- New root cause found in `business_claude_agent_task`: the delegated worker auto-selected only the shared design-pack skills by default, not the Takyon product-method skills that contain the shell/product workflow rules we had just patched.
- Patched the worker guidance-selection path in `hermes-agent-main/plugins/takyon/core.py` so `product/site` work auto-loads `takyon-build-product`, and explicit deep `/app` workflow instructions auto-load `takyon-product-workflow` too.
- Added regressions in `hermes-agent-main/tests/test_claude_agent_task_defaults.py` covering:
  - default `product/site` guidance now including `takyon-build-product`
  - workflow phrasing adding `takyon-product-workflow`
  - real distilled `takyon-build-product` guidance including the support-route/scaffold-removal rules
- Started another fresh business `longer-0615161956` after deploying the guidance-selection fix and confirmed the delegated worker still rewrote all five customer-visible screen files into scaffold-shaped output.
- New root cause found in `hermes-agent-main/plugins/takyon/core.py::_compose_worker_guidance_block`: auto-loaded non-design guidance skills were still framed as optional “use this if it helps” advice.
- Tightened Takyon-method guidance preamble so `takyon-*` worker skills are treated as required product-method contract guidance, not optional UX suggestions.
- Started a third fresh business `longer-0615163615` after deploying the required-method preamble and still observed the delegated worker writing scaffold-shaped `landing.tsx`, `app-layout.tsx`, `app-home.tsx`, `profile.tsx`, and `support.tsx` into its live temp workspace.
- New root cause found in `hermes-agent-main/plugins/takyon/core.py::build_worker_instruction`: the direct worker instruction itself still let the CEO free-author a narrow "build landing/app screens" task, so the named support/sentinel obligations were only present as background guidance.
- Patched the shared `product/site` worker instruction to prepend an explicit required completion checklist naming the exact screen files and starter phrases that must be rewritten before the worker can return.
