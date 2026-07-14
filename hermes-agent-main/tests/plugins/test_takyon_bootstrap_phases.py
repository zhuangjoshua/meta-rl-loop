from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.takyon.bootstrap_phases import (
    AuthoritativePhaseEvidence,
    BOOTSTRAP_PHASES,
    BootstrapPhaseError,
    PHASE_ALLOWED_TOOLS,
    PostgresBootstrapPhaseStore,
    phase_prompt,
)
from plugins.takyon import core, worker


class _Cursor:
    def __init__(self, one=None):
        self.one = one

    def fetchone(self):
        return self.one


class _PhaseConn:
    def __init__(self) -> None:
        self.row = None
        self.selects: list[str] = []
        self.select_params: list[tuple[object, ...]] = []
        self.updates: list[str] = []
        self.update_params: list[tuple[object, ...]] = []

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if normalized.startswith("select 1 from public.businesses"):
            return _Cursor((1,))
        if normalized.startswith("insert into public.bootstrap_phase_runs"):
            if self.row is None:
                (
                    job_id,
                    session_id,
                    owner,
                    business,
                    _digest,
                    inputs,
                    _plan,
                    idempotency,
                    current,
                    _first_attempt,
                    _last_attempt,
                ) = params
                self.row = {
                    "job_id": job_id,
                    "sdk_session_id": session_id,
                    "owner_user_id": owner,
                    "business_slug": business,
                    "immutable_inputs": json.loads(inputs),
                    "phase_idempotency": json.loads(idempotency),
                    "current_phase": current,
                    "completed_phases": [],
                    "phase_evidence": {},
                    "phase_receipts": {},
                    "phase_attempts": {},
                    "status": "running",
                }
            return _Cursor()
        if normalized.startswith("select job_id"):
            self.selects.append(normalized)
            self.select_params.append(tuple(params))
            return _Cursor(dict(self.row) if self.row is not None else None)
        if "set last_job_attempt = greatest" in normalized:
            self.updates.append(normalized)
            self.update_params.append(tuple(params))
            return _Cursor()
        if "set phase_attempts =" in normalized:
            self.updates.append(normalized)
            self.update_params.append(tuple(params))
            self.row["phase_attempts"] = json.loads(params[0])
            return _Cursor()
        if "set phase_receipts =" in normalized:
            self.updates.append(normalized)
            self.update_params.append(tuple(params))
            self.row["phase_receipts"] = json.loads(params[0])
            return _Cursor()
        if "set completed_phases =" in normalized:
            self.updates.append(normalized)
            self.update_params.append(tuple(params))
            completed, evidence, current, status = params[:4]
            self.row["completed_phases"] = json.loads(completed)
            self.row["phase_evidence"] = json.loads(evidence)
            self.row["current_phase"] = current
            self.row["status"] = status
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")


def _new_store():
    conn = _PhaseConn()
    owner = str(uuid.uuid4())
    store = PostgresBootstrapPhaseStore(
        operator_user_id=owner,
        business_slug="acme",
        connection_factory=lambda: conn,
    )
    ids = {
        "job_id": str(uuid.uuid4()),
        "sdk_session_id": str(uuid.uuid4()),
        "owner_user_id": owner,
        "business_slug": "acme",
        "immutable_inputs": {
            "goal": "Build a real planning product",
            "business_name": "Acme",
            "workflow_requested": True,
            "archetype": "web_saas",
        },
        "job_attempt": 1,
    }
    return store, conn, ids


def test_phase_store_locks_and_reconciles_committed_effect_before_model_retry() -> None:
    store, conn, ids = _new_store()
    run = store.initialize_or_load(**ids)
    assert run.current_phase == "preflight"

    committed_artifacts = {"preflight", "brief"}
    model_calls = 0

    def verifier(_run, phase):
        if phase in committed_artifacts:
            return AuthoritativePhaseEvidence("test-runtime", {"phase": phase})
        return None

    run = store.reconcile_first_incomplete(ids["job_id"], verifier)
    assert run.current_phase == "surface"
    assert run.completed_phases == ("preflight", "brief")
    assert model_calls == 0
    # Only the two post-update return reads omit a lock; every load/mutation read is FOR UPDATE.
    assert sum("for update" not in query for query in conn.selects) == 2
    assert all("owner_user_id = %s::uuid" in query for query in conn.selects)
    assert all("business_slug = %s" in query for query in conn.selects)
    assert all("owner_user_id = %s::uuid" in query for query in conn.updates)
    assert all("business_slug = %s" in query for query in conn.updates)
    assert all(
        params == (ids["job_id"], ids["owner_user_id"], ids["business_slug"])
        for params in conn.select_params
    )
    assert all(
        params[-3:] == (
            ids["job_id"],
            ids["owner_user_id"],
            ids["business_slug"],
        )
        for params in conn.update_params
    )


