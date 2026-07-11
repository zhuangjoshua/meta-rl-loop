"""LEVEL 3 rig — real CEO wakes, real skill, stubbed providers, injected ROAS.

This is an OFFLINE ACCEPTANCE HARNESS (not a runtime tool; nothing imports it). It answers
the question the level-1 simulator (rl_sim.py) cannot: does the ACTUAL CEO, actually
executing the ACTUAL takyon-meta-ads-v2 skill through the real launch tool, change its real
creative choices because of the ROAS run history in metrics/roas/meta.md?

What is REAL here: the wake turn (worker.ceo_wake_handler — the same code the 6h cron
calls), the skills index, the launch tool's full path (credit reserve -> plan.json ->
create -> receipts -> ad-spend policy registry), the assembler (metrics/roas/meta.md), the
Postgres store. What is FAKE, because it must be: the external providers (a recording
safebox stand-in accepts uploads/creates and returns ids — no network, no money) and the
OUTCOME (the sync receipt's ROAS, drawn from a hidden truth table keyed on the creative
kind the CEO chose: video vs image ads have different sealed qualities).

Modes:
  --fake-ceo            plumbing self-test, zero tokens: a scripted actor reads the same
                        run history and calls the REAL launch handler. Validates stubs,
                        credits, receipts, injection, assembler, scoring end to end.
  (default: real CEO)   worker.ceo_wake_handler fires a genuine agent turn per wake.
                        Model/provider come from --model/--provider; --transport direct
                        builds a plain client from ANTHROPIC_API_KEY (no safebox broker),
                        --transport gateway uses the operator-gateway lane as configured.

Usage (throwaway migrated Postgres, exactly like rl_sim):
    export TAKYON_TEST_PG_DSN=postgres://postgres:postgres@127.0.0.1:55432/rlsim_demo
    export TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1
    python -m plugins.takyon.rl_wake_rig --fake-ceo --wakes 6          # plumbing, free
    export ANTHROPIC_API_KEY=sk-ant-...
    python -m plugins.takyon.rl_wake_rig --wakes 5 --model claude-sonnet-5 \
        --provider anthropic --transport direct                        # the real thing
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # pragma: no cover - direct-script convenience
    sys.path.insert(0, str(_REPO_ROOT))

KINDS = ("video", "image")


# ------------------------------------------------------------------ hidden truth (ROAS)

@dataclass
class RigWorld:
    """Sealed truth: ROAS by creative KIND (the one deterministic attribute of a launch we
    can score without parsing free-form copy). Shuffled by seed so the operator/actor can't
    know which kind is best until reveal."""

    seed: int
    noise: float = 0.4
    rng: random.Random = field(init=False)
    mapping: dict[str, float] = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        values = [0.8, 4.6]  # one clearly-bad, one clearly-good creative kind
        random.Random(self.seed * 7919 + 3).shuffle(values)
        self.mapping = dict(zip(KINDS, values))

    def best_kind(self) -> str:
        return max(self.mapping, key=lambda k: self.mapping[k])

    def sync_totals(self, kind: str, spend_usd: float = 10.41) -> dict[str, Any]:
        """One period of delivery. Creative quality expresses itself the way REALITY does —
        through CLICK-THROUGH: a bad creative fails to attract link clicks; conversion among
        those who do click is kind-independent (the landing page is the same page). ROAS then
        EMERGES from link_clicks x conversion x price instead of being set directly. This
        matters because the operator's funnel policy diagnoses by shape: few link clicks +
        healthy conversion -> creative problem (cut/switch); many clicks + low conversion ->
        landing problem (keep ad, fix site). The first world model set ROAS directly with
        kind-independent clicks, so every loser masqueraded as a landing-page problem and the
        policy's cut-the-creative branch was unreachable (observed: a real CEO correctly
        followed the funnel policy and never cut the sealed loser)."""
        quality = max(0.15, self.rng.gauss(self.mapping.get(kind, 1.0), self.noise))
        link_clicks = max(1, int(round(spend_usd * quality * self.rng.uniform(2.4, 3.0))))
        clicks = int(link_clicks * self.rng.uniform(1.3, 1.7))
        conversion = max(0.008, self.rng.gauss(0.019, 0.004))  # page quality: kind-independent
        purchases = int(round(link_clicks * conversion))
        value = round(purchases * self.rng.uniform(17.0, 21.0), 2)  # ~$19 plan price
        return {
            "rows": 1,
            "spend_cents": int(round(spend_usd * 100)),
            "spend_usd": spend_usd,
            "impressions": int(spend_usd * self.rng.uniform(900, 1400)),
            "clicks": clicks,
            "link_clicks": link_clicks,
            "link_click_conversion_rate": round((purchases / link_clicks) * 100.0, 4),
            "purchase_count": purchases,
            "purchase_value_usd": value,
            "roas": round(value / spend_usd, 4),
        }


# ------------------------------------------------------------------ provider stand-ins

class RigSafebox:
    """Recording stand-in for the safebox surface meta_ads_v2 touches. Shapes mirror the
    meta test harness's fake (uploads return ids/hashes, MCP creates return {'id': ...},
    graph GETs return an empty data envelope). No network, no secrets, no money."""

    def __init__(self) -> None:
        self.values = {"META_AD_ACCOUNT_ID": "act_1234567890", "META_PAGE_ID": "9876543210"}
        self.calls: list[dict[str, Any]] = []
        self._n = 0
        # object_id -> [insights row]. The rig registers every campaign's CUMULATIVE
        # delivery here as it injects periods, so the CEO's own mid-wake insight syncs
        # return the SAME truth the note records. The first real production-rhythm run
        # failed on exactly this contradiction: the note said ROAS 1.18 while the live
        # sync said zero-delivery, and the CEO rationally trusted the live API and froze
        # in a 'needs data' state for seven wakes.
        self.insights_rows: dict[str, list[dict[str, Any]]] = {}

    def _next(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    def first_env_backed_value(self, *keys: str) -> str:
        for key in keys:
            if key in self.values:
                return self.values[key]
        return ""

    def meta_config(self) -> dict[str, Any]:
        return {
            "token": "", "has_token": True, "has_mcp_oauth_token": True,
            "mcp_endpoint": "https://mcp.example.invalid/ads", "version": "v21.0",
            "ad_account_id": self.values["META_AD_ACCOUNT_ID"],
            "page_id": self.values["META_PAGE_ID"], "instagram_user_id": "",
        }

    def meta_graph_upload_video(self, **kw: Any) -> str:
        self.calls.append({"op": "upload_video", "name": kw.get("name")})
        return self._next("video")

    def meta_graph_upload_image(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({"op": "upload_image", "name": kw.get("name")})
        return {"hash": self._next("imghash"), "url": "https://cdn.example.invalid/i.png"}

    def meta_mcp_call(self, *, tool_name: str, arguments: Any = None, timeout: float = 60.0) -> dict[str, Any]:
        self.calls.append({"op": "mcp", "tool": tool_name, "arguments": dict(arguments or {})})
        payload = {"id": self._next(tool_name)}
        return {"content": [{"type": "text", "text": json.dumps(payload)}],
                "structuredContent": payload, **payload}

    def meta_graph_forward(self, *, method: str, path: str, params: Any = None, **kw: Any) -> dict[str, Any]:
        self.calls.append({"op": "graph", "method": method, "path": path,
                           "params": dict(params or {})})
        if str(method).upper() == "GET":
            if path.rstrip("/").endswith("/insights"):
                object_id = path.rstrip("/").rsplit("/insights", 1)[0]
                return {"data": [dict(r) for r in self.insights_rows.get(object_id, [])]}
            return {"data": []}
        return {"success": True, "id": self._next("obj")}

    def meta_graph_ensure_custom_conversion(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({"op": "ensure_custom_conversion", **{k: str(v)[:60] for k, v in kw.items()}})
        return {"id": self._next("cc"), "created": True}


# ------------------------------------------------------------------ rig environment

@contextlib.contextmanager
def rig_environment(dsn: str, home: Path, *, model: str = "", provider: str = "",
                    transport: str = "gateway") -> Iterator[dict[str, Any]]:
    """Everything a real in-process wake needs, isolated and restorable:
    - temp TAKYON_HOME with the repo skills copied in (so the CEO's skills index carries the
      REAL takyon-meta-ads-v2, including its run-history instruction) and a model config;
    - local storage backend; store constructor defaulted to the rig DSN (fixture pattern);
    - the RigSafebox patched over the exact safebox attributes meta_ads_v2 calls;
    - optionally (--transport direct) a plain model client instead of the operator-gateway
      broker, built from ANTHROPIC_API_KEY.
    """
    from plugins.takyon import core as takyon_core

    home.mkdir(parents=True, exist_ok=True)
    skills_dst = home / "skills"
    if not skills_dst.exists():
        shutil.copytree(_REPO_ROOT / "skills", skills_dst)
    (home / "config.yaml").write_text(
        "model:\n"
        f"  provider: {provider or 'anthropic'}\n"
        f"  default: {model or ''}\n"
        "conversation:\n  response_style: concise\n",
        encoding="utf-8",
    )

    saved_env = {k: os.environ.get(k) for k in
                 ("TAKYON_HOME", "TAKYON_STORAGE_BACKEND", "TAKYON_STORAGE_LOCAL_DIR",
                  "TAKYON_ALLOW_LEGACY_DB_ROLES")}
    os.environ["TAKYON_HOME"] = str(home)
    os.environ["TAKYON_STORAGE_BACKEND"] = "local"
    os.environ["TAKYON_STORAGE_LOCAL_DIR"] = str(home / "bucket")
    # Throwaway rig DBs connect as the superuser; accept the tracked legacy-role opt-in so the
    # ledger role gates admit the session (same switch the worker_pg test rig uses).
    os.environ["TAKYON_ALLOW_LEGACY_DB_ROLES"] = "1"

    orig_store_cls = takyon_core.TakyonStore

    class _RigStore(orig_store_cls):  # every construction (incl. call-time imports) hits the DSN
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("database_url", dsn)
            super().__init__(*args, **kwargs)

        def _sync_business_workspace_cache(self, slug: str, root: Any) -> None:
            # The rig writes business files out-of-band (budget top-ups, assets, injected
            # sync receipts); re-materializing the head revision on every sync=True resolve
            # would clobber them (observed: the 80k budget top-up reverting to the 40k
            # captured in wake 1's revision). In the rig the LOCAL tree is canonical.
            return None

    takyon_core.TakyonStore = _RigStore  # type: ignore[misc]

    safebox = RigSafebox()
    sb_mod = takyon_core.safebox
    patched = ("first_env_backed_value", "meta_config", "meta_graph_upload_video",
               "meta_graph_upload_image", "meta_mcp_call", "meta_graph_forward",
               "meta_graph_ensure_custom_conversion")
    saved_sb = {name: getattr(sb_mod, name, None) for name in patched}
    for name in patched:
        setattr(sb_mod, name, getattr(safebox, name))
    # Money/provisioning authority through the LOCAL safebox path: provisioning + billing run
    # their SECURITY DEFINER ledger SQL on the rig's own throwaway DB instead of delegating to
    # a remote safebox host (same posture as the worker_pg test rig).
    saved_local_authority = getattr(sb_mod, "_local_authority_enabled", None)
    sb_mod._local_authority_enabled = lambda: True  # type: ignore[attr-defined]

    # The real wake mounts an ISOLATED workspace materialized from the DB head revision —
    # which cannot contain the rig's out-of-band writes (history file, assets, budget config),
    # so the CEO would see an empty business tree (verified by dress rehearsal). In the rig
    # the local tree is canonical: the wake works directly on TAKYON_HOME.
    from plugins.takyon import turn_runtime as _turn_runtime
    from plugins.takyon import worker as _worker_mod

    @contextlib.contextmanager
    def _rig_workspace_ctx(_slug: str, *_a: Any, **_kw: Any) -> Iterator[str]:
        yield str(home)

    saved_ws_ctx = _turn_runtime._business_workspace_execution_context
    _turn_runtime._business_workspace_execution_context = _rig_workspace_ctx  # type: ignore[assignment]
    saved_ws_ctx_worker = getattr(_worker_mod, "_business_workspace_execution_context", None)
    if saved_ws_ctx_worker is not None:
        _worker_mod._business_workspace_execution_context = _rig_workspace_ctx  # type: ignore[attr-defined]

    # The agent's tool registry is a SECOND import of this same package: the plugin loader
    # (takyon_cli/plugins.py::_load_module) loads plugins/takyon/__init__.py by file path under
    # its own module name, so every registered business_* handler resolves TakyonStore, safebox,
    # and turn_runtime from `takyon_plugins.takyon.*` TWIN module objects — not the ones patched
    # above. Observed in a real wake: every tool call failed "no operator database URL configured"
    # while the wake's own pre-wake store work (plugins.takyon identity) succeeded. Load the twins
    # now (model_tools import triggers plugin discovery) and apply the SAME patches; restored on exit.
    import model_tools  # noqa: F401  (side effect: discover_plugins -> takyon_plugins.takyon)

    saved_twin: list[tuple[Any, str, Any]] = []
    twin_core = sys.modules.get("takyon_plugins.takyon.core")
    if twin_core is not None:
        saved_twin.append((twin_core, "TakyonStore", twin_core.TakyonStore))

        class _RigStoreTwin(twin_core.TakyonStore):  # same fixture pattern, twin base class
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("database_url", dsn)
                super().__init__(*args, **kwargs)

            def _sync_business_workspace_cache(self, slug: str, root: Any) -> None:
                return None  # local tree is canonical in the rig (see _RigStore)

        twin_core.TakyonStore = _RigStoreTwin  # type: ignore[misc]
    twin_sb = sys.modules.get("takyon_plugins.takyon.safebox")
    if twin_sb is not None:
        for name in patched:
            saved_twin.append((twin_sb, name, getattr(twin_sb, name, None)))
            setattr(twin_sb, name, getattr(safebox, name))
        saved_twin.append((twin_sb, "_local_authority_enabled",
                           getattr(twin_sb, "_local_authority_enabled", None)))
        twin_sb._local_authority_enabled = lambda: True  # type: ignore[attr-defined]
    twin_turn = sys.modules.get("takyon_plugins.takyon.turn_runtime")
    if twin_turn is not None:
        saved_twin.append((twin_turn, "_business_workspace_execution_context",
                           getattr(twin_turn, "_business_workspace_execution_context", None)))
        twin_turn._business_workspace_execution_context = _rig_workspace_ctx  # type: ignore[attr-defined]

    saved_builder = None
    saved_runtime_resolver = None
    if transport == "direct":
        from plugins.takyon import operator_gateway
        import takyon_cli.runtime_provider as _rtp_mod

        saved_builder = operator_gateway.build_operator_gateway_agent
        saved_runtime_resolver = _rtp_mod.resolve_runtime_provider

        def _direct_runtime_resolver(**_kw: Any) -> dict[str, Any]:
            # Direct transport replaces the agent builder outright, so runtime
            # resolution only feeds provider/api_mode strings into _direct_builder.
            # Bypass the provider registry (it has no plain "openai" entry — only
            # OAuth openai-codex) and echo the rig's configured provider.
            return {"provider": provider or "anthropic", "api_mode": None,
                    "base_url": "", "api_key": "", "source": "rig-direct"}

        _rtp_mod.resolve_runtime_provider = _direct_runtime_resolver  # type: ignore[assignment]

        def _direct_builder(*, runtime: dict[str, Any], model: str, agent_kwargs: Any = None, **_kw: Any):
            from run_agent import AIAgent

            rt_provider = runtime.get("provider") or "anthropic"
            extra: dict[str, Any] = {}
            if rt_provider == "openai":
                # AIAgent's explicit-credentials lane needs BOTH api_key and base_url
                # (there is no registered "openai" provider profile, so key-only init
                # dies in the provider router). gpt-5.x on api.openai.com auto-upgrades
                # to the Responses API inside agent_init.
                key = os.environ.get("OPENAI_API_KEY", "")
                if not key:
                    raise RuntimeError(
                        "--transport direct with --provider openai requires OPENAI_API_KEY in the environment")
                extra["base_url"] = "https://api.openai.com/v1"
            else:
                key = os.environ.get("ANTHROPIC_API_KEY", "")
                if not key:
                    raise RuntimeError("--transport direct requires ANTHROPIC_API_KEY in the environment")
            return AIAgent(model=model, provider=rt_provider,
                           api_key=key, api_mode=runtime.get("api_mode"),
                           **extra, **dict(agent_kwargs or {}))

        operator_gateway.build_operator_gateway_agent = _direct_builder  # type: ignore[assignment]

    try:
        yield {"safebox": safebox, "store_cls": _RigStore}
    finally:
        takyon_core.TakyonStore = orig_store_cls  # type: ignore[misc]
        for name in patched:
            if saved_sb[name] is not None:
                setattr(sb_mod, name, saved_sb[name])
        if saved_local_authority is not None:
            sb_mod._local_authority_enabled = saved_local_authority  # type: ignore[attr-defined]
        _turn_runtime._business_workspace_execution_context = saved_ws_ctx  # type: ignore[assignment]
        if saved_ws_ctx_worker is not None:
            _worker_mod._business_workspace_execution_context = saved_ws_ctx_worker  # type: ignore[attr-defined]
        for _mod, _attr, _val in saved_twin:
            if _val is not None:
                setattr(_mod, _attr, _val)
        if saved_builder is not None:
            from plugins.takyon import operator_gateway

            operator_gateway.build_operator_gateway_agent = saved_builder  # type: ignore[assignment]
        if saved_runtime_resolver is not None:
            import takyon_cli.runtime_provider as _rtp_mod

            _rtp_mod.resolve_runtime_provider = saved_runtime_resolver  # type: ignore[assignment]
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ------------------------------------------------------------------ business seeding

def seed_business(dsn: str, store: Any, slug: str) -> None:
    """A live-mode business pinned to the meta-ads task: goal + work_focus make 'launch a
    meta traffic campaign' the wake's one obvious move (Hermes-native pinning through
    business state), credits + allowance make the real money gates pass, and both creative
    assets exist so the CEO can genuinely choose video vs image."""
    import psycopg

    from plugins.takyon import billing, business_credits
    from plugins.takyon.control_plane import provision_user_on_first_login

    goal = (
        "Grow through paid Meta ads ONLY. Campaigns run for multiple days, so each wake is a "
        "CHECK-IN on the live campaign, not automatically a launch. Every wake: read "
        "metrics/roas/meta.md FIRST — its header is the operator policy, follow it exactly. "
        "Hold a live campaign whose latest measured ROAS is at or above the policy threshold "
        "(no new launch). Cut a live campaign that is measurably below it: pause it with "
        "business_meta_ad_control (operation 'pause', the receipt's campaign_id), then launch "
        "exactly ONE changed-approach replacement with business_meta_ad_launch (objective "
        "Traffic, mode paused, link_url https://demo.localtest.me, daily_budget_usd 10). "
        "When NO campaign is live (the last one completed), launch ONE new campaign informed "
        "by the history. Creative kinds: asset_kind 'video' (asset_path "
        "product/ugc-ads/shared/ad.mp4, thumbnail_path product/ugc-ads/shared/thumbnail.png) "
        "or asset_kind 'image' (asset_path product/static-ads/shared/hero.png). Use a fresh "
        "campaign slug each time (rig-c1, rig-c2, ...). Only one campaign may be live at a "
        "time. Do not do research, posting, SEO, or product work."
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        # first-login provisioning creates the user AND its billing account (grant_allowance
        # refuses users without one — same recipe as the worker_pg test rig).
        uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{slug}")
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode, goal, work_focus) "
            "values (%s, %s, %s, 'live', %s, 'marketing')",
            (slug, "Rig Demo Co", uid, goal),
        )
        billing.grant_allowance(conn, uid, 100_000, f"rig-allowance-{slug}")
        business_credits.grant_credits(conn, slug, 500_000, f"rig-credits-{slug}")
    for rel, payload in (
        ("product/ugc-ads/shared/ad.mp4", b"\x00\x00\x00 ftypisom rig dummy video"),
        ("product/ugc-ads/shared/thumbnail.png", b"\x89PNG\r\n\x1a\n rig dummy thumb"),
        ("product/static-ads/shared/hero.png", b"\x89PNG\r\n\x1a\n rig dummy image"),
    ):
        path = store._resolve_business_file(slug, rel, sync=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    set_meta_channel_budget(store, slug, 40_000)


def set_meta_channel_budget(store: Any, slug: str, credits: int) -> None:
    """Channel budgets default to 0 for meta (the operator allocates deliberately). Each
    launch reserves the channel's REMAINING budget as that campaign's total authority
    (reddit-parity), so a multi-wake run needs a per-wake capacity top-up — the rig plays
    the operator allocating a fresh period budget before every wake."""
    budgets = store._resolve_business_file(
        slug, "metrics/channel-credit-budgets.json", sync=False)
    budgets.parent.mkdir(parents=True, exist_ok=True)
    budgets.write_text(json.dumps({"meta": {"credits": int(credits)},
                                   "reddit": {"credits": 0}, "x": {"credits": 0}}),
                       encoding="utf-8")


# ------------------------------------------------------------------ per-wake mechanics

def launched_campaigns(store: Any, slug: str) -> dict[str, dict[str, Any]]:
    """campaign -> plan dict, for every campaign with a written launch plan."""
    out: dict[str, dict[str, Any]] = {}
    base = store._resolve_business_file(slug, "distribution/meta-ads", sync=False)
    if not base.is_dir():
        return out
    for plan_path in sorted(base.glob("*/plan.json")):
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if isinstance(plan, dict):
                out[plan_path.parent.name] = plan
        except Exception:
            continue
    return out


def inject_outcomes(store: Any, slug: str, world: RigWorld,
                    tracker: dict[str, dict[str, Any]], campaign_wakes: int,
                    safebox: "RigSafebox | None" = None) -> int:
    """PRODUCTION-RHYTHM injection: a campaign's schedule spans ``campaign_wakes`` wakes, and
    each wake every LIVE campaign receives one period's worth of delivery. The sync receipt
    carries CUMULATIVE campaign totals (the same semantics a real insights sync has), so the
    run-history note shows an evolving mid-flight ROAS the CEO must judge on partial data.
    Settlement to ``completed`` happens ONLY when the schedule ends (period == campaign_wakes)
    — the same budget-exhausted transition production applies. A campaign the CEO paused
    mid-flight stops delivering and stays paused: cutting a loser early really does stop the
    (fake) spend. ``campaign_wakes=1`` reproduces the level-3 compressed behavior."""
    from plugins.takyon import core as takyon_core

    injected = 0
    for campaign, plan in launched_campaigns(store, slug).items():
        state = tracker.setdefault(campaign, {
            "kind": str(plan.get("asset_kind") or "video"), "period": 0,
            "spend_usd": 0.0, "revenue_usd": 0.0, "impressions": 0,
            "clicks": 0, "link_clicks": 0, "purchases": 0,
        })
        if state["period"] >= campaign_wakes:
            continue  # schedule already finished and settled
        try:
            policy = takyon_core._load_ad_spend_policy(slug, channel="meta", slug=campaign)
            policy_status = str(getattr(policy, "status", "") or "")
        except Exception:
            policy_status = ""
        if policy_status == "paused":
            # The CEO cut this campaign mid-flight: no further delivery, no settle — the
            # unspent remainder of its schedule is money the cut saved.
            state.setdefault("cut_at_period", state["period"])
            continue
        state["period"] += 1
        period_draw = world.sync_totals(state["kind"])
        state["spend_usd"] = round(state["spend_usd"] + period_draw["spend_usd"], 2)
        state["revenue_usd"] = round(state["revenue_usd"] + period_draw["purchase_value_usd"], 2)
        for key in ("impressions", "clicks", "link_clicks", "purchases"):
            state[key] += int(period_draw[key if key != "purchases" else "purchase_count"])
        totals = {
            "rows": state["period"],
            "spend_cents": int(round(state["spend_usd"] * 100)),
            "spend_usd": state["spend_usd"],
            "impressions": state["impressions"],
            "clicks": state["clicks"],
            "link_clicks": state["link_clicks"],
            "link_click_conversion_rate": (
                round((state["purchases"] / state["link_clicks"]) * 100.0, 4)
                if state["link_clicks"] else None),
            "purchase_count": state["purchases"],
            "purchase_value_usd": state["revenue_usd"],
            "roas": (round(state["revenue_usd"] / state["spend_usd"], 4)
                     if state["spend_usd"] else None),
        }
        syncs = store._resolve_business_file(slug, f"metrics/meta-ads/{campaign}/syncs", sync=False)
        syncs.mkdir(parents=True, exist_ok=True)
        (syncs / f"rig-sync-{state['period']}.json").write_text(
            json.dumps({"totals": totals}), encoding="utf-8")
        # Keep the LIVE sync surface consistent with the note: register the same cumulative
        # delivery as an insights row on the stub, keyed by every real object id the CEO's
        # own business_meta_ad_insights_sync can query (the receipt's campaign/adset/ad).
        # The real aggregator then computes receipt totals matching what the rig injected.
        if safebox is not None:
            row = {
                "date_start": "2026-07-01",
                "date_stop": f"2026-07-{min(28, state['period'] + 1):02d}",
                "spend": f"{state['spend_usd']:.2f}",
                "impressions": str(state["impressions"]),
                "clicks": str(state["clicks"]),
                "actions": [
                    {"action_type": "purchase", "value": str(state["purchases"])},
                    {"action_type": "link_click", "value": str(state["link_clicks"])},
                ],
                "action_values": [
                    {"action_type": "purchase", "value": f"{state['revenue_usd']:.2f}"},
                ],
            }
            try:
                receipt_path = store._resolve_business_file(
                    slug, f"distribution/meta-ads/{campaign}/receipt.json", sync=False)
                ids = (json.loads(receipt_path.read_text(encoding="utf-8")).get("ids") or {})
                for oid in {str(v) for k, v in ids.items()
                            if v and k in ("campaign_id", "adset_id", "ad_id")}:
                    safebox.insights_rows[oid] = [row]
            except Exception:
                pass
        if state["period"] >= campaign_wakes:
            try:
                takyon_core._update_ad_spend_policy(
                    slug, channel="meta", slug=campaign, status="completed",
                    metadata_patch={"rig_settled": True},
                )
            except Exception:
                pass
        injected += 1
    return injected


_HIST_KIND_RE = re.compile(r"(video|image) ad.*?ROAS ([0-9]+(?:\.[0-9]+)?)")
_HIST_ENTRY_RE = re.compile(
    r"campaign (rig-c\d+) \|.*?(video|image) ad.*?ROAS ([0-9]+(?:\.[0-9]+)?)")

_HOLD_THRESHOLD = 2.5  # mirrors core._ROAS_HISTORY_HOLD_THRESHOLD (the note-header policy)


def _rig_launch(store: Any, slug: str, kind: str, campaign: str, wake_idx: int) -> None:
    from plugins.takyon import meta_ads_v2

    asset = ("product/ugc-ads/shared/ad.mp4" if kind == "video"
             else "product/static-ads/shared/hero.png")
    raw = meta_ads_v2.handle_business_meta_ad_launch({
        "business": slug,
        "slug": campaign,
        "asset_kind": kind,
        "asset_path": asset,
        **({"thumbnail_path": "product/ugc-ads/shared/thumbnail.png"} if kind == "video" else {}),
        "link_url": "https://demo.localtest.me",
        "headline": f"Rig wake {wake_idx + 1} ({kind})",
        "message": "Scripted actor launch — plumbing self-test.",
        "objective": "OUTCOME_TRAFFIC",
        "mode": "paused",
        "daily_budget_usd": 10,
        "idempotency_key": f"rig-{slug}-{campaign}",
    })
    payload = json.loads(raw)
    if not payload.get("success"):
        raise RuntimeError(f"fake-ceo launch failed: {payload.get('error')}")


def fake_ceo_wake(store: Any, slug: str, wake_idx: int) -> None:
    """Zero-token stand-in for the agent turn, following the SAME operator policy the note
    header states, at production rhythm:
    - live campaign with latest cumulative ROAS >= threshold (or no reading yet) -> HOLD;
    - live campaign measurably below threshold -> CUT (pause via the REAL control tool,
      which frees the one-live-campaign slot) and launch ONE changed-approach successor;
    - no live campaign -> launch: a measured winner's kind if one exists, else an untried
      kind, else the best measured mean."""
    from plugins.takyon import core as takyon_core, meta_ads_v2

    history_path = store._resolve_business_file(slug, "metrics/roas/meta.md", sync=False)
    text = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    latest_by_campaign: dict[str, tuple[str, float]] = {}
    samples: dict[str, list[float]] = {}
    for match in _HIST_ENTRY_RE.finditer(text):
        latest_by_campaign[match.group(1)] = (match.group(2), float(match.group(3)))
    for kind_name, roas in latest_by_campaign.values():
        samples.setdefault(kind_name, []).append(roas)

    live = [p for p in takyon_core._list_ad_spend_policies(
                slug, statuses=["reserved", "created_paused", "active"])
            if str(getattr(p, "channel", "")) == "meta"]
    next_campaign = f"rig-c{len(launched_campaigns(store, slug)) + 1}"

    if live:
        current = str(live[0].slug)
        reading = latest_by_campaign.get(current)
        if reading is None or reading[1] >= _HOLD_THRESHOLD:
            return  # HOLD: performing (or no measured evidence yet) — leave it running
        # CUT: pause through the real control tool using the receipt's real object id.
        receipt_path = store._resolve_business_file(
            slug, f"distribution/meta-ads/{current}/receipt.json", sync=False)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        object_id = str(((receipt.get("ids") or {}).get("campaign_id")) or "")
        raw = meta_ads_v2.handle_business_meta_ad_control({
            "business": slug, "slug": current, "operation": "pause",
            "object_id": object_id, "idempotency_key": f"rig-cut-{current}",
        })
        if not json.loads(raw).get("success"):
            raise RuntimeError(f"fake-ceo cut failed: {raw}")
        bad_kind = reading[0]
        kind = next(k for k in KINDS if k != bad_kind)
        _rig_launch(store, slug, kind, next_campaign, wake_idx)
        return

    # No live campaign: continue a measured winner if one exists; otherwise explore an
    # untried kind; otherwise take the best measured mean.
    means = {k: sum(v) / len(v) for k, v in samples.items()}
    winners = [k for k, m in means.items() if m >= _HOLD_THRESHOLD]
    untried = [k for k in KINDS if k not in samples]
    if winners:
        kind = max(winners, key=lambda k: means[k])
    elif untried:
        kind = untried[wake_idx % len(untried)]
    else:
        kind = max(means, key=lambda k: means[k])
    _rig_launch(store, slug, kind, next_campaign, wake_idx)


def real_ceo_wake(slug: str, wake_idx: int, *, max_turns: int) -> None:
    from plugins.takyon import worker

    result = worker.ceo_wake_handler(SimpleNamespace(
        id=f"rig-wake-{wake_idx + 1}", business_slug=slug,
        payload={"max_turns": max_turns},
    ))
    status = getattr(result, "result", None)
    if isinstance(status, dict) and status.get("error"):
        raise RuntimeError(f"wake {wake_idx + 1} failed: {status['error']}")


# ------------------------------------------------------------------ scoring + report

def score(store: Any, slug: str, world: RigWorld, wakes: int,
          tracker: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    from plugins.takyon import core as takyon_core

    plans = launched_campaigns(store, slug)
    ordered = [plan for _name, plan in sorted(plans.items())]
    kinds = [str(p.get("asset_kind") or "?") for p in ordered]
    best = world.best_kind()
    late = kinds[len(kinds) // 2:]
    history_path = store._resolve_business_file(slug, "metrics/roas/meta.md", sync=False)

    # Mid-flight behavior (production rhythm): per-campaign timelines + the two behaviors
    # the choice metric can't see — did winners get HELD to completion, did losers get CUT
    # before burning their whole schedule?
    timelines: list[dict[str, Any]] = []
    winners_cut_early = 0
    losers_cut_early = 0
    losers_run_full = 0
    for campaign in sorted(plans):
        state = (tracker or {}).get(campaign, {})
        try:
            policy = takyon_core._load_ad_spend_policy(slug, channel="meta", slug=campaign)
            status = str(getattr(policy, "status", "") or "")
        except Exception:
            status = "?"
        kind = str(plans[campaign].get("asset_kind") or "?")
        spend = float(state.get("spend_usd") or 0.0)
        roas = round(float(state.get("revenue_usd") or 0.0) / spend, 2) if spend else None
        timelines.append({
            "campaign": campaign, "kind": kind, "status": status,
            "periods_delivered": int(state.get("period") or 0),
            "cumulative_roas": roas,
        })
        if status == "paused":
            if kind == best:
                winners_cut_early += 1
            else:
                losers_cut_early += 1
        elif status == "completed" and kind != best:
            losers_run_full += 1
    return {
        "wakes": wakes,
        "campaigns_launched": len(kinds),
        "kinds_in_order": kinds,
        "sealed_truth": dict(world.mapping),
        "true_best_kind": best,
        "late_share_on_best": (round(sum(1 for k in late if k == best) / len(late), 4)
                               if late else None),
        "campaign_timelines": timelines,
        "winners_cut_early": winners_cut_early,   # want 0: winners are HELD, never paused
        "losers_cut_early": losers_cut_early,     # want >0 when a loser flew: cut, not ridden
        "losers_run_full_schedule": losers_run_full,  # each one = budget a timely cut would have saved
        "history_file": str(history_path),
    }


def run(dsn: str, *, wakes: int, seed: int, fake_ceo: bool, model: str, provider: str,
        transport: str, max_turns: int, campaign_wakes: int = 3) -> dict[str, Any]:
    home = Path(tempfile.mkdtemp(prefix="rl-wake-rig-home-"))
    slug = f"rig{uuid.uuid4().hex[:8]}"
    world = RigWorld(seed)
    tracker: dict[str, dict[str, Any]] = {}
    with rig_environment(dsn, home, model=model, provider=provider, transport=transport) as ctx:
        from plugins.takyon import core as takyon_core

        # No explicit root: default to TAKYON_HOME, the SAME tree every handler-internal
        # _store() resolves — assets/config written here are the ones the real tools read.
        store = takyon_core.TakyonStore()
        seed_business(dsn, store, slug)
        for i in range(wakes):
            # PRODUCTION RHYTHM: each wake, every live campaign first receives one period's
            # partial delivery (as the pre-wake insights refresh would have pulled), then
            # the wake runs — the REAL pre-wake assembler turns the new partial receipts
            # into run-history entries the CEO reads THIS wake and must judge mid-flight.
            # The operator (played by the rig) also allocates this period's channel budget.
            set_meta_channel_budget(store, slug, 40_000 * (i + 1))
            inject_outcomes(store, slug, world, tracker, campaign_wakes, safebox=ctx["safebox"])
            if fake_ceo:
                store.assemble_roas_run_history(slug)  # the worker hook, invoked directly
                fake_ceo_wake(store, slug, i)
            else:
                real_ceo_wake(slug, i, max_turns=max_turns)
            live_now = sum(1 for c, s in tracker.items()
                           if 0 < s.get("period", 0) < campaign_wakes and "cut_at_period" not in s)
            print(f"  wake {i + 1}/{wakes} done "
                  f"({len(launched_campaigns(store, slug))} campaign(s), {live_now} mid-flight)")
        inject_outcomes(store, slug, world, tracker, campaign_wakes, safebox=ctx["safebox"])
        store.assemble_roas_run_history(slug)
        report = score(store, slug, world, wakes, tracker)
    report["campaign_wakes"] = campaign_wakes
    report["takyon_home"] = str(home)
    report["business"] = slug
    return report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Level-3 rig: real CEO wakes with injected ROAS.")
    ap.add_argument("--dsn", default=os.environ.get("TAKYON_TEST_PG_DSN", ""))
    ap.add_argument("--wakes", type=int, default=5)
    ap.add_argument("--seed", type=int, default=int.from_bytes(os.urandom(2), "big"))
    ap.add_argument("--fake-ceo", action="store_true",
                    help="plumbing self-test: scripted actor + real launch tool, zero tokens")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--transport", choices=("gateway", "direct"), default="direct",
                    help="'direct' builds a plain model client from ANTHROPIC_API_KEY; "
                         "'gateway' uses the operator-gateway lane as configured")
    ap.add_argument("--max-turns", type=int, default=24,
                    help="hard cap on agent iterations per real wake (cost guard)")
    ap.add_argument("--campaign-wakes", type=int, default=3,
                    help="how many wakes one campaign's schedule spans (production rhythm: "
                         "each wake injects one period of PARTIAL cumulative results and the "
                         "CEO judges mid-flight; 1 = the old compressed one-wake-per-campaign)")
    args = ap.parse_args(argv)
    if not args.dsn:
        print("error: set TAKYON_TEST_PG_DSN (a migrated throwaway Postgres) or pass --dsn",
              file=sys.stderr)
        return 2

    report = run(args.dsn, wakes=args.wakes, seed=args.seed, fake_ceo=args.fake_ceo,
                 model=args.model, provider=args.provider, transport=args.transport,
                 max_turns=args.max_turns, campaign_wakes=args.campaign_wakes)
    print("\n── level-3 rig report ──")
    for key in ("business", "wakes", "campaign_wakes", "campaigns_launched",
                "kinds_in_order", "sealed_truth", "true_best_kind", "late_share_on_best",
                "winners_cut_early", "losers_cut_early", "losers_run_full_schedule",
                "history_file", "takyon_home"):
        print(f"  {key:<26} {report[key]}")
    print("  campaign timelines:")
    for t in report.get("campaign_timelines", []):
        print(f"    {t['campaign']:<10} {t['kind']:<6} {t['status']:<14} "
              f"periods={t['periods_delivered']} cumROAS={t['cumulative_roas']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
