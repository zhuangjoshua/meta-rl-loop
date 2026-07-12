from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from plugins.takyon import app_actions


BUILD_A = "a" * 32
BUILD_B = "b" * 32


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def _empty_bundle(build_id: str) -> dict[str, str]:
    encoded = json.dumps(
        {"files": [], "http_action_names": [], "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "live_build_id": build_id,
        "action_bundle_json": encoded,
        "action_bundle_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def test_pg_materialization_rejects_pointer_flip_before_using_cached_bundle(tmp_path, monkeypatch):
    """A request that read build A must fail if the pointer becomes B before bundle resolution.

    The second resolution intentionally has a valid cached A artifact. The app-role function is
    still called with expected=A and returns no row after the pointer flip, so the stale cache can
    never bypass the database's current-pointer check.
    """

    class _PgConn:
        live_build_id = BUILD_A
        calls: list[tuple[str, tuple[str, ...] | None]] = []

        def execute(self, query, params=None):
            sql = str(query)
            normalized = None if params is None else tuple(params)
            self.calls.append((sql, normalized))
            if "has_function_privilege" in sql:
                return _Result(
                    {
                        "role": "takyon_app_runtime__subuser_one",
                        "can_read_action_bundle": True,
                    }
                )
            assert "takyon_app_live_action_bundle(%s, %s, %s)" in sql
            assert normalized is not None
            business, session_hash, expected_build_id = normalized
            assert business == "biz"
            assert session_hash == hashlib.sha256(b"session").hexdigest()
            row = _empty_bundle(expected_build_id) if expected_build_id == self.live_build_id else None
            return _Result(row)

    conn = _PgConn()

    class _Store:
        root = tmp_path / "runtime"

        @contextmanager
        def _connect(self):
            yield conn

    monkeypatch.setattr(app_actions, "_is_pg_conn", lambda _conn: True)
    store = _Store()
    expected_surface = {"live_build_id": BUILD_A}

    cache_root, _certified = app_actions.materialize_live_action_bundle(
        store,
        business_slug="biz",
        surface=expected_surface,
        session_token="session",
    )
    assert cache_root.is_dir()

    conn.live_build_id = BUILD_B
    with pytest.raises(app_actions.ActionContractError, match="no longer current"):
        app_actions.materialize_live_action_bundle(
            store,
            business_slug="biz",
            surface=expected_surface,
            session_token="session",
        )

    bound_calls = [
        params
        for sql, params in conn.calls
        if "FROM takyon_app_live_action_bundle" in sql
    ]
    assert bound_calls == [
        ("biz", hashlib.sha256(b"session").hexdigest(), BUILD_A),
        ("biz", hashlib.sha256(b"session").hexdigest(), BUILD_A),
    ]


def test_materialization_rejects_wrong_build_even_with_valid_digest(tmp_path, monkeypatch):
    """Defense in depth: a buggy DB function may not smuggle B into an A-stamped execution."""

    class _Store:
        root = tmp_path / "runtime"

        @contextmanager
        def _connect(self):
            yield object()

    monkeypatch.setattr(
        app_actions,
        "_live_action_bundle_row",
        lambda *_args, **_kwargs: _empty_bundle(BUILD_B),
    )

    with pytest.raises(
        app_actions.ActionContractError,
        match=f"expected {BUILD_A}, resolved {BUILD_B}",
    ):
        app_actions.materialize_live_action_bundle(
            _Store(),
            business_slug="biz",
            surface={"live_build_id": BUILD_A},
            session_token="session",
        )

    assert not (_Store.root / "cache").exists()


def test_sqlite_bundle_lookup_allows_only_current_or_unexpired_previous():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_surface_contracts (business_slug TEXT PRIMARY KEY, live_build_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE product_builds (build_id TEXT PRIMARY KEY, business_slug TEXT, "
        "action_bundle_json TEXT, action_bundle_sha256 TEXT, status TEXT, created_at TEXT, "
        "servable_until TEXT, activation_state TEXT)"
    )
    current = "1" * 32
    staged = "2" * 32
    stale_staged = "3" * 32
    previous = "4" * 32
    expired = "5" * 32
    conn.execute("INSERT INTO app_surface_contracts VALUES ('biz', ?)", (current,))
    rows = [
        (current, "live", "live", "now", None),
        (staged, "staged", "staged", "now", None),
        (stale_staged, "staged", "pointer_pending", "old", None),
        (previous, "previous", "previous", "old", "future"),
        (expired, "previous", "previous", "old", "past"),
    ]
    for build_id, status, activation_state, created, until in rows:
        bundle = _empty_bundle(build_id)
        created_sql = "datetime('now')" if created == "now" else "datetime('now', '-30 minutes')"
        until_sql = (
            "NULL"
            if until is None
            else "datetime('now', '+5 minutes')"
            if until == "future"
            else "datetime('now', '-5 minutes')"
        )
        conn.execute(
            "INSERT INTO product_builds VALUES (?, 'biz', ?, ?, ?, "
            f"{created_sql}, {until_sql}, ?)",
            (
                build_id,
                bundle["action_bundle_json"],
                bundle["action_bundle_sha256"],
                status,
                activation_state,
            ),
        )

    def resolve(build_id):
        return app_actions._live_action_bundle_row(
            conn,
            business_slug="biz",
            live_build_id=build_id,
            session_token="session",
        )

    assert resolve(current)["live_build_id"] == current
    assert resolve(staged) is None
    assert resolve(previous)["live_build_id"] == previous
    assert resolve(stale_staged) is None
    assert resolve(expired) is None

    conn.execute("UPDATE app_surface_contracts SET live_build_id = ? WHERE business_slug = 'biz'", (staged,))
    assert resolve(staged) is None
    conn.execute(
        "UPDATE product_builds SET status = 'live', activation_state = 'inactive' WHERE build_id = ?",
        (staged,),
    )
    assert resolve(staged) is None
    conn.execute(
        "UPDATE product_builds SET activation_state = 'pointer_pending' WHERE build_id = ?",
        (staged,),
    )
    assert resolve(staged)["live_build_id"] == staged


@pytest.mark.skipif(shutil.which("deno") is None, reason="deno not installed")
def test_nested_imported_action_module_is_bundled_materialized_and_executable(tmp_path, monkeypatch):
    import sqlite3

    source = tmp_path / "source"
    (source / "actions" / "lib").mkdir(parents=True)
    (source / "_takyon").mkdir()
    (source / "src").mkdir()
    (source / "actions" / "lib" / "format.ts").write_text(
        "export const format = (name: string) => `hello ${name}`;\n",
        encoding="utf-8",
    )
    (source / "actions" / "coach.ts").write_text(
        'import { format } from "./lib/format.ts";\n'
        "export default async (payload: TakyonActionPayload, _ctx: TakyonActionContext) => "
        "({ message: format(String(payload.name || 'world')) });\n",
        encoding="utf-8",
    )
    (source / "_takyon" / "runtime-client.js").write_text(
        "export function createActionContext(ctx) { return ctx; }\n",
        encoding="utf-8",
    )
    (source / "src" / "app.ts").write_text(
        'client.invokeAction("coach", {});\n',
        encoding="utf-8",
    )
    bundle = app_actions.build_action_bundle(source)
    bundled_paths = {item["path"] for item in json.loads(bundle["json"])["files"]}
    assert "actions/coach.ts" in bundled_paths
    assert "actions/lib/format.ts" in bundled_paths

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_surface_contracts (business_slug TEXT PRIMARY KEY, live_build_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE product_builds (build_id TEXT PRIMARY KEY, business_slug TEXT, "
        "action_bundle_json TEXT, action_bundle_sha256 TEXT, status TEXT, created_at TEXT, "
        "servable_until TEXT, activation_state TEXT)"
    )
    conn.execute("INSERT INTO app_surface_contracts VALUES ('biz', ?)", (BUILD_A,))
    conn.execute(
        "INSERT INTO product_builds VALUES (?, 'biz', ?, ?, 'live', datetime('now'), NULL, 'live')",
        (BUILD_A, bundle["json"], bundle["sha256"]),
    )

    class Store:
        root = tmp_path / "runtime"

        @contextmanager
        def _connect(self):
            yield conn

    materialized, certified = app_actions.materialize_live_action_bundle(
        Store(),
        business_slug="biz",
        surface={"live_build_id": BUILD_A},
        session_token="session",
    )
    assert certified == {"coach"}
    assert (materialized / "actions" / "lib" / "format.ts").is_file()

    monkeypatch.setattr(app_actions, "_operator_host_requires_action_sandbox", lambda: False)
    result, metadata = app_actions._run_action_subprocess(
        action_path=materialized / "actions" / "coach.ts",
        base=app_actions.RailsBase(origin="https://example.com", hostport="example.com:443"),
        outbound_hosts=[],
        request={"payload": {"name": "Ada"}, "ctx": {}},
        timeout_seconds=10,
        cpu_quota_percent=50,
        memory_max_mb=256,
    )

    assert result == {"message": "hello Ada"}
    assert metadata["isolation"] in {"subprocess", "subprocess-fallback"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_runtime_forwards_baked_build_identity_on_action_request():
    runtime_client = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "takyon"
        / "subuser_app_kit"
        / "runtime-client.js"
    )
    build_id = "c" * 32
    script = f"""
import {{ createSubuserRuntimeClient }} from {json.dumps(runtime_client.as_uri())};
globalThis.__TAKYON_LIVE_BUILD_ID__ = {json.dumps(build_id)};
let observed = null;
globalThis.fetch = async (url, init) => {{
  observed = {{ url: String(url), headers: init.headers }};
  return {{ ok: true, status: 200, json: async () => ({{ success: true, result: {{ ok: true }} }}) }};
}};
const client = createSubuserRuntimeClient({{
  runtimeFeatures: ["actions"],
  railState: {{ actions: "live" }},
  runtimeApiBase: "/api/takyon/apps/biz",
  location: {{ href: "https://biz.coscale.app/app", origin: "https://biz.coscale.app" }},
}});
await client.invokeAction("coach", {{}}, {{ idempotency_key: "browser-1" }});
console.log(JSON.stringify(observed));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "--input-type=module", "-e", script],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["headers"]["X-Takyon-Live-Build-Id"] == build_id
    assert observed["url"].endswith("/api/takyon/apps/biz/actions/coach")


@pytest.mark.parametrize(
    ("import_line", "message"),
    [
        ('import config from "./config.json" with { type: "json" };\n', "explicit bundled .ts"),
        ('import { z } from "zod";\n', "unsupported module"),
        ('import { helper } from "../src/helper.ts";\n', "escapes or is missing"),
    ],
)
def test_action_bundle_rejects_typechecked_modules_the_deno_artifact_cannot_read(
    tmp_path,
    import_line,
    message,
):
    root = tmp_path / "site"
    actions = root / "actions"
    actions.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "helper.ts").write_text("export const helper = 1;\n", encoding="utf-8")
    (actions / "config.json").write_text('{"ok":true}\n', encoding="utf-8")
    (actions / "coach.ts").write_text(
        import_line
        + "export default async (_payload: TakyonActionPayload, _ctx: TakyonActionContext) => ({ ok: true });\n",
        encoding="utf-8",
    )

    with pytest.raises(app_actions.ActionContractError, match=message):
        app_actions.build_action_bundle(root)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "export default async (_payload: any, ctx: any) => ctx.madeUp();\n",
            "explicit `any`",
        ),
        (
            "type FakeContext = { madeUp(): void };\n"
            "export default async (_payload: TakyonActionPayload, ctx: FakeContext) => ctx.madeUp();\n",
            "TakyonActionContext",
        ),
        (
            '/// <reference lib="dom" />\nexport default async () => window.location.href;\n',
            "triple-slash ambient reference",
        ),
        (
            "// @ts-ignore\nexport default async () => window.location.href;\n",
            "suppression directive",
        ),
        (
            "export default async (_payload: TakyonActionPayload, ctx: TakyonActionContext) => "
            "`${((_ctx: any) => _ctx.madeUp())(ctx)}`;\n",
            "explicit `any`",
        ),
    ],
)
def test_action_bundle_rejects_type_environment_bypasses(tmp_path, source, message):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (root / "actions" / "coach.ts").write_text(source, encoding="utf-8")

    with pytest.raises(app_actions.ActionContractError, match=message):
        app_actions.build_action_bundle(root)


def test_action_bundle_allows_erased_platform_type_import(tmp_path):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (root / "_takyon").mkdir()
    (root / "_takyon" / "runtime-client.js").write_text(
        "export const runtime = true;\n",
        encoding="utf-8",
    )
    (root / "actions" / "coach.ts").write_text(
        'import type { RecordRef } from "../_takyon/runtime-client.js";\n'
        "export default async function run(payload: TakyonActionPayload, "
        "ctx: TakyonActionContext) {\n"
        "  return { ref: payload.ref as RecordRef, callable: ctx.isRailCallable('records') };\n"
        "}\n",
        encoding="utf-8",
    )

    bundle = app_actions.build_action_bundle(root)

    assert bundle["file_count"] == 2


def test_action_bundle_resolves_extensionless_erased_typescript_import(tmp_path):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (root / "_takyon").mkdir()
    (root / "_takyon" / "runtime-client.d.ts").write_text(
        "export type RecordRef = string & { readonly __opaque: unique symbol };\n",
        encoding="utf-8",
    )
    (root / "actions" / "coach.ts").write_text(
        'import type { RecordRef } from "../_takyon/runtime-client";\n'
        "export default async function run(payload: TakyonActionPayload, "
        "ctx: TakyonActionContext) {\n"
        "  return { ref: payload.ref as RecordRef, callable: ctx.isRailCallable('records') };\n"
        "}\n",
        encoding="utf-8",
    )

    bundle = app_actions.build_action_bundle(root)

    # The declaration is erased and therefore excluded from the immutable runtime bundle.
    assert bundle["file_count"] == 1


def test_action_bundle_extensionless_type_import_still_cannot_escape_site(tmp_path):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (tmp_path / "outside.d.ts").write_text("export type Secret = string;\n", encoding="utf-8")
    (root / "actions" / "coach.ts").write_text(
        'import type { Secret } from "../../outside";\n'
        "export default async function run(payload: TakyonActionPayload, "
        "ctx: TakyonActionContext) { return { payload, ctx: Boolean(ctx) } }\n",
        encoding="utf-8",
    )

    with pytest.raises(app_actions.ActionContractError, match="escapes or is missing"):
        app_actions.build_action_bundle(root)


@pytest.mark.parametrize("loop", [False, True])
def test_action_bundle_rejects_symlinked_type_import_components(tmp_path, loop):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    types = root / "types"
    types.mkdir()
    (types / "contract.d.ts").write_text("export type Ref = string;\n", encoding="utf-8")
    alias = root / "alias"
    alias.symlink_to(alias if loop else types, target_is_directory=True)
    (root / "actions" / "coach.ts").write_text(
        'import type { Ref } from "../alias/contract";\n'
        "export default async function run(payload: TakyonActionPayload, "
        "ctx: TakyonActionContext) { return { payload, ctx: Boolean(ctx) } }\n",
        encoding="utf-8",
    )

    with pytest.raises(app_actions.ActionContractError, match="escapes or is missing"):
        app_actions.build_action_bundle(root)


def test_action_bundle_ignores_import_and_any_examples_in_comments_and_strings(tmp_path):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (root / "actions" / "coach.ts").write_text(
        "// Bad example only: ctx: any; import { z } from 'zod'\n"
        "const docs = \"@ts-ignore /// <reference lib='dom' /> export default async "
        "(payload: TakyonActionPayload, ctx: TakyonActionContext) => import('zod')\";\n"
        "export default async (payload: TakyonActionPayload, ctx: TakyonActionContext)"
        ": Promise<TakyonActionPayload> => ({ ...payload, ok: ctx.isRailCallable('records') });\n",
        encoding="utf-8",
    )

    bundle = app_actions.build_action_bundle(root)

    assert bundle["file_count"] == 1


def test_materialization_serializes_two_customers_on_one_cold_replica(tmp_path, monkeypatch):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (root / "actions" / "coach.ts").write_text(
        "export default async (_payload: TakyonActionPayload, _ctx: TakyonActionContext) => ({ ok: true });\n",
        encoding="utf-8",
    )
    bundle = app_actions.build_action_bundle(root)
    row = {
        "live_build_id": BUILD_A,
        "action_bundle_json": bundle["json"],
        "action_bundle_sha256": bundle["sha256"],
    }

    class Store:
        root = tmp_path / "runtime"

        @contextmanager
        def _connect(self):
            yield object()

    monkeypatch.setattr(app_actions, "_live_action_bundle_row", lambda *_a, **_k: row)
    real_replace = app_actions.os.replace
    replacements: list[tuple[Path, Path]] = []

    def counted_replace(source, target):
        replacements.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(app_actions.os, "replace", counted_replace)

    def materialize():
        return app_actions.materialize_live_action_bundle(
            Store(),
            business_slug="biz",
            surface={"live_build_id": BUILD_A},
            session_token="session",
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        resolved = list(pool.map(lambda _index: materialize(), range(2)))

    assert resolved[0] == resolved[1]
    assert resolved[0].name == bundle["sha256"]
    assert (resolved[0] / "actions" / "coach.ts").is_file()
    assert len(replacements) == 1


def test_hot_materialization_uses_returned_contract_without_rewriting_cache(tmp_path, monkeypatch):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (root / "actions" / "coach.ts").write_text(
        "export default async (_payload: TakyonActionPayload, _ctx: TakyonActionContext) => ({ ok: true });\n",
        encoding="utf-8",
    )
    bundle = app_actions.build_action_bundle(
        root,
        {
            "actions": [{"name": "coach", "trigger": "http"}],
            "outbound_hosts": ["api.example.com"],
        },
    )
    row = {
        "live_build_id": BUILD_A,
        "action_bundle_json": bundle["json"],
        "action_bundle_sha256": bundle["sha256"],
    }

    class Store:
        root = tmp_path / "runtime"

        @contextmanager
        def _connect(self):
            yield object()

    monkeypatch.setattr(app_actions, "_live_action_bundle_row", lambda *_a, **_k: row)
    cache_root, _certified, expected_contract = app_actions._materialize_live_action_bundle(
        Store(),
        business_slug="biz",
        surface={"live_build_id": BUILD_A},
        session_token="session",
    )
    derived_contract = cache_root / ".execution-contract.json"
    derived_contract.write_text('{"poison":true}\n', encoding="utf-8")

    resolved, _certified, returned_contract = app_actions._materialize_live_action_bundle(
        Store(),
        business_slug="biz",
        surface={"live_build_id": BUILD_A},
        session_token="session",
    )

    assert resolved == cache_root
    assert returned_contract == expected_contract
    assert returned_contract["outbound_hosts"] == ["api.example.com"]
    assert derived_contract.read_text(encoding="utf-8") == '{"poison":true}\n'


def test_corrupt_digest_keyed_cache_is_never_replaced_in_place(tmp_path, monkeypatch):
    root = tmp_path / "site"
    (root / "actions").mkdir(parents=True)
    (root / "actions" / "coach.ts").write_text(
        "export default async (_payload: TakyonActionPayload, _ctx: TakyonActionContext) => ({ ok: true });\n",
        encoding="utf-8",
    )
    bundle = app_actions.build_action_bundle(root)
    row = {
        "live_build_id": BUILD_A,
        "action_bundle_json": bundle["json"],
        "action_bundle_sha256": bundle["sha256"],
    }

    class Store:
        root = tmp_path / "runtime"

        @contextmanager
        def _connect(self):
            yield object()

    monkeypatch.setattr(app_actions, "_live_action_bundle_row", lambda *_a, **_k: row)
    cache_root, _certified = app_actions.materialize_live_action_bundle(
        Store(),
        business_slug="biz",
        surface={"live_build_id": BUILD_A},
        session_token="session",
    )
    action_path = cache_root / "actions" / "coach.ts"
    action_path.write_text("corrupt\n", encoding="utf-8")

    with pytest.raises(app_actions.ActionContractError, match="immutable action bundle cache is corrupt"):
        app_actions.materialize_live_action_bundle(
            Store(),
            business_slug="biz",
            surface={"live_build_id": BUILD_A},
            session_token="session",
        )

    assert cache_root.is_dir()
    assert action_path.read_text(encoding="utf-8") == "corrupt\n"


def test_materialization_uses_process_lock_outside_replaceable_cache(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache" / "action-bundles" / "biz" / BUILD_A
    allowed_root = tmp_path / "cache" / "action-bundles"
    calls: list[int] = []

    class Fcntl:
        LOCK_EX = 2
        LOCK_UN = 8

        @staticmethod
        def flock(_fd, operation):
            calls.append(operation)

    monkeypatch.setattr(app_actions, "fcntl", Fcntl)
    lock_path = app_actions._action_bundle_process_lock_path(cache_root, allowed_root)
    with app_actions._hold_action_bundle_cache_lock(cache_root, allowed_root):
        assert lock_path.is_file()
        assert cache_root not in lock_path.parents

    assert calls == [Fcntl.LOCK_EX, Fcntl.LOCK_UN]


def test_principal_and_action_are_part_of_server_idempotency_namespace():
    common = {
        "principal_kind": "session",
        "live_build_id": BUILD_A,
        "caller_idempotency_key": "save",
    }
    user_a = app_actions._action_reservation_key(
        app_user_id="user-a", action_name="coach", **common
    )
    user_b = app_actions._action_reservation_key(
        app_user_id="user-b", action_name="coach", **common
    )
    other_action = app_actions._action_reservation_key(
        app_user_id="user-a", action_name="export", **common
    )
    assert len({user_a, user_b, other_action}) == 3


def test_durable_action_claim_replays_across_replica_store_instances(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE app_action_invocations (
          business_slug TEXT NOT NULL,
          app_user_id TEXT NOT NULL,
          reservation_key TEXT NOT NULL,
          finish_token_hash TEXT NOT NULL,
          action_name TEXT NOT NULL,
          live_build_id TEXT NOT NULL,
          status TEXT NOT NULL,
          result_json TEXT,
          run_json TEXT,
          receipt_path TEXT,
          error TEXT,
          claimed_at TEXT NOT NULL,
          completed_at TEXT,
          PRIMARY KEY (business_slug, reservation_key)
        );
        """
    )

    class Store:
        @contextmanager
        def _connect(self):
            yield conn

    kwargs = {
        "business_slug": "biz",
        "app_user_id": "user-a",
        "session_token": "session",
        "finish_token": "finish-capability",
        "reservation_key": "principal:key",
        "action_name": "coach",
        "live_build_id": BUILD_A,
        "receipt_path": "metrics/receipt.json",
    }
    first = app_actions._claim_action_invocation(Store(), **kwargs)
    simultaneous = app_actions._claim_action_invocation(Store(), **kwargs)
    assert first["is_new"] is True
    assert simultaneous["is_new"] is False
    assert simultaneous["status"] == "running"

    app_actions._finish_action_invocation(
        Store(),
        business_slug="biz",
        app_user_id="user-a",
        finish_token="finish-capability",
        reservation_key="principal:key",
        status="completed",
        result={"answer": 42},
        run={"isolation": "replica-1"},
        receipt_path="metrics/receipt.json",
    )
    replay = app_actions._claim_action_invocation(Store(), **kwargs)
    assert replay["is_new"] is False
    assert replay["status"] == "completed"
    assert replay["result"] == {"answer": 42}
    assert replay["run"] == {"isolation": "replica-1"}
