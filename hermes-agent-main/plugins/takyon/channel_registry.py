"""Channel publisher registry — the single source of truth for outreach-publish channels.

§6b item 1 of the modularization plan (ChannelPublisher registry). X and Reddit are
structurally identical outreach-publish handlers: both run the same creative-credit
reserve → publish → commit/release money envelope, differing only in

  * a few descriptor scalars (credit action, budget bucket, Composio toolkit, the
    per-channel published-artifact subdirectory), and
  * the channel-specific publish body + the metadata each stamps on its reservation.

Before this module those facts were smeared across ~9 files: two ~490/~265-line worker
handlers duplicating the money envelope verbatim, four parallel per-action dicts in
``core.py`` (credit cost / cost-env / bucket / audience keyed by action), the Composio
alias rows repeated per channel in ``core._API_ENV_ALIASES``, and the ``_is_x_provider_name``
style name predicates. This module makes ONE ``ChannelPublisher`` descriptor per channel the
source those surfaces read from, so adding a third channel is one descriptor, not a fork.

HARD INVARIANT: this is a pure extraction. Every value here is byte-faithful to the
pre-extraction constants (verified against the live handlers + core dicts). The
creative-credit reserve → commit/release semantics are unchanged — the money envelope
itself lives in ``worker.channel_publish_outreach_handler`` and was lifted verbatim from the
two handlers; this module only supplies the descriptor data + the channel-specific callables
that the generic envelope invokes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


# ── the publish context + outcome carried across the generic money envelope ──────────────────────


@dataclass
class PublishContext:
    """Everything a channel's publish body needs, plus the reservation handle the envelope owns.

    The generic handler builds this once (after a successful reserve) and hands it to the
    channel's ``publish`` callable. ``reservation`` / ``credit_result`` are threaded back so a
    channel body can seed the durable marker / receipt fields exactly as the original handlers did.
    """

    job: Any
    slug: str
    payload: Mapping[str, Any]
    body: str
    work_request_id: str
    reservation_key: str
    reservation: dict[str, Any] | None = None
    # Partial-progress outcome the publish body updates AS side effects ship, so the money envelope's
    # failure path can commit-partial (never refund a shipped post) even when publish() raises before
    # returning. This preserves the original handlers' ``if thread_posts:`` / ``if post_id:`` semantics.
    partial: "PublishOutcome | None" = None


@dataclass
class PublishOutcome:
    """The result of a channel publish body — what the money envelope commits + records against.

    ``posted`` is the channel's own "did a real side effect ship?" signal (X: any thread post;
    Reddit: a post id) that the envelope's failure path uses to decide commit-partial vs release.
    """

    post_id: str = ""
    post_url: str = ""
    provider_response: dict[str, Any] = field(default_factory=dict)
    media: list[dict[str, Any]] = field(default_factory=list)
    # Channel-specific side-channel state (X thread_posts) the receipt writer + failure metadata read.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def posted(self) -> bool:
        return bool(self.post_id) or bool(self.extra.get("thread_posts"))


# ── the frozen descriptor ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChannelPublisher:
    """A frozen descriptor for one outreach-publish channel.

    Data fields (byte-faithful to the pre-extraction constants) plus the small per-channel callables
    the generic money envelope needs. See ``CHANNEL_REGISTRY`` for the x/reddit instances.
    """

    slug: str
    # Composio name spellings this channel answers to (was ``_is_x_provider_name`` / the reddit
    # ``_file_slug in {"reddit"}`` predicate, and the repeated ``_API_ENV_ALIASES`` composio rows).
    aliases: tuple[str, ...]
    # Creative-credit action key (was the key in all four core per-action dicts).
    credit_action: str
    # Composio toolkit slug (the organic publish toolkit, matching composio_distribution).
    toolkit_slug: str
    # Default creative-credit bucket for this action (was ``_CREATIVE_CREDIT_ACTION_DEFAULT_BUCKETS``).
    budget_bucket: str
    # Safebox creative-gate audience (was ``_CREATIVE_CREDIT_ACTION_AUDIENCES``).
    credit_audience: str
    # Fixed unit credit cost (was ``_CREATIVE_CREDIT_COST_DEFAULTS``).
    credit_cost_default: int
    # Env override name for the credit cost (was ``_CREATIVE_CREDIT_COST_ENVS``).
    credit_cost_env: str
    # Where live receipts land: the published-artifact subdir the receipt writer uses. MUST match
    # the SKILL.md ``publication`` frontmatter (which lists the parent ``distribution/local-published``);
    # this is the channel-scoped leaf under it. ONE source of truth here — the receipt writer reads it.
    publication_root: str
    # Composio env-alias provider spellings this channel's key resolves under (all ``COMPOSIO_API_KEY``).
    # Each entry becomes an ``_API_ENV_ALIASES`` row so the denylist/require-api surfaces stay whole.
    env_alias_names: tuple[str, ...]
    # The kind string HANDLERS registers this channel under (``x.publish_outreach``).
    job_kind: str

    # ── channel-specific callables (unique steps live behind these, never a forked handler) ──

    # The publish body: reserve is already done; do the provider side effects, return a PublishOutcome.
    publish: Callable[[PublishContext], PublishOutcome]
    # Build the reservation metadata dict (channel-specific keys: X thread_external_id; Reddit + subreddit).
    reservation_metadata: Callable[[PublishContext], dict[str, Any]]
    # Build the commit metadata dict on the SUCCESS path.
    commit_metadata: Callable[[PublishContext, PublishOutcome], dict[str, Any]]
    # Build the commit metadata dict on the PARTIAL-FAILED path (posted something, then failed).
    partial_failed_metadata: Callable[[PublishContext, PublishOutcome, Exception], dict[str, Any]]
    # Build the release metadata dict on the FULL-FAILED path (nothing posted).
    release_metadata: Callable[[PublishContext, Exception], dict[str, Any]]
    # Record the durable receipt/artifacts (calls the channel's worker-module receipt writer).
    record_result: Callable[..., dict[str, str]]
    # Fully-posted-replay preamble: return a terminal ``JobRunResult`` when this exact job already
    # shipped AND committed on a prior attempt (X's durable posted-marker), so a retry re-derives the
    # result + re-writes the receipt WITHOUT re-posting/re-charging; return None to run normally.
    # Reddit has no marker, so its hook always returns None (one skeleton, no fork).
    replay_if_complete: Callable[[PublishContext], Any]
    # Resolve the canonical ``post_url`` on the SUCCESS path AFTER commit, running any channel-only
    # post-commit side effects (X: whoami lookup + terminal committed marker before the receipt write;
    # Reddit: just returns the url publish already resolved).
    finalize_post_url: Callable[[PublishContext, "PublishOutcome"], str]
    # Test-mode local-suppressed publish root (shared ``business_publish_test_outreach`` writes here,
    # routed by channel name). Held so a channel's suppressed path is a descriptor value, not a literal.
    test_local_publish_root: str


# ── X channel ────────────────────────────────────────────────────────────────────────────────────


def _x_reservation_metadata(ctx: PublishContext) -> dict[str, Any]:
    reply_to = str(ctx.payload.get("thread_external_id") or "").strip()
    return {
        "business": ctx.slug,
        "action": "x_publish_outreach",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "x",
        "provider": "x",
        "thread_external_id": reply_to or None,
    }


def _x_commit_metadata(ctx: PublishContext, outcome: PublishOutcome) -> dict[str, Any]:
    thread_posts = outcome.extra.get("thread_posts") or []
    return {
        "business": ctx.slug,
        "action": "x_publish_outreach",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "x",
        "provider": "x",
        "post_id": outcome.post_id,
        "thread_post_count": len(thread_posts),
    }


def _x_partial_failed_metadata(ctx: PublishContext, outcome: PublishOutcome, exc: Exception) -> dict[str, Any]:
    thread_posts = outcome.extra.get("thread_posts") or []
    return {
        "business": ctx.slug,
        "action": "x_publish_outreach",
        "status": "partial_failed",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "x",
        "provider": "x",
        "post_id": outcome.post_id or None,
        "thread_post_count": len(thread_posts),
        "thread_posts": thread_posts,
        "error": str(exc),
    }


def _x_release_metadata(ctx: PublishContext, exc: Exception) -> dict[str, Any]:
    return {
        "business": ctx.slug,
        "action": "x_publish_outreach",
        "status": "failed",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "x",
        "provider": "x",
        "error": str(exc),
    }


def _x_publish(ctx: PublishContext) -> PublishOutcome:
    """The X publish body — media upload → thread segmentation → link reply, with the durable
    posted-marker idempotency the original handler had. Provider/marker/record calls resolve through
    the ``worker`` module so the tests' ``monkeypatch.setattr(worker, ...)`` still applies."""
    from . import core as takyon_core
    from . import worker as _worker

    composio_distribution = _worker.composio_distribution
    job = ctx.job
    slug = ctx.slug
    payload = ctx.payload
    body = ctx.body
    reply_to = str(payload.get("thread_external_id") or "").strip()

    # Re-entry after a partial publish: resume from the durable marker so already-posted segments
    # are NOT re-tweeted. (Full-posted replay is handled by the generic envelope's replay preamble.)
    posted_marker = _worker._read_x_posted_marker(slug, str(job.id))
    already_posted_segments: list[dict[str, Any]] = []
    if isinstance(posted_marker, dict):
        raw_segments = posted_marker.get("thread_posts")
        if isinstance(raw_segments, list):
            already_posted_segments = [seg for seg in raw_segments if isinstance(seg, dict)]

    post_id = ""
    thread_posts: list[dict[str, Any]] = []
    if already_posted_segments:
        thread_posts = [dict(seg) for seg in already_posted_segments]
        post_id = str(posted_marker.get("post_id") or "").strip()
        provider_response_seed = posted_marker.get("provider_response")
        provider_response_resume = dict(provider_response_seed) if isinstance(provider_response_seed, dict) else {}
    else:
        provider_response_resume = {}
    posted_index_to_id = {
        int(seg.get("index")): str(seg.get("post_id") or "")
        for seg in thread_posts
        if isinstance(seg.get("index"), int)
    }

    # Publish into a shared partial outcome so the envelope's failure path sees the shipped segments
    # (commit-partial) even if a later provider call raises. ``thread_posts``/``media_records`` below
    # are the SAME list objects held on ``partial``, so appends are visible without re-assignment.
    partial = PublishOutcome(post_id=post_id, extra={"thread_posts": thread_posts})
    ctx.partial = partial

    segments = _worker._split_x_thread_segments(body)
    if not segments:
        raise RuntimeError("x publish job is missing a body")
    provider_response: dict[str, Any] = provider_response_resume
    media_ids: list[str] = []
    media_records: list[dict[str, Any]] = []
    for raw_rel in payload.get("media_paths") or []:
        rel = takyon_core._safe_relpath(str(raw_rel or ""), field="media_paths").as_posix()
        abs_path = takyon_core._store()._resolve_business_file(slug, rel)
        if not abs_path.is_file():
            raise RuntimeError(f"media file not found: {rel}")
        descriptor = composio_distribution.upload_file_descriptor(
            toolkit_slug="twitter",
            tool_slug="TWITTER_UPLOAD_MEDIA",
            file_path=abs_path,
            timeout=180.0,
        )
        response = composio_distribution.twitter_execute_tool(
            "TWITTER_UPLOAD_MEDIA",
            arguments={
                # [composio-schema] Confirm TWITTER_UPLOAD_MEDIA uses the media file argument name "media".
                "media": descriptor,
            },
            timeout=180.0,
        )
        media_id = _worker._extract_x_media_id(response)
        if not media_id:
            raise RuntimeError(f"X media upload returned no media id for {rel}")
        media_ids.append(media_id)
        media_records.append({"path": rel, "media_id": media_id})
    partial.media = media_records
    current_reply_to = reply_to
    for index, segment in enumerate(segments):
        # Idempotency: if this segment already shipped on a prior attempt, do not re-tweet it.
        if index in posted_index_to_id:
            current_reply_to = posted_index_to_id[index] or current_reply_to
            continue
        arguments: dict[str, Any] = {"text": segment}
        if current_reply_to:
            arguments["reply_in_reply_to_tweet_id"] = current_reply_to
        if index == 0 and media_ids:
            # [composio-schema] Confirm TWITTER_CREATION_OF_A_POST uses the flattened media_media_ids argument.
            arguments["media_media_ids"] = list(media_ids)
        response = composio_distribution.twitter_execute_tool(
            "TWITTER_CREATION_OF_A_POST",
            arguments=arguments,
            timeout=120.0,
        )
        current_post_id = _worker._extract_x_post_id(response) or (post_id if post_id else str(job.id))
        if not post_id:
            post_id = current_post_id
            provider_response = dict(response)
            partial.post_id = post_id
        thread_posts.append(
            {
                "index": index,
                "post_id": current_post_id,
                "body": segment,
                "reply_to": current_reply_to,
                "media": list(media_records) if index == 0 and media_records else [],
                "provider_response": dict(response),
            }
        )
        posted_index_to_id[index] = current_post_id
        current_reply_to = current_post_id
        # Durably mark each shipped segment immediately so a crash/retry between this
        # tweet and the credit commit cannot re-post it.
        _worker._write_x_posted_marker(
            slug,
            str(job.id),
            {
                "job_id": str(job.id),
                "post_id": post_id,
                "thread_posts": thread_posts,
                "media": media_records,
                "provider_response": provider_response,
                "credits_committed": False,
            },
        )
    # Acquisition rail (takyon-x skill contract: "no link in the tweet body — the link goes
    # in a reply"). Nothing used to post that reply, so destination_url only ever reached the
    # receipt and never the timeline — an X post with no path to the product. Post the link
    # now as one reply to the thread tail. It rides the SAME creative-credit reservation as
    # the thread (one outreach action = its segments + its link reply), so it is not a
    # separately gated paid call. Idempotent across retries via the durable posted-marker
    # (the appended entry carries kind="link" so a resume never re-posts it).
    destination_url = str(payload.get("destination_url") or "").strip()
    link_already_posted = any(
        isinstance(seg, dict) and seg.get("kind") == "link" for seg in thread_posts
    )
    body_urls = {
        normalized
        for normalized in (
            takyon_core._normalize_destination_url(raw.rstrip(".,!?;:"))
            for raw in re.findall(r"https?://[^\s<>()\[\]{}\"']+", body)
        )
        if normalized
    }
    destination_root = takyon_core._normalize_destination_url(
        destination_url.split("?", 1)[0].split("#", 1)[0]
    )
    destination_candidates = {candidate for candidate in (destination_url, destination_root) if candidate}
    link_in_body = any(candidate in body_urls for candidate in destination_candidates)
    if destination_url and current_reply_to and not link_already_posted and not link_in_body:
        link_text = _worker._compose_x_link_reply(
            destination_url,
            label=str(payload.get("destination_label") or "").strip(),
        )
        link_response = composio_distribution.twitter_execute_tool(
            "TWITTER_CREATION_OF_A_POST",
            arguments={
                "text": link_text,
                "reply_in_reply_to_tweet_id": current_reply_to,
            },
            timeout=120.0,
        )
        link_post_id = _worker._extract_x_post_id(link_response)
        thread_posts.append(
            {
                "index": len(thread_posts),
                "kind": "link",
                "post_id": link_post_id or "",
                "body": link_text,
                "reply_to": current_reply_to,
                "media": [],
                "provider_response": dict(link_response),
            }
        )
        if link_post_id:
            current_reply_to = link_post_id
        # Durably extend the marker so a crash before the credit commit cannot re-post the
        # link reply on retry.
        _worker._write_x_posted_marker(
            slug,
            str(job.id),
            {
                "job_id": str(job.id),
                "post_id": post_id,
                "thread_posts": thread_posts,
                "media": media_records,
                "provider_response": provider_response,
                "credits_committed": False,
            },
        )
    if len(thread_posts) > 1:
        provider_response["thread_posts"] = thread_posts
    elif media_records:
        provider_response["media"] = media_records
    partial.post_id = post_id
    partial.provider_response = provider_response
    partial.media = media_records
    return partial