def test_phase_store_rejects_changed_immutable_retry_and_model_text_evidence() -> None:
    store, _conn, ids = _new_store()
    store.initialize_or_load(**ids)
    changed = {**ids, "immutable_inputs": {**ids["immutable_inputs"], "goal": "changed"}}
    with pytest.raises(BootstrapPhaseError, match="changed immutable"):
        store.initialize_or_load(**changed)
    with pytest.raises(BootstrapPhaseError, match="runtime-authoritative"):
        store.complete_phase(
            ids["job_id"],
            "preflight",
            AuthoritativePhaseEvidence("model-text", {"claim": "done"}),
        )


def test_phase_tool_receipts_are_bound_to_current_phase_and_stable_key() -> None:
    store, conn, ids = _new_store()
    run = store.initialize_or_load(**ids)
    store.complete_phase(
        ids["job_id"], "preflight", AuthoritativePhaseEvidence("runtime", {})
    )
    run = store.load(ids["job_id"])
    key = run.phase_idempotency["brief"]["artifact"]
    store.record_tool_receipt(
        ids["job_id"],
        "brief",
        tool_name="business_write_file",
        args={"idempotency_key": key},
        result=json.dumps({"success": True}),
    )
    assert conn.row["phase_receipts"]["brief"][0]["success"] is True
    store.record_tool_receipt(
        ids["job_id"],
        "brief",
        tool_name="skill_read_resource",
        args={"skill": "design-taste-frontend", "path": "references/rules.md"},
        result="published skill guidance",
    )
    update_key = run.phase_idempotency["brief"]["operator_update"]
    store.record_operator_update_receipt(
        ids["job_id"],
        "brief",
        args={"idempotency_key": update_key},
        result=json.dumps({"success": True, "results": [{"event": "event-1"}]}),
    )
    assert conn.row["phase_receipts"]["brief"][-1]["tool"] == (
        "business_post_operator_update"
    )
    with pytest.raises(BootstrapPhaseError, match="unbound idempotency"):
        store.record_tool_receipt(
            ids["job_id"],
            "brief",
            tool_name="business_write_file",
            args={"idempotency_key": "fresh-unsafe-key"},
            result=json.dumps({"success": True}),
        )


def test_phase_prompts_and_toolsets_are_bounded() -> None:
    store, _conn, ids = _new_store()
    run = store.initialize_or_load(**ids)
    run = store.complete_phase(
        ids["job_id"], "preflight", AuthoritativePhaseEvidence("runtime", {})
    )
    prompt = phase_prompt(
        run,
        "brief",
        public_site_url="https://acme.coscale.app/",
        animations=False,
    )
    assert "research/strategy.md" in prompt
    assert "final_workflow_build_publish" not in prompt
    assert "business_refresh_product_surface" not in prompt
    assert "business_claude_agent_task" not in set().union(*PHASE_ALLOWED_TOOLS.values())
    assert "business_post_operator_update" not in set().union(
        *PHASE_ALLOWED_TOOLS.values()
    )
    final_prompt = phase_prompt(
        run,
        "final_workflow_build_publish",
        public_site_url="https://acme.coscale.app/",
        animations=False,
    )
    assert "design-taste-frontend" in final_prompt
    assert "business_invoke_app_action" not in PHASE_ALLOWED_TOOLS[
        "final_workflow_build_publish"
    ]
    assert tuple(PHASE_ALLOWED_TOOLS) == BOOTSTRAP_PHASES
    assert PHASE_ALLOWED_TOOLS["preflight"] == frozenset()
    for phase in (
        "brief",
        "landing_build_publish",
        "logo",
        "final_workflow_build_publish",
        "mobile",
    ):
        assert {
            "business_read_business",
            "business_read_file",
            "business_list_files",
        } <= PHASE_ALLOWED_TOOLS[phase]
    assert {
        "business_read_business",
        "business_read_file",
        "business_list_files",
    } <= PHASE_ALLOWED_TOOLS["surface"]
    with pytest.raises(BootstrapPhaseError, match="no model prompt"):
        phase_prompt(
            run,
            "preflight",
            public_site_url="https://acme.coscale.app/",
            animations=False,
        )


