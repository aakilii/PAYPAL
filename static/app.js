/* Mandate Web UI — plain HTML/CSS/JS, zero build tools. */

const state = {
  config: null,
  activeMandate: null,
  spentTodayUsd: 0,
  draftRawText: "",
  draftAssumptions: [],
  killSwitch: false,
  latestSeq: 0,
  orders: [],
  disputes: [],
  disputesAvailable: false,
  catalogLoaded: false,
  seenEvents: new Set(),
  ledgerOk: true,
  tamperedSeen: false,
};

const RULE_LABELS = {
  KILL_SWITCH: "Emergency Kill Switch",
  MANDATE_INACTIVE: "Mandate Confirmed & Active",
  MANDATE_EXPIRED: "Within Time Window",
  OVER_ITEM_CAP: "Per-Item Price Cap",
  OVER_DAY_CAP: "Daily Budget Cap",
  ITEM_MISMATCH: "Matches Requested Item",
  UNKNOWN_SELLER: "Trusted Seller Allowlist",
  HIGH_RISK: "Seller Risk Threshold",
};

function escapeHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function isSafeHttpUrl(raw) {
  if (!raw || typeof raw !== "string") return false;
  try {
    const parsed = new URL(raw, window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function formatShortTime(isoStr) {
  if (!isoStr) return "";
  const dt = new Date(isoStr);
  if (Number.isNaN(dt.getTime())) return String(isoStr);
  return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function setServerOffline(offline) {
  const banner = document.getElementById("server-offline-banner");
  if (banner) banner.classList.toggle("hidden", !offline);
}

async function withButtonBusy(btn, busyText, fn) {
  if (!btn) return fn();
  if (btn.disabled) return undefined;
  const origHtml = btn.innerHTML;
  const isCard = btn.classList.contains("scenario-card");
  btn.disabled = true;
  if (!isCard && busyText) {
    btn.textContent = busyText;
  }
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    if (!isCard) {
      btn.innerHTML = origHtml;
    }
  }
}

function showAlert(message, kind = "danger") {
  const box = document.getElementById("global-alert");
  box.className = `alert alert-${kind}`;
  box.textContent = message;
  box.classList.remove("hidden");
}

function hideAlert() {
  document.getElementById("global-alert").classList.add("hidden");
}

async function apiJson(url, options = {}) {
  let res;
  try {
    res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    setServerOffline(false);
  } catch {
    setServerOffline(true);
    throw new Error(
      "Cannot reach the Mandate server (http://127.0.0.1:8000). Start it with `uvicorn app.main:app --reload`."
    );
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `Request failed (HTTP ${res.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function updateJudgeGuide() {
  const stepsEl = document.getElementById("guide-steps");
  const hintEl = document.getElementById("guide-next-hint");
  if (!stepsEl || !hintEl) return;

  const hasMandate = Boolean(state.activeMandate) || state.seenEvents.has("MANDATE_CONFIRMED");
  const hasOrder = state.orders.length > 0 || state.seenEvents.has("ORDER_CREATED");
  const hasHoldOrBlock = state.seenEvents.has("HUMAN_RESOLVED") || state.seenEvents.has("PURCHASE_DECIDED");
  const hasDispute = state.disputes.length > 0 || state.seenEvents.has("DISPUTE_REPLY_DRAFTED");
  const hasTamper = !state.ledgerOk || state.tamperedSeen;

  const doneMap = {
    mandate: hasMandate,
    normal: hasOrder,
    protect: hasOrder && hasHoldOrBlock,
    merchant: hasDispute,
    ledger: hasTamper,
  };

  const order = ["mandate", "normal", "protect", "merchant", "ledger"];
  const firstPending = order.find((k) => !doneMap[k]) || null;

  stepsEl.querySelectorAll("[data-step]").forEach((el) => {
    const key = el.dataset.step;
    el.classList.toggle("done", Boolean(doneMap[key]));
    el.classList.toggle("current", key === firstPending);
  });

  if (!hasMandate) {
    hintEl.innerHTML =
      "<strong>Step 1:</strong> Click <em>Parse mandate</em> (to inspect AI's assumed rules) or <em>1-Click Quick Setup</em> to lock in a $35 jacket budget.";
  } else if (!hasOrder) {
    hintEl.innerHTML =
      "<strong>Step 2:</strong> Click <em>Normal buy ($24)</em> below to watch the 8 Guard checks pass and create a PayPal order.";
  } else if (!state.seenEvents.has("HUMAN_RESOLVED")) {
    hintEl.innerHTML =
      "<strong>Step 3:</strong> Try <em>Over the cap</em>, <em>Fake seller</em> (then review it in <em>2. Approvals</em>), and <em>Injection attack</em> to watch code beat prompt hacks.";
  } else if (!hasDispute) {
    hintEl.innerHTML =
      "<strong>Step 4:</strong> Open <em>3. Merchant &amp; Disputes</em> to inspect the <em>Verified mandate</em> badge and run <em>Simulate dispute &rarr; Build defense</em>.";
  } else if (!hasTamper) {
    hintEl.innerHTML =
      "<strong>Step 5:</strong> Open <em>4. Audit Ledger</em>, verify the SHA-256 chain, and click <em>Tamper demo</em> to see Mandate catch a modified row instantly.";
  } else {
    hintEl.innerHTML =
      "<strong>Demo complete!</strong> You tested user guardrails, deterministic Guard blocks, human approvals, PayPal proof, Dispute Defender, and SHA-256 tamper detection.";
  }
}

function switchTab(tabName) {
  const tabs = ["shop", "approvals", "merchant", "ledger"];
  for (const name of tabs) {
    const btn = document.getElementById(`tab-btn-${name}`);
    const panel = document.getElementById(`panel-${name}`);
    const active = name === tabName;
    btn.setAttribute("aria-selected", active ? "true" : "false");
    panel.classList.toggle("hidden", !active);
  }
  if (tabName === "approvals") loadHolds();
  if (tabName === "merchant") loadMerchant();
  if (tabName === "ledger") loadLedger();
}

function updateHeaderLedgerBadge(verification) {
  const badge = document.getElementById("badge-ledger");
  if (!badge || !verification) return;
  state.ledgerOk = Boolean(verification.ok);
  if (!verification.ok) state.tamperedSeen = true;
  if (verification.ok) {
    badge.className = "badge badge-ok";
    badge.textContent = `Chain: Intact (${verification.checked})`;
  } else {
    badge.className = "badge badge-danger";
    badge.textContent = `Chain: TAMPERED (#${verification.first_bad_seq})`;
  }
  updateJudgeGuide();
}

async function refreshHeaderLedgerBadge() {
  try {
    const v = await apiJson("/api/ledger/verify");
    updateHeaderLedgerBadge(v);
  } catch {
    // Ignore transient poll errors
  }
}

async function loadConfig() {
  const cfg = await apiJson("/api/config");
  state.config = cfg;

  const llmBadge = document.getElementById("badge-llm");
  llmBadge.textContent = `LLM: ${cfg.llm_mode}`;
  llmBadge.className = `badge ${cfg.llm_mode === "gemini" ? "badge-accent" : ""}`;

  const ppBadge = document.getElementById("badge-paypal");
  const ppLabel = cfg.paypal_mode === "sandbox" ? "SANDBOX" : "MOCK";
  ppBadge.textContent = `PayPal: ${ppLabel}`;
  ppBadge.className = `badge ${cfg.paypal_mode === "sandbox" ? "badge-ok" : "badge-warn"}`;
}

async function loadKillSwitch() {
  const res = await apiJson("/api/kill-switch");
  renderKillSwitch(Boolean(res.enabled));
}

function renderKillSwitch(enabled) {
  state.killSwitch = enabled;
  const btn = document.getElementById("btn-kill-switch");
  const banner = document.getElementById("kill-banner");
  btn.textContent = enabled ? "Kill Switch: ON (Frozen)" : "Kill Switch: OFF";
  btn.setAttribute("aria-pressed", enabled ? "true" : "false");
  btn.className = enabled ? "btn-danger" : "";
  banner.classList.toggle("hidden", !enabled);
}

async function toggleKillSwitch() {
  hideAlert();
  const btn = document.getElementById("btn-kill-switch");
  await withButtonBusy(btn, "Updating...", async () => {
    try {
      const res = await apiJson("/api/kill-switch", {
        method: "POST",
        body: JSON.stringify({ enabled: !state.killSwitch }),
      });
      renderKillSwitch(Boolean(res.enabled));
      await pollActivity();
    } catch (err) {
      showAlert(`Could not toggle kill switch: ${err.message}`);
    }
  });
}

function renderSpendMeter(mandate, spentToday) {
  const card = document.getElementById("spend-meter-card");
  const label = document.getElementById("spend-meter-label");
  const bar = document.getElementById("spend-meter-bar");
  const fill = document.getElementById("spend-meter-fill");
  if (!card || !mandate) {
    if (card) card.classList.add("hidden");
    return;
  }
  const maxDay = Math.max(0.01, Number(mandate.max_day_usd || 0));
  const spent = Math.max(0, Number(spentToday || 0));
  const pct = Math.min(100, Math.round((spent / maxDay) * 100));

  card.classList.remove("hidden");
  label.textContent = `$${spent.toFixed(2)} / $${maxDay.toFixed(2)}`;
  bar.setAttribute("aria-valuenow", String(pct));
  fill.style.width = `${pct}%`;
  fill.className = `meter-fill ${pct >= 90 ? "danger" : pct >= 65 ? "warn" : ""}`;
}

function renderActiveMandate(mandate, spentToday = state.spentTodayUsd) {
  state.activeMandate = mandate;
  state.spentTodayUsd = Number(spentToday || 0);
  renderSpendMeter(mandate, state.spentTodayUsd);

  const box = document.getElementById("active-mandate-box");
  if (!mandate) {
    box.classList.add("hidden");
    box.innerHTML = "";
    updateJudgeGuide();
    return;
  }
  box.className = "alert alert-ok active-mandate-card";
  box.innerHTML = `
    <div>
      <strong>Active Mandate Confirmed</strong> <span class="muted">(user-confirmed, logged to ledger)</span>
      <div class="mandate-chips">
        <span class="mandate-chip">ID: <code>${escapeHtml(mandate.id)}</code></span>
        <span class="mandate-chip">Keywords: <code>${escapeHtml(mandate.item_keywords.join(", "))}</code></span>
        <span class="mandate-chip">Item cap: <strong>$${Number(mandate.max_item_usd).toFixed(2)}</strong></span>
        <span class="mandate-chip">Daily cap: <strong>$${Number(mandate.max_day_usd).toFixed(2)}</strong></span>
        <span class="mandate-chip">Sellers: <code>${escapeHtml(mandate.allowed_sellers.join(", "))}</code></span>
      </div>
    </div>
    <button id="btn-revoke-mandate" class="btn-danger btn-sm" type="button">Revoke</button>
  `;

  const revokeBtn = document.getElementById("btn-revoke-mandate");
  if (revokeBtn) {
    revokeBtn.addEventListener("click", () => handleRevokeMandate(mandate.id, revokeBtn));
  }
  updateJudgeGuide();
}

async function handleRevokeMandate(mandateId, btn) {
  hideAlert();
  await withButtonBusy(btn, "Revoking...", async () => {
    try {
      await apiJson(`/api/mandates/${encodeURIComponent(mandateId)}/revoke`, { method: "POST" });
      renderActiveMandate(null, 0);
      showAlert(`Mandate ${mandateId} revoked and recorded in the ledger.`, "warn");
      await Promise.all([pollActivity(), refreshHeaderLedgerBadge()]);
    } catch (err) {
      showAlert(`Could not revoke mandate: ${err.message}`);
    }
  });
}

async function loadActiveMandate() {
  const data = await apiJson("/api/mandates/active");
  renderActiveMandate(data.mandate, data.spent_today_usd ?? 0);
}

async function handleParseMandate() {
  hideAlert();
  const text = document.getElementById("mandate-input").value.trim();
  if (!text) {
    showAlert("Please describe what the agent should buy before clicking Parse mandate.", "warn");
    return;
  }
  const btn = document.getElementById("btn-parse-mandate");
  await withButtonBusy(btn, "Parsing...", async () => {
    try {
      const draft = await apiJson("/api/mandates/parse", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      state.draftRawText = draft.raw_text;
      state.draftAssumptions = draft.assumptions || [];

      document.getElementById("draft-keywords").value = (draft.item_keywords || []).join(", ");
      document.getElementById("draft-item-cap").value = draft.max_item_usd;
      document.getElementById("draft-day-cap").value = draft.max_day_usd;
      document.getElementById("draft-sellers").value = (draft.allowed_sellers || []).join(", ");
      document.getElementById("draft-ttl").value = draft.ttl_hours;

      const joined = state.draftAssumptions.join(" ").toLowerCase();
      document.getElementById("assumed-keywords").classList.toggle("hidden", !joined.includes("item_keywords"));
      document.getElementById("assumed-item-cap").classList.toggle("hidden", !joined.includes("max_item_usd"));
      document.getElementById("assumed-day-cap").classList.toggle("hidden", !joined.includes("max_day_usd"));
      document.getElementById("assumed-sellers").classList.toggle("hidden", !joined.includes("allowed_sellers"));
      document.getElementById("assumed-ttl").classList.toggle("hidden", !joined.includes("expires_at"));

      const ul = document.getElementById("draft-assumptions");
      ul.innerHTML = state.draftAssumptions
        .map((a) => `<li>Assumed: ${escapeHtml(a)}</li>`)
        .join("");

      document.getElementById("draft-form").classList.remove("hidden");
    } catch (err) {
      showAlert(`Could not parse mandate: ${err.message}`);
    }
  });
}

async function handleQuickMandate() {
  hideAlert();
  const btn = document.getElementById("btn-quick-mandate");
  const text = document.getElementById("mandate-input").value.trim() || "get me a black jacket under $35";
  await withButtonBusy(btn, "Setting up...", async () => {
    try {
      const draft = await apiJson("/api/mandates/parse", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      const res = await apiJson("/api/mandates", {
        method: "POST",
        body: JSON.stringify({
          raw_text: draft.raw_text,
          item_keywords: draft.item_keywords,
          max_item_usd: draft.max_item_usd,
          max_day_usd: draft.max_day_usd,
          allowed_sellers: draft.allowed_sellers,
          ttl_hours: draft.ttl_hours,
          assumptions: draft.assumptions || [],
        }),
      });
      document.getElementById("draft-form").classList.add("hidden");
      renderActiveMandate(res.mandate, 0);
      await Promise.all([pollActivity(), refreshHeaderLedgerBadge()]);
    } catch (err) {
      showAlert(`Quick setup failed: ${err.message}`);
    }
  });
}

async function handleConfirmMandate(evt) {
  evt.preventDefault();
  hideAlert();
  const keywords = document
    .getElementById("draft-keywords")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const sellers = document
    .getElementById("draft-sellers")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const payload = {
    raw_text: state.draftRawText || document.getElementById("mandate-input").value.trim(),
    item_keywords: keywords,
    max_item_usd: Number(document.getElementById("draft-item-cap").value),
    max_day_usd: Number(document.getElementById("draft-day-cap").value),
    allowed_sellers: sellers,
    ttl_hours: Number(document.getElementById("draft-ttl").value),
    assumptions: state.draftAssumptions,
  };

  const btn = document.getElementById("btn-confirm-mandate");
  await withButtonBusy(btn, "Confirming...", async () => {
    try {
      const res = await apiJson("/api/mandates", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderActiveMandate(res.mandate, 0);
      document.getElementById("draft-form").classList.add("hidden");
      await Promise.all([pollActivity(), refreshHeaderLedgerBadge()]);
    } catch (err) {
      showAlert(`Could not confirm mandate: ${err.message}`);
    }
  });
}

function decisionBadgeClass(decision) {
  if (decision === "APPROVED") return "badge-ok";
  if (decision === "HELD") return "badge-warn";
  return "badge-danger";
}

function renderRiskMeterHtml(scoreVal) {
  const score = Math.max(0, Math.min(1, Number(scoreVal || 0)));
  const threshold = Number(state.config?.risk_threshold ?? 0.7);
  const pct = Math.round(score * 100);
  const tone = score >= threshold ? "danger" : score >= 0.35 ? "warn" : "";
  return `
    <div class="risk-row">
      <h4>Seller Risk Score: <strong>${score.toFixed(2)}</strong> <span class="muted">(hold &ge; ${threshold.toFixed(2)})</span></h4>
      <div class="meter-track" aria-label="Risk score ${score.toFixed(2)} out of 1.00">
        <div class="meter-fill ${tone}" style="width:${pct}%"></div>
      </div>
    </div>
  `;
}

function renderAgentResult(res) {
  const container = document.getElementById("agent-result");
  container.classList.remove("hidden");

  const att = res.attempt;
  const pick = res.agent_pick;
  const order = res.order;

  const attackNotice = pick.attack_label
    ? `<div class="alert alert-warn"><strong>${escapeHtml(pick.attack_label)}</strong><br />Agent reasoning: ${escapeHtml(pick.why)}</div>`
    : `<p class="muted"><strong>Agent choice:</strong> <code>${escapeHtml(pick.product_id)}</code> &mdash; ${escapeHtml(pick.why)}</p>`;

  const safeComp = res.safe_agent_comparison
    ? `<div class="alert alert-ok">
         <strong>Safe-prompt comparison:</strong> Would pick <code>${escapeHtml(res.safe_agent_comparison.product_id)}</code> (${escapeHtml(res.safe_agent_comparison.product_title)} &mdash; $${Number(res.safe_agent_comparison.price_usd || 0).toFixed(2)}).<br />
         <span class="muted">${escapeHtml(res.safe_agent_comparison.note)}</span>
       </div>`
    : "";

  const checksHtml = (att.checks || [])
    .map((chk) => {
      let badge = `<span class="badge badge-ok">PASS</span>`;
      if (chk.skipped) badge = `<span class="badge">SKIP</span>`;
      else if (chk.passed === false) badge = `<span class="badge badge-danger">FAIL</span>`;
      const friendly = RULE_LABELS[chk.name] || chk.name;
      return `<li>${badge} <div><strong>${escapeHtml(friendly)}</strong> <span class="mono muted">(${escapeHtml(chk.name)})</span><br /><span class="muted">${escapeHtml(chk.detail)}</span></div></li>`;
    })
    .join("");

  const riskHtml = (att.risk_reasons || [])
    .map((r) => `<li>${escapeHtml(r)}</li>`)
    .join("");

  let orderHtml = "";
  if (res.paypal_error) {
    orderHtml = `<div class="alert alert-danger"><strong>PayPal error:</strong> ${escapeHtml(res.paypal_error)}</div>`;
  } else if (order) {
    const modeNote =
      order.source === "sandbox"
        ? "Opens PayPal Sandbox Checkout. Why login? Real Sandbox tests require a fake Personal Sandbox Buyer account from developer.paypal.com → Sandbox Accounts (or set PAYPAL_MODE=mock in .env for instant 1-click simulated capture)."
        : "Simulated mock PayPal approval (PAYPAL_MODE=mock).";
    const approveLink =
      order.approval_url && isSafeHttpUrl(order.approval_url)
        ? `<a class="btn-link btn-primary" href="${escapeHtml(order.approval_url)}">Approve in PayPal</a>`
        : "";
    orderHtml = `
      <div class="alert alert-ok">
        <div class="dispute-head">
          <div>
            <strong>Order Created (${escapeHtml(order.source.toUpperCase())})</strong><br />
            Order ID: <code>${escapeHtml(order.paypal_order_id)}</code> &middot;
            Amount: <strong>$${Number(order.amount_usd).toFixed(2)}</strong> &middot;
            Status: <strong>${escapeHtml(order.status)}</strong>
            <div class="muted">${escapeHtml(modeNote)}</div>
          </div>
          ${approveLink}
        </div>
      </div>
    `;
  } else if (att.decision === "HELD") {
    orderHtml = `
      <div class="alert alert-warn">
        <strong>Held for Human Review:</strong> Open the <strong>2. Approvals</strong> tab to approve or deny this purchase.
      </div>
    `;
  }

  container.innerHTML = `
    <div class="result-card">
      <div class="result-header">
        <h3>${escapeHtml(att.product_title)} &mdash; $${Number(att.price_usd).toFixed(2)} <span class="muted">(${escapeHtml(att.seller)})</span></h3>
        <span class="badge ${decisionBadgeClass(att.decision)}">${escapeHtml(att.decision)} ${att.reason_codes.length ? `(${escapeHtml(att.reason_codes.join(", "))})` : ""}</span>
      </div>
      ${attackNotice}
      ${safeComp}
      ${orderHtml}
      <h4>Deterministic Guard Checks (8 Rules in Python)</h4>
      <ul class="checks-list">${checksHtml}</ul>
      ${renderRiskMeterHtml(att.risk_score)}
      <ul class="assumptions-list">${riskHtml}</ul>
    </div>
  `;
}

async function runAgentScenario(requestText, attack = "none", triggerBtn = null) {
  hideAlert();
  if (!state.activeMandate) {
    showAlert(
      "Step 1 required: Click 'Parse mandate' (or '1-Click Quick Setup') above first so the Guard has your spending limits.",
      "warn"
    );
    document.getElementById("btn-quick-mandate")?.focus();
    return;
  }
  document.getElementById("agent-request-input").value = requestText;
  await withButtonBusy(triggerBtn, "Running...", async () => {
    try {
      const res = await apiJson("/api/agent/run", {
        method: "POST",
        body: JSON.stringify({
          mandate_id: state.activeMandate.id,
          request_text: requestText,
          attack,
        }),
      });
      renderAgentResult(res);
      await Promise.all([
        pollActivity(),
        refreshHoldsBadge(),
        loadActiveMandate(),
        refreshOrdersSummary(),
        refreshHeaderLedgerBadge(),
      ]);
    } catch (err) {
      showAlert(`Agent run failed: ${err.message}`);
    }
  });
}

async function loadCatalogPreview() {
  const target = document.getElementById("catalog-preview");
  if (!target || state.catalogLoaded) return;
  try {
    const data = await apiJson("/api/catalog");
    const products = data.products || [];
    state.catalogLoaded = true;
    target.innerHTML = products
      .map((p) => {
        const trustBadge = p.seller_verified
          ? `<span class="badge badge-ok">Verified</span>`
          : `<span class="badge badge-warn">Unverified</span>`;
        return `
          <div class="catalog-item">
            <div class="catalog-item-head">
              <span>${escapeHtml(p.title)}</span>
              <strong>$${Number(p.price_usd).toFixed(2)}</strong>
            </div>
            <div style="margin-bottom:0.25rem;"><code>${escapeHtml(p.seller)}</code> ${trustBadge}</div>
            <div class="muted">${escapeHtml(p.description)}</div>
          </div>
        `;
      })
      .join("");
  } catch (err) {
    target.innerHTML = `<div class="alert alert-danger">Could not load catalog: ${escapeHtml(err.message)}</div>`;
  }
}

async function refreshOrdersSummary() {
  try {
    const orderData = await apiJson("/api/orders");
    state.orders = orderData.orders || [];
    updateDisputeButtons();
    updateJudgeGuide();
  } catch {
    // Ignore transient poll errors
  }
}

function updateDisputeButtons() {
  const simBtn = document.getElementById("btn-sim-dispute");
  if (!simBtn) return;
  const hasOrders = state.orders.length > 0;
  simBtn.disabled = !hasOrders;
  simBtn.title = hasOrders
    ? "Simulate a customer dispute on your latest order"
    : "Run Normal buy on Shop tab first to create an order";
}

async function refreshHoldsBadge() {
  try {
    const data = await apiJson("/api/holds");
    document.getElementById("holds-count").textContent = String((data.holds || []).length);
  } catch {
    // Ignore transient polling errors
  }
}

async function loadHolds() {
  try {
    const data = await apiJson("/api/holds?include_resolved=true");
    const holds = data.holds || [];
    const pendingCount = holds.filter((h) => !h.human_resolution).length;
    document.getElementById("holds-count").textContent = String(pendingCount);

    const listEl = document.getElementById("holds-list");
    if (!holds.length) {
      listEl.innerHTML = `<div class="empty-state">No held purchases yet. Run the <strong>Fake seller</strong> scenario on the Shop tab to see a purchase pause here.</div>`;
      return;
    }

    listEl.innerHTML = holds
      .map((h) => {
        const pending = !h.human_resolution;
        const statusBadge = pending
          ? `<span class="badge badge-warn">PENDING REVIEW</span>`
          : `<span class="badge ${h.human_resolution === "approved" ? "badge-ok" : "badge-danger"}">HUMAN: ${escapeHtml(h.human_resolution.toUpperCase())}</span>`;
        const reasons = (h.risk_reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
        const actions = pending
          ? `<div class="btn-group">
               <button class="btn-ok" type="button" data-approve-id="${escapeHtml(h.id)}">Approve</button>
               <button class="btn-danger" type="button" data-deny-id="${escapeHtml(h.id)}">Deny</button>
             </div>`
          : "";
        return `
          <div class="card">
            <div class="dispute-head">
              <div>
                <strong>${escapeHtml(h.product_title)} &mdash; $${Number(h.price_usd).toFixed(2)} (${escapeHtml(h.seller)})</strong>
                <p class="muted">
                  Attempt <code>${escapeHtml(h.id)}</code> &middot; Reasons: <code>${escapeHtml(h.reason_codes.join(", "))}</code>
                </p>
              </div>
              ${statusBadge}
            </div>
            ${renderRiskMeterHtml(h.risk_score)}
            <ul class="assumptions-list">${reasons}</ul>
            ${actions}
          </div>
        `;
      })
      .join("");

    listEl.querySelectorAll("[data-approve-id]").forEach((btn) => {
      btn.addEventListener("click", () => handleHoldAction(btn.dataset.approveId, "approve", btn));
    });
    listEl.querySelectorAll("[data-deny-id]").forEach((btn) => {
      btn.addEventListener("click", () => handleHoldAction(btn.dataset.denyId, "deny", btn));
    });
  } catch (err) {
    showAlert(`Could not load holds: ${err.message}`);
  }
}

async function handleHoldAction(attemptId, action, btn) {
  hideAlert();
  await withButtonBusy(btn, action === "approve" ? "Approving..." : "Denying...", async () => {
    try {
      const res = await apiJson(`/api/holds/${encodeURIComponent(attemptId)}/${action}`, { method: "POST" });
      if (res.paypal_error) {
        showAlert(`Approved hold, but PayPal order failed: ${res.paypal_error}`);
      } else if (res.order && res.order.approval_url) {
        showAlert(`Hold approved! Order ${res.order.paypal_order_id} created — check Merchant tab or click Approve in PayPal.`, "ok");
      }
      await Promise.all([
        loadHolds(),
        pollActivity(),
        loadMerchant(),
        loadActiveMandate(),
        refreshHeaderLedgerBadge(),
      ]);
    } catch (err) {
      showAlert(`Hold ${action} failed: ${err.message}`);
    }
  });
}

async function loadMerchant() {
  try {
    const orderData = await apiJson("/api/orders");
    const orders = orderData.orders || [];
    state.orders = orders;
    updateDisputeButtons();

    const ordersEl = document.getElementById("orders-list");
    if (!orders.length) {
      ordersEl.innerHTML = `<div class="empty-state">No orders created yet. Run <strong>Normal buy</strong> on the Shop tab first.</div>`;
    } else {
      const rowsHtml = orders
        .map((o) => {
          const verifiedBadge = o.verified_mandate
            ? `<span class="badge badge-ok">&#10003; Verified mandate</span>`
            : `<span class="badge badge-danger">&#10005; Unverified / Tampered</span>`;
          const sourceBadge = `<span class="badge">${escapeHtml(o.source)}</span>`;
          const actionCell =
            (o.status === "CREATED" || o.status === "PAYER_ACTION_REQUIRED") &&
            o.approval_url &&
            isSafeHttpUrl(o.approval_url)
              ? `<a class="btn-link btn-primary btn-sm" href="${escapeHtml(o.approval_url)}">Approve in PayPal</a>`
              : `<span class="mono">${escapeHtml(o.capture_id || "—")}</span>`;
          return `
            <tr>
              <td><code>${escapeHtml(o.id)}</code><br /><span class="mono muted">${escapeHtml(o.paypal_order_id)}</span></td>
              <td>${escapeHtml(o.product_title || o.product_id)}<br /><span class="muted">${escapeHtml(o.seller || "")}</span></td>
              <td><strong>$${Number(o.amount_usd).toFixed(2)}</strong></td>
              <td>${escapeHtml(o.status)} ${sourceBadge}</td>
              <td>${verifiedBadge}</td>
              <td>${actionCell}</td>
            </tr>
          `;
        })
        .join("");

      ordersEl.innerHTML = `
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Order / PayPal ID</th>
                <th>Product</th>
                <th>Amount</th>
                <th>Status</th>
                <th>Mandate Proof</th>
                <th>Capture / Checkout</th>
              </tr>
            </thead>
            <tbody>${rowsHtml}</tbody>
          </table>
        </div>
      `;
    }

    await Promise.all([loadDisputes(), refreshHeaderLedgerBadge()]);
    updateJudgeGuide();
  } catch (err) {
    showAlert(`Could not load merchant orders: ${err.message}`);
  }
}

async function loadDisputes() {
  const disputesEl = document.getElementById("disputes-list");
  const syncNoteEl = document.getElementById("dispute-sync-note");
  try {
    const isSandbox = Boolean(state.config && state.config.paypal_mode === "sandbox");
    const syncParam = isSandbox ? "?sync_paypal=true" : "";
    const data = await apiJson(`/api/disputes${syncParam}`);
    state.disputesAvailable = true;
    const disputes = data.disputes || [];
    state.disputes = disputes;

    if (syncNoteEl) {
      if (data.sync_note) {
        syncNoteEl.textContent = `PayPal Sandbox Disputes API: ${data.sync_note}`;
        syncNoteEl.classList.remove("hidden");
      } else if (isSandbox) {
        syncNoteEl.textContent = "Synced live with PayPal Sandbox Disputes API.";
        syncNoteEl.classList.remove("hidden");
      } else {
        syncNoteEl.classList.add("hidden");
      }
    }

    if (!disputes.length) {
      disputesEl.innerHTML = state.orders.length
        ? `<div class="empty-state">Ready! Click <strong>Simulate dispute</strong> above to test Dispute Defender on your order.</div>`
        : `<div class="empty-state">No disputes yet. Complete a <strong>Normal buy</strong> on Shop first, or click <strong>Simulate unknown-order dispute</strong> to see how Mandate flags missing records.</div>`;
      return;
    }

    disputesEl.innerHTML = disputes
      .map((d) => {
        const strengthClass =
          d.strength === "STRONG"
            ? "badge-ok"
            : d.strength === "WEAK"
              ? "badge-warn"
              : "badge-danger";
        const sourceBadge = `<span class="badge">${escapeHtml(d.source)}</span>`;
        const packetHtml = d.evidence
          ? `<details class="evidence-details">
               <summary>View Evidence Packet (Ledger-only)</summary>
               <pre class="json-box">${escapeHtml(JSON.stringify(d.evidence, null, 2))}</pre>
             </details>`
          : "";
        const replyHtml = d.draft_reply
          ? `<div class="alert ${d.strength === "STRONG" ? "alert-ok" : "alert-warn"} draft-reply-box">
               <div class="draft-reply-header">
                 <strong>Draft Reply</strong>
                 <button class="btn-sm" type="button" data-copy-reply="${escapeHtml(d.draft_reply)}">Copy reply</button>
               </div>
               <div>${escapeHtml(d.draft_reply)}</div>
             </div>`
          : "";
        return `
          <div class="card dispute-card">
            <div class="dispute-head">
              <div>
                <strong>Dispute <code>${escapeHtml(d.id)}</code></strong> on Order <code>${escapeHtml(d.order_id)}</code> ${sourceBadge}
                <p class="muted">Claim: "${escapeHtml(d.claim)}"</p>
              </div>
              <div class="dispute-actions">
                <span class="badge ${strengthClass}">Strength: ${escapeHtml(d.strength)}</span>
                <button class="btn-primary btn-sm" type="button" data-defend-id="${escapeHtml(d.id)}">Build defense</button>
              </div>
            </div>
            ${replyHtml}
            ${packetHtml}
          </div>
        `;
      })
      .join("");

    disputesEl.querySelectorAll("[data-defend-id]").forEach((btn) => {
      btn.addEventListener("click", () => handleBuildDefense(btn.dataset.defendId, btn));
    });
    disputesEl.querySelectorAll("[data-copy-reply]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(btn.dataset.copyReply || "");
          btn.textContent = "Copied!";
          setTimeout(() => {
            btn.textContent = "Copy reply";
          }, 1500);
        } catch {
          showAlert("Clipboard copy unavailable in this browser context.", "warn");
        }
      });
    });
    updateJudgeGuide();
  } catch (err) {
    disputesEl.innerHTML = `<div class="alert alert-danger">Could not load disputes: ${escapeHtml(err.message)}</div>`;
  }
}

async function handleSimulateDispute(unknownOrder = false, btn = null) {
  hideAlert();
  await withButtonBusy(btn, "Simulating...", async () => {
    try {
      const completedOrder = state.orders.find((o) => o.status === "COMPLETED");
      const targetOrder = completedOrder || state.orders[0];
      if (!unknownOrder && !targetOrder) {
        showAlert("Create an order with Normal buy on the Shop tab first, or click 'Simulate unknown-order dispute'.", "warn");
        return;
      }
      const orderId = unknownOrder ? "ord_unknown_fake" : targetOrder.id;
      await apiJson("/api/disputes/simulate", {
        method: "POST",
        body: JSON.stringify({
          order_id: orderId,
          claim: "I did not authorize this AI purchase.",
        }),
      });
      await Promise.all([loadDisputes(), pollActivity(), refreshHeaderLedgerBadge()]);
    } catch (err) {
      showAlert(`Dispute simulation failed: ${err.message}`);
    }
  });
}

async function handleBuildDefense(disputeId, btn) {
  hideAlert();
  await withButtonBusy(btn, "Building...", async () => {
    try {
      await apiJson(`/api/disputes/${encodeURIComponent(disputeId)}/defend`, { method: "POST" });
      await Promise.all([loadDisputes(), pollActivity(), refreshHeaderLedgerBadge()]);
    } catch (err) {
      showAlert(`Could not build defense: ${err.message}`);
    }
  });
}

async function loadLedger() {
  try {
    const data = await apiJson("/api/ledger");
    renderLedgerVerification(data.verification);

    const events = data.events || [];
    const listEl = document.getElementById("ledger-list");
    if (!events.length) {
      listEl.innerHTML = `<div class="empty-state">Ledger is empty. Confirm a mandate to write the GENESIS-linked entry.</div>`;
      return;
    }

    const devMode = Boolean(state.config && state.config.dev_mode);
    const rowsHtml = events
      .slice()
      .reverse()
      .map((ev) => {
        const tamperBtn = devMode
          ? `<button class="btn-danger btn-sm" type="button" data-tamper-seq="${ev.seq}">Tamper demo</button>`
          : "";
        return `
          <tr>
            <td><strong>#${ev.seq}</strong></td>
            <td><code>${escapeHtml(ev.type)}</code><br /><span class="mono muted">${escapeHtml(ev.ref_id)}</span></td>
            <td>
              <div class="mono">prev: ${escapeHtml(ev.prev_hash.slice(0, 14))}...</div>
              <div class="mono">hash: ${escapeHtml(ev.hash.slice(0, 14))}...</div>
              <div class="muted">${escapeHtml(formatShortTime(ev.ts))}</div>
            </td>
            <td>
              <details class="evidence-details">
                <summary>Body JSON</summary>
                <pre class="json-box">${escapeHtml(JSON.stringify(ev.body, null, 2))}</pre>
              </details>
            </td>
            ${devMode ? `<td>${tamperBtn}</td>` : ""}
          </tr>
        `;
      })
      .join("");

    listEl.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Seq</th>
              <th>Event / Ref</th>
              <th>Hash Chain</th>
              <th>Payload</th>
              ${devMode ? "<th>Dev Demo</th>" : ""}
            </tr>
          </thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
    `;

    listEl.querySelectorAll("[data-tamper-seq]").forEach((btn) => {
      btn.addEventListener("click", () => handleTamperDemo(Number(btn.dataset.tamperSeq), btn));
    });
  } catch (err) {
    showAlert(`Could not load ledger: ${err.message}`);
  }
}

function renderLedgerVerification(v) {
  updateHeaderLedgerBadge(v);
  const banner = document.getElementById("ledger-verify-banner");
  if (v.ok) {
    banner.className = "alert alert-ok";
    banner.innerHTML = `<strong>Chain Intact (Tamper-Evident Check Passed)</strong> &mdash; Verified ${v.checked} entries. Head hash: <code class="mono">${escapeHtml(v.head_hash)}</code>`;
  } else {
    banner.className = "alert alert-danger";
    banner.innerHTML = `<strong>TAMPER DETECTED!</strong> Hash verification failed at entry <strong>#${v.first_bad_seq}</strong> (checked ${v.checked} entries).`;
  }
}

async function handleVerifyLedger() {
  hideAlert();
  const btn = document.getElementById("btn-verify-ledger");
  await withButtonBusy(btn, "Verifying...", async () => {
    try {
      const v = await apiJson("/api/ledger/verify");
      renderLedgerVerification(v);
    } catch (err) {
      showAlert(`Verification error: ${err.message}`);
    }
  });
}

async function handleTamperDemo(seq, btn) {
  hideAlert();
  await withButtonBusy(btn, "Tampering...", async () => {
    try {
      const res = await apiJson(`/api/dev/tamper/${seq}`, { method: "POST" });
      renderLedgerVerification(res.verification);
      await loadLedger();
    } catch (err) {
      showAlert(`Tamper demo failed: ${err.message}`);
    }
  });
}

function eventBadgeClass(type) {
  if (type === "MANDATE_CONFIRMED" || type === "ORDER_CAPTURED") return "badge-ok";
  if (type === "MANDATE_REVOKED" || type === "PAYPAL_ERROR") return "badge-danger";
  if (type === "DISPUTE_RECEIVED" || type === "KILL_SWITCH_CHANGED") return "badge-warn";
  return "badge-accent";
}

async function pollActivity() {
  try {
    const data = await apiJson(`/api/activity?since=${state.latestSeq}`);
    const events = data.events || [];
    if (!events.length) return;

    state.latestSeq = data.latest_seq || state.latestSeq;
    const feed = document.getElementById("activity-feed");
    const empty = feed.querySelector(".empty-state");
    if (empty) empty.remove();

    for (const ev of events) {
      state.seenEvents.add(ev.type);
      const li = document.createElement("li");
      li.className = "activity-item";
      li.innerHTML = `
        <div class="activity-meta">
          <span class="badge ${eventBadgeClass(ev.type)}">#${ev.seq} ${escapeHtml(ev.type)}</span>
          <span class="muted mono">${escapeHtml(formatShortTime(ev.ts))}</span>
        </div>
        <div>${escapeHtml(ev.summary)}</div>
      `;
      feed.prepend(li);
    }
    updateJudgeGuide();
  } catch {
    // Offline banner is already handled inside apiJson on network failure
  }
}

const TOUR_STEPS = [
  {
    selector: "#card-mandate",
    tab: "shop",
    title: "1. User-Confirmed Spending Guardrails",
    body: "Start with a plain-English request like 'black jacket under $35'. Mandate highlights every assumed limit (daily cap, trusted stores, expiry) so you approve exact bounds before the AI can spend.",
  },
  {
    selector: "#card-guard",
    tab: "shop",
    title: "2. Deterministic Code Beats Prompt Attacks",
    body: "The AI shopper picks only a product ID. Plain Python code loads the real catalog price and checks 8 rules—so even if a fake listing tricks the AI with a hidden SYSTEM NOTICE, code blocks it cold.",
  },
  {
    selector: "#tab-btn-approvals",
    tab: "shop",
    title: "3. Human-in-the-Loop Holds & PayPal Sandbox",
    body: "Risky sellers (like 6-day unverified domains scoring ≥ 0.70) pause in Approvals for manual Approve/Deny. Safe buys create real PayPal Sandbox orders using idempotency keys.",
  },
  {
    selector: "#tab-btn-merchant",
    tab: "merchant",
    title: "4. Merchant Proof & Dispute Defender",
    body: "Merchants see a Verified Mandate badge and can defend chargebacks using an evidence packet built strictly from the audit ledger—rating defense strength STRONG or warning WEAK/NO_RECORD.",
  },
  {
    selector: "#tab-btn-ledger",
    tab: "ledger",
    title: "5. SHA-256 Tamper-Evident Ledger",
    body: "Every action chains to the previous SHA-256 hash from GENESIS. Click 'Tamper demo' on any entry to simulate database tampering and watch verification pinpoint the exact modified row.",
  },
];

let tourIndex = -1;
let highlightedEl = null;

function positionPopoverNear(pop, rect, widthFallback = 300) {
  const pad = 12;
  const popRect = pop.getBoundingClientRect();
  const popW = popRect.width || widthFallback;
  const popH = popRect.height || 140;

  let top = rect.bottom + 8;
  if (top + popH > window.innerHeight - pad) {
    top = Math.max(pad, rect.top - popH - 8);
  }
  let left = rect.left;
  if (left + popW > window.innerWidth - pad) {
    left = Math.max(pad, window.innerWidth - popW - pad);
  }
  left = Math.max(pad, left);
  pop.style.top = `${Math.round(top)}px`;
  pop.style.left = `${Math.round(left)}px`;
}

function setupTooltips() {
  const pop = document.getElementById("tooltip-popover");
  if (!pop) return;
  let activeTrigger = null;

  const hideTip = () => {
    pop.classList.add("hidden");
    activeTrigger = null;
  };

  const showTip = (btn) => {
    const text = btn.getAttribute("data-tip");
    if (!text) return;
    activeTrigger = btn;
    pop.textContent = text;
    pop.classList.remove("hidden");
    positionPopoverNear(pop, btn.getBoundingClientRect(), 290);
  };

  document.querySelectorAll(".tip-btn[data-tip]").forEach((btn) => {
    btn.addEventListener("mouseenter", () => showTip(btn));
    btn.addEventListener("focus", () => showTip(btn));
    btn.addEventListener("mouseleave", hideTip);
    btn.addEventListener("blur", hideTip);
    btn.addEventListener("click", (evt) => {
      evt.stopPropagation();
      if (activeTrigger === btn && !pop.classList.contains("hidden")) {
        hideTip();
      } else {
        showTip(btn);
      }
    });
  });

  document.addEventListener("click", (evt) => {
    if (!evt.target.closest(".tip-btn")) hideTip();
  });
}

function closeTour() {
  tourIndex = -1;
  if (highlightedEl) {
    highlightedEl.classList.remove("tour-highlight");
    highlightedEl = null;
  }
  const pop = document.getElementById("tour-popover");
  if (pop) pop.classList.add("hidden");
}

function showTourStep(idx) {
  const pop = document.getElementById("tour-popover");
  if (!pop) return;
  if (idx < 0 || idx >= TOUR_STEPS.length) {
    closeTour();
    return;
  }
  tourIndex = idx;
  const step = TOUR_STEPS[idx];
  switchTab(step.tab);

  if (highlightedEl) highlightedEl.classList.remove("tour-highlight");
  const target = document.querySelector(step.selector);
  if (target) {
    highlightedEl = target;
    target.classList.add("tour-highlight");
    target.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  document.getElementById("tour-step-count").textContent = `Step ${idx + 1} of ${TOUR_STEPS.length}`;
  document.getElementById("tour-title").textContent = step.title;
  document.getElementById("tour-body").textContent = step.body;

  const prevBtn = document.getElementById("tour-prev-btn");
  const nextBtn = document.getElementById("tour-next-btn");
  prevBtn.disabled = idx === 0;
  nextBtn.textContent = idx === TOUR_STEPS.length - 1 ? "Finish" : "Next";

  pop.classList.remove("hidden");
  if (target) {
    positionPopoverNear(pop, target.getBoundingClientRect(), 340);
  }
}

function setupTour() {
  const startBtn = document.getElementById("btn-start-tour");
  const closeBtn = document.getElementById("tour-close-btn");
  const prevBtn = document.getElementById("tour-prev-btn");
  const nextBtn = document.getElementById("tour-next-btn");
  if (startBtn) startBtn.addEventListener("click", () => showTourStep(0));
  if (closeBtn) closeBtn.addEventListener("click", closeTour);
  if (prevBtn) prevBtn.addEventListener("click", () => showTourStep(tourIndex - 1));
  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (tourIndex >= TOUR_STEPS.length - 1) closeTour();
      else showTourStep(tourIndex + 1);
    });
  }
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape" && tourIndex >= 0) closeTour();
  });
}

