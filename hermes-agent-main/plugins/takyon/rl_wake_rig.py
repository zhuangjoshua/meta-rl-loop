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
        roas = max(0.05, self.rng.gauss(self.mapping.get(kind, 1.0), self.noise))
        value = round(spend_usd * roas, 2)
        purchases = max(1, int(round(value / 19.0)))  # ~$19 plan price per purchase
        return {
            "rows": 1,
            "spend_cents": int(round(spend_usd * 100)),
            "spend_usd": spend_usd,
            "impressions": int(spend_usd * self.rng.uniform(900, 1400)),
            "clicks": int(spend_usd * self.rng.uniform(15, 22)),
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
        "Grow through paid Meta ads ONLY. Each wake: launch exactly ONE new meta ad campaign "
        "with business_meta_ad_launch (objective Traffic, mode paused, link_url "
        "https://demo.localtest.me, daily_budget_usd 10). Choose the creative kind "
        "deliberately: asset_kind 'video' (asset_path product/ugc-ads/shared/ad.mp4, "
        "thumbnail_path product/ugc-ads/shared/thumbnail.png) or "
        "asset_kind 'image' (asset_path product/static-ads/shared/hero.png) — read "
        "metrics/roas/meta.md first and pick the kind with the better measured ROAS. "
        "Use a fresh campaign slug each time (rig-c1, rig-c2, ...). Do not do research, "
        "posting, SEO, or product work."
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


def inject_outcomes(store: Any, slug: str, world: RigWorld) -> int:
    """For every launched campaign that has no sync receipt yet, write ONE — totals drawn
    from the sealed truth for the creative kind the plan actually used. This is the single
    deliberately-fake seam: 'what did the campaign return'."""
    injected = 0
    for campaign, plan in launched_campaigns(store, slug).items():
        syncs = store._resolve_business_file(slug, f"metrics/meta-ads/{campaign}/syncs", sync=False)
        if syncs.is_dir() and any(syncs.glob("*.json")):
            continue
        syncs.mkdir(parents=True, exist_ok=True)
        totals = world.sync_totals(str(plan.get("asset_kind") or "video"))
        (syncs / "rig-sync-1.json").write_text(json.dumps({"totals": totals}), encoding="utf-8")
        injected += 1
    return injected


_HIST_KIND_RE = re.compile(r"(video|image) ad.*?ROAS ([0-9]+(?:\.[0-9]+)?)")


def fake_ceo_wake(store: Any, slug: str, wake_idx: int) -> None:
    """Zero-token stand-in for the agent turn: reads the SAME run history the real skill is
    instructed to read, picks the creative kind with the better average measured ROAS
    (untried kinds first), and calls the REAL launch handler end to end."""
    from plugins.takyon import meta_ads_v2

    history_path = store._resolve_business_file(slug, "metrics/roas/meta.md", sync=False)
    text = history_path.read_text(encoding="utf-8") if history_path.exists() else ""
    samples: dict[str, list[float]] = {}
    for match in _HIST_KIND_RE.finditer(text):
        samples.setdefault(match.group(1), []).append(float(match.group(2)))
    unseen = [k for k in KINDS if k not in samples]
    if unseen:
        kind = unseen[wake_idx % len(unseen)]
    else:
        kind = max(KINDS, key=lambda k: sum(samples[k]) / len(samples[k]))
    asset = ("product/ugc-ads/shared/ad.mp4" if kind == "video"
             else "product/static-ads/shared/hero.png")
    raw = meta_ads_v2.handle_business_meta_ad_launch({
        "business": slug,
        "slug": f"rig-c{wake_idx + 1}",
        "asset_kind": kind,
        "asset_path": asset,
        **({"thumbnail_path": "product/ugc-ads/shared/thumbnail.png"} if kind == "video" else {}),
        "link_url": "https://demo.localtest.me",
        "headline": f"Rig wake {wake_idx + 1} ({kind})",
        "message": "Scripted actor launch — plumbing self-test.",
        "objective": "OUTCOME_TRAFFIC",
        "mode": "paused",
        "daily_budget_usd": 10,
        "idempotency_key": f"rig-{slug}-w{wake_idx + 1}",
    })
    payload = json.loads(raw)
    if not payload.get("success"):
        raise RuntimeError(f"fake-ceo launch failed: {payload.get('error')}")


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

def score(store: Any, slug: str, world: RigWorld, wakes: int) -> dict[str, Any]:
    plans = launched_campaigns(store, slug)
    ordered = [plan for _name, plan in sorted(plans.items())]
    kinds = [str(p.get("asset_kind") or "?") for p in ordered]
    best = world.best_kind()
    late = kinds[len(kinds) // 2:]
    history_path = store._resolve_business_file(slug, "metrics/roas/meta.md", sync=False)
    return {
        "wakes": wakes,
        "campaigns_launched": len(kinds),
        "kinds_in_order": kinds,
        "sealed_truth": dict(world.mapping),
        "true_best_kind": best,
        "late_share_on_best": (round(sum(1 for k in late if k == best) / len(late), 4)
                               if late else None),
        "history_file": str(history_path),
    }


def run(dsn: str, *, wakes: int, seed: int, fake_ceo: bool, model: str, provider: str,
        transport: str, max_turns: int) -> dict[str, Any]:
    home = Path(tempfile.mkdtemp(prefix="rl-wake-rig-home-"))
    slug = f"rig{uuid.uuid4().hex[:8]}"
    world = RigWorld(seed)
    with rig_environment(dsn, home, model=model, provider=provider, transport=transport) as ctx:
        from plugins.takyon import core as takyon_core

        # No explicit root: default to TAKYON_HOME, the SAME tree every handler-internal
        # _store() resolves — assets/config written here are the ones the real tools read.
        store = takyon_core.TakyonStore()
        seed_business(dsn, store, slug)
        for i in range(wakes):
            # outcomes for last wake's launches land first (as the pre-wake insights
            # refresh would have), then the wake runs: the REAL pre-wake assembler turns
            # them into run-history entries the skill reads this wake. The operator
            # (played by the rig) also allocates this period's channel budget.
            set_meta_channel_budget(store, slug, 40_000 * (i + 1))
            inject_outcomes(store, slug, world)
            if fake_ceo:
                store.assemble_roas_run_history(slug)  # the worker hook, invoked directly
                fake_ceo_wake(store, slug, i)
            else:
                real_ceo_wake(slug, i, max_turns=max_turns)
            print(f"  wake {i + 1}/{wakes} done "
                  f"({len(launched_campaigns(store, slug))} campaign(s) so far)")
        inject_outcomes(store, slug, world)
        store.assemble_roas_run_history(slug)
        report = score(store, slug, world, wakes)
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
    args = ap.parse_args(argv)
    if not args.dsn:
        print("error: set TAKYON_TEST_PG_DSN (a migrated throwaway Postgres) or pass --dsn",
              file=sys.stderr)
        return 2

    report = run(args.dsn, wakes=args.wakes, seed=args.seed, fake_ceo=args.fake_ceo,
                 model=args.model, provider=args.provider, transport=args.transport,
                 max_turns=args.max_turns)
    print("\n── level-3 rig report ──")
    for key in ("business", "wakes", "campaigns_launched", "kinds_in_order",
                "sealed_truth", "true_best_kind", "late_share_on_best",
                "history_file", "takyon_home"):
        print(f"  {key:<22} {report[key]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
