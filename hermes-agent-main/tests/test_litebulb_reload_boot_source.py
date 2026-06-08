from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = REPO_ROOT / "web" / "src" / "lib" / "api.ts"
HOOK_SOURCE = REPO_ROOT / "web" / "src" / "litebulb" / "takyon" / "useTakyonLitebulb.ts"


def test_workspace_api_supports_boot_view_reads():
    source = API_SOURCE.read_text(encoding="utf-8")

    assert 'getTakyonBusinessWorkspace: (slug: string, limit = 50, view: "full" | "boot" = "full") =>' in source
    assert '&view=${encodeURIComponent(view)}' in source
    assert 'getTakyonBusinessHome: (slug: string) =>' in source
    assert '`/api/takyon/businesses/${encodeURIComponent(slug)}/home`' in source


def test_open_business_boots_shell_before_full_workspace_refresh():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'const loadBusinessHomeShell = useCallback(async (slug: string) => {' in source
    assert 'api.getTakyonBusinessHome(businessSlug)' in source
    assert 'if (activeBusiness?.slug === businessSlug && sessionBusinessRef.current === businessSlug) {' in source
    assert 'setChatMessages([]);' in source
    assert 'loadBusinessHomeShell(businessSlug).catch(() => undefined),' in source
    assert 'void loadWorkspace(businessSlug).catch(() => undefined);' in source


def test_full_workspace_load_parallelizes_preview_fetch():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert "const [workspaceResult, previewResult] = await Promise.allSettled([" in source
    assert 'api.getTakyonBusinessWorkspace(businessSlug, 60, "full")' in source
    assert "api.getTakyonBusinessSitePreview(businessSlug)" in source


def test_business_switch_ignores_stale_workspace_preview_and_session_writes():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'const visibleBusinessRef = useRef("");' in source
    assert "const isVisibleBusiness = useCallback((slug: string) => {" in source
    assert "if (!isVisibleBusiness(businessSlug)) return;" in source
    assert 'if (workspaceResult.status === "fulfilled" && isVisibleBusiness(businessSlug)) {' in source
    assert 'if (previewResult.status === "fulfilled" && isVisibleBusiness(businessSlug)) {' in source
    assert 'if (!isVisibleBusiness(businessSlug)) return "";' in source
    assert "visibleBusinessRef.current = businessSlug;" in source
    assert 'sessionIdRef.current = "";' in source
    assert 'sessionBusinessRef.current = "";' in source


def test_litebulb_reuses_stored_session_before_creating_a_new_one():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'const LITEBULB_SESSION_STORAGE_KEY = "takyon.litebulb.sessions.v1";' in source
    assert 'const LITEBULB_PENDING_TURN_STORAGE_KEY = "takyon.litebulb.pendingTurns.v1";' in source
    assert "function historyHasPendingReply(payload: HistoryPayload | null | undefined) {" in source
    assert "function historyUserTexts(payload: HistoryPayload | null | undefined) {" in source
    assert "function historyHasPendingTurn(" in source
    assert "const nextUserText = userTexts[pendingTurn.userCountBefore];" in source
    assert 'if (sessionIdRef.current && sessionBusinessRef.current === businessSlug) {' in source
    assert "const loaded = await loadHistory(sessionIdRef.current);" in source
    assert "const resolveStoredSessionId = async (storedSessionId: string) => {" in source
    assert "api.getSessionLatestDescendant(candidate)" in source
    assert 'const readDurableSessionId = async (sessionId: string) => {' in source
    assert 'gateway.request<SessionTitlePayload>("session.title"' in source
    assert "function isBusyError(error: unknown) {" in source
    assert "function isMissingSessionError(error: unknown) {" in source
    assert "function wait(ms: number) {" in source
    assert "const chatMessagesRef = useRef<ChatMessage[]>([]);" in source
    assert "const sessionRunningRef = useRef(false);" in source
    assert "const pending = Boolean(history.running) || historyHasPendingReply(history) || pendingTurnMissing;" in source
    assert "const storedSessionId = await resolveStoredSessionId(" in source
    assert "readStoredLitebulbSession(businessSlug)" in source
    assert 'gateway.request<SessionResumePayload>("session.resume"' in source
    assert "_takyon_boot_business: businessSlug || undefined," in source
    assert "const replayPendingTurn = useCallback(async (" in source
    assert 'writeStoredPendingTurn(activeBusiness.slug, pendingTurn);' in source
    assert 'userCountBefore: chatMessagesRef.current.filter((message) => message.who === "user").length,' in source
    assert 'const openingBusinessRef = useRef("");' in source
    assert 'if (openingBusinessRef.current === businessSlug) return;' in source
    assert "void replayPendingTurn(sessionIdRef.current, businessSlug, loaded.pendingTurn);" in source
    assert 'writeStoredLitebulbSession(' in source
    assert 'clearStoredPendingTurn(sessionBusinessRef.current);' in source
    assert 'clearStoredLitebulbSession(businessSlug);' in source
    assert 'await gateway.request("session.interrupt", {' in source
