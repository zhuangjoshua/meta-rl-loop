from __future__ import annotations

import json

from plugins.takyon import core as takyon_core


def test_reddit_ads_config_reads_saved_state_without_safebox_keyerror(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    state_path = tmp_path / "secrets" / "reddit_ads.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "client_id": "reddit-client",
                "client_secret": "reddit-secret",
                "business_id": "business-1",
                "ad_account_id": "a2_demo",
                "profile_id": "t2_profile",
                "funding_instrument_id": "fi_1",
                "pixel_id": "pixel_1",
                "refresh_token": "reddit-refresh-token",
                "user_agent": "takyon-tests/1.0",
            }
        ),
        encoding="utf-8",
    )

    cfg = takyon_core._reddit_ads_config(require_auth=True)

    assert cfg["client_id"] == "reddit-client"
    assert cfg["client_secret"] == "reddit-secret"
    assert cfg["business_id"] == "business-1"
    assert cfg["ad_account_id"] == "a2_demo"
    assert cfg["profile_id"] == "t2_profile"
    assert cfg["funding_instrument_id"] == "fi_1"
    assert cfg["pixel_id"] == "pixel_1"
    assert cfg["refresh_token"] == "reddit-refresh-token"
