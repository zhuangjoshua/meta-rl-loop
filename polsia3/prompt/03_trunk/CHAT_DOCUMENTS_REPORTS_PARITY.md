# Chat, Documents, Reports, And Mission Parity

V3 must preserve the v2 operator/CEO communication and document system.

## AI Chat / Inbox

Port v2 semantics:
- `business_inbox_messages` style persisted messages
- operator messages saved with profile id, author label, body, source, created timestamp
- message creation writes an event
- operator can forward a message to the CEO wake loop
- CEO reply is saved back into the inbox
- CEO wake failures are saved as CEO inbox messages instead of disappearing
- dashboard shows recent operator messages, CEO replies, and day reports in one conversational surface

## Verified Implementation Status - 2026-05-20

Implemented:
- The right-side CEO chat no longer only saves a message and waits for a later wakeup. `sendTakyonCeoMessageFromForm` now calls a scoped CEO chat router.
- The router saves the operator message, reads current business state, and returns a CEO answer immediately using the configured AI provider/model.
- The router reads company/site status, documents, workflow jobs, tasks, events, social posts, community targets, leads, media jobs, generated deployments, and cron rows.
- The router can enqueue bounded workflow actions only: website rebuild/improvement, X post lane, Sora creative lane, community research, outreach/lead refresh, and CEO digest.
- The router now treats `/goal get_first_customer` as a deterministic command. It starts or resumes a persistent `get_first_customer` goal, stores the goal as a `business_campaigns` row with `kind = goal`, queues `goal_get_first_customer`, and replies immediately in chat.
- Unsupported `/goal ...` commands are persisted as operator messages and answered honestly; only `get_first_customer` is wired.
- The goal tick writes a `Get First Customer Goal` task-report document and a `goals/get_first_customer` memory record on each pass, so the chat/document system shows what target, offer, channel, blocker, and next workflow actions the goal is currently pursuing.
- Website edit requests are passed as `operator_instruction` into `website_build_deploy`; the local worker forwards that instruction to the Claude Agent SDK surface builder. The chat does not directly edit generated app files.
- Daily digest/chat report requests save a `daily_report` document.
- The CEO prompt explicitly states that `agent_runner` cron is only a pulse and queued workflow jobs execute only when the local Mac worker or replacement worker process is running.
- The dashboard chat composer now auto-expands like a chat input within a bounded height.
- While a CEO answer is pending, the operator message is shown as a normal right-aligned chat message and the pending state is only the word `Thinking`.
- The CEO prompt now requires plain conversational text: no Markdown bold markers, headings, bullet lists, or local-machine implementation leakage in normal operator chat.
- Chat-enqueued actions now create a parent `Run CEO request` task and attach queued workflow jobs to it, so the normal In progress tile can show the requested work instead of making the board look idle.
- Latexflow has a scoped cached CEO answer for descriptive outreach-campaign questions such as `describe your current outreach campaign`. The answer is returned as a normal CEO chat message, does not label itself as cached, does not call the AI provider, and does not queue lead/outreach jobs.

Verified:
- `npm run typecheck`
- `npm run build`
- Direct CEO router invocation against local company `19687d0b-e1d4-4e78-a45c-2d11aa2a2161` queued `community_research`, `outreach_copy`, and `ceo_wakeup`, and saved a `CEO` inbox answer.
- In-app browser reload of that company showed the operator message, the CEO answer, and a `Daily Report 2026-05-20` document on the v2-style dashboard.
- Direct CEO router invocation against Latexflow company `552d6401-632b-4f12-851c-dcf7127867ad` with `describe your current outreach campaign` returned the numbered outreach campaign answer with `queued: []`, `source: ceo_chat`, and no `cached` wording in the operator-visible body.
- Code verification for `/goal get_first_customer`: `npm run typecheck` and `npm run build` passed after adding the slash-command router, goal worker dispatch, `goal` lane type, migration, and skill file.

Still pending:
- Browser typed-form verification of `/goal get_first_customer`.
- Real E2E proof that the goal loop reaches the success condition from a paid Stripe checkout/webhook receipt.
- Browser textarea typing through the in-app browser automation was flaky, so the chat action itself was verified by direct server-side invocation plus browser reload of persisted output rather than a full typed-form submit.
- The queued jobs still require a running local worker. Vercel cron does not execute them.
- The chat can enqueue bounded website improvement jobs, but the actual website edit/deploy still depends on the local worker and Claude Agent SDK build lane completing.
- Existing historical CEO messages may still contain Markdown or old runtime wording because stored inbox rows are not rewritten; the new prompt controls future CEO answers.

V2 reference paths:
- `/Users/Zygote/Downloads/polsia2/src/lib/inbox.ts`
- `/Users/Zygote/Downloads/polsia2/src/app/dashboard/businesses/[businessId]/actions.ts`
- `/Users/Zygote/Downloads/polsia2/src/app/dashboard/businesses/[businessId]/page.tsx`
- `/Users/Zygote/Downloads/polsia2/src/components/takyon/TakyonBusinessWorkspace.tsx`

## Documents

Port v2 document semantics:
- `business_documents` style persisted docs
- document kinds:
  - `mission`
  - `research_report`
  - `daily_report`
  - `task_report`
  - `website_brief`
  - `document`
- document source:
  - `agent`
  - `workflow`
  - `system`
  - `operator`
- upsert by business/title
- document save writes an event
- dashboard and document library separate strategic documents from reports

V2 reference paths:
- `/Users/Zygote/Downloads/polsia2/src/lib/documents.ts`
- `/Users/Zygote/Downloads/polsia2/src/app/documents/page.tsx`
- `/Users/Zygote/Downloads/polsia2/src/app/dashboard/businesses/[businessId]/page.tsx`

## Mission And Research

Port v2 foundation documents:
- `Mission` document with kind `mission`
- `Market Research` document with kind `research_report`
- generated from foundation output
- mission includes:
  - public title
  - one-liner / pitch
  - customer
  - pain
  - offer
  - first workflow
- market research includes:
  - summary
  - buying intent
  - competitors
  - evidence

V2 reference path:
- `/Users/Zygote/Downloads/polsia2/src/lib/agentic-pipeline.ts`

## Daily Reports

Port v2 CEO daily-report behavior:
- CEO wakeup inspects business state, documents, tasks, events, integrations, and prior work
- CEO wakeup writes operator-readable daily brief/report
- report is saved as a `daily_report` business document
- report appears in the chat/report stream and document library
- malformed CEO output falls back to a state-based queue/report instead of failing silently

V2 reference path:
- `/Users/Zygote/Downloads/polsia2/src/lib/ceo-agent.ts`

## Acceptance

The rebuild is not feature-complete until:
- operator can send an inbox/chat message
- message can be forwarded to CEO wake loop
- CEO reply is persisted and visible
- mission and market research documents are created during foundation flow
- daily report is created during CEO wake
- document library shows strategic docs and reports
- all of the above write events or run logs where v2 did
