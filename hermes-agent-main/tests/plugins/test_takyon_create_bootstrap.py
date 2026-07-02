from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import cron.jobs as cron_jobs

from plugins.takyon import cli as takyon_cli
from plugins.takyon.core import TakyonStore


def test_create_enqueues_bootstrap_after_business_persists(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    observed = {"scheduled_inline": False, "enqueued": None, "upsert_op": None}

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            if any((op or {}).get("action") == "cron.ensure_ceo_wakeup" for op in operations or []):
                observed["scheduled_inline"] = True
            for op in operations or []:
                if (op or {}).get("action") == "business.upsert":
                    observed["upsert_op"] = dict(op)
            return {"results": [{"action": "business.upsert"}]}

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        observed["enqueued"] = {
            "slug": slug,
            "goal": goal,
            "mode": mode,
            "schedule": schedule,
            "max_turns": max_turns,
        }
        return {
            "action": "ceo_bootstrap.enqueue",
            "business": slug,
            "job_id": "job-123",
            "status": "queued",
            "created": True,
            "schedule": schedule or "",
        }

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_enqueue_pg_ceo_bootstrap", fake_enqueue)
    # This test exercises the enqueue contract, not slug-collision handling. The FakeStore reports the
    # business on read, which the create chokepoint's pre-create _business_exists probe would treat as an
    # existing slug and auto-increment to `latexflow-2`; stub it so the slug under test stays `latexflow`.
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *_a, **_k: False)
    # The chokepoint also runs the operator balance preflight + seeds free credits against the real
    # control-plane DB (each covered by its own PG-gated suite); stub them so this enqueue unit is hermetic.
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(takyon_cli, "_seed_business_free_credits", lambda *_a, **_k: None)

    result = takyon_cli.run_takyon_command(
        ["create", "--live", "--schedule", "every 6h", "latexflow", "overleaf competitor"],
        model="",
        max_turns=7,
    )

    assert observed["scheduled_inline"] is False
    assert observed["enqueued"] == {
        "slug": "latexflow",
        "goal": "overleaf competitor",
        "mode": "live",
        "schedule": "every 6h",
        "max_turns": 7,
    }
    assert observed["upsert_op"]["skip_initial_workspace_sync"] is True
    assert result["success"] is True
    assert result["bootstrap_job"]["job_id"] == "job-123"


def test_create_enqueues_bootstrap_even_when_starter_credit_seed_fails(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    observed = {"enqueued": None}

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            return {"results": [{"action": "business.upsert"}]}

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        observed["enqueued"] = slug
        return {
            "action": "ceo_bootstrap.enqueue",
            "business": slug,
            "job_id": "job-seed-fail",
            "status": "queued",
            "created": True,
            "schedule": schedule or "",
        }

    def fail_seed(*_args, **_kwargs):
        raise RuntimeError("creative credit ledger temporarily unavailable")

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_enqueue_pg_ceo_bootstrap", fake_enqueue)
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *_a, **_k: False)
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(takyon_cli, "_seed_business_free_credits", fail_seed)

    result = takyon_cli.run_takyon_command(
        ["create", "--live", "meal-coach", "meal prep coach"],
        model="",
        max_turns=7,
    )

    assert observed["enqueued"] == "meal-coach"
    assert result["success"] is True
    assert result["bootstrap_job"]["job_id"] == "job-seed-fail"
    assert result["starter_credit_seed"]["status"] == "failed"
    assert "creative credit ledger temporarily unavailable" in result["starter_credit_seed"]["error"]


def test_create_follow_logs_announces_bootstrap_before_credit_seed(monkeypatch, capsys):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            return {"results": [{"action": "business.upsert"}]}

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        return {
            "action": "ceo_bootstrap.enqueue",
            "business": slug,
            "job_id": "job-follow",
            "status": "queued",
            "created": True,
            "schedule": schedule or "",
        }

    observed: list[str] = []

    def fake_seed(*_args, **_kwargs):
        observed.append("seed")
        return {
            "action": "business_credits.bootstrap_free_seed",
            "business": "claimscope",
            "status": "ok",
            "credits": 3,
        }

    def fake_follow(*_args, **_kwargs):
        observed.append("follow")
        return {
            "action": "bootstrap.follow",
            "job_id": "job-follow",
            "status": "running",
        }

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_enqueue_pg_ceo_bootstrap", fake_enqueue)
    monkeypatch.setattr(takyon_cli, "_try_seed_business_free_credits", fake_seed)
    monkeypatch.setattr(takyon_cli, "_follow_worker_job", fake_follow)
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *_a, **_k: False)
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", lambda *_a, **_k: None)

    takyon_cli.run_takyon_command(
        ["create", "--live", "claimscope", "insurance claim copilot"],
        model="",
        max_turns=7,
        follow_logs=True,
    )

    captured = capsys.readouterr().out
    assert "[bootstrap] queued job job-follow for business:claimscope; attaching after starter credit seed..." in captured
    assert observed == ["seed", "follow"]


