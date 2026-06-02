"""Tests for the bundled Takyon Agent Map dashboard plugin backend."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


PLUGIN_FILE = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "dashboard"
    / "plugin_api.py"
)


def _load_plugin_router():
    spec = importlib.util.spec_from_file_location(
        "takyon_dashboard_plugin_agent_map_test",
        PLUGIN_FILE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.router


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / ".takyon"
    (home / "cron").mkdir(parents=True)
    monkeypatch.setenv("TAKYON_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    app = FastAPI()
    app.include_router(_load_plugin_router(), prefix="/api/plugins/takyon-map")
    return TestClient(app)


def test_graph_endpoint_returns_ceo_prompt_metadata(client):
    response = client.get("/api/plugins/takyon-map/graph")
    assert response.status_code == 200, response.text

    data = response.json()
    ceo_prompt = next(
        node for node in data["nodes"] if node["id"] == "prompt:ceo"
    )

    assert ceo_prompt["metadata"]["skills_root"].endswith("skills/takyon")
    assert ceo_prompt["metadata"]["build_index_script"].endswith(
        "scripts/build_skills_index.py"
    )