function setupEvents() {
  setupTooltips();
  setupTour();
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  const toggleGuideBtn = document.getElementById("btn-toggle-guide");
  if (toggleGuideBtn) {
    toggleGuideBtn.addEventListener("click", () => {
      const body = document.getElementById("guide-body");
      const hidden = body.classList.toggle("hidden");
      toggleGuideBtn.textContent = hidden ? "Show guide" : "Hide guide";
      toggleGuideBtn.setAttribute("aria-expanded", hidden ? "false" : "true");
    });
  }

  document.getElementById("btn-kill-switch").addEventListener("click", toggleKillSwitch);
  document.getElementById("btn-parse-mandate").addEventListener("click", handleParseMandate);
  document.getElementById("btn-quick-mandate").addEventListener("click", handleQuickMandate);
  document.getElementById("draft-form").addEventListener("submit", handleConfirmMandate);

  document.getElementById("btn-run-agent").addEventListener("click", (evt) => {
    const txt = document.getElementById("agent-request-input").value.trim();
    if (!txt) {
      showAlert("Enter a shopping prompt for the agent first.", "warn");
      return;
    }
    runAgentScenario(txt, "none", evt.currentTarget);
  });

  document.getElementById("btn-scen-normal").addEventListener("click", (evt) =>
    runAgentScenario("get me a black denim jacket", "none", evt.currentTarget)
  );
  document.getElementById("btn-scen-overcap").addEventListener("click", (evt) =>
    runAgentScenario("get me a black leather jacket", "none", evt.currentTarget)
  );
  document.getElementById("btn-scen-fake").addEventListener("click", (evt) =>
    runAgentScenario("buy black winter jacket from cheap-jackets-now.shop", "none", evt.currentTarget)
  );
  document.getElementById("btn-scen-injection").addEventListener("click", (evt) =>
    runAgentScenario("get me a black jacket", "injection", evt.currentTarget)
  );

  const catalogDrawer = document.getElementById("catalog-drawer");
  if (catalogDrawer) {
    catalogDrawer.addEventListener("toggle", () => {
      if (catalogDrawer.open) loadCatalogPreview();
    });
  }

  document.getElementById("btn-verify-ledger").addEventListener("click", handleVerifyLedger);
  document.getElementById("btn-sim-dispute").addEventListener("click", (evt) =>
    handleSimulateDispute(false, evt.currentTarget)
  );
  document.getElementById("btn-sim-fake-dispute").addEventListener("click", (evt) =>
    handleSimulateDispute(true, evt.currentTarget)
  );
}

