"""ChannelPublisher registry — field fidelity, single-source publication root, and the
one-descriptor-adds-a-channel property (§6b item 1).

These are pure, no-Postgres unit tests: the registry is data + callables, and the generic
``worker.channel_publish_outreach_handler`` is exercised with the money rails monkeypatched (exactly
as the existing X/Reddit handler tests do), so nothing here needs a DB.

The point of the extraction is behavior identity, so the field-fidelity block asserts the descriptor
values against the *literal* pre-extraction constants (credit cost, cost-env, bucket, audience,
aliases, toolkit, publication root) — a byte-faithful contract, not a tautology against core.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from plugins.takyon import channel_registry, core, worker
from plugins.takyon.channel_registry import (
    CHANNEL_REGISTRY,
    ChannelPublisher,
    PublishContext,
    PublishOutcome,
)


# ── 1. registry field fidelity: x + reddit values match the pre-extraction constants ─────────────


def test_registry_has_exactly_x_and_reddit():
    assert set(CHANNEL_REGISTRY) == {"x", "reddit"}


def test_x_channel_fields_match_preextraction_literals():
    x = CHANNEL_REGISTRY["x"]
    assert x.slug == "x"
    assert x.aliases == ("x", "twitter", "x_social")
    assert x.credit_action == "x_publish_outreach"
    assert x.toolkit_slug == "twitter"
    assert x.budget_bucket == "x"
    assert x.credit_audience == "creative.x_publish"
    assert x.credit_cost_default == 1
    assert x.credit_cost_env == "TAKYON_CREATIVE_CREDITS_X_POST"
    assert x.publication_root == "distribution/local-published/x"
    assert x.env_alias_names == ("x", "x_social", "twitter")
    assert x.job_kind == "x.publish_outreach"


def test_reddit_channel_fields_match_preextraction_literals():
    r = CHANNEL_REGISTRY["reddit"]
    assert r.slug == "reddit"
    assert r.aliases == ("reddit",)
    assert r.credit_action == "reddit_publish_outreach"
    assert r.toolkit_slug == "reddit"
    assert r.budget_bucket == "reddit"
    assert r.credit_audience == "creative.reddit_publish"
    assert r.credit_cost_default == 1
    assert r.credit_cost_env == "TAKYON_CREATIVE_CREDITS_REDDIT_POST"
    assert r.publication_root == "distribution/local-published/reddit"
    assert r.env_alias_names == ("reddit",)
    assert r.job_kind == "reddit.publish_outreach"


def test_core_per_action_dicts_derive_channel_rows_from_registry():
    """The four parallel core dicts read their x/reddit rows FROM the registry — the descriptor is
    the source of truth, not a hand-synced copy."""
    for channel in CHANNEL_REGISTRY.values():
        action = channel.credit_action
        assert core._CREATIVE_CREDIT_COST_DEFAULTS[action] == channel.credit_cost_default
        assert core._CREATIVE_CREDIT_COST_ENVS[action] == channel.credit_cost_env
        assert core._CREATIVE_CREDIT_ACTION_DEFAULT_BUCKETS[action] == channel.budget_bucket
        assert core._CREATIVE_CREDIT_ACTION_AUDIENCES[action] == channel.credit_audience


def test_api_env_alias_composio_rows_derive_from_registry():
    """The per-channel Composio alias rows in ``_API_ENV_ALIASES`` come from the descriptor."""
    for channel in CHANNEL_REGISTRY.values():
        for alias in channel.env_alias_names:
            assert core._API_ENV_ALIASES[alias] == ("COMPOSIO_API_KEY",)


def test_is_x_provider_name_predicate_reads_registry_aliases():
    for spelling in ("x", "twitter", "x_social", "X", "Twitter"):
        assert core._is_x_provider_name(spelling)
    for other in ("reddit", "meta", "", None):
        assert not core._is_x_provider_name(other)


def test_alias_resolver_maps_every_spelling_back_to_its_channel():
    assert channel_registry.channel_for_alias("twitter") is CHANNEL_REGISTRY["x"]
    assert channel_registry.channel_for_alias("x_social") is CHANNEL_REGISTRY["x"]
    assert channel_registry.channel_for_alias("reddit") is CHANNEL_REGISTRY["reddit"]
    assert channel_registry.channel_for_alias("linkedin") is None


# ── 2. publication_root single-source: registry value == the value the receipt writer uses ───────


def test_x_publication_root_is_the_receipt_writer_artifact_prefix(monkeypatch):
    """The receipt writer must write the live artifact under the descriptor's publication_root —
    ONE source of truth, not two hand-synced strings. Capture the artifact path the writer commits
    and assert it lives under ``X_CHANNEL.publication_root``."""
    committed: dict[str, Any] = {}

    class _FakeStore:
        def commit(self, *, scope, operations, idempotency_key, reason, actor):
            for op in operations:
                if op.get("action") == "artifact.write" and op.get("path", "").endswith(".md"):
                    committed["artifact"] = op["path"]

    monkeypatch.setattr(worker, "TakyonStore", _FakeStore, raising=False)
    # TakyonStore is imported lazily inside the writer from .core; patch there too.
    monkeypatch.setattr(core, "TakyonStore", _FakeStore, raising=False)

    result = worker._record_x_publish_result(
        "acme",
        job_id="job-1",
        payload={"body": "hi", "subject": "hi"},
        post_id="tweet-9",
        post_url="https://x.com/u/status/tweet-9",
        provider_response={},
    )
    root = CHANNEL_REGISTRY["x"].publication_root
    assert committed["artifact"].startswith(root + "/")
    assert result["artifact"].startswith(root + "/")


def test_reddit_publication_root_is_the_receipt_writer_artifact_prefix(monkeypatch):
    committed: dict[str, Any] = {}

    class _FakeStore:
        def commit(self, *, scope, operations, idempotency_key, reason, actor):
            for op in operations:
                if op.get("action") == "artifact.write" and op.get("path", "").endswith(".md"):
                    committed["artifact"] = op["path"]

    monkeypatch.setattr(worker, "TakyonStore", _FakeStore, raising=False)
    monkeypatch.setattr(core, "TakyonStore", _FakeStore, raising=False)

    result = worker._record_reddit_publish_result(
        "acme",
        job_id="job-2",
        payload={"body": "hi", "subreddit": "test"},
        post_id="t3_x",
        post_url="https://www.reddit.com/r/test/comments/x/",
        provider_response={},
    )
    root = CHANNEL_REGISTRY["reddit"].publication_root
    assert committed["artifact"].startswith(root + "/")
    assert result["artifact"].startswith(root + "/")


# ── 3. adding a hypothetical third channel is one descriptor (generic handler dispatches it) ─────


def _make_third_channel(publish_spy: dict[str, Any]) -> ChannelPublisher:
    """A minimal synthetic ChannelPublisher — one descriptor, no forked handler. Its publish body is
    a stub that records it ran and returns a posted outcome; the metadata callables are trivial."""

    def _publish(ctx: PublishContext) -> PublishOutcome:
        publish_spy["published"] = True
        publish_spy["slug"] = ctx.slug
        return PublishOutcome(post_id="mastodon-1", post_url="https://m.example/@a/1", provider_response={"ok": True})

    def _reservation_metadata(ctx: PublishContext) -> dict:
        return {"action": "mastodon_publish_outreach", "channel": "mastodon"}

    def _commit_metadata(ctx: PublishContext, outcome: PublishOutcome) -> dict:
        return {"action": "mastodon_publish_outreach", "post_id": outcome.post_id}

    def _partial_failed_metadata(ctx, outcome, exc) -> dict:
        return {"action": "mastodon_publish_outreach", "status": "partial_failed"}

    def _release_metadata(ctx, exc) -> dict:
        return {"action": "mastodon_publish_outreach", "status": "failed"}

    def _record_result(ctx, outcome, **kwargs) -> dict:
        publish_spy["recorded_kwargs"] = kwargs
        return {"artifact": "distribution/local-published/mastodon/x.md", "receipt": "metrics/receipts/outreach/x.json"}

    def _replay_if_complete(ctx):
        return None

    def _finalize_post_url(ctx, outcome) -> str:
        return outcome.post_url

    return ChannelPublisher(
        slug="mastodon",
        aliases=("mastodon",),
        credit_action="mastodon_publish_outreach",
        toolkit_slug="mastodon",
        budget_bucket="mastodon",
        credit_audience="creative.mastodon_publish",
        credit_cost_default=1,
        credit_cost_env="TAKYON_CREATIVE_CREDITS_MASTODON_POST",
        publication_root="distribution/local-published/mastodon",
        env_alias_names=("mastodon",),
        job_kind="mastodon.publish_outreach",
        publish=_publish,
        reservation_metadata=_reservation_metadata,
        commit_metadata=_commit_metadata,
        partial_failed_metadata=_partial_failed_metadata,
        release_metadata=_release_metadata,
        record_result=_record_result,
        replay_if_complete=_replay_if_complete,
        finalize_post_url=_finalize_post_url,
        test_local_publish_root="distribution/local-published/mastodon",
    )


def test_generic_handler_dispatches_a_hypothetical_third_channel(monkeypatch):
    """Constructing ONE descriptor is enough for the generic money envelope to publish + charge it —
    no new handler function, no envelope fork. Proves the registry seam."""
    publish_spy: dict[str, Any] = {}
    third = _make_third_channel(publish_spy)

    reserve_calls: list[dict[str, Any]] = []
    commit_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda slug, *, action, reservation_key, budget_bucket, metadata: reserve_calls.append(
            {"action": action, "budget_bucket": budget_bucket, "reservation_key": reservation_key}
        )
        or {"requested_credits": 1, "budget_bucket": "mastodon"},
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda reservation_key, *, action, budget_bucket, metadata: commit_calls.append(
            {"action": action, "budget_bucket": budget_bucket}
        )
        or {"actual_credits": 1, "budget_bucket": "mastodon", "balance_credits": 4},
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("release must not run on a successful publish")),
    )

    job = SimpleNamespace(id="job-mastodon", business_slug="acme", payload={"body": "toot", "work_request_id": ""})
    result = worker.channel_publish_outreach_handler(job, third)

    # the generic envelope drove the descriptor's publish + reserve + commit + record — one path.
    assert publish_spy["published"] is True
    assert publish_spy["slug"] == "acme"
    assert reserve_calls == [
        {"action": "mastodon_publish_outreach", "budget_bucket": "mastodon", "reservation_key": "mastodon-publish:job-mastodon"}
    ]
    assert commit_calls == [{"action": "mastodon_publish_outreach", "budget_bucket": "mastodon"}]
    assert result.result["provider"] == "mastodon"
    assert result.result["post_id"] == "mastodon-1"
    assert result.result["post_url"] == "https://m.example/@a/1"
    assert result.actual_cost_cents == 0
    # the record_result callable received the derived credits/bucket the envelope computed.
    assert publish_spy["recorded_kwargs"]["credits_charged"] == 1
    assert publish_spy["recorded_kwargs"]["budget_bucket"] == "mastodon"


def test_generic_handler_releases_when_third_channel_publish_fails(monkeypatch):
    """A publish that ships nothing releases the reservation (never commits) — the money-safety
    invariant holds for an arbitrary channel, proving it lives in the envelope, not per-channel."""
    third = _make_third_channel({})

    def _failing_publish(ctx: PublishContext) -> PublishOutcome:
        raise RuntimeError("provider down")

    third = ChannelPublisher(**{**third.__dict__, "publish": _failing_publish})

    released: list[Any] = []
    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *a, **k: {"requested_credits": 1, "budget_bucket": "mastodon"},
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("commit must not run when nothing shipped")),
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *a, **k: released.append(k) or {"balance_credits": 5, "budget_bucket": "mastodon"},
    )

    job = SimpleNamespace(id="job-fail", business_slug="acme", payload={"body": "toot", "work_request_id": ""})
    import pytest

    with pytest.raises(RuntimeError, match="provider down"):
        worker.channel_publish_outreach_handler(job, third)
    assert released  # released, not committed
