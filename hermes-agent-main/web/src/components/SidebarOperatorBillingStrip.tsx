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

function operatorSpendableCents(
  account?: TakyonOperatorAccountResponse | null,
): number | null {
  if (!account?.available) return null;
  const cents = Number(account.spendable_cents ?? 0);
  return Number.isFinite(cents) ? Math.max(0, cents) : 0;
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
  const [amount, setAmount] = useState("25");
  const [busy, setBusy] = useState<"topup" | "withdraw" | null>(null);
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

  const submitTopup = useCallback(async () => {
    const dollars = Number.parseFloat(amount);
    const amountCents = Number.isFinite(dollars) ? Math.round(dollars * 100) : 0;
    if (amountCents <= 0) {
      setError("Enter a valid top-up amount.");
      return;
    }
    setBusy("topup");
    setError(null);
    try {
      const res = await api.createTakyonOperatorTopupCheckout(
        amountCents,
        currentDashboardReturnPath(),
      );
      if (!res.checkout_url) {
        throw new Error("Top-up checkout URL unavailable.");
      }
      window.location.assign(res.checkout_url);
    } catch (err) {
      setError(operatorActionErrorMessage(err, "Top-up failed."));
      setBusy(null);
    }
  }, [amount]);

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

  const spendableCents = operatorSpendableCents(account);
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
          <span className="text-muted-foreground/45">budget</span>{" "}
          <span className="font-medium text-midground">
            {spendableCents === null ? "—" : formatBudgetCents(spendableCents)}
          </span>
        </p>
        <p className="break-words">
          <span className="text-muted-foreground/45">customer payouts</span>{" "}
          <span className="font-medium text-midground">
            {account?.available
              ? formatBudgetCents(account.owed_balance_cents)
              : "—"}
          </span>
        </p>
      </div>

      <div className="mt-2 flex gap-1.5">
        <input
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          inputMode="decimal"
          placeholder="25"
          disabled={!account?.available || busy !== null}
          className={cn(
            "min-w-0 flex-1 border border-current/20 bg-transparent px-2 py-1",
            "text-[0.62rem] text-midground placeholder:text-muted-foreground/35",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
            "disabled:opacity-40",
          )}
          aria-label="Operator top-up amount"
        />
        <button
          type="button"
          onClick={() => void submitTopup()}
          disabled={!account?.available || busy !== null}
          className={cn(
            "border border-current/20 px-2 py-1 text-midground transition-opacity",
            "hover:bg-midground/5 disabled:opacity-40",
          )}
        >
          {busy === "topup" ? "Opening…" : "Top up"}
        </button>
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
