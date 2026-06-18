import { useState } from "react";
import type { TakyonOperatorAccountResponse } from "@/lib/api";
import type { Theme } from "../App";
import { useAuth } from "../auth/useAuth";
import { Button, Card, Divider, FormField, Input } from "../composer-ui/lib";
import "./settings.css";

export type SettingsSection = "profile" | "billing" | "plans";

const TABS: Array<{ key: SettingsSection; label: string }> = [
  { key: "profile", label: "Profile" },
  { key: "plans", label: "Plans" },
  { key: "billing", label: "Billing" },
];

function formatUsd(cents: number | null | undefined) {
  if (typeof cents !== "number" || !Number.isFinite(cents)) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

function formatPercent(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

function formatResetDate(value: string | null | undefined) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function planLabel(account: { operator_plan_name?: string | null } | null | undefined) {
  const raw = String(account?.operator_plan_name || "").trim();
  return raw || "Plan";
}

export function Settings(props: {
  section: SettingsSection;
  theme: Theme;
  account: TakyonOperatorAccountResponse | null;
  portalBusy: boolean;
  topupBusy: boolean;
  nudge?: string;
  subscribeBusy?: string | null;
  onTheme: (t: Theme) => void;
  onOpenPortal: () => void;
  onTopup: (amountCents: number) => void;
  onSubscribe?: (planId: string) => void;
  onClose: () => void;
}) {
  const {
    section,
    theme,
    account,
    portalBusy,
    nudge,
    subscribeBusy,
    onTheme,
    onOpenPortal,
    onSubscribe,
    onClose,
  } = props;
  const { user } = useAuth();
  const [sec, setSec] = useState<SettingsSection>(section);
  const allowanceIncluded = Number(account?.allowance_included_cents || 0);
  const weeklyIncluded = Number(account?.operator_plan_weekly_allowance_cents || allowanceIncluded || 0);
  const reservedUsagePercent = allowanceIncluded > 0
    ? (Number(account?.reserved_allowance_cents || 0) / allowanceIncluded) * 100
    : null;
  const resetLabel = formatResetDate(account?.allowance_resets_at);

  return (
    <div className="lb-modal-scrim" onClick={onClose}>
      <div className="lb-modal lb-setm" role="dialog" aria-modal="true" aria-label="Settings" onClick={(event) => event.stopPropagation()}>
        <header className="lb-modal__head">
          <h2 className="lb-modal__title">Settings</h2>
          <button className="lb-modal__x" onClick={onClose} aria-label="Close settings">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M4 4l8 8M12 4l-8 8" /></svg>
          </button>
        </header>

        <nav className="lb-setm__tabs">
          {TABS.map((tab) => (
            <button key={tab.key} className={`lb-set__tab${sec === tab.key ? " is-active" : ""}`} onClick={() => setSec(tab.key)}>
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="lb-setm__body lb-set__body">
          {sec === "profile" && (
            <Card variant="outline" className="lb-set__card">
              <div className="lb-set__card-h">Profile</div>
              <div className="lb-set__avatarrow">
                <span className="lb-set__avatar">{(user?.name?.[0] ?? "U").toUpperCase()}</span>
                <Button variant="secondary" size="small" disabled>Managed by Auth0</Button>
              </div>
              <FormField label="Name"><Input value={user?.name ?? ""} readOnly /></FormField>
              <FormField label="Email"><Input value={user?.email ?? ""} readOnly /></FormField>
              <Divider className="lb-set__rule" />
              <div className="lb-set__card-h">Appearance</div>
              <div className="lb-set__row">
                <div><div>Theme</div><div className="lb-set__muted">Stored locally for this operator workspace.</div></div>
                <div className="lb-set__seg" role="group" aria-label="Theme">
                  <button type="button" className={theme === "light" ? "is-active" : ""} onClick={() => onTheme("light")}>Light</button>
                  <button type="button" className={theme === "dark" ? "is-active" : ""} onClick={() => onTheme("dark")}>Dark</button>
                </div>
              </div>
            </Card>
          )}

          {sec === "plans" && (
            <Card variant="outline" className="lb-set__card lb-plans">
              <div className="lb-set__card-h">Plans</div>
              {(() => {
                const plans = account?.operator_plans ?? [];
                const currentPlan = String(account?.operator_plan_name || "").trim();
                if (plans.length === 0) {
                  return <div className="lb-set__muted">No subscription tiers are configured yet.</div>;
                }
                return (
                  <div className="lb-plans__grid">
                    {plans.map((plan) => {
                      const isCurrent = currentPlan !== "" && currentPlan === plan.name;
                      const busy = subscribeBusy === plan.id;
                      return (
                        <div key={plan.id} className={`lb-plans__card${plan.featured ? " is-featured" : ""}`}>
                          {plan.featured && <span className="lb-plans__tag">Most popular</span>}
                          <div className="lb-plans__name">{plan.name}</div>
                          {plan.amount_cents ? (
                            <div className="lb-plans__price">
                              {formatUsd(plan.amount_cents)}
                              <span className="lb-plans__per">/{plan.interval || "month"}</span>
                            </div>
                          ) : null}
                          {plan.tagline ? <div className="lb-set__muted">{plan.tagline}</div> : null}
                          {plan.weekly_allowance_cents ? (
                            <div className="lb-set__muted">{formatUsd(plan.weekly_allowance_cents)} weekly included usage</div>
                          ) : null}
                          {plan.features && plan.features.length > 0 ? (
                            <ul className="lb-plans__feats">{plan.features.map((f) => <li key={f}>{f}</li>)}</ul>
                          ) : null}
                          <Button
                            variant={plan.featured ? "primary" : "secondary"}
                            size="small"
                            disabled={isCurrent || busy || !onSubscribe}
                            onClick={() => onSubscribe?.(plan.id)}
                          >
                            {isCurrent ? "Current plan" : busy ? "Opening checkout…" : `Choose ${plan.name}`}
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                );
              })()}
            </Card>
          )}

          {sec === "billing" && (
            <>
              {nudge ? (
                <div className="lb-set__nudge" role="status">{nudge}</div>
              ) : null}
              <Card variant="outline" className="lb-set__card">
                <div className="lb-set__card-h">Operator wallet</div>
                <div className="lb-set__planrow">
                  <div>
                    <div className="lb-set__plan-name">{planLabel(account)}</div>
                    <div className="lb-set__muted">
                      {formatPercent(account?.allowance_percent_remaining ?? null)} remaining of {formatUsd(weeklyIncluded)} included this week{resetLabel ? ` · resets ${resetLabel}` : ""}.
                    </div>
                  </div>
                </div>
                <Divider className="lb-set__rule" />
                <div className="lb-set__usage"><span>Weekly included</span><span className="lb-set__muted">{formatUsd(weeklyIncluded)}</span></div>
                <div className="lb-set__usage"><span>Used this week</span><span className="lb-set__muted">{formatPercent(account?.allowance_percent_used ?? null)}</span></div>
                <div className="lb-set__usage"><span>Reserved usage</span><span className="lb-set__muted">{formatPercent(reservedUsagePercent)}</span></div>
                {(Number(account?.topup_balance_cents || 0) > 0) && (
                  <div className="lb-set__usage"><span>Extra funds</span><span className="lb-set__muted">{formatUsd(account?.topup_balance_cents ?? null)}</span></div>
                )}
                {(Number(account?.reserved_topup_cents || 0) > 0) && (
                  <div className="lb-set__usage"><span>Reserved funds</span><span className="lb-set__muted">{formatUsd(account?.reserved_topup_cents ?? null)}</span></div>
                )}
              </Card>
              <Card variant="outline" className="lb-set__card">
                <div className="lb-set__card-h">Stripe</div>
                <div className="lb-set__row">
                  <div>
                    <div>Payment methods &amp; invoices</div>
                    <div className="lb-set__muted">Managed securely in Stripe's Customer Portal.</div>
                  </div>
                  <Button variant="secondary" size="small" disabled={portalBusy} onClick={onOpenPortal}>
                    Open Stripe portal
                  </Button>
                </div>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