def test_long_seeded_goal_does_not_skip_taste_brief_phase(tmp_path) -> None:
    goal = (
        "Build a real planning product for distributed teams that turns uncertain "
        "decisions into accountable weekly actions without fabricated evidence."
    )
    strategy = tmp_path / "research" / "strategy.md"
    strategy.parent.mkdir(parents=True)
    strategy.write_text(f"# Acme\n\nGoal: {goal}\n", encoding="utf-8")

    run = SimpleNamespace(
        business_slug="acme",
        owner_user_id=str(uuid.uuid4()),
        phase_receipts={},
        phase_idempotency={},
    )
    store = SimpleNamespace(_business_root=lambda _slug: tmp_path)

    assert len(strategy.read_text(encoding="utf-8")) > 80
    assert (
        worker._bootstrap_phase_authoritative_evidence(
            store,
            run,
            "brief",
            workflow_requested=True,
            archetype="web_saas",
        )
        is None
    )

    strategy.write_text(
        """# Acme

## Audience
Distributed product teams coordinating high-consequence weekly decisions.

## Offer
A planning workspace that turns uncertainty into owned actions and reviewable outcomes.

## Positioning and tone
Calm, precise, evidence-aware, and operational rather than motivational.
""",
        encoding="utf-8",
    )
    evidence = worker._bootstrap_phase_authoritative_evidence(
        store,
        run,
        "brief",
        workflow_requested=True,
        archetype="web_saas",
    )
    assert evidence is not None
    assert evidence.source == "workspace-artifact"


def test_final_product_phase_does_not_gate_on_skill_receipts(
    monkeypatch,
) -> None:
    class SurfaceStore:
        def _business_root(self, _slug):
            return Path(".")

        def _connect(self):
            return nullcontext(object())

        def _app_surface_contract(self, _conn, _slug):
            return {"live_build_id": "final-build"}

    run = SimpleNamespace(
        business_slug="acme",
        owner_user_id=str(uuid.uuid4()),
        phase_receipts={"final_workflow_build_publish": []},
        phase_idempotency={},
    )
    monkeypatch.setattr(
        worker, "_bootstrap_has_durable_live_product", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        worker,
        "_bootstrap_real_http_actions",
        lambda *_args, **_kwargs: {"generate-plan"},
    )

    evidence = worker._bootstrap_phase_authoritative_evidence(
        SurfaceStore(),
        run,
        "final_workflow_build_publish",
        workflow_requested=True,
        archetype="web_saas",
    )
    assert evidence is not None
    assert "required_skills_invoked" not in evidence.details


def test_landing_publication_does_not_gate_on_skill_receipts() -> None:
    class SurfaceStore:
        def _business_root(self, _slug):
            return Path(".")

        def _connect(self):
            return nullcontext(object())

        def _app_surface_contract(self, _conn, _slug):
            return {
                "live_build_id": "landing-build",
                "metadata": {
                    "takyon_publish": {
                        "status": "published",
                        "public_url": "https://acme.coscale.app/",
                    }
                },
            }

    evidence = worker._bootstrap_phase_authoritative_evidence(
        SurfaceStore(),
        SimpleNamespace(
            business_slug="acme",
            owner_user_id=str(uuid.uuid4()),
            phase_receipts={"landing_build_publish": []},
            phase_idempotency={},
        ),
        "landing_build_publish",
        workflow_requested=True,
        archetype="web_saas",
    )

    assert evidence is not None
    assert evidence.source == "live-product-publication"


def test_landing_publication_prefers_authoritative_columns_over_stale_attempt() -> None:
    class SurfaceStore:
        def _business_root(self, _slug):
            return Path(".")

        def _connect(self):
            return nullcontext(object())

        def _app_surface_contract(self, _conn, _slug):
            return {
                "publish_status": "published",
                "live_build_id": "landing-build",
                "public_url": "https://acme.coscale.app/",
                "metadata": {
                    "takyon_publish": {
                        "status": "blocked",
                        "blocker": "old typecheck failure",
                    }
                },
            }

    evidence = worker._bootstrap_phase_authoritative_evidence(
        SurfaceStore(),
        SimpleNamespace(
            business_slug="acme",
            owner_user_id=str(uuid.uuid4()),
            phase_receipts={"landing_build_publish": []},
            phase_idempotency={},
        ),
        "landing_build_publish",
        workflow_requested=True,
        archetype="web_saas",
    )

    assert evidence is not None
    assert evidence.details == {
        "build_id": "landing-build",
        "public_url": "https://acme.coscale.app/",
        "status": "published",
    }


def test_brief_phase_does_not_gate_on_failed_skill_receipt(tmp_path) -> None:
    strategy = tmp_path / "research" / "strategy.md"
    strategy.parent.mkdir(parents=True)
    strategy.write_text(
        "# Acme\n\n## Audience\nTeams with real planning needs.\n\n"
        "## Offer\nA focused planning product with accountable outcomes.\n",
        encoding="utf-8",
    )
    run = SimpleNamespace(
        business_slug="acme",
        owner_user_id=str(uuid.uuid4()),
        phase_receipts={
            "brief": [
                {
                    "tool": "__primary_agent_runtime__",
                    "status": "failed",
                    "skills_invoked": [
                        "takyon-approved-skills:design-taste-frontend"
                    ],
                }
            ]
        },
        phase_idempotency={},
    )

    evidence = worker._bootstrap_phase_authoritative_evidence(
        SimpleNamespace(_business_root=lambda _slug: tmp_path),
        run,
        "brief",
        workflow_requested=True,
        archetype="web_saas",
    )
    assert evidence is not None
    assert evidence.source == "workspace-artifact"