async function init() {
  setupEvents();
  try {
    await Promise.all([
      loadConfig(),
      loadKillSwitch(),
      loadActiveMandate(),
      refreshHoldsBadge(),
      refreshOrdersSummary(),
      refreshHeaderLedgerBadge(),
    ]);
    await pollActivity();
    updateJudgeGuide();
  } catch (err) {
    showAlert(`Startup error: ${err.message}`);
  }

  const params = new URLSearchParams(window.location.search);
  const tab = params.get("tab");
  if (tab && ["shop", "approvals", "merchant", "ledger"].includes(tab)) {
    switchTab(tab);
  }
  if (params.get("captured")) {
    showAlert(`PayPal checkout completed and captured order ${params.get("captured")}!`, "ok");
  } else if (params.get("paypal_error")) {
    showAlert(`PayPal checkout failed: ${params.get("paypal_error")}`, "danger");
  } else if (params.get("paypal_cancelled")) {
    showAlert("PayPal checkout was cancelled by the buyer. No funds were captured.", "warn");
  }
  if (params.toString()) {
    window.history.replaceState({}, "", window.location.pathname);
  }

  setInterval(() => {
    pollActivity();
    refreshHoldsBadge();
  }, 2000);
}

window.addEventListener("DOMContentLoaded", init);
