"""Targeted tests for the MVP /goal "task-ui" build group.

Covers four cards:
  * Task rollup in Ui and renaming  -> canonical_task description/category/title + task_id grouping
  * Dont show all raw documents       -> _takyon_hide_operator_output filters internal files
  * Surface bootstrap artifacts linearly -> deliverables stay newest-first
  * we should show the actual url       -> publish_target reaches overview.product (backend slice)

These are presentation/backend-contract tests; the UI rendering is exercised by
the grouped E2E (see DONE.md GROUP E2E entry).
"""

from tui_gateway import server


def _tasks_for(overview):
    return server._takyon_live_state_payload(overview, None)["tasks"]


# ── Task rollup: canonical_task enriches every task ─────────────────────────

def test_canonical_task_includes_description_and_category():
    """Spec criterion #5: each task dict has non-empty description + a category
    in {RESEARCH, PRODUCT, LAUNCH}."""
    overview = {
        "tasks": [
            {
                "id": "t1",
                "source": "job",
                "label": "Research LinkedIn ghostwriting market",
                "status": "completed",
                "detail": "Wrote research/market.md with competitor pricing.",
            },
            {
                "id": "t2",
                "source": "job",
                "label": "business_write_file",
                "status": "running",
                "detail": "Building the product site offer page.",
            },
            {
                "id": "t3",
                "source": "cron",
                "label": "CEO wake loop",
                "status": "queued",
                "detail": "Next wake scheduled.",
            },
        ],
    }
    tasks = _tasks_for(overview)
    by_id = {t["id"]: t for t in tasks}

    for task in tasks:
        assert task["description"], f"empty description on {task['id']}"
        assert task["category"] in {"RESEARCH", "PRODUCT", "LAUNCH"}, task["category"]
        # status pill must be a canonical value, never a raw runtime status.
        assert task["status"] in {"running", "queued", "failed", "completed", "idle"}
        assert task["status_label"]

    assert by_id["t1"]["category"] == "RESEARCH"
    assert by_id["t2"]["category"] == "PRODUCT"
    assert by_id["t3"]["category"] == "LAUNCH"


def test_canonical_task_title_is_intent_first_not_raw_tool_name():
    """Spec criterion #2: raw tool-call strings are humanised into intent titles."""
    overview = {
        "tasks": [
            {
                "id": "t1",
                "source": "job",
                "label": "business_write_file",
                "status": "running",
                "detail": "Writing the product spec.",
            },
        ],
    }
    task = _tasks_for(overview)[0]
    # BUG-005 fail-closed: a raw tool-shaped label ("business_write_file") never
    # reaches the card as a de-identified tool name ("Business Write File"); it is
    # replaced with a general business-language title so no raw tool identifier can
    # title an operator-facing card.
    assert task["title"] == "Working on the company"
    assert "business_write_file" not in task["title"]
    assert "Write File" not in task["title"]
    # The raw label is preserved (so the detail panel can show it) but is not the title.
    assert task["label"] == "business_write_file"


def test_raw_runtime_events_group_under_current_task_id():
    """Spec criterion #6: low-level runtime events attach to a parent task_id
    rather than appearing as standalone flat entries."""
    overview = {
        "tasks": [
            {
                "id": "intent-1",
                "source": "job",
                "label": "Ship the offer page",
                "status": "running",
                "detail": "Building the product site.",
            },
            {
                "id": "trace-1",
                "source": "runtime",
                "label": "CEO live trace",
                "status": "running",
                "detail": "tool: write_file product/site/index.html",
            },
        ],
    }
    payload = server._takyon_live_state_payload(overview, None)
    tasks = {t["id"]: t for t in payload["tasks"]}
    assert payload["current_task_id"] == "intent-1"
    # The raw runtime trace is re-parented to the intent task.
    assert tasks["trace-1"]["task_id"] == "intent-1"
    # The intent task is its own parent.
    assert tasks["intent-1"]["task_id"] == "intent-1"


