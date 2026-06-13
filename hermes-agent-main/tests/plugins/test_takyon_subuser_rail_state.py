from __future__ import annotations

from plugins.takyon import core as takyon_core


def test_normalize_subuser_rail_state_maps_legacy_unverified_to_declared():
    state = takyon_core._normalize_subuser_rail_state(  # type: ignore[attr-defined]
        {"auth": "unverified", "generate": "live"},
        declared_rails=["auth", "generate"],
    )

    assert state == {"auth": "declared", "generate": "live"}


def test_surface_subuser_app_shape_defaults_declared_rails_to_declared():
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

    # `generate` pulls in auth/account via the rail dependency graph; checkout is no
    # longer auto-added (subscription_style is gone), so it must not appear here.
    assert shape["rail_state"] == {
        "auth": "declared",
        "account": "declared",
        "generate": "declared",
    }


def test_merge_subuser_app_metadata_keeps_rail_truth_for_declared_rails():
    merged = takyon_core._merge_subuser_app_metadata(  # type: ignore[attr-defined]
        {
            "subuser_app": {
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
    )

    # rail_state is filtered to the currently declared rails; `generate` drops out.
    assert merged["subuser_app"]["rail_state"] == {
        "auth": "live",
        "account": "blocked",
        "checkout": "blocked",
    }
    # The deleted app-shape taxonomy is never persisted back into subuser_app metadata.
    assert "app_mode" not in merged["subuser_app"]
    assert "subscription_style" not in merged["subuser_app"]
    assert "api_mode" not in merged["subuser_app"]