def test_create_caps_bootstrap_turn_budget(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    observed = {"enqueued": None}

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            return {"results": [{"action": "business.upsert"}]}

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        observed["enqueued"] = {
            "slug": slug,
            "goal": goal,
            "mode": mode,
            "schedule": schedule,
            "max_turns": max_turns,
        }
        return {
            "action": "ceo_bootstrap.enqueue",
            "business": slug,
            "job_id": "job-123",
            "status": "queued",
            "created": True,
            "schedule": schedule or "",
        }

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_enqueue_pg_ceo_bootstrap", fake_enqueue)
    # This test exercises the enqueue contract, not slug-collision handling. The FakeStore reports the
    # business on read, which the create chokepoint's pre-create _business_exists probe would treat as an
    # existing slug and auto-increment to `latexflow-2`; stub it so the slug under test stays `latexflow`.
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *_a, **_k: False)
    # The chokepoint also runs the operator balance preflight + seeds free credits against the real
    # control-plane DB (each covered by its own PG-gated suite); stub them so this enqueue unit is hermetic.
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(takyon_cli, "_seed_business_free_credits", lambda *_a, **_k: None)

    # Request a turn budget well ABOVE the cap so this asserts the capping relationship, not a literal:
    # the enqueued budget must be clamped down to _DEFAULT_BOOTSTRAP_MAX_TURNS (a change-detector pinned
    # to the old literal 20 would silently break every time that cap is retuned).
    takyon_cli.run_takyon_command(
        ["create", "--live", "latexflow", "overleaf competitor"],
        model="",
        max_turns=takyon_cli._DEFAULT_BOOTSTRAP_MAX_TURNS + 50,
    )

    assert observed["enqueued"] == {
        "slug": "latexflow",
        "goal": "overleaf competitor",
        "mode": "live",
        "schedule": "every 6h",
        "max_turns": takyon_cli._DEFAULT_BOOTSTRAP_MAX_TURNS,
    }


def test_create_does_not_read_summary_before_enqueue(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    observed: dict[str, object] = {}

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            return {"results": [{"action": "business.upsert", "business": "mealcoach"}]}

        def read(self, *args, **kwargs):
            raise AssertionError("create should not issue a summary read before enqueue")

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        observed["slug"] = slug
        observed["goal"] = goal
        return {
            "action": "ceo_bootstrap.enqueue",
            "business": slug,
            "job_id": "job-no-summary-read",
            "status": "queued",
            "created": True,
            "schedule": schedule or "",
        }

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_enqueue_pg_ceo_bootstrap", fake_enqueue)
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *_a, **_k: False)
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(takyon_cli, "_seed_business_free_credits", lambda *_a, **_k: None)

    result = takyon_cli.run_takyon_command(
        ["create", "--live", "mealcoach", "daily nutrition tracker"],
        model="",
        max_turns=7,
    )

    assert observed == {"slug": "mealcoach", "goal": "daily nutrition tracker"}
    assert result["bootstrap_job"]["job_id"] == "job-no-summary-read"


