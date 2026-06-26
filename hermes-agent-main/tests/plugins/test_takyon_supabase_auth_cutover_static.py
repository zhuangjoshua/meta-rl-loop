from __future__ import annotations

from pathlib import Path

from plugins.takyon import core as takyon_core
from plugins.takyon import app_identity


REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_auth_runtime_contract_is_supabase_only() -> None:
    rail = takyon_core.PRODUCT_RUNTIME_RAILS["auth"]
    assert rail["tools"] == ["business_supabase_login", "business_read_app_account"]
    assert rail["endpoints"] == [("POST", "auth/session"), ("GET", "session")]
    assert "auth/request" not in "\n".join(rail["worker_contract"])
    assert "auth/verify" not in "\n".join(rail["worker_contract"])

    tool_names = {tool["name"] for tool in takyon_core.TAKYON_TOOL_DEFINITIONS}
    assert "business_supabase_login" in tool_names
    assert "business_request_app_magic_link" not in tool_names
    assert "business_verify_app_magic_link" not in tool_names
    assert "business_request_app_magic_link" not in takyon_core.TAKYON_AUTHORITY_TOOL_NAMES
    assert "business_verify_app_magic_link" not in takyon_core.TAKYON_AUTHORITY_TOOL_NAMES
    assert not hasattr(app_identity, "create_magic_link")
    assert not hasattr(app_identity, "verify_magic_link")


def test_live_app_runtime_routes_do_not_expose_magic_links() -> None:
    web_server = _read("takyon_cli/web_server.py")
    assert 'parts == ["auth", "request"]' not in web_server
    assert 'parts == ["auth", "verify"]' not in web_server
    assert "handle_business_request_app_magic_link" not in web_server
    assert "handle_business_verify_app_magic_link" not in web_server

    runtime_client = _read("plugins/takyon/subuser_app_kit/runtime-client.js")
    assert 'routeUrl("auth/request")' not in runtime_client
    assert 'routeUrl("auth/verify")' not in runtime_client
    assert "loginWithSupabase" in runtime_client


def test_plugin_metadata_does_not_advertise_magic_link_tools() -> None:
    plugin_yaml = _read("plugins/takyon/plugin.yaml")
    assert "business_supabase_login" in plugin_yaml
    assert "business_request_app_magic_link" not in plugin_yaml
    assert "business_verify_app_magic_link" not in plugin_yaml


def test_app_magic_links_table_has_drop_migration() -> None:
    migration = _read("plugins/takyon/db/migrations/0042_drop_app_magic_links.sql")
    assert "drop table if exists app_magic_links" in migration.lower()
