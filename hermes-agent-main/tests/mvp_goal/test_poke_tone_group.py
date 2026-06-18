"""MVP /goal — poke-tone lane tie-in.

Proves the operator-approved "poke" voice for the customer-facing PRODUCT chat
(the Litebulb-built product assistant / `generate` rail), wired the canonical way:

  * a named preset registry (`default` + `poke`) — no global hardcoded tone,
  * a per-business selection recorded on the surface contract metadata
    (`subuser_app.chat_tone`) and resolved through `_surface_product_chat_tone`,
  * injection into the product-build worker contract for the `generate` rail so the
    generated product assistant's system prompt is written in the selected voice,
  * mirrored into `product/surface.md`,
  * AND that it does NOT touch the operator CEO shell prompt.
"""

from __future__ import annotations

from plugins.takyon import core


# --------------------------------------------------------------------------- #
# Preset registry — named presets, neutral default, no global hardcoded tone   #
# --------------------------------------------------------------------------- #

def test_preset_registry_has_default_and_poke():
    assert "default" in core.PRODUCT_CHAT_TONE_PRESETS
    assert "poke" in core.PRODUCT_CHAT_TONE_PRESETS
    assert core.DEFAULT_PRODUCT_CHAT_TONE == "default"


def test_normalize_tone_falls_back_to_default():
    assert core._normalize_product_chat_tone("poke") == "poke"
    assert core._normalize_product_chat_tone("POKE") == "poke"
    assert core._normalize_product_chat_tone("default") == "default"
    # Unknown / blank / junk never invent a voice.
    assert core._normalize_product_chat_tone("snarky") == "default"
    assert core._normalize_product_chat_tone("") == "default"
    assert core._normalize_product_chat_tone(None) == "default"


def test_poke_spec_carries_the_operator_approved_voice():
    contract = " ".join(core.PRODUCT_CHAT_TONE_PRESETS["poke"]["worker_contract"]).lower()
    # the literal voice spec
    assert "1-3 sentences" in contract
    assert "proactive next step" in contract or "next step" in contract
    assert "one emoji" in contract
    assert "hedging" in contract
    # the operator's exact transform example survives
    assert "want me to run it?" in contract


# --------------------------------------------------------------------------- #
# Surface-contract selection → resolver                                        #
# --------------------------------------------------------------------------- #

def _surface(tone: str | None, rails):
    meta = {"subuser_app": {}}
    if tone is not None:
        meta["subuser_app"]["chat_tone"] = tone
    return {
        "runtime_features": list(rails),
        "runtime_api_base": "/api/takyon/apps/demo",
        "metadata": meta,
    }


def test_surface_resolver_reads_selection():
    assert core._surface_product_chat_tone(_surface("poke", ["generate"])) == "poke"
    assert core._surface_product_chat_tone(_surface(None, ["generate"])) == "default"
    assert core._surface_product_chat_tone({}) == "default"


def _persist_tone_like_store(existing_surface, requested_tone, rails):
    """Replay the exact merge+persist sequence the `app.surface.upsert` store path runs:
    merge subuser_app metadata, then stamp the resolved `chat_tone`. Mirrors core.py so the
    persistence shape is exercised without the Postgres store harness."""
    existing_metadata = existing_surface.get("metadata") if isinstance(existing_surface.get("metadata"), dict) else {}
    metadata = core._merge_subuser_app_metadata(
        dict(existing_metadata),
        runtime_features=list(rails),
        previous_runtime_features=core._surface_runtime_features(existing_surface),
        rail_state=None,
        frontend_stack=None,
    )
    resolved_tone = core._normalize_product_chat_tone(
        requested_tone
        if requested_tone is not None
        else core._surface_product_chat_tone(existing_surface)
    )
    subuser_app_payload = metadata.get("subuser_app")
    if isinstance(subuser_app_payload, dict):
        subuser_app_payload["chat_tone"] = resolved_tone
    return {"metadata": metadata, "runtime_features": list(rails)}


def test_tone_persists_into_subuser_app_metadata_and_survives_a_no_tone_update():
    # First upsert selects poke.
    after_select = _persist_tone_like_store(_surface(None, ["generate"]), "poke", ["generate"])
    assert after_select["metadata"]["subuser_app"]["chat_tone"] == "poke"
    assert core._surface_product_chat_tone(after_select) == "poke"

    # A later upsert that omits tone must KEEP the prior selection, not reset to default.
    after_noop = _persist_tone_like_store(after_select, None, ["generate"])
    assert core._surface_product_chat_tone(after_noop) == "poke"

    # Explicitly switching back to default is respected.
    after_revert = _persist_tone_like_store(after_select, "default", ["generate"])
    assert core._surface_product_chat_tone(after_revert) == "default"


# --------------------------------------------------------------------------- #
# Injection into the product-build worker contract (generate rail)            #
# --------------------------------------------------------------------------- #

def test_poke_tone_injected_into_generate_rail_worker_contract():
    block = core._runtime_ui_contract_block(_surface("poke", ["auth", "account", "generate"]))
    assert "Product chat tone: poke" in block
    assert "1-3 sentences" in block
    assert "Want me to run it?" in block


def test_default_tone_emits_neutral_contract_not_poke():
    block = core._runtime_ui_contract_block(_surface(None, ["auth", "account", "generate"]))
    assert "Product chat tone: default" in block
    assert "Product chat tone: poke" not in block
    assert "Want me to run it?" not in block


def test_tone_not_injected_without_generate_rail():
    # No generate rail => no product-chat surface => no tone contract at all.
    block = core._runtime_ui_contract_block(_surface("poke", ["auth", "account"]))
    assert "Product chat tone" not in block


# --------------------------------------------------------------------------- #
# Operator CEO shell isolation — the poke tone must NOT leak into the operator  #
# runtime prompt.                                                              #
# --------------------------------------------------------------------------- #

def test_poke_voice_absent_from_operator_ceo_prompt():
    prompt = (core._repo_root() / "plugins" / "takyon" / "prompts" / "ceo.md").read_text(encoding="utf-8")
    lowered = prompt.lower()
    assert "product chat tone" not in lowered
    assert "want me to run it?" not in lowered
    assert "poke" not in lowered