def test_enqueue_pg_ceo_bootstrap_stamps_preferred_worker_claim(monkeypatch):
    captured: dict[str, object] = {}

    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeStore:
        def _connect(self):
            return _Ctx()

        def _leaf_conn(self, _conn):
            return _Ctx()

    monkeypatch.setenv("TAKYON_PREFERRED_WORKER_ID_PREFIX", "mac-operator-Local-")
    monkeypatch.setenv("TAKYON_PREFERRED_WORKER_CLAIM_SECONDS", "90")

    from plugins.takyon import jobs as takyon_jobs

    monkeypatch.setattr(takyon_jobs, "list_jobs", lambda *_a, **_k: [])

    def fake_enqueue(_raw, slug, kind, *, idempotency_key, payload, max_attempts):
        captured.update(
            {
                "slug": slug,
                "kind": kind,
                "payload": dict(payload),
                "max_attempts": max_attempts,
                "idempotency_key": idempotency_key,
            }
        )
        return SimpleNamespace(id="job-pref", status="queued")

    monkeypatch.setattr(takyon_jobs, "enqueue", fake_enqueue)

    result = takyon_cli._enqueue_pg_ceo_bootstrap(
        FakeStore(),
        "latexflow",
        goal="overleaf competitor",
        mode="live",
        schedule="every 6h",
        max_turns=7,
    )

    assert captured["slug"] == "latexflow"
    assert captured["kind"] == "ceo_bootstrap"
    assert captured["max_attempts"] == 2
    assert captured["payload"] == {
        "goal": "overleaf competitor",
        "mode": "live",
        "schedule": "every 6h",
        "max_turns": 7,
        "estimate_cents": takyon_cli._operator_turn_estimate_cents(),
        "preferred_worker_id_prefix": "mac-operator-Local-",
        "preferred_worker_claim_seconds": 90,
    }
    assert result["job_id"] == "job-pref"


def test_bootstrap_preferred_worker_claim_defaults_to_one_hour(monkeypatch):
    monkeypatch.setenv("TAKYON_PREFERRED_WORKER_ID_PREFIX", "mac-operator-Local-")
    monkeypatch.delenv("TAKYON_PREFERRED_WORKER_CLAIM_SECONDS", raising=False)

    assert takyon_cli._bootstrap_preferred_worker_claim_payload() == {
        "preferred_worker_id_prefix": "mac-operator-Local-",
        "preferred_worker_claim_seconds": 3600,
    }


def test_bootstrap_goal_requests_product_workflow_for_featureful_saas_goal():
    goal = (
        "Build a consumer SaaS that helps renters dispute unfair security-deposit deductions "
        "with photo evidence, statute-aware deadlines, and demand-letter automation."
    )

    assert takyon_cli._bootstrap_goal_requests_product_workflow(goal) is True
    assert takyon_cli._bootstrap_turn_cap_for_goal(goal) == takyon_cli._WORKFLOW_BOOTSTRAP_MAX_TURNS


def test_bootstrap_goal_does_not_flag_simple_brand_goal_as_product_workflow():
    goal = "Overleaf competitor for legal teams."

    assert takyon_cli._bootstrap_goal_requests_product_workflow(goal) is False
    assert takyon_cli._bootstrap_turn_cap_for_goal(goal) == takyon_cli._DEFAULT_BOOTSTRAP_MAX_TURNS


def test_ensure_ceo_wakeup_can_defer_first_run(monkeypatch, tmp_path):
    store = TakyonStore(tmp_path)

    class _FakeConn:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    created: dict[str, object] = {}

    def _fake_create_job(**kwargs):
        created["create_kwargs"] = dict(kwargs)
        return {
            "id": "job-123",
            "schedule_display": "every 360m",
            "next_run_at": "",
        }

    def _fake_update_job(job_id, updates):
        created["update_job_id"] = job_id
        created["update_payload"] = dict(updates)
        return {
            "id": job_id,
            "schedule_display": "every 360m",
            "next_run_at": str(updates.get("next_run_at") or ""),
        }

    monkeypatch.setattr(store, "_connect", lambda: _FakeConn())
    monkeypatch.setattr(store, "_control_blocker", lambda conn, scope: None)
    monkeypatch.setattr("plugins.takyon.core._db_backend", lambda: "sqlite")
    monkeypatch.setattr(cron_jobs, "list_jobs", lambda include_disabled=True: [])
    monkeypatch.setattr(cron_jobs, "create_job", _fake_create_job)
    monkeypatch.setattr(cron_jobs, "update_job", _fake_update_job)

    before = datetime.now(timezone.utc)
    result = store._ensure_ceo_cron(
        "crm",
        schedule="every 6h",
        reason="bootstrap completed and enabled CEO wake loop",
        defer_first_run=True,
    )

    deferred = datetime.fromisoformat(str(result["next_run_at"]))
    assert deferred >= before + timedelta(hours=5, minutes=59)
    assert deferred <= before + timedelta(hours=6, minutes=1)
    assert created["update_job_id"] == "job-123"
    assert created["update_payload"] == {"next_run_at": str(result["next_run_at"])}
    assert result["defer_first_run"] is True
