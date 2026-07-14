from tools.skills_sync import should_sync_legacy_skills


def test_legacy_skill_sync_is_disabled_for_immutable_sdk_plugin(monkeypatch):
    monkeypatch.delenv("TAKYON_DISABLE_LEGACY_SKILL_SYNC", raising=False)
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    monkeypatch.setenv("TAKYON_CLAUDE_SKILLS_PLUGIN", "/opt/takyon/releases/sha/skills")

    assert should_sync_legacy_skills() is False


def test_legacy_skill_sync_is_disabled_on_non_operator_planes(monkeypatch):
    monkeypatch.delenv("TAKYON_DISABLE_LEGACY_SKILL_SYNC", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_SKILLS_PLUGIN", raising=False)

    for host_role in ("subuser", "safebox"):
        monkeypatch.setenv("TAKYON_HOST_ROLE", host_role)
        assert should_sync_legacy_skills() is False


def test_legacy_skill_sync_remains_available_only_for_compatibility(monkeypatch):
    monkeypatch.delenv("TAKYON_DISABLE_LEGACY_SKILL_SYNC", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_SKILLS_PLUGIN", raising=False)
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)

    assert should_sync_legacy_skills() is True


def test_disable_flag_makes_direct_sync_a_side_effect_free_noop(monkeypatch, tmp_path):
    from tools import skills_sync

    destination = tmp_path / "must-not-exist"
    monkeypatch.setattr(skills_sync, "SKILLS_DIR", destination)
    monkeypatch.setenv("TAKYON_DISABLE_LEGACY_SKILL_SYNC", "1")
    monkeypatch.delenv("TAKYON_CLAUDE_SKILLS_PLUGIN", raising=False)
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)

    assert skills_sync.sync_skills(quiet=True) == {
        "copied": [],
        "updated": [],
        "skipped": 0,
        "user_modified": [],
        "cleaned": [],
        "total_bundled": 0,
    }
    assert not destination.exists()