def _x_finalize_post_url(ctx: PublishContext, outcome: PublishOutcome) -> str:
    """X-only: resolve the canonical post_url + persist the terminal committed marker BEFORE the
    receipt write (lifted verbatim from the original success path — the marker guards a retry from
    re-tweeting/re-charging if the receipt write races the mirror). Returns the post_url."""
    from . import worker as _worker

    composio_distribution = _worker.composio_distribution
    slug = ctx.slug
    job = ctx.job
    post_id = outcome.post_id
    thread_posts = outcome.extra.get("thread_posts") or []
    media_records = outcome.media
    provider_response = outcome.provider_response
    credit_result = outcome.extra.get("credit_result") or {}
    reservation = ctx.reservation or {}

    whoami = composio_distribution.twitter_execute_tool(
        "TWITTER_USER_LOOKUP_ME",
        arguments={"user_fields": ["username"]},
        timeout=30.0,
    )
    username = _worker._extract_x_username(whoami)
    post_url = (
        f"https://x.com/{username}/status/{post_id}"
        if username
        else f"https://x.com/i/web/status/{post_id}"
    )
    _committed_budget_bucket = str(
        (credit_result or {}).get("budget_bucket")
        or (reservation or {}).get("budget_bucket")
        or "x"
    ).strip() or "x"
    _committed_credits = int(
        (credit_result or {}).get("actual_credits")
        or (reservation or {}).get("requested_credits")
        or 0
    )
    _worker._write_x_posted_marker(
        slug,
        str(job.id),
        {
            "job_id": str(job.id),
            "post_id": post_id,
            "post_url": post_url,
            "thread_posts": thread_posts,
            "media": media_records,
            "provider_response": provider_response,
            "credits_committed": True,
            "credits_charged": _committed_credits,
            "budget_bucket": _committed_budget_bucket,
            "channel_budget": (credit_result or {}).get("channel_budget"),
        },
    )
    return post_url


