from __future__ import annotations

import pytest

from plugins.takyon import core as takyon_core
from plugins.takyon.core import TakyonError


def test_seo_build_credentials_uses_canonical_gsc_safebox_alias(monkeypatch):
    seen: list[tuple[str, ...]] = []

    def _fake_first(*keys: str) -> str:
        seen.append(tuple(keys))
        return ""

    monkeypatch.setattr(takyon_core.safebox, "first_env_backed_value", _fake_first)

    with pytest.raises(TakyonError) as excinfo:
        takyon_core._seo_build_credentials(["https://www.googleapis.com/auth/webmasters"])

    assert seen == [("TAKYON_GSC_SERVICE_ACCOUNT_KEY",)]
    assert "TAKYON_GSC_SERVICE_ACCOUNT_KEY" in str(excinfo.value)
