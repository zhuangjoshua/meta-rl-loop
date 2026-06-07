import { useState } from "react";
import type { TakyonOperatorAccountResponse } from "@/lib/api";
import type { Theme } from "../App";
import { useAuth } from "../auth/useAuth";
import { Button, Card, Divider, FormField, Input } from "../composer-ui/lib";
import "./settings.css";

export type SettingsSection = "profile" | "billing";

const TABS: Array<{ key: SettingsSection; label: string }> = [
  { key: "profile", label: "Profile" },
  { key: "billing", label: "Billing" },
];

function formatUsd(cents: number | null | undefined) {
  if (typeof cents !== "number" || !Number.isFinite(cents)) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

export function Settings({
  section,
  theme,
  account,
  portalBusy,
  topupBusy,
  onTheme,
  onOpenPortal,
  onTopup,
  onClose,
}: {
  section: SettingsSection;
  theme: Theme;
  account: TakyonOperatorAccountResponse | null;
  portalBusy: boolean;
  topupBusy: boolean;
  onTheme: (t: Theme) => void;
  onOpenPortal: () => void;
  onTopup: (amountCents: number) => void;
  onClose: () => void;
}) {
  const { user } = useAuth();
  const [sec, setSec] = useState<SettingsSection>(section);

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

          {sec === "billing" && (
            <>
              <Card variant="outline" className="lb-set__card">
                <div className="lb-set__card-h">Operator wallet</div>
                <div className="lb-set__planrow">
                  <div>
                    <div className="lb-set__plan-name">{formatUsd(account?.spendable_cents ?? null)}</div>
                    <div className="lb-set__muted">Spendful CEO turns and wakes draw from this balance.</div>
                  </div>
                </div>
                <Divider className="lb-set__rule" />
                <div className="lb-set__usage"><span>Included remaining</span><span className="lb-set__muted">{formatUsd(account?.allowance_remaining_cents ?? null)}</span></div>
                <div className="lb-set__usage"><span>Added funds</span><span className="lb-set__muted">{formatUsd(account?.topup_balance_cents ?? null)}</span></div>
                <div className="lb-set__usage"><span>Reserved</span><span className="lb-set__muted">{formatUsd(account?.reserved_cents ?? null)}</span></div>
                <div className="lb-btnrow lb-set__save">
                  <Button disabled={topupBusy} onClick={() => onTopup(2500)}>Add $25</Button>
                  <Button variant="secondary" disabled={topupBusy} onClick={() => onTopup(10000)}>Add $100</Button>
                </div>
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