def _x_record_result(ctx: PublishContext, outcome: PublishOutcome, **kwargs: Any) -> dict[str, str]:
    from . import worker as _worker

    return _worker._record_x_publish_result(ctx.slug, **kwargs)


def _x_replay_if_complete(ctx: PublishContext) -> Any:
    """Fully-posted retry: the tweet(s) already shipped and credits were already committed on the
    first attempt. Do NOT reserve/charge again — re-derive the published result and (re)write the
    receipt artifacts so the job reaches a clean terminal state idempotently. Lifted verbatim from
    the original X handler preamble. Returns a JobRunResult or None (run normally)."""
    from . import worker as _worker

    slug = ctx.slug
    job = ctx.job
    payload = ctx.payload
    work_request_id = ctx.work_request_id
    posted_marker = _worker._read_x_posted_marker(slug, str(job.id))
    already_posted_segments: list[dict[str, Any]] = []
    if isinstance(posted_marker, dict):
        raw_segments = posted_marker.get("thread_posts")
        if isinstance(raw_segments, list):
            already_posted_segments = [seg for seg in raw_segments if isinstance(seg, dict)]
    if not (already_posted_segments and bool(posted_marker.get("credits_committed"))):
        return None
    post_id = str(posted_marker.get("post_id") or "").strip()
    post_url = str(posted_marker.get("post_url") or "").strip()
    provider_response = dict(posted_marker.get("provider_response") or {})
    media_records = [dict(m) for m in (posted_marker.get("media") or []) if isinstance(m, dict)]
    credits_charged = int(posted_marker.get("credits_charged") or 0)
    budget_bucket = str(posted_marker.get("budget_bucket") or "x").strip() or "x"
    artifacts = _worker._record_x_publish_result(
        slug,
        job_id=str(job.id),
        payload=payload,
        post_id=post_id or str(job.id),
        post_url=post_url,
        provider_response=provider_response,
        media=media_records,
        credits_charged=credits_charged,
        budget_bucket=budget_bucket,
        channel_budget=posted_marker.get("channel_budget"),
    )
    if work_request_id:
        _worker._update_work_request(
            slug,
            work_request_id,
            status="completed",
            payload_updates={
                "artifact_path": artifacts["artifact"],
                "receipt_path": artifacts["receipt"],
                "post_id": post_id,
                "post_url": post_url,
                "credits_charged": credits_charged,
                "budget_bucket": budget_bucket,
                "idempotent_replay": True,
            },
        )
    return _worker.JobRunResult(
        result={
            "business_slug": slug,
            "provider": "x",
            "post_id": post_id,
            "post_url": post_url,
            "artifact_path": artifacts["artifact"],
            "receipt_path": artifacts["receipt"],
            "credits_charged": credits_charged,
            "budget_bucket": budget_bucket,
            "idempotent_replay": True,
        },
        actual_cost_cents=0,
    )


