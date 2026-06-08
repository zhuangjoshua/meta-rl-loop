from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = REPO_ROOT / "web" / "src" / "litebulb" / "App.tsx"
HOOK_SOURCE = REPO_ROOT / "web" / "src" / "litebulb" / "takyon" / "useTakyonLitebulb.ts"


def test_new_build_flow_resets_stale_ready_or_error_state():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'if ((buildState.status === "ready" || buildState.status === "error") && buildState.goal !== pendingIdea)' in source
    assert "resetBuildState();" in source
    assert "A fresh build request must not inherit the previous company's redirect state." in source


def test_start_build_clears_previous_company_redirect_before_queueing_new_idea():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert """const startBuild = (idea: string) => {
    const text = idea.trim();
    if (!text) return;
    resetBuildState();
    setPendingIdea(text);""" in source


def test_litebulb_hook_exports_fresh_build_state_helper():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert "function createEmptyBuildState(): BuildState" in source
    assert "const resetBuildState = useCallback(() => {" in source
    assert "setBuildState(createEmptyBuildState());" in source
