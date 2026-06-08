from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_SOURCE = REPO_ROOT / "web" / "src" / "litebulb" / "product" / "Product.tsx"


def test_product_uses_background_run_as_progress_fallback():
    source = PRODUCT_SOURCE.read_text(encoding="utf-8")

    assert "function backgroundRunProgress(" in source
    assert "const run = workspace?.background_run;" in source
    assert 'const payload = run as Record<string, unknown>;' in source
    assert 'const progress = liveProgress(payload.status, payload.detail);' in source
    assert 'const currentAction = (overview as Record<string, unknown>).current_action;' not in source
    assert 'const ceoLoop = (overview as Record<string, unknown>).ceo_loop;' not in source
    assert 'const tasks = (overview as Record<string, unknown>).tasks;' not in source
    assert "const effectiveProgress = chatProgress ?? backgroundRunProgress(workspace);" in source
    assert 'progress={effectiveProgress}' in source