def _reddit_reservation_metadata(ctx: PublishContext) -> dict[str, Any]:
    thread_external_id = str(ctx.payload.get("thread_external_id") or "").strip()
    subreddit = str(ctx.payload.get("subreddit") or "").strip()
    return {
        "business": ctx.slug,
        "action": "reddit_publish_outreach",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "reddit",
        "provider": "reddit",
        "thread_external_id": thread_external_id or None,
        "subreddit": subreddit or None,
    }


def _reddit_commit_metadata(ctx: PublishContext, outcome: PublishOutcome) -> dict[str, Any]:
    subreddit = str(ctx.payload.get("subreddit") or "").strip()
    post_kind = str(ctx.payload.get("post_kind") or "").strip() or "self"
    return {
        "business": ctx.slug,
        "action": "reddit_publish_outreach",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "reddit",
        "provider": "reddit",
        "post_id": outcome.post_id,
        "subreddit": subreddit or None,
        "post_kind": post_kind,
    }


def _reddit_partial_failed_metadata(ctx: PublishContext, outcome: PublishOutcome, exc: Exception) -> dict[str, Any]:
    subreddit = str(ctx.payload.get("subreddit") or "").strip()
    post_kind = str(ctx.payload.get("post_kind") or "").strip() or "self"
    return {
        "business": ctx.slug,
        "action": "reddit_publish_outreach",
        "status": "partial_failed",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "reddit",
        "provider": "reddit",
        "post_id": outcome.post_id,
        "post_url": outcome.post_url or None,
        "subreddit": subreddit or None,
        "post_kind": post_kind,
        "error": str(exc),
    }


