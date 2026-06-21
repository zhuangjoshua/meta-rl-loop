"""Build-derived runtime-rail declaration.

A product app self-declares every runtime rail it actually calls (records, media, …),
not just `actions`. This is the root cure for `rail_unavailable:<rail>:undeclared`: the
declared contract is derived from the built source, so declared >= used by construction
for every business, regardless of what the bootstrap seed listed.
"""
from __future__ import annotations

from plugins.takyon import app_actions, core as takyon_core


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scanner_detects_app_rail_usage_and_skips_the_kit(tmp_path):
    site = tmp_path / "product" / "site"
    # App source genuinely calls the records + media rails through the runtime client.
    _write(
        site / "src" / "App.tsx",
        "import { client } from './lib/runtime';\n"
        "export const Plants = () => client.listRecords({ type: 'plant' });\n"
        "export const Upload = (f) => client.uploadMedia(f);\n",
    )
    # The kit DEFINES every rail method but lives under `_takyon`; it must be skipped so the
    # method definitions are not mistaken for app usage.
    _write(
        site / "_takyon" / "runtime-client.js",
        "async listRecords(){ ensureRail('records'); }\n"
        "async generate(){ ensureRail('generate'); }\n"
        "async search(){ ensureRail('search'); }\n"
        "async listConnections(){ ensureRail('connections'); }\n",
    )
    assert app_actions.referenced_runtime_rails_in_source(site) == {"records", "media"}


def test_scanner_returns_empty_when_no_rails_used(tmp_path):
    site = tmp_path / "product" / "site"
    _write(site / "src" / "App.tsx", "export const App = () => 'hello';\n")
    assert app_actions.referenced_runtime_rails_in_source(site) == set()


def test_scanner_detects_action_internal_generate(tmp_path):
    site = tmp_path / "product" / "site"
    # AI flows run inside action files via ctx.generate — the generate rail must be derived.
    _write(
        site / "actions" / "draft-reply.ts",
        "export default async (payload, ctx) => ctx.generate({ prompt: payload.text });\n",
    )
    assert "generate" in app_actions.referenced_runtime_rails_in_source(site)


def test_materialized_surface_self_declares_records_when_app_reads_records(tmp_path):
    # The exact prod bug: the stored contract (bootstrap seed) never declared `records`, but
    # the built app reads via the records rail. Materialization must self-declare it.
    workspace = tmp_path / "product" / "site"
    _write(
        workspace / "src" / "App.tsx",
        "import { client } from './lib/runtime';\n"
        "export const Plants = () => client.listRecords({ type: 'plant' });\n",
    )
    surface = {
        "runtime_features": ["auth", "account", "profile", "checkout"],  # seed: no records
        "metadata": {"subuser_app": {}},
        "routes": ["/", "/app"],
    }
    materialized = takyon_core._materialized_surface_for_workspace(workspace, surface=surface)

    assert "records" in materialized["_workspace_file_rails"]
    # …and it flows all the way to the effective set baked into surface-context.js.
    assert "records" in takyon_core._surface_declared_runtime_features(materialized)
    assert "records" in takyon_core._surface_effective_runtime_features(materialized)


def test_unused_rail_is_not_declared(tmp_path):
    # A product that uses no data rail must not over-declare records/media/etc.
    workspace = tmp_path / "product" / "site"
    _write(workspace / "src" / "App.tsx", "export const App = () => 'hi';\n")
    surface = {
        "runtime_features": ["auth", "account", "profile", "checkout"],
        "metadata": {"subuser_app": {}},
        "routes": ["/", "/app"],
    }
    materialized = takyon_core._materialized_surface_for_workspace(workspace, surface=surface)
    declared = takyon_core._surface_declared_runtime_features(materialized)
    assert "records" not in declared
    assert "media" not in declared


def test_build_derived_set_covers_actions_and_data_rails():
    # `actions` stays in the derived set (its file-backed derivation is unchanged); the data /
    # media / AI / social rails join it so the channel is one general mechanism.
    for rail in ("actions", "records", "media", "directory", "connections", "generate", "search"):
        assert rail in takyon_core._BUILD_DERIVED_RAILS
    # Always-seeded shell rails are NOT build-derived.
    for rail in ("auth", "account", "profile", "checkout"):
        assert rail not in takyon_core._BUILD_DERIVED_RAILS
