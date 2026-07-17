# Ad Creative Stack (collected copies)

This folder collects the Meta ads, UGC, and ad-copy skill material in one place
for reading and iteration. These are **copies** — the canonical, runnable
locations are unchanged:

| Here | Canonical (runnable) location |
| --- | --- |
| `takyon-meta-ads-v2/` | `hermes-agent-main/skills/takyon/takyon-meta-ads-v2/` |
| `ugc-video-ad/` | `hermes-agent-main/skills/takyon/ugc-video-ad/` |
| `takyon-lightreel-seedance-fal-ugc/` | `hermes-agent-main/skills/takyon/takyon-lightreel-seedance-fal-ugc/` |
| `ad-copy/` | **No canonical skill exists** (see below) |
| `semantic-gradient.md` | **Pure-prose improvement operator** (no code) — an agent executes it: takes the current policy + ad history + goal, produces one thesis rendered as K policy revisions, smallest to boldest, for the noise schedule to pick from |

**The current policy for the improvement loop is the workspace-root `POLICY.md`**
(`meta-policy-v4`, moved in by the operator 2026-07-15): the five-profile parallel
cold-start batch policy that pins its own version per run, emits an immutable
observation bundle, and explicitly hands interpretation/revision to the external
RL/semantic-gradient system (this loop). An earlier extracted draft
(`policy/meta-ads.md`, hold-at-2.5 doctrine) was retired 2026-07-15 in its favor —
do not resurrect it as a gradient input.

## ad-copy is not a real skill yet

`takyon-meta-ads-v2` references a sibling "ad-copy" skill in three places
(SKILL.md lines 62 and 136, `references/campaign-options.md` line 55, which
expects it to supply `message`, `headline`, `description`), but no such skill
was ever built. In practice the CEO writes Meta ad copy freehand while
drafting `plan.json`. The `ad-copy/` folder here holds the two fragments that
describe copy today:

- `dialogue-action-framework.md` — the Rob Palmer ad-copy framework the UGC
  skill uses for dialogue (copied from `ugc-video-ad/references/`).
- `campaign-options.md` — documents the copy fields Meta launches consume
  (copied from `takyon-meta-ads-v2/references/`).

If a real ad-copy skill is built, follow the parsimonious addition path in
CLAUDE.md: new folder under `hermes-agent-main/skills/takyon/`, exact file
inventory in `skills/release-skills.yaml`, manifest rebuild.

## Why editing here does NOT change runtime behavior

The Takyon runtime never reads this folder. Skills run only when:

1. The skill folder lives at the `source_path` listed in
   `hermes-agent-main/skills/release-skills.yaml`.
2. `python3 scripts/build_approved_skills_manifest.py` regenerates
   `approved-skills.json` (and `--check` passes).
3. Deploy publishes that exact inventory into the content-addressed
   read-only plugin the CEO discovers skills from.
4. The `business_meta_*` / `business_ugc_ad_generate` tools the skills call
   remain implemented in `hermes-agent-main/plugins/takyon/` and granted by
   `hermes-agent-main/skills/sdk-runtime-policy.yaml` — those never move
   with skill prose.

To promote an edit made here: diff it back into the canonical folder, then
run the manifest build + deploy path. To make THIS folder canonical instead,
update each skill's `source_path` in `release-skills.yaml` and rebuild the
manifest — but note CLAUDE.md currently expects skills under
`hermes-agent-main/skills/takyon/`.

Collected 2026-07-15 from the trees as of commit 102707a4.
