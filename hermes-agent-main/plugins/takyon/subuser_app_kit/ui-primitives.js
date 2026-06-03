function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function stateTone(state) {
  switch (String(state || "").toLowerCase()) {
    case "live":
      return "success";
    case "blocked":
      return "warn";
    case "broken":
      return "danger";
    default:
      return "muted";
  }
}

export function renderRailBadge(rail, state) {
  const tone = stateTone(state);
  return `<span class="tk-pill tk-pill-${tone}">${escapeHtml(rail)}: ${escapeHtml(
    state || "unverified",
  )}</span>`;
}

export function renderRailStatusList(context = {}) {
  const rails = Array.isArray(context.runtimeFeatures) ? context.runtimeFeatures : [];
  if (!rails.length) {
    return `<div class="tk-note">No runtime rails declared for this surface.</div>`;
  }
  const items = rails
    .map((rail) => renderRailBadge(rail, context.railState?.[rail] || "unverified"))
    .join("");
  return `<div class="tk-pill-row">${items}</div>`;
}

export function renderBlockedRail({ rail, state, title, body, actionLabel, actionHref } = {}) {
  const badge = renderRailBadge(rail || "rail", state || "blocked");
  const action = actionLabel && actionHref
    ? `<a class="tk-button tk-button-ghost" href="${escapeHtml(actionHref)}">${escapeHtml(actionLabel)}</a>`
    : "";
  return `
    <section class="tk-card tk-blocked">
      <div class="tk-card-head">
        ${badge}
      </div>
      <h3>${escapeHtml(title || "This runtime rail is not live yet.")}</h3>
      <p>${escapeHtml(body || "Keep the action visible, but do not simulate the result.")}</p>
      ${action}
    </section>
  `.trim();
}

export function renderUsageSummary(account = {}) {
  const usage = account?.usage_this_period || {};
  const revenue = account?.revenue || {};
  const events = Number(usage.events || 0);
  const estimated = Number(usage.estimated_cost_microusd || 0);
  const actual = Number(usage.actual_cost_microusd || 0);
  const paid = Number(revenue.amount_paid_cents || 0);
  return `
    <div class="tk-grid tk-grid-compact">
      <div class="tk-card"><strong>${events}</strong><span>usage events</span></div>
      <div class="tk-card"><strong>${(actual / 1000000).toFixed(2)}</strong><span>actual USD</span></div>
      <div class="tk-card"><strong>${(estimated / 1000000).toFixed(2)}</strong><span>estimated USD</span></div>
      <div class="tk-card"><strong>${(paid / 100).toFixed(2)}</strong><span>paid USD</span></div>
    </div>
  `.trim();
}

export function renderPricingCards(plans = []) {
  const cards = (Array.isArray(plans) ? plans : [])
    .map((plan) => `
      <article class="tk-card tk-plan">
        <div class="tk-plan-head">
          <strong>${escapeHtml(plan.name || plan.plan_key || "Plan")}</strong>
          <span>${escapeHtml(plan.interval || plan.billing_interval || "")}</span>
        </div>
        <div class="tk-price">${escapeHtml(plan.price || "")}</div>
        <p>${escapeHtml(plan.description || "")}</p>
      </article>
    `)
    .join("");
  return `<div class="tk-grid">${cards}</div>`;
}

export function renderApiQuickstart({ title, installCommand, codeSample } = {}) {
  return `
    <section class="tk-card">
      <h3>${escapeHtml(title || "Quickstart")}</h3>
      <div class="tk-code">${escapeHtml(installCommand || "npm install your-sdk")}</div>
      <pre class="tk-code"><code>${escapeHtml(codeSample || "client.doThing();")}</code></pre>
    </section>
  `.trim();
}