def _reddit_release_metadata(ctx: PublishContext, exc: Exception) -> dict[str, Any]:
    subreddit = str(ctx.payload.get("subreddit") or "").strip()
    post_kind = str(ctx.payload.get("post_kind") or "").strip() or "self"
    return {
        "business": ctx.slug,
        "action": "reddit_publish_outreach",
        "status": "failed",
        "job_id": str(ctx.job.id),
        "work_request_id": ctx.work_request_id or None,
        "channel": "reddit",
        "provider": "reddit",
        "subreddit": subreddit or None,
        "post_kind": post_kind,
        "error": str(exc),
    }


def _reddit_publish(ctx: PublishContext) -> PublishOutcome:
    """The Reddit publish body — a comment on a thread_external_id, else a self/link post to a
    subreddit. Provider/extract/record calls resolve through the ``worker`` module so the tests'
    ``monkeypatch.setattr(worker, ...)`` still applies."""
    from . import worker as _worker

    composio_distribution = _worker.composio_distribution
    payload = ctx.payload
    body = ctx.body
    title = str(payload.get("title") or payload.get("subject") or "").strip()
    post_kind = str(payload.get("post_kind") or "").strip() or "self"
    subreddit = str(payload.get("subreddit") or "").strip()
    url = str(payload.get("url") or "").strip()
    thread_external_id = str(payload.get("thread_external_id") or "").strip()

    if thread_external_id:
        provider_response = composio_distribution.reddit_execute_tool(
            "REDDIT_POST_REDDIT_COMMENT",
            arguments={
                # [composio-schema] Confirm REDDIT_POST_REDDIT_COMMENT uses thing_id and text.
                "thing_id": thread_external_id,
                "text": body,
            },
            timeout=120.0,
        )
    else:
        arguments: dict[str, Any] = {
            "subreddit": subreddit,
            "title": title,
            # [composio-schema] Confirm REDDIT_CREATE_REDDIT_POST uses kind with values self/link.
            "kind": "self" if post_kind == "self" else "link",
        }
        if post_kind == "self":
            # [composio-schema] Confirm REDDIT_CREATE_REDDIT_POST uses text for self-post bodies.
            arguments["text"] = body
        else:
            arguments["url"] = url
        provider_response = composio_distribution.reddit_execute_tool(
            "REDDIT_CREATE_REDDIT_POST",
            arguments=arguments,
            timeout=120.0,
        )
    publish_ref = _worker._extract_reddit_publish_ref(provider_response)
    post_id = str(publish_ref.get("post_id") or "").strip()
    post_url = str(publish_ref.get("post_url") or "").strip()
    if not post_id:
        raise RuntimeError("Reddit publish returned no post id")
    return PublishOutcome(post_id=post_id, post_url=post_url, provider_response=dict(provider_response))