def test_phase_runtime_receipt_preserves_native_skill_observability() -> None:
    store, conn, ids = _new_store()
    store.initialize_or_load(**ids)
    conn.row["current_phase"] = "final_workflow_build_publish"

    store.record_runtime_completion(
        ids["job_id"],
        "final_workflow_build_publish",
        runtime_receipt={
            "session_id": ids["sdk_session_id"],
            "invocation_id": str(uuid.uuid4()),
            "skill_receipt": {
                "attempted": [
                    "takyon-approved-skills:design-taste-frontend"
                ],
                "invoked": [
                    "takyon-approved-skills:design-taste-frontend"
                ],
            },
        },
    )

    receipt = conn.row["phase_receipts"]["final_workflow_build_publish"][0]
    assert receipt["skills_invoked"] == [
        "takyon-approved-skills:design-taste-frontend"
    ]


def test_phase_transition_posts_one_runtime_owned_customer_update(monkeypatch) -> None:
    store, _conn, ids = _new_store()
    run = store.initialize_or_load(**ids)
    run = store.complete_phase(
        ids["job_id"], "preflight", AuthoritativePhaseEvidence("runtime", {})
    )
    posted = {}
    recorded = {}

    def post(args):
        posted.update(args)
        return json.dumps({"success": True, "results": [{"event": "event-1"}]})

    class ReceiptStore:
        def record_operator_update_receipt(self, job_id, phase, **kwargs):
            recorded.update({"job_id": job_id, "phase": phase, **kwargs})

    monkeypatch.setattr(core, "handle_business_post_operator_update", post)
    worker._post_bootstrap_phase_operator_update(ReceiptStore(), run, "brief")

    assert posted["idempotency_key"] == run.phase_idempotency["brief"][
        "operator_update"
    ]
    assert posted["headline"] == "Defining your offer"
    assert posted["milestones"][0]["status"] == "running"
    assert recorded["phase"] == "brief"
    assert "bootstrap" not in (posted["headline"] + posted["summary"]).lower()


def test_final_phase_posts_a_distinct_all_completed_operator_update(monkeypatch) -> None:
    store, _conn, ids = _new_store()
    run = store.initialize_or_load(**ids)
    posted = {}
    recorded = {}

    def post(args):
        posted.update(args)
        return json.dumps({"success": True, "results": [{"event": "event-final"}]})

    class ReceiptStore:
        def record_operator_update_receipt(self, job_id, phase, **kwargs):
            recorded.update({"job_id": job_id, "phase": phase, **kwargs})

    monkeypatch.setattr(core, "handle_business_post_operator_update", post)
    worker._post_bootstrap_phase_operator_update(
        ReceiptStore(), run, "finalize", completed=True
    )

    assert posted["idempotency_key"] == run.phase_idempotency["finalize"][
        "operator_update_completed"
    ]
    assert all(item["status"] == "completed" for item in posted["milestones"])
    assert recorded["phase"] == "finalize"
    assert "bootstrap" not in (posted["headline"] + posted["summary"]).lower()


def test_completed_run_accepts_only_the_bound_final_customer_update() -> None:
    store, conn, ids = _new_store()
    run = store.initialize_or_load(**ids)
    for phase in BOOTSTRAP_PHASES:
        run = store.complete_phase(
            ids["job_id"], phase, AuthoritativePhaseEvidence("runtime", {})
        )
    assert run.status == "completed"

    completed_key = run.phase_idempotency["finalize"][
        "operator_update_completed"
    ]
    store.record_operator_update_receipt(
        ids["job_id"],
        "finalize",
        args={"idempotency_key": completed_key},
        result=json.dumps({"success": True, "results": [{"event": "event-final"}]}),
    )
    assert conn.row["phase_receipts"]["finalize"][-1][
        "idempotency_key"
    ] == completed_key

    with pytest.raises(BootstrapPhaseError, match="not runtime-bound"):
        store.record_operator_update_receipt(
            ids["job_id"],
            "finalize",
            args={"idempotency_key": "unbound-final-update"},
            result=json.dumps({"success": True}),
        )


def test_0091_migration_is_operator_only_row_locked_state() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "takyon"
        / "db"
        / "migrations"
        / "0091_bootstrap_phase_runs.sql"
    ).read_text(encoding="utf-8").lower()
    assert "force row level security" in migration
    assert "takyon_operator_runtime" in migration
    assert "grant select, insert, update" in migration
    assert "revoke all on table public.bootstrap_phase_runs" in migration
    assert "to takyon_app_runtime" not in migration
    assert "immutable_guard" in migration
    assert "phase_receipts" in migration
    assert "grant delete" not in migration
