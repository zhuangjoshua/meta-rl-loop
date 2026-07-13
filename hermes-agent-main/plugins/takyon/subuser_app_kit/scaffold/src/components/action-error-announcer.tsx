import { useEffect, useState } from "react";
import {
  APP_ACTION_ERROR_EVENT,
  type AppActionErrorNotice,
} from "../lib/hooks";

/** AppKit-owned fallback: action failures are visible even when product UI forgets an error view. */
export function ActionErrorAnnouncer() {
  const [notice, setNotice] = useState<AppActionErrorNotice | null>(null);

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<AppActionErrorNotice>).detail;
      if (detail?.message) setNotice(detail);
    };
    window.addEventListener(APP_ACTION_ERROR_EVENT, receive);
    return () => window.removeEventListener(APP_ACTION_ERROR_EVENT, receive);
  }, []);

  if (!notice) return null;
  return (
    <aside
      key={notice.id}
      role="alert"
      aria-live="assertive"
      data-takyon-appkit="action-error"
      className="fixed right-4 top-4 z-[100] w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-destructive/40 bg-background p-4 text-foreground shadow-xl"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-semibold">Action could not be completed</p>
          <p className="mt-1 break-words text-sm text-muted-foreground">{notice.message}</p>
          {notice.kind === "budget" && notice.checkoutUrl ? (
            <a
              href={notice.checkoutUrl}
              className="mt-2 inline-flex text-sm font-semibold underline underline-offset-4"
            >
              Upgrade plan
            </a>
          ) : null}
        </div>
        <button
          type="button"
          className="shrink-0 rounded px-2 py-1 text-sm font-medium hover:bg-muted"
          aria-label="Dismiss action error"
          onClick={() => setNotice(null)}
        >
          Dismiss
        </button>
      </div>
    </aside>
  );
}