def _reddit_record_result(ctx: PublishContext, outcome: PublishOutcome, **kwargs: Any) -> dict[str, str]:
    from . import worker as _worker

    return _worker._record_reddit_publish_result(ctx.slug, **kwargs)


def _reddit_replay_if_complete(ctx: PublishContext) -> Any:
    # Reddit has no durable posted-marker, so there is nothing to replay — always run normally.
    return None


def _reddit_finalize_post_url(ctx: PublishContext, outcome: PublishOutcome) -> str:
    # Reddit resolves post_url inside publish (from the provider response); no post-commit step.
    return outcome.post_url


X_CHANNEL = ChannelPublisher(
    slug="x",
    aliases=("x", "twitter", "x_social"),
    credit_action="x_publish_outreach",
    toolkit_slug="twitter",
    budget_bucket="x",
    credit_audience="creative.x_publish",
    credit_cost_default=1,
    credit_cost_env="TAKYON_CREATIVE_CREDITS_X_POST",
    publication_root="distribution/local-published/x",
    env_alias_names=("x", "x_social", "twitter"),
    job_kind="x.publish_outreach",
    publish=_x_publish,
    reservation_metadata=_x_reservation_metadata,
    commit_metadata=_x_commit_metadata,
    partial_failed_metadata=_x_partial_failed_metadata,
    release_metadata=_x_release_metadata,
    record_result=_x_record_result,
    replay_if_complete=_x_replay_if_complete,
    finalize_post_url=_x_finalize_post_url,
    test_local_publish_root="distribution/local-published/x",
)

