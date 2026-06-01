import { LogIn, LogOut, ShieldUser } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

import { Typography } from "@/components/NouiTypography";
import { useSidebarStatus } from "@/hooks/useSidebarStatus";
import {
  api,
  TAKYON_BASE_PATH,
  type DashboardAuthStateResponse,
  type TakyonOperatorAccountResponse,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

const ACCOUNT_POLL_MS = 30_000;

function formatOperatorUserId(userId?: string): string | null {
  const value = (userId || "").trim();
  if (!value) return null;
  if (value.length <= 18) return value;
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function businessCountLabel(count?: number): string | null {
  if (typeof count !== "number" || !Number.isFinite(count) || count < 0) return null;
  return `${count} business${count === 1 ? "" : "es"}`;
}

function buildAuthHref(action: "login" | "logout", returnTo: string): string {
  const qs = new URLSearchParams({ return_to: returnTo || "/" });
  return `${TAKYON_BASE_PATH}/auth/${action}?${qs.toString()}`;
}

export function SidebarFooter() {
  const status = useSidebarStatus();
  const { pathname, search, hash } = useLocation();
  const { t } = useI18n();
  const [authState, setAuthState] = useState<DashboardAuthStateResponse | null>(null);
  const [operatorAccount, setOperatorAccount] =
    useState<TakyonOperatorAccountResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      const [authResult, operatorResult] = await Promise.allSettled([
        api.getDashboardAuthState(),
        api.getTakyonOperatorAccount(),
      ]);
      if (cancelled) return;

      if (authResult.status === "fulfilled") {
        setAuthState(authResult.value);
      } else {
        setAuthState((prev) => prev ?? { authenticated: false, auth0_required: false });
      }

      if (operatorResult.status === "fulfilled") {
        setOperatorAccount(operatorResult.value);
      } else {
        setOperatorAccount((prev) => prev ?? null);
      }
    };

    void load();
    const intervalId = window.setInterval(() => {
      void load();
    }, ACCOUNT_POLL_MS);
    const onFocus = () => {
      void load();
    };
    window.addEventListener("focus", onFocus);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const returnTo = useMemo(() => {
    const localPath = `${pathname || "/"}${search}${hash}` || "/";
    return `${TAKYON_BASE_PATH}${localPath === "/" ? "/" : localPath}`;
  }, [hash, pathname, search]);

  const authRequired = authState?.auth0_required === true;
  const signedIn = authState?.authenticated === true;
  const authName = (authState?.user?.name || "").trim();
  const authEmail = (authState?.user?.email || "").trim();
  const operatorUserId = formatOperatorUserId(operatorAccount?.user_id);
  const operatorCount = businessCountLabel(operatorAccount?.owned_business_count);

  const primaryLabel = useMemo(() => {
    if (authState == null) return "Checking account...";
    if (!authRequired) return "Local dashboard session";
    if (!signedIn) return "Not signed in";
    return authName || authEmail || "Authenticated operator";
  }, [authEmail, authName, authRequired, authState, signedIn]);

  const detailLines = useMemo(() => {
    const lines: string[] = [];
    if (authEmail && authEmail !== authName) {
      lines.push(authEmail);
    }
    if (operatorUserId) {
      lines.push(`Takyon user ${operatorUserId}`);
    } else if (signedIn && operatorAccount && operatorAccount.available === false) {
      lines.push("Takyon user mapping unavailable");
    }
    if (operatorCount) {
      lines.push(operatorCount);
    }
    return lines;
  }, [authEmail, authName, operatorAccount, operatorCount, operatorUserId, signedIn]);

  return (
    <div
      className={cn(
        "flex shrink-0 flex-col gap-2",
        "px-3 py-2.5",
        "border-t border-current/10",
      )}
    >
      <div className="rounded-md border border-current/10 bg-foreground/[0.02] px-2.5 py-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-muted-foreground/60">
              <ShieldUser className="h-3.5 w-3.5 shrink-0" />
              <Typography
                mondwest
                className="text-[0.58rem] tracking-[0.15em] uppercase"
              >
                Account
              </Typography>
            </div>

            <Typography className="mt-1 truncate text-[0.78rem] leading-tight normal-case text-midground">
              {primaryLabel}
            </Typography>

            {detailLines.length > 0 && (
              <div className="mt-1 flex flex-col gap-0.5">
                {detailLines.map((line) => (
                  <Typography
                    key={line}
                    className="truncate font-mono-ui text-[0.66rem] normal-case tracking-normal text-muted-foreground/70"
                  >
                    {line}
                  </Typography>
                ))}
              </div>
            )}
          </div>

          {authRequired && (
            <a
              href={buildAuthHref(signedIn ? "logout" : "login", returnTo)}
              className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-current/15 px-2 py-1",
                "font-mondwest text-[0.62rem] tracking-[0.14em] uppercase text-midground/80",
                "transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
              )}
            >
              {signedIn ? (
                <LogOut className="h-3 w-3 shrink-0" />
              ) : (
                <LogIn className="h-3 w-3 shrink-0" />
              )}
              <span>{signedIn ? "Log out" : "Log in"}</span>
            </a>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 px-2">
        <Typography
          mondwest
          className="font-mono-ui text-[0.7rem] tabular-nums tracking-[0.1em] text-muted-foreground/70 lowercase"
        >
          {status?.version != null ? `v${status.version}` : "—"}
        </Typography>

        <a
          href="https://nousresearch.com"
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            "font-mondwest text-[0.65rem] tracking-[0.15em] text-midground",
            "transition-opacity hover:opacity-90",
            "focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
          )}
          style={{ mixBlendMode: "plus-lighter" }}
        >
          {t.app.footer.org}
        </a>
      </div>
    </div>
  );
}
