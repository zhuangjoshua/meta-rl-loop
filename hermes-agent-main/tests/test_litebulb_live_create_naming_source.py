from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITEBULB_ADAPTERS = [
    REPO_ROOT / "web" / "public" / "litebulb" / "takyon-adapter.js",
    REPO_ROOT / "takyon_cli" / "web_dist" / "litebulb" / "takyon-adapter.js",
]


def test_live_create_no_longer_invents_browser_business_names():
    for path in LITEBULB_ADAPTERS:
        source = path.read_text(encoding="utf-8")

        assert 'const businessName = name || "";' in source
        assert 'const businessSlug = name ? slugifyName(name) : "";' in source
        assert "deriveBrand(name || goal)" not in source


def test_live_shell_no_longer_rebrands_from_goal_text():
    for path in LITEBULB_ADAPTERS:
        source = path.read_text(encoding="utf-8")

        assert "summary.name || humanizeKey(business)" in source
        assert "summary.name || brand.name" not in source