def test_work_request_payload_milestone_surfaces_on_card():
    """A work-request job whose payload carries an operator-facing milestone
    (title/description/category) surfaces those verbatim on the card instead of
    the static job_label/job_detail."""
    overview = {
        "tasks": [
            {
                "id": "job:wr-1",
                "source": "job",
                # The job loop maps the CEO milestone title->label / description->detail
                # and also passes them explicitly so they survive verbatim.
                "label": "Build the drift-detection agent",
                "title": "Build the drift-detection agent",
                "description": "Stand up the monitoring agent that flags model drift for customers.",
                "category": "PRODUCT",
                "status": "running",
                "detail": "Stand up the monitoring agent that flags model drift for customers.",
            },
        ],
    }
    task = _tasks_for(overview)[0]
    # Operator-facing milestone text wins over any static job_label humanisation.
    assert task["title"] == "Build the drift-detection agent"
    assert task["description"] == (
        "Stand up the monitoring agent that flags model drift for customers."
    )
    # The CEO-chosen category is honoured, not re-derived heuristically.
    assert task["category"] == "PRODUCT"


def test_failed_branch_still_exposes_canonical_task_fields():
    """Regression guard: the failed/stale-running branch still emits the new fields."""
    payload = server._takyon_live_state_payload(
        {
            "ceo_loop": {"status": "recovering", "headline": "1 blocker", "detail": "blocked"},
            "tasks": [
                {"id": "r", "source": "runtime", "label": "CEO live trace", "status": "running", "detail": "old"},
                {"id": "j", "source": "job", "label": "CEO turn", "status": "failed", "detail": "exit -15"},
            ],
        },
        None,
    )
    assert payload["status"] == "failed"
    failed = next(t for t in payload["tasks"] if t["status"] == "failed")
    assert failed["description"]
    assert failed["category"] in {"RESEARCH", "PRODUCT", "LAUNCH"}


# ── Curated operator update: business_post_operator_update → card + milestones ──

def test_operator_update_milestones_become_primary_intent_cards():
    """A curated CEO update (business_post_operator_update) lands its milestones as
    primary intent cards (title/description/category/status), and the running
    milestone is the anchor that raw runtime/tool events nest under."""
    overview = {
        "tasks": [
            # Operator-authored milestone (source operator_update) — the PRIMARY card.
            {
                "id": "milestone:0",
                "source": "operator_update",
                "label": "Build the autonomous drift-detection agent",
                "title": "Build the autonomous drift-detection agent",
                "description": "Connect to a model API and track prediction patterns for customers.",
                "category": "PRODUCT",
                "status": "running",
                "detail": "Connect to a model API and track prediction patterns for customers.",
            },
            # Raw worker trace that should NOT be a top-level row — it nests under
            # the running milestone.
            {
                "id": "trace-1",
                "source": "runtime",
                "label": "Claude Agent Task",
                "status": "running",
                "detail": "tool: write_file product/site/index.html",
            },
        ],
    }
    payload = server._takyon_live_state_payload(overview, None)
    tasks = {t["id"]: t for t in payload["tasks"]}
    milestone = tasks["milestone:0"]
    # The CEO-chosen milestone text/category survive verbatim (not re-derived).
    assert milestone["title"] == "Build the autonomous drift-detection agent"
    assert milestone["description"] == (
        "Connect to a model API and track prediction patterns for customers."
    )
    assert milestone["category"] == "PRODUCT"
    assert milestone["status_label"] == "RUNNING"
    # The milestone is the intent anchor; the raw worker trace nests under it.
    assert payload["current_task_id"] == "milestone:0"
    assert tasks["trace-1"]["task_id"] == "milestone:0"


