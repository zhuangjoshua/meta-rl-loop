# Composio Distribution Expansion Plan

Implementation spec for three additions to the Takyon distribution rails:

- **A. X media attachments** — extend the existing X publish rail so posts can carry images/video.
- **B. X recent search** — new read-only `business_x_search` tool for thread/lead discovery.
- **C. Reddit organic channel** — new `reddit` toolkit transport, `business_reddit_publish_outreach` tool, worker job, and a new `takyon-reddit` skill.

All work happens in `hermes-agent-main/` (the active trunk). Read the workspace `CLAUDE.md` first; this plan follows its rules (parsimony, canonical sources, credit rails, skill sync verification). Commit in the **outer** workspace repo.

**Explicitly out of scope:** `TWITTER_GET_POST_ANALYTICS`. The existing metrics sync (`_x_metrics_lookup`, `core.py` ~7240) already requests `public_metrics`, `non_public_metrics`, and `organic_metrics` via `TWITTER_POST_LOOKUP_BY_POST_ID`. Do not add a second analytics path.

**Credential reality:** `COMPOSIO_API_KEY` is currently provisioned nowhere (verified 2026-06-12 on the Mac and the VPS). Every live path must keep failing fast with the existing `ComposioDistributionError("missing COMPOSIO_API_KEY")`. Everything below must be fully testable in business test mode and with monkeypatched `composio_distribution` calls. Do not stub, fake, or soften live behavior to compensate for the missing key.

---

## Shared architecture facts (read before coding)

- **Transport:** `plugins/takyon/composio_distribution.py`. One `_request()` helper; per-toolkit `resolve_*_connected_account_id()` resolvers (explicit env key → user_id env keys → alias match → single-active fallback); `execute_tool()` POSTs `tools/execute/{tool_slug}`; `upload_file_descriptor()` stages local files via `files/upload/request` + presigned PUT and returns `{name, mimetype, s3key}` to pass as a file-typed tool argument.
- **Tool registry:** tools are dicts (`name`, `description`, `handler`, `schema` via `_schema(...)`) in the big list in `plugins/takyon/core.py` (X publish entry at ~27385). Tool names are also listed in `plugins/takyon/plugin.yaml`.
- **Live publish pattern** (mirror of `handle_business_x_publish_outreach`, `core.py:19730`): normalize body → test mode short-circuits to `handle_business_publish_test_outreach` → live mode runs `_creative_credit_preflight_gate` → enqueues a worker job via `_commit_tool` with `operation = {"action": "job.enqueue", "kind": "<channel>.publish_outreach", "worker_queue": True, "worker_max_attempts": 1, ...}`.
- **Worker pattern** (`plugins/takyon/worker.py:1174`, `x_publish_outreach_handler`): reserve credits (`takyon_core._reserve_creative_credits`, reservation key `f"x-publish:{job.id}"`) → call Composio → commit credits on success / release on failure (`finalized` flag in `finally`) → write artifact + receipt via `_record_x_publish_result` (receipt at `metrics/receipts/outreach/<ts>-x-<stem>.json`, plus outbound conversation message + event) → update the work request. Handler registered in `HANDLERS` dict (`worker.py` ~1545) under kind `"x.publish_outreach"`.
- **Credits:** three dicts in `core.py` ~21979: `_CREATIVE_CREDIT_COST_DEFAULTS`, `_CREATIVE_CREDIT_COST_ENVS`, `_CREATIVE_CREDIT_ACTION_DEFAULT_BUCKETS`. This is the only pricing surface — never add a skill-local price.
- **Path containment:** `_safe_relpath(raw, field=...)` for user-supplied relative paths; `store._resolve_business_file(business, rel)` to resolve under the business root. Use both — never trust a raw path.
- **Composio payload unwrapping:** `_composio_tool_unwrap` / `_composio_tool_mapping` (`core.py` ~7174) peel nested `data` envelopes. Use them on every Composio response.
- **Composio argument naming caveat:** Composio flattens nested X API fields (`reply.in_reply_to_tweet_id` → `reply_in_reply_to_tweet_id`). Argument names below marked **[VERIFY]** must be confirmed against the live tool schema (`GET /api/v3.1/tools/{TOOL_SLUG}` with the API key, or from the validation error Composio returns on a wrong key). Where the key cannot be verified before the key exists, code the documented best guess, fail loudly on rejection, and leave a `# [composio-schema]` comment marking the assumption.

