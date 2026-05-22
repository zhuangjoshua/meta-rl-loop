# Community Research Add-On

## User-Facing Behavior

The UI should look like a finished product surface.

It should show:
- real communities
- real source URLs
- why each target matches
- generated launch/community copy

It should not label main UI outputs as "drafts" or "policy-blocked".

## Verified Implementation Status - 2026-05-20 PT

Implemented:
- Community targets remain sourced from real search results and stored with source URLs.
- The dashboard now maps community rows into prepared community post-copy cards rather than duplicating the Leads lane.
- The generated copy is framed as ready community launch copy and does not claim that posting happened.
- Community rows with source URLs are clickable through the prepared post card.

## Backend Policy

No Reddit/community posting in v0.

Tier 1 browse-only sites:
- Reddit
- X/Twitter
- Instagram
- LinkedIn
- TikTok
- Facebook
- ProductHunt
- IndieHackers
- YouTube

Allowed: read/extract public info.

Forbidden: login, create account, post, DM, comment.
