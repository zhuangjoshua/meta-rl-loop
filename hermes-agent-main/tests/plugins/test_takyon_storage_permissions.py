from pathlib import Path

from plugins.takyon import storage


def test_sync_down_replaces_unreadable_local_cache_file(tmp_path):
    bucket = tmp_path / "bucket"
    backend = storage.LocalStorageBackend(bucket)

    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "product").mkdir()
    (source / "product" / "surface.md").write_text("fresh\n", encoding="utf-8")
    storage.sync_up(backend, "scopesync", source)

    dest = tmp_path / "dest"
    (dest / "product").mkdir(parents=True)
    unreadable = dest / "product" / "surface.md"
    unreadable.write_text("stale\n", encoding="utf-8")
    unreadable.chmod(0)
    try:
        report = storage.sync_down(backend, "scopesync", dest, delete_local=True)
    finally:
        unreadable.chmod(0o600)

    assert "product/surface.md" in report.downloaded
    assert (dest / "product" / "surface.md").read_text(encoding="utf-8") == "fresh\n"
