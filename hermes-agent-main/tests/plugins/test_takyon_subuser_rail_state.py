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

    assert shape["rail_state"] == {"auth": "unknown", "generate": "unknown"}