---

## Phase A — X media attachments

### A1. Tool schema (`core.py`, `business_x_publish_outreach` entry ~27385)

Add to the schema properties:

```python
"media_paths": {
    "type": "array",
    "items": {"type": "string"},
    "description": "Optional business-relative paths (e.g. product/ads/<slug>/image.png) to attach to the first post segment. Up to 4 images or 1 video.",
},
```

### A2. Enqueue-time validation (`_handle_live_business_x_publish_outreach`, `core.py:19746`)

After body normalization, before the test-mode branch:

1. Normalize `media_paths` to a list of strings; empty list if absent.
2. For each: `rel = _safe_relpath(raw, field="media_paths").as_posix()`, then `abs_path = store._resolve_business_file(business, rel)`; require `abs_path.is_file()` else `TakyonError(f"media file not found: {rel}")`.
3. Classify by suffix: images `{.png, .jpg, .jpeg, .gif, .webp}`, video `{.mp4}`. Reject anything else. Enforce X composition rules: ≤4 images, or exactly 1 video, never mixed.
4. Store the **validated relative paths** in the job payload (`payload["media_paths"] = [...]`) and in `metadata["media_paths"]` so the test-mode receipt records them too (the canonical_args already flow into `handle_business_publish_test_outreach` — just make sure `metadata` is set before the test-mode short-circuit).

### A3. Worker upload + attach (`x_publish_outreach_handler`, `worker.py:1174`)

Before the segment loop (after credit reservation):

```python
media_ids: list[str] = []
media_records: list[dict] = []
for rel in payload.get("media_paths") or []:
    abs_path = takyon_core._store()._resolve_business_file(slug, rel)  # re-validate inside worker
    descriptor = composio_distribution.upload_file_descriptor(
        toolkit_slug="twitter",
        tool_slug="TWITTER_UPLOAD_MEDIA",
        file_path=abs_path,
        timeout=180.0,
    )
    response = composio_distribution.twitter_execute_tool(
        "TWITTER_UPLOAD_MEDIA",
        arguments={"media": descriptor},   # [VERIFY] file argument name on TWITTER_UPLOAD_MEDIA
        timeout=180.0,
    )
    media_id = _extract_x_media_id(response)  # new defensive extractor, see A4
    if not media_id:
        raise RuntimeError(f"X media upload returned no media id for {rel}")
    media_ids.append(media_id)
    media_records.append({"path": rel, "media_id": media_id})
```

Attach to the **first segment only**:

```python
if index == 0 and media_ids:
    arguments["media_media_ids"] = media_ids   # [VERIFY] flattened name on TWITTER_CREATION_OF_A_POST
```

Notes:
- Upload happens **after** credit reservation so a failed upload releases the reservation through the existing failure path (no new exception handling needed — let it raise).
- Mirror `_extract_x_post_id`'s defensive style for `_extract_x_media_id`: unwrap via `takyon_core._composio_tool_mapping`, probe keys `media_id_string`, `media_id`, `id`, including one level under `media`.
- Record `media_records` in the first `thread_posts` entry and pass into `_record_x_publish_result` (A4).

### A4. Receipt (`_record_x_publish_result`, `worker.py:333`)

Add an optional `media: list[dict]` parameter; when non-empty, include `"media": media` in `receipt_payload`. Callers pass `media_records`.

### A5. Skill prose (`skills/takyon/takyon-x/SKILL.md`)

