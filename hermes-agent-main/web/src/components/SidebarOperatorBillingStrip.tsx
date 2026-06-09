import { useCallback, useEffect, useState } from "react";

import {
  api,
  type TakyonOperatorAccountResponse,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const BUDGET_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function formatBudgetCents(value?: number | null): string {
  const cents = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return BUDGET_FORMATTER.format(cents / 100);
}

function formatPercent(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

function operatorUsageRemainingPercent(
  account?: TakyonOperatorAccountResponse | null,
): number | null {
  if (!account?.available) return null;
  const percent = Number(account.allowance_percent_remaining ?? NaN);
  return Number.isFinite(percent) ? Math.max(0, percent) : null;
}

function currentDashboardReturnPath(): string {
  if (typeof window === "undefined") return "/";
  const path = window.location.pathname || "/";
  const search = window.location.search || "";
  return `${path}${search}`;
}

function operatorActionErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  const raw = error.message.replace(/^\d+:\s*/, "");
  try {
    const payload = JSON.parse(raw) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    // Keep the original text when the backend didn't return JSON.
  }
  return raw || fallback;
}

export function SidebarOperatorBillingStrip() {
  const [account, setAccount] = useState<TakyonOperatorAccountResponse | null>(null);
  const [busy, setBusy] = useState<"withdraw" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshAccount = useCallback(async () => {
    try {
      setAccount(await api.getTakyonOperatorAccount());
    } catch {
      setAccount((prev) => prev || { available: false, reason: "request_failed" });
    }
  }, []);

  useEffect(() => {
    void refreshAccount();
    const timer = window.setInterval(() => {
      void refreshAccount();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [refreshAccount]);

  const openWithdraw = useCallback(async () => {
    setBusy("withdraw");
    setError(null);
    try {
      const res = await api.createTakyonOperatorPayoutConnect(
        currentDashboardReturnPath(),
      );
      if (!res.connect_url) {
        throw new Error("Payout link unavailable.");
      }
      window.location.assign(res.connect_url);
    } catch (err) {
      setError(operatorActionErrorMessage(err, "Withdraw failed."));
      setBusy(null);
      await refreshAccount();
    }
  }, [refreshAccount]);

  const usageRemainingPercent = operatorUsageRemainingPercent(account);
  const planName = String(account?.operator_plan_name || "").trim();
  const payoutStatus = account?.available
    ? String(account.stripe_connect_status || "none")
    : "none";
  const withdrawLabel =
    payoutStatus === "active" ? "Withdraw" : "Connect payouts";

  return (
    <div
      className={cn(
        "border-t border-current/10 px-5 py-2",
        "font-mondwest text-[0.62rem] leading-snug tracking-[0.11em]",
        "text-muted-foreground/75",
      )}
    >
      <p className="pb-1 text-muted-foreground/45">operator billing</p>

      <div className="space-y-1">
        <p className="break-words">
          <span className="text-muted-foreground/45">plan</span>{" "}
          <span className="font-medium text-midground">
            {planName || "—"}
          </span>
        </p>
        <p className="break-words">
          <span className="text-muted-foreground/45">weekly usage</span>{" "}
          <span className="font-medium text-midground">
            {formatPercent(usageRemainingPercent)}
          </span>
        </p>
        {(Number(account?.topup_balance_cents || 0) > 0) && (
          <p className="break-words">
          <span className="text-muted-foreground/45">top-ups</span>{" "}
          <span className="font-medium text-midground">
            {account?.available
              ? formatBudgetCents(account.topup_balance_cents)
              : "—"}
          </span>
          </p>
        )}
        <p className="break-words">
          <span className="text-muted-foreground/45">customer payouts</span>{" "}
          <span className="font-medium text-midground">
            {account?.available
              ? formatBudgetCents(account.owed_balance_cents)
              : "—"}
          </span>
        </p>
      </div>

      <button
        type="button"
        onClick={() => void openWithdraw()}
        disabled={!account?.available || busy !== null}
        className={cn(
          "mt-1.5 w-full border border-current/20 px-2 py-1 text-left text-midground transition-opacity",
          "hover:bg-midground/5 disabled:opacity-40",
        )}
      >
        {busy === "withdraw" ? "Opening…" : withdrawLabel}
      </button>

      {error ? (
        <p className="mt-1.5 break-words text-[0.58rem] text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
