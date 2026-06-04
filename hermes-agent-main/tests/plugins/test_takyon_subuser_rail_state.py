from __future__ import annotations

from plugins.takyon import core as takyon_core


def test_normalize_subuser_rail_state_maps_legacy_unverified_to_unknown():
    state = takyon_core._normalize_subuser_rail_state(  # type: ignore[attr-defined]
        {"auth": "unverified", "generate": "live"},
        declared_rails=["auth", "generate"],
    )

    assert state == {"auth": "unknown", "generate": "live"}


def test_surface_subuser_app_shape_defaults_declared_rails_to_unknown():
    shape = takyon_core._surface_subuser_app_shape(  # type: ignore[attr-defined]
        {
            "runtime_features": ["auth", "generate"],
            "metadata": {
                "subuser_app": {
                    "rail_state": {
                        "auth": "unverified",
                    }
                }
            },
        }
    )

    assert shape["rail_state"] == {
        "auth": "unknown",
        "account": "unknown",
        "checkout": "unknown",
        "generate": "unknown",
    }


def test_merge_subuser_app_metadata_resets_stale_rail_state_when_shape_changes():
    merged = takyon_core._merge_subuser_app_metadata(  # type: ignore[attr-defined]
        {
            "subuser_app": {
                "app_mode": "ai_tool",
                "subscription_style": "monthly",
                "api_mode": "none",
                "rail_state": {
                    "auth": "live",
                    "account": "blocked",
                    "checkout": "blocked",
                    "generate": "blocked",
                },
            }
        },
        runtime_features=["auth", "account", "checkout"],
        previous_runtime_features=["auth", "account", "checkout", "generate"],
        app_mode="standard_saas",
        subscription_style="monthly",
        api_mode="none",
    )

    assert merged["subuser_app"]["rail_state"] == {
        "auth": "unknown",
        "account": "unknown",
        "checkout": "unknown",
    }
