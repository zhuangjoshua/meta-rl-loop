declare global {
  interface Window {
    /** Set true by the server only for `takyon dashboard --tui` (or TAKYON_DASHBOARD_TUI=1). */
    __TAKYON_DASHBOARD_EMBEDDED_CHAT__?: boolean;
    /** @deprecated Older injected name; treated as on when true. */
    __TAKYON_DASHBOARD_TUI__?: boolean;
  }
}

/** True only when the dashboard was started with embedded TUI Chat (`takyon dashboard --tui`). */
export function isDashboardEmbeddedChatEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (window.__TAKYON_DASHBOARD_EMBEDDED_CHAT__ === true) return true;
  return window.__TAKYON_DASHBOARD_TUI__ === true;
}