- Quick Reference: note `media_paths` on `business_x_publish_outreach`, with the 4-image/1-video rule.
- Procedure: add a media branch — if the post needs an image/video that does not exist, route upstream to `static-ad-creative-generator` / `ugc-video-ad` first (mirror the routing language in `takyon-reddit-ads`'s frontmatter); never invent placeholder media paths.
- Verification Checklist: receipt at `metrics/receipts/outreach/<ts>-x-<stem>.json` must show the `media` entries when media was requested.
- Bump `version`.

**Sync gotcha:** `takyon-x` is already synced into `$TAKYON_HOME/skills/`, so a repo edit will be **skipped** on relaunch. After merging: `takyon skills reset takyon-x --restore`, relaunch, verify the on-disk copy matches (see Verification section).

### A6. Tests (`tests/plugins/`)

- `test_takyon_plugin.py`: schema includes `media_paths`; enqueue rejects (a) path escaping the business root (`../`), (b) nonexistent file, (c) 5 images, (d) image+video mix; test-mode publish with `media_paths` writes a local receipt recording them and never touches `composio_distribution` (monkeypatch the module functions to raise if called).
- `test_takyon_worker_pg.py`: mirror `test_x_publish_outreach_handler_posts_and_records_receipt` (line ~574) with media — monkeypatch `upload_file_descriptor` + `twitter_execute_tool`; assert upload called once per file, `media_media_ids` present on the first post call only, receipt contains `media`, credits committed. Add a failure case: upload raises → credits released, job fails, no post call made.

---

## Phase B — `business_x_search` (read-only)

### B1. Handler (`core.py`, near the other X handlers)

```python
def handle_business_x_search(args: dict, **_: Any) -> str:
    # business (required), query (required), max_results (10..100, default 10), since_id (optional)
```

- Calls `composio_distribution.twitter_execute_tool("TWITTER_RECENT_SEARCH", arguments={"query": query, "max_results": n, "tweet_fields": ["created_at", "public_metrics", "author_id"]}, timeout=60.0)`. Pass `since_id` only when provided. **[VERIFY]** `max_results`/`since_id` argument names.
- Unwrap with `_composio_tool_mapping` / handle list payloads (`{"items": [...]}` shape from `_parse_jsonish_output` is not relevant here; Composio returns the X API envelope — expect tweets under `data` once unwrapped, plus `meta`).
- Persist a truthful snapshot via the `_commit_tool` artifact pattern used by the metrics sync: write `metrics/x/search/<UTC-ts>-<idempotency>.json` containing `{query, requested_max_results, result_count, newest_id, oldest_id, tweets: [...]}` and return the snapshot path + a compact tweet list in the tool result.
- **No credit gate** — this is a provider read, not a spendful creative action. **No test/live fork** — but in test mode, do not call the provider: return a hard `TakyonError("business_x_search requires live mode; test mode has no provider reads")`? **No** — keep it simpler and truthful: allow the call in any mode (it has no side effects), but it will naturally fail fast when `COMPOSIO_API_KEY` is missing. Do not add a stub result path.

### B2. Registration

- Tool entry in the `core.py` registry list: name `business_x_search`, schema props `business`, `query` (required), `max_results`, `since_id`, `idempotency_key`, `reason`, `actor`; required `["business", "query", "idempotency_key"]`.
- Add `business_x_search` to `plugins/takyon/plugin.yaml` next to the other `business_x_*` tools.

### B3. Skill prose

- `takyon-x/SKILL.md`: add `business_x_search` to Quick Reference tools and a short Procedure note ("find threads worth replying to before drafting; record chosen threads as conversation messages"). `takyon-x` is the owner; do not duplicate routing into other skills. Add at most a one-line related mention in `takyon-conversation-followup` only if its body already enumerates discovery options (inspect first — if not, leave it alone).

### B4. Tests

- `test_takyon_plugin.py`: schema present; missing `query` rejected; `max_results` clamped; happy path with monkeypatched `twitter_execute_tool` writes the snapshot file under `metrics/x/search/` and returns tweet summaries; Composio failure surfaces as a tool error (no silent empty result).

---

## Phase C — Reddit organic channel

### C1. Transport (`plugins/takyon/composio_distribution.py`)

Add, mirroring the twitter trio exactly:

```python
_REDDIT_ORGANIC_TOOLKIT_SLUG = "reddit"
_REDDIT_ORGANIC_DEFAULT_USER_ID = "takyon_prod_operator"
_REDDIT_ORGANIC_DEFAULT_ALIAS = "takyon-prod-reddit"

def resolve_reddit_organic_connected_account_id() -> str:
    return _resolve_connected_account_id(
        toolkit_slug=_REDDIT_ORGANIC_TOOLKIT_SLUG,
        explicit_env_key="COMPOSIO_REDDIT_CONNECTED_ACCOUNT_ID",
        user_id_env_keys=("COMPOSIO_REDDIT_USER_ID", "COMPOSIO_USER_ID"),
        alias_env_key="COMPOSIO_REDDIT_ALIAS",
        default_user_id=_REDDIT_ORGANIC_DEFAULT_USER_ID,
        default_alias=_REDDIT_ORGANIC_DEFAULT_ALIAS,
    )

def reddit_execute_tool(tool_slug, *, arguments=None, connected_account_id=None, timeout=120.0):
    # mirror twitter_execute_tool, resolving via resolve_reddit_organic_connected_account_id()
```

Naming: keep `reddit_organic` in the resolver name so it cannot be confused with `resolve_reddit_ads_connected_account_id` (`reddit_ads` toolkit).

### C2. Credits (`core.py` ~21979)

```python
_CREATIVE_CREDIT_COST_DEFAULTS["reddit_publish_outreach"] = 1
_CREATIVE_CREDIT_COST_ENVS["reddit_publish_outreach"] = "TAKYON_CREATIVE_CREDITS_REDDIT_POST"
_CREATIVE_CREDIT_ACTION_DEFAULT_BUCKETS["reddit_publish_outreach"] = "reddit"
```

Bucket `"reddit"` is intentionally shared with `reddit_ad_launch` — it is the channel bucket, same as `"x"` covers all X actions. Do not invent a separate bucket.

### C3. Tool handler (`core.py`, mirror the X publish pair)

`handle_business_reddit_publish_outreach` + `_handle_live_business_reddit_publish_outreach`:

**Args:** `business` (req), `subreddit` (req for posts; normalize by stripping a leading `r/`), `title` (req for posts), `body` (text-post body / comment text), `url` (link-post URL), `post_kind` (`"self"` | `"link"`, default `"self"`; `link` requires `url`, `self` requires `body`), `thread_external_id` (Reddit fullname `t3_...`/`t1_...` — when present this is a **comment**: `body` required, `subreddit`/`title` optional), `metadata`, `idempotency_key` (req), `reason`, `actor`.

**Flow (copy the X handler's shape):**
1. Normalize body through `_normalize_outreach_body`; run `_canonicalize_business_product_links` on it (same as X — product links must resolve to the canonical product URL).
2. Test mode → `handle_business_publish_test_outreach` with `channel="reddit"`, `provider="reddit"`, `destination_url` = `https://www.reddit.com/r/<subreddit>/` (or the parent fullname noted in metadata for comments).
3. Live mode → `_creative_credit_preflight_gate(business, action="reddit_publish_outreach", budget_bucket="reddit", metadata=...)`; on insufficient credits return the same structured "not enqueued" result the X handler produces.
4. Enqueue: `kind="reddit.publish_outreach"`, `worker_queue=True`, `worker_max_attempts=1`, payload carrying `subreddit`, `title`, `body`, `url`, `post_kind`, `thread_external_id`, `metadata`, `requires_api=["reddit"]`.

**Validation hard-fails:** missing subreddit+title for a post; missing body for self-post/comment; missing url for link post; `thread_external_id` not matching `^t[13]_`.

### C4. Worker handler (`worker.py`, mirror `x_publish_outreach_handler`)

`reddit_publish_outreach_handler(job)`:

1. Reserve credits: action `reddit_publish_outreach`, bucket `reddit`, reservation key `f"reddit-publish:{job.id}"`; same `InsufficientCreativeCredits` / `CreativeCreditBudgetExceeded` handling and work-request updates as the X handler.
2. Execute:
   - Comment (`thread_external_id` present): `composio_distribution.reddit_execute_tool("REDDIT_POST_REDDIT_COMMENT", arguments={"thing_id": thread_external_id, "text": body})` **[VERIFY]** argument names.
   - Post: `reddit_execute_tool("REDDIT_CREATE_REDDIT_POST", arguments={"subreddit": subreddit, "title": title, "kind": post_kind, ...})` with `"text": body` for self posts or `"url": url` for link posts **[VERIFY]** argument names.
3. Extract id + permalink defensively (`_extract_reddit_post_ref(response)` — unwrap with `takyon_core._composio_tool_mapping`, probe `json.data.id`, `json.data.url`, `json.data.permalink`, `id`, `name`, `url`; Reddit API responses are messy — return `{"post_id": ..., "post_url": ...}` with best effort, never fabricate).
4. Commit credits on success / release on failure with the same `finalized` flag pattern.
5. `_record_reddit_publish_result` mirroring `_record_x_publish_result`: artifact under `distribution/local-published/`, receipt `metrics/receipts/outreach/<UTC-ts>-reddit-<stem>.json` with `channel="reddit"`, `provider="reddit"`, `subreddit`, `post_id`, `post_url`, `post_kind`, credits metadata; outbound conversation message; `business_record_event`-equivalent event (`reason="worker recorded live Reddit publish receipt"`).
6. Register: `HANDLERS["reddit.publish_outreach"] = reddit_publish_outreach_handler`.

### C5. Registration

- Tool entry in the `core.py` registry; description must state the hard-fail posture (mirror the X tool's wording: "Missing credentials or budget gates hard-fail instead of falling back to local suppressed publication.").
- Add `business_reddit_publish_outreach` to `plugin.yaml`.

### C6. New skill `skills/takyon/takyon-reddit/SKILL.md`

Start from `skills/takyon/SKILL-TEMPLATE.md`; keep the canonical section order (`Overview`, `When to Use`, `Quick Reference`, `Prerequisites`, `How to Run`, `Procedure`, `Output Format`, `Publication`, `Common Pitfalls`, `Verification Checklist`, `Rules`, `Troubleshooting`). Frontmatter must be valid YAML.

Frontmatter sketch:

```yaml
name: takyon-reddit
description: Honest organic Reddit participation for one Takyon business — subreddit-fit posts, comments, and durable Reddit voice. Not for paid Reddit ads.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, reddit, distribution, organic, community, posts, comments]
    related_skills: [takyon-reddit-ads, takyon-distribution, takyon-x, takyon-conversation-followup, takyon-business-metrics]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_read_business,
        business_calculate_pulse,
        business_reddit_publish_outreach,
        business_record_conversation_message,
        business_update_conversation_message_status,
      ]
    routing:
      owns: organic Reddit posts, comments, subreddit selection, and durable Reddit voice
      when_to_use:
        - the business needs an organic subreddit post or comment
        - a wake or campaign turn is clearly Reddit-shaped and not a paid ad
      do_not_use_for:
        - paid Reddit ads or promoted posts (use takyon-reddit-ads)
        - astroturfing, vote manipulation, or undisclosed promotion
  takyon:
    scope: business
    allowed_roots: [distribution, metrics, research]
    output_root: distribution
    publication:
      - distribution/voice/reddit.md
      - distribution/campaign
      - distribution/local-published
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
```

Body requirements (keep the rich Hermes operational detail — name exact tools, files-checked-first, branch points, receipts):
- **Judgment core:** subreddit fit and rules come first — read the target subreddit's posting norms (self-promo policy, flair requirements) before drafting; default to genuinely useful content with disclosure, never disguised ads. One business voice file at `distribution/voice/reddit.md` (mirror the `takyon-x` voice pattern).
- **Procedure branches:** test mode → `business_reddit_publish_outreach` routes to suppressed local publication under `distribution/local-published/` (verify the receipt); live mode → credit gate (1 credit, bucket `reddit`), worker job, receipt at `metrics/receipts/outreach/<ts>-reddit-<...>.json`.
- **Comment flow:** replying to an existing thread uses `thread_external_id` (`t3_`/`t1_` fullname) and records the exchange via `business_record_conversation_message`.
- **Verification Checklist:** exact receipt paths, `business_read_file` checks, and the no-fabrication rule (a post without a receipt did not happen).

### C7. Sibling skill touch-ups (minimal)

- `takyon-reddit-ads/SKILL.md` frontmatter: add `takyon-reddit` to `related_skills`; add a `do_not_use_for` line: "organic subreddit posts or comments (use takyon-reddit)". Bump version. (Same `--restore` sync gotcha as `takyon-x`.)
- `takyon-distribution/SKILL.md`: only if its body enumerates channels explicitly — inspect; if it routes generically through the skills index, change nothing.

### C8. Docs

`plugins/takyon/API_REQUIREMENTS.md`, next to the existing Composio entries:

```
- Reddit organic (skill/tool requirement): `COMPOSIO_API_KEY` plus one active Composio Reddit connected
  account on the `reddit` toolkit (`COMPOSIO_REDDIT_CONNECTED_ACCOUNT_ID`, or a resolvable
  `COMPOSIO_REDDIT_USER_ID` / `COMPOSIO_REDDIT_ALIAS` pair; default alias `takyon-prod-reddit`).
  Composio managed auth is available for this toolkit — no Reddit OAuth app registration required.
```

### C9. Tests

- `test_takyon_plugin.py`:
  - Tool schema present (and in `plugin.yaml` — there may be an existing schema/yaml parity test; if so it covers this).
  - Validation: missing subreddit/title → error; link post without url → error; bad `thread_external_id` → error; `r/foo` normalized to `foo`.
  - Test mode end-to-end: business in test mode → suppressed local receipt under `distribution/local-published/`, **zero** Composio calls (monkeypatch `composio_distribution.reddit_execute_tool` to raise).
  - Live mode without credits → structured not-enqueued result (mirror the X credit-gate test if one exists; check `test_takyon_store_pg.py` too).
- `test_takyon_worker_pg.py` (mirror lines ~194/574/665):
  - `test_handlers_registry_maps_reddit_publish_outreach`.
  - Happy path: monkeypatched `reddit_execute_tool` returns a Reddit-shaped payload → receipt written with `post_id`/`post_url`, credits committed, work request completed.
  - Failure path: provider call raises → credits released, work request failed, no receipt.
  - Comment path: `thread_external_id` present → `REDDIT_POST_REDDIT_COMMENT` called with the fullname, not `REDDIT_CREATE_REDDIT_POST`.
- Transport unit tests (add to wherever the worker tests monkeypatch today — there is no dedicated composio test file; a small new `tests/plugins/test_takyon_composio_distribution.py` is acceptable): `resolve_reddit_organic_connected_account_id` env-override precedence and the multi-account/alias error messages, with `_request` monkeypatched.

---

## Order of implementation

1. Phase C transport (C1) — smallest, unblocks worker tests.
2. Phase A (media) end-to-end, tests green.
3. Phase C tool + worker + skill (C2–C9), tests green.
4. Phase B (`business_x_search`), tests green.
5. Skill sync + docs + full verification.

## Verification (do all of it; do not skip the sync checks)

1. `scripts/run_tests.sh tests/plugins/ -q` (CI-parity wrapper — never bare pytest). Also run any Postgres-gated suites touched (`test_takyon_worker_pg.py`, `test_takyon_store_pg.py`) — see the local PG rig notes if they skip without a DB.
2. `git diff --check`.
3. Skill sync (per workspace CLAUDE.md step 7):
   - New skill: relaunch `./takyon`, then confirm `$TAKYON_HOME/skills/takyon/takyon-reddit/SKILL.md` exists and matches the repo file, `takyon skills list` shows it, and no "user-modified, skipping" warning fired.
   - Edited skills (`takyon-x`, `takyon-reddit-ads`): these WILL be skipped by the conservative sync. Run `takyon skills reset takyon-x --restore` and `takyon skills reset takyon-reddit-ads --restore`, relaunch, then diff the on-disk copies against the repo.
4. Shell E2E in test mode (temp `TAKYON_HOME`, real shell path): `/create` a test business, ask the CEO for a Reddit post, confirm the suppressed receipt lands under `distribution/local-published/` and `/files` shows it; same for an X post with a generated image (route through the static-ad skill).
5. Live-path sanity without credentials: a live-mode publish attempt must fail with the exact `missing COMPOSIO_API_KEY` / "no active Composio reddit connected account" gate error in the receipt/work request — not a silent skip, not a fake success.
6. Deploy via the fast path: commit in the outer repo, `git push origin main`, then `gh run list --repo tejdiv/takyon-workspace --branch main --limit 5` and `gh run watch <run-id> --exit-status`. No manual VPS rsync unless the workflow fails.

## Rails — do not violate

- No second pricing table, no skill-local ledger: credits only through the three `_CREATIVE_CREDIT_*` dicts and the gate/reserve/commit/release helpers.
- No deterministic channel router: the CEO discovers `takyon-reddit` through the skills index and tool schemas. Do not add "if reddit then…" routing to prompts or shell code.
- No parallel test flags: test/live forks only via `businesses.mode` through the existing `handle_business_publish_test_outreach` short-circuit.
- No placeholder media, posts, or post IDs anywhere — missing inputs route upstream or hard-fail with the exact gate error.
- Tool descriptions/schemas are the discovery surface — make the hard-fail posture and credit cost visible there, not in UI copy.
- The CEO prompt (`prompts/ceo.md`) does **not** change — nothing here alters general policy or safety contracts.
