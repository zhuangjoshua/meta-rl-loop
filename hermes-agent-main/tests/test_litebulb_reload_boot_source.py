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


def test_dashboard_company_x_uses_operator_delete_api():
    api_source = API_SOURCE.read_text(encoding="utf-8")
    hook_source = HOOK_SOURCE.read_text(encoding="utf-8")
    app_source = (REPO_ROOT / "web" / "src" / "litebulb" / "App.tsx").read_text(encoding="utf-8")
    companies_source = (REPO_ROOT / "web" / "src" / "litebulb" / "app" / "companies.tsx").read_text(encoding="utf-8")

    assert "deleteTakyonBusiness: (slug: string) =>" in api_source
    assert "`/api/takyon/businesses/${encodeURIComponent(slug)}`" in api_source
    assert '{ method: "DELETE" }' in api_source
    assert "await api.deleteTakyonBusiness(businessSlug);" in hook_source
    assert "onDelete={deleteBusiness}" in app_source
    assert "onClick={(e) => { e.stopPropagation(); void handleDelete(c.slug, c.name); }}" in companies_source


def test_open_business_boots_shell_before_full_workspace_refresh():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'const loadBusinessHomeShell = useCallback(async (slug: string) => {' in source
    assert 'api.getTakyonBusinessHome(businessSlug)' in source
    assert 'if (!current || current.business_slug !== businessSlug) {' in source
    assert 'return current;' in source
    assert 'if (activeBusiness?.slug === businessSlug && sessionBusinessRef.current === businessSlug) {' in source
    assert 'setChatMessages([]);' in source
    assert 'loadBusinessHomeShell(businessSlug).catch(() => undefined),' in source
    assert 'void loadWorkspace(businessSlug).catch(() => undefined);' in source


def test_full_workspace_load_uses_only_the_workspace_snapshot():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert "const workspacePayload = await api.getTakyonBusinessWorkspace(businessSlug, 60, \"full\");" in source
    assert "setWorkspace(workspacePayload);" in source
    assert 'api.getTakyonBusinessWorkspace(businessSlug, 60, "full")' in source
    assert "api.getTakyonBusinessSitePreview(businessSlug)" not in source
    assert "Promise.allSettled" not in source


def test_published_product_preview_uses_workspace_preview_metadata():
    source = (REPO_ROOT / "web" / "src" / "litebulb" / "product" / "Product.tsx").read_text(encoding="utf-8")

    assert 'const previewAvailable = Boolean(product.preview_available);' in source
    assert 'const previewPath = typeof product.preview_path === "string" ? product.preview_path : "product/site";' in source
    assert 'buildTakyonBusinessSitePreviewFrameUrl(business.slug, previewPath)' in source
    assert "previewUrl" not in source


def test_business_switch_ignores_stale_workspace_and_session_writes():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'const visibleBusinessRef = useRef("");' in source
    assert 'const isVisibleScope = useCallback((slug: string) => {' in source
    assert 'return trimText(slug).toLowerCase() === visibleBusinessRef.current;' in source
    assert "const isVisibleBusiness = useCallback((slug: string) => {" in source
    assert 'return Boolean(businessSlug) && isVisibleScope(businessSlug);' in source
    assert "if (!isVisibleBusiness(businessSlug)) return;" in source
    assert 'setWorkspace(workspacePayload);' in source
    assert "sitePreviewUrl" not in source
    assert 'if (!isVisibleScope(businessSlug)) return "";' in source
    assert "visibleBusinessRef.current = businessSlug;" in source
    assert 'sessionIdRef.current = "";' in source
    assert 'sessionBusinessRef.current = "";' in source


def test_litebulb_polls_only_the_workspace_snapshot_after_boot():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert "homeShellPollRef" not in source
    assert "window.setInterval(() => {\n      void loadWorkspace(activeBusiness.slug);" in source
    assert "loadBusinessHomeShell(activeBusiness.slug);" not in source


def test_litebulb_reuses_stored_session_before_creating_a_new_one():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'const LITEBULB_SESSION_STORAGE_KEY = "takyon.litebulb.sessions.v1";' in source
    assert 'const LITEBULB_PENDING_TURN_STORAGE_KEY = "takyon.litebulb.pendingTurns.v1";' in source
    assert "function historyHasPendingReply(payload: HistoryPayload | null | undefined) {" in source
    assert "function historyUserTexts(payload: HistoryPayload | null | undefined) {" in source
    assert "function historyHasPendingTurn(" in source
    assert "function latestAssistantReply(messages: ChatMessage[]) {" in source
    assert "const nextUserText = userTexts[pendingTurn.userCountBefore];" in source
    assert 'const liveWorkingAssistant = [...prev].reverse().find((message) => message.who === "agent" && message.working);' in source
    assert "const trailingAssistant = latestAssistantReply(next);" in source
    assert "&& trimText(trailingAssistant.text).length >= trimText(liveWorkingAssistant.text).length" in source
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
    assert 'await ensureGateway().request("session.interrupt", {' in source


def test_create_flow_uses_global_scope_session_and_recovers_missing_session():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'visibleBusinessRef.current = "";' in source
    assert 'visibleBusinessRef.current = businessSlug;' in source
    assert 'let sessionId = await ensureSession("");' in source
    assert 'for (let attempt = 0; attempt < 4; attempt += 1) {' in source
    assert 'if (isMissingSessionError(error)) {' in source
    assert 'sessionIdRef.current = "";' in source
    assert 'sessionBusinessRef.current = "";' in source
    assert 'sessionId = await ensureSession("");' in source
    assert 'if (attempt < 3 && isBusyError(error)) {' in source


def test_litebulb_clears_bootstrap_transcript_when_authoritative_history_resets():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert "const mappedHistory = mapHistoryMessages(history);" in source
    assert "if (!pending && !pendingTurnMissing && mappedHistory.length === 0) {" in source
    assert "chatMessagesRef.current = [];" in source
    assert "return [];" in source


def test_same_business_fast_path_still_marks_the_business_visible():
    source = HOOK_SOURCE.read_text(encoding="utf-8")

    assert 'visibleBusinessRef.current = businessSlug;\n    if (activeBusiness?.slug === businessSlug && sessionBusinessRef.current === businessSlug) {' in source
