import { db } from "./db";
import { ForbiddenError, NotFoundError } from "./errors";
import { createEvent } from "./events";
import { toJson } from "./json";

export type BudgetAccountRow = {
  id: string;
  business_id: string;
  campaign_id: string | null;
  name: string;
  currency: string;
  status: "active" | "frozen" | "killed" | "archived";
  hard_limit_microusd: string;
  committed_microusd: string;
};

export type BudgetLedgerRow = {
  id: string;
  business_id: string;
  budget_account_id: string | null;
  campaign_id: string | null;
  workflow_job_id: string | null;
  profile_id: string | null;
  kind: string;
  status: string;
  amount_microusd: string;
  currency: string;
  provider: string | null;
  external_ref: string | null;
  purpose: string;
  metadata: unknown;
  created_at: string;
};

export function usdToMicrousd(amountUsd: number) {
  if (!Number.isFinite(amountUsd) || amountUsd < 0) throw new Error("Budget amount must be a non-negative number.");
  return BigInt(Math.round(amountUsd * 1_000_000));
}

export function microusdToUsd(value: string | number | bigint) {
  return Number(value) / 1_000_000;
}

export async function ensureBudgetAccount(input: {
  businessId: string;
  campaignId?: string | null;
  name?: string;
  hardLimitMicrousd: bigint | number | string;
  currency?: string;
  metadata?: Record<string, unknown>;
}) {
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    INSERT INTO business_budget_accounts (
      business_id,
      campaign_id,
      name,
      currency,
      hard_limit_microusd,
      metadata
    )
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      ${input.name ?? "default"},
      ${input.currency ?? "USD"},
      ${String(input.hardLimitMicrousd)},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (
      business_id,
      COALESCE(campaign_id, '00000000-0000-0000-0000-000000000000'::uuid),
      name
    )
    DO UPDATE SET
      hard_limit_microusd = EXCLUDED.hard_limit_microusd,
      currency = EXCLUDED.currency,
      metadata = business_budget_accounts.metadata || EXCLUDED.metadata,
      updated_at = now()
    RETURNING id
  `;
  return rows[0];
}

export async function getBudgetAccount(input: { businessId: string; campaignId?: string | null; name?: string }) {
  const sql = db();
  const rows = await sql<BudgetAccountRow[]>`
    SELECT a.id,
           a.business_id,
           a.campaign_id,
           a.name,
           a.currency,
           a.status,
           a.hard_limit_microusd::text,
           COALESCE((
             SELECT SUM(CASE
               WHEN l.kind IN ('release', 'refund') THEN -l.amount_microusd
               ELSE l.amount_microusd
             END)
             FROM business_budget_ledger l
             WHERE l.budget_account_id = a.id
               AND l.status IN ('active', 'committed')
           ), 0)::text AS committed_microusd
    FROM business_budget_accounts a
    WHERE a.business_id = ${input.businessId}
      AND COALESCE(a.campaign_id, '00000000-0000-0000-0000-000000000000'::uuid) =
          COALESCE(${input.campaignId ?? null}::uuid, '00000000-0000-0000-0000-000000000000'::uuid)
      AND a.name = ${input.name ?? "default"}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function requireBudgetAccount(input: { businessId: string; campaignId?: string | null; name?: string }) {
  const account = await getBudgetAccount(input);
  if (!account) throw new NotFoundError("Budget account not found.");
  return account;
}

export async function reserveBusinessBudget(input: {
  businessId: string;
  amountMicrousd: bigint | number | string;
  campaignId?: string | null;
  workflowJobId?: string | null;
  profileId?: string | null;
  provider?: string | null;
  purpose?: string;
  accountName?: string;
  metadata?: Record<string, unknown>;
}) {
  const account = await requireBudgetAccount({
    businessId: input.businessId,
    campaignId: input.campaignId ?? null,
    name: input.accountName ?? "default"
  });
  if (account.status !== "active") throw new ForbiddenError(`Budget account is ${account.status}.`);

  const requested = BigInt(input.amountMicrousd);
  if (requested < 0n) throw new ForbiddenError("Budget reservation amount must be non-negative.");
  const committed = BigInt(account.committed_microusd);
  const cap = BigInt(account.hard_limit_microusd);
  if (committed + requested > cap) {
    throw new ForbiddenError(
      `Budget cap exceeded: committed $${microusdToUsd(committed).toFixed(2)}, requested $${microusdToUsd(requested).toFixed(2)}, cap $${microusdToUsd(cap).toFixed(2)}.`
    );
  }

  const sql = db();
  const rows = await sql<BudgetLedgerRow[]>`
    INSERT INTO business_budget_ledger (
      business_id,
      budget_account_id,
      campaign_id,
      workflow_job_id,
      profile_id,
      kind,
      status,
      amount_microusd,
      currency,
      provider,
      purpose,
      metadata
    )
    VALUES (
      ${input.businessId},
      ${account.id},
      ${input.campaignId ?? null},
      ${input.workflowJobId ?? null},
      ${input.profileId ?? null},
      'reservation',
      'active',
      ${requested.toString()},
      ${account.currency},
      ${input.provider ?? null},
      ${input.purpose ?? ""},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    RETURNING id, business_id, budget_account_id, campaign_id, workflow_job_id, profile_id,
              kind, status, amount_microusd::text, currency, provider, external_ref, purpose, metadata, created_at
  `;

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "budget.reserved",
    subjectType: "budget_ledger",
    subjectId: rows[0].id,
    payload: {
      amount_microusd: requested.toString(),
      campaign_id: input.campaignId ?? null,
      provider: input.provider ?? null,
      purpose: input.purpose ?? ""
    }
  });
  return rows[0];
}