def test_operator_update_copy_surfaces_on_live_state():
    """The curated headline + summary (mirrored onto ceo_loop) is copied onto the
    live_state so the chat 'CEO update' card shows curated copy, not raw text."""
    live_state = {"status": "running", "label": "x", "detail": "y", "tasks": []}
    server._takyon_attach_operator_update_copy(
        live_state,
        {
            "ceo_loop": {
                "status": "working",
                "headline": "Standing up your drift-detection product",
                "detail": "I'm wiring the monitoring agent and putting the first page online.",
            }
        },
    )
    assert live_state["headline"] == "Standing up your drift-detection product"
    assert live_state["summary"] == (
        "I'm wiring the monitoring agent and putting the first page online."
    )


def test_operator_update_categories_match_gateway_taxonomy():
    """The CEO-facing milestone category taxonomy (core.OPERATOR_UPDATE_CATEGORIES)
    stays in lockstep with the gateway Tasks-panel taxonomy so a posted milestone
    category always renders a valid pill (no parallel taxonomy)."""
    from plugins.takyon import core

    assert tuple(core.OPERATOR_UPDATE_CATEGORIES) == server._TAKYON_TASK_CATEGORIES
    # Every operator-update status maps to a canonical Tasks-panel pill label.
    for status in core.OPERATOR_UPDATE_STATUSES:
        assert status in server._TAKYON_TASK_STATUS_LABELS


# ── Dont show all raw documents: internal files are hidden ──────────────────

def test_hide_operator_output_filters_internal_files():
    hide = server._takyon_hide_operator_output
    # Internal toolchain/config files are hidden.
    assert hide("product/site/SKILL.md") is True
    assert hide("config.yaml") is True
    assert hide("product/site/package.json") is True
    assert hide("product/site/uv.lock") is True
    assert hide("product/site/pnpm-lock.yaml") is True
    assert hide("product/site/node_modules/react/index.js") is True
    assert hide("product/site/.next/cache/chunk.js") is True
    assert hide("src/app/page.tsx") is True  # code module
    # Business-meaningful artifacts stay visible.
    assert hide("research/market.md") is False
    assert hide("product/surface.md") is False
    assert hide("product/site/index.html") is False
    assert hide("metrics/receipts/outreach/x-1.json") is False


def test_deliverables_payload_drops_internal_and_keeps_business_docs():
    overview = {
        "research": {"outputs": [{"path": "research/market.md", "at": 30, "source": "research"}]},
    }
    outputs = [
        {"id": "a", "path": "research/market.md", "at": 30},
        {"id": "b", "path": "product/site/index.html", "at": 20},
        {"id": "c", "path": "product/site/package.json", "at": 25},
        {"id": "d", "path": "config.yaml", "at": 10},
        {"id": "e", "path": "product/site/SKILL.md", "at": 15},
    ]
    items = server._takyon_workspace_deliverables_payload(overview, outputs)
    paths = [item["path"] for item in items]
    assert "product/site/package.json" not in paths
    assert "config.yaml" not in paths
    assert "product/site/SKILL.md" not in paths
    assert "research/market.md" in paths
    assert "product/site/index.html" in paths
    # Newest-first sort (criterion for "Surface bootstrap artifacts linearly").
    ats = [item["at"] for item in items]
    assert ats == sorted(ats, reverse=True)


# ── Show the actual url: publish_target derives slug.fourmanifold.com ───────

def test_product_publish_target_is_canonical_fourmanifold_host():
    from plugins.takyon import core

    target = core._product_publish_target("myco")
    assert target == "https://myco.fourmanifold.com/"
    assert ".app" not in target


# ── GROUPED E2E: the real workspace payload ties all four cards together ─────

