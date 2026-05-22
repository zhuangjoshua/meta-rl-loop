# Meta And Sora Add-On

## V0 Scope

Allowed:
- generate Sora video/ad creative through OpenAI if configured
- store media job
- poll media job status
- display creative, copy, audience, and landing URL in Takyon UI

Forbidden in v0:
- create Meta campaigns
- create Meta ad sets
- create Meta ads
- upload videos to Meta
- turn on spend
- pause/modify live Meta objects

## Future Scope

Meta launch can return later behind:
- explicit add-on enablement
- company action policy
- active budget
- approval or automatic policy depending on operator setting
- full vendor receipts

## Verified Implementation Status - 2026-05-19 PT

Implemented:
- The existing internal lane id remains `meta_seedance` for DB/backward compatibility, but v0 execution now uses OpenAI Sora instead of Atlas/Seedance.
- `OPENAI_API_KEY` is the required media secret for this lane.
- Local worker submitted a real OpenAI Sora job for company `bdffff4e-074f-4d3a-ab67-e924e19b9797`.
- Receipt: `media_generation_jobs.provider = openai`, `model = sora-2`, `status = completed`, a real `video_...` provider job id, and proxied `output_url = /api/media-generation/{jobId}/content` were saved.
- No Meta API upload, campaign, ad set, ad, or spend action was performed.

## Verified Implementation Status - 2026-05-20 PT

Implemented:
- Submitted Sora jobs are no longer treated as playable media. The dashboard card shows a pending/sync state when a media row has a provider job id but no output URL.
- The worker now queues a follow-up Sora sync job when OpenAI returns `submitted`, `processing`, `in_progress`, or `queued`.
- Completed Sora sync can queue `observe_campaign_results` as a visible v0 placeholder for later engagement learning.
- For future cached Latexflow projects (`template = latexflow-v1`), the Sora row is not created immediately when the company is made. Build Company queues the `meta_seedance` lane with a roughly 3 minute `run_after`; when that delayed job runs, the worker writes the Sora media row.
- The delayed Latexflow path uses a completed cached Latexflow Sora creative when available. It creates a new company-owned `media_generation_jobs` row pointing at the authenticated Takyon media proxy and records the cached source job id. It still does not upload to Meta, create a campaign, or turn on spend.
- The media proxy supports a row-scoped `public_media_token` for display-only cached creative URLs. Membership auth remains the default path, but a signed cached media URL can play in the dashboard without depending on the viewer's exact Auth0 membership row.

Verified:
- The latest Latexflow media rows initially had OpenAI `sora-2` provider job ids with status `submitted` and no output URL.
- A later sync moved the two latest Latexflow media rows to `completed` with proxied output URLs.
- Cached Latexflow Sora media row `2d49d8bf-5188-45c0-80cd-6862a1543cb1` was patched with a row-scoped media token. Local route verification returned `200` and `video/mp4`.

Reason the video may appear delayed:
- OpenAI Sora video generation is asynchronous. The first API call can return a provider job id before the video asset is ready. Takyon must poll/sync that provider job and only then can display the `/api/media-generation/{jobId}/content` output URL.

Blocked/pending:
- Manual browser playback of the proxied media asset should be rechecked after each deploy because the content route is authenticated.
- The engagement-learning loop after Sora/X is a TODO stub, not implemented analytics.
- If no completed cached Latexflow Sora creative exists, the delayed cached lane must block/fail honestly. It must not silently fall back to a fake media row or a Meta action.