REDDIT_CHANNEL = ChannelPublisher(
    slug="reddit",
    aliases=("reddit",),
    credit_action="reddit_publish_outreach",
    toolkit_slug="reddit",
    budget_bucket="reddit",
    credit_audience="creative.reddit_publish",
    credit_cost_default=1,
    credit_cost_env="TAKYON_CREATIVE_CREDITS_REDDIT_POST",
    publication_root="distribution/local-published/reddit",
    env_alias_names=("reddit",),
    job_kind="reddit.publish_outreach",
    publish=_reddit_publish,
    reservation_metadata=_reddit_reservation_metadata,
    commit_metadata=_reddit_commit_metadata,
    partial_failed_metadata=_reddit_partial_failed_metadata,
    release_metadata=_reddit_release_metadata,
    record_result=_reddit_record_result,
    replay_if_complete=_reddit_replay_if_complete,
    finalize_post_url=_reddit_finalize_post_url,
    test_local_publish_root="distribution/local-published/reddit",
)


CHANNEL_REGISTRY: dict[str, ChannelPublisher] = {
    X_CHANNEL.slug: X_CHANNEL,
    REDDIT_CHANNEL.slug: REDDIT_CHANNEL,
}


def channel_for_slug(slug: Any) -> ChannelPublisher | None:
    return CHANNEL_REGISTRY.get(str(slug or "").strip().lower())


def channel_for_alias(name: Any) -> ChannelPublisher | None:
    """Resolve a channel from any of its Composio name spellings (the old ``_is_x_provider_name``
    style predicates, generalized to the registry)."""
    text = str(name or "").strip().lower()
    if not text:
        return None
    for channel in CHANNEL_REGISTRY.values():
        if text in channel.aliases:
            return channel
    return None


def is_channel_provider_name(slug: str, name: Any) -> bool:
    """True when ``name`` is one of ``slug``'s Composio aliases (replaces ``_is_x_provider_name``)."""
    channel = CHANNEL_REGISTRY.get(str(slug or "").strip().lower())
    if channel is None:
        return False
    return str(name or "").strip().lower() in channel.aliases