def test_workspace_payload_e2e_ties_task_ui_group_together(monkeypatch):
    """Drive the real _takyon_workspace_payload entrypoint (the payload the
    Litebulb CompanyTab/Product UI consumes) and assert all four cards are wired:

      1. Task rollup    -> live_state.tasks carry title/description/category/status_label + grouping
      2. Raw documents  -> deliverables exclude internal files (SKILL.md, package.json, lock, node_modules)
      3. Linear feed     -> deliverables are newest-first (chronological)
      4. Actual url      -> overview.product.publish_target is slug.fourmanifold.com (no .app)
    """

    class _FakeStore:
        def read(self, scope, query, **kwargs):
            if query == "summary":
                return {"business": {"slug": "testco", "name": "Testco"}}
            return {}

    monkeypatch.setattr(server, "_takyon_store", lambda session: _FakeStore())
    monkeypatch.setattr(
        server,
        "_takyon_business_overview_payload",
        lambda store, slug, *, summary_data=None: {
            "product": {
                "publish_status": "draft",
                "public_url": "",
                # canonical expected URL even before publish (card "actual url"):
                "publish_target": "https://testco.fourmanifold.com/",
            },
            "artifacts": {"website": {"status": "local_source", "path": "product/site/index.html"}},
            "research": {
                "outputs": [
                    {"path": "research/market.md", "updated_at": 30, "source": "research"},
                ],
            },
            "tasks": [
                {
                    "id": "intent-1",
                    "source": "job",
                    "label": "business_write_file",
                    "status": "running",
                    "detail": "Building the product offer page.",
                },
                {
                    "id": "trace-1",
                    "source": "runtime",
                    "label": "CEO live trace",
                    "status": "running",
                    "detail": "tool: write_file product/site/index.html",
                },
            ],
        },
    )
    monkeypatch.setattr(
        server,
        "_takyon_historical_outputs_payload",
        lambda store, slug, limit=50: [
            {"id": "out:research", "title": "market.md", "path": "research/market.md", "kind": "file", "at": 30},
            {"id": "out:site", "title": "index.html", "path": "product/site/index.html", "kind": "file", "at": 20},
            {"id": "out:pkg", "title": "package.json", "path": "product/site/package.json", "kind": "file", "at": 25},
            {"id": "out:skill", "title": "SKILL.md", "path": "product/site/SKILL.md", "kind": "file", "at": 28},
            {"id": "out:lock", "title": "uv.lock", "path": "product/site/uv.lock", "kind": "file", "at": 27},
            {"id": "out:nm", "title": "react.js", "path": "product/site/node_modules/react/index.js", "kind": "file", "at": 26},
        ],
    )
    monkeypatch.setattr(server, "_takyon_get_background_run", lambda slug: None)
    monkeypatch.setattr(server, "_takyon_reconcile_background_run", lambda slug, run, overview: None)

    payload = server._takyon_workspace_payload({"takyon_operator_user_id": "demo"}, "testco")

    # ── Card 1: task rollup enrichment + grouping ──
    tasks = {t["id"]: t for t in payload["live_state"]["tasks"]}
    intent = tasks["intent-1"]
    # BUG-005 fail-closed: a raw "business_*" tool label is replaced with a general
    # business-language title, never the de-identified tool name.
    assert intent["title"] == "Working on the company"  # general title, not raw tool name
    assert intent["description"]                       # non-empty one-sentence
    assert intent["category"] in {"RESEARCH", "PRODUCT", "LAUNCH", "GROWTH", "OPS"}
    assert intent["status"] == "running"
    assert intent["status_label"] == "RUNNING"
    assert payload["live_state"]["current_task_id"] == "intent-1"
    assert tasks["trace-1"]["task_id"] == "intent-1"  # raw event nested under intent

    # ── Cards 2 + 3: deliverables filter internal files + newest-first ──
    paths = [d["path"] for d in payload["deliverables"]]
    assert "product/site/package.json" not in paths
    assert "product/site/SKILL.md" not in paths
    assert "product/site/uv.lock" not in paths
    assert not any("node_modules" in p for p in paths)
    assert "research/market.md" in paths
    assert "product/site/index.html" in paths
    ats = [d["at"] for d in payload["deliverables"]]
    assert ats == sorted(ats, reverse=True)

    # ── Card 4: canonical product URL (no fabricated .app) ──
    publish_target = payload["overview"]["product"]["publish_target"]
    assert publish_target == "https://testco.fourmanifold.com/"
    assert ".app" not in publish_target
