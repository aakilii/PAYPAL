/* Mandate Web UI — plain HTML/CSS/JS, zero build tools. */

const state = {
  config: null,
  activeMandate: null,
  draftRawText: "",
  draftAssumptions: [],
  killSwitch: false,
  latestSeq: 0,
  orders: [],
  disputesAvailable: false,
};

function escapeHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
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
}

function renderActiveMandate(mandate) {
  state.activeMandate = mandate;
  const box = document.getElementById("active-mandate-box");
  if (!mandate) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.className = "alert alert-ok";
  box.innerHTML = `
    <strong>Active Mandate Confirmed</strong> (confirmed by user — not cryptographically signed)<br />
    ID: <code>${escapeHtml(mandate.id)}</code> &middot;
    Keywords: <code>${escapeHtml(mandate.item_keywords.join(", "))}</code> &middot;
    Max/item: <strong>$${Number(mandate.max_item_usd).toFixed(2)}</strong> &middot;
    Max/day: <strong>$${Number(mandate.max_day_usd).toFixed(2)}</strong> &middot;
    Sellers: <code>${escapeHtml(mandate.allowed_sellers.join(", "))}</code>
  `;
}

async function loadActiveMandate() {
  const data = await apiJson("/api/mandates/active");
  renderActiveMandate(data.mandate);
}

async function handleParseMandate() {
  hideAlert();
  const text = document.getElementById("mandate-input").value.trim();
  if (!text) {
    showAlert("Please describe what the agent should buy before clicking Parse mandate.", "warn");
    return;
  }
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

  try {
    const res = await apiJson("/api/mandates", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderActiveMandate(res.mandate);
    document.getElementById("draft-form").classList.add("hidden");
    await pollActivity();
  } catch (err) {
    showAlert(`Could not confirm mandate: ${err.message}`);
  }
}

function decisionBadgeClass(decision) {
  if (decision === "APPROVED") return "badge-ok";
  if (decision === "HELD") return "badge-warn";
  return "badge-danger";
}

function renderAgentResult(res) {
  const container = document.getElementById("agent-result");
  container.classList.remove("hidden");

  const att = res.attempt;
  const pick = res.agent_pick;
  const order = res.order;

  const attackNotice = pick.attack_label
    ? `<div class="alert alert-warn"><strong>${escapeHtml(pick.attack_label)}</strong><br />Agent reasoning: ${escapeHtml(pick.why)}</div>`
    : `<p class="muted"><strong>Agent choice:</strong> <code>${escapeHtml(pick.product_id)}</code> — ${escapeHtml(pick.why)}</p>`;

  const safeComp = res.safe_agent_comparison
    ? `<div class="alert alert-ok">
         <strong>Safe-prompt comparison:</strong> Would pick <code>${escapeHtml(res.safe_agent_comparison.product_id)}</code> (${escapeHtml(res.safe_agent_comparison.product_title)} — $${Number(res.safe_agent_comparison.price_usd || 0).toFixed(2)}).<br />
         <span class="muted">${escapeHtml(res.safe_agent_comparison.note)}</span>
       </div>`
    : "";

  const checksHtml = (att.checks || [])
    .map((chk) => {
      let badge = `<span class="badge badge-ok">PASS</span>`;
      if (chk.skipped) badge = `<span class="badge">SKIP</span>`;
      else if (chk.passed === false) badge = `<span class="badge badge-danger">FAIL</span>`;
      return `<li>${badge} <div><strong>${escapeHtml(chk.name)}</strong>: ${escapeHtml(chk.detail)}</div></li>`;
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
        ? "Opens real PayPal Sandbox buyer checkout (login with your Sandbox personal account)."
        : "Simulated mock PayPal approval (PAYPAL_MODE=mock).";
    orderHtml = `
      <div class="alert alert-ok">
        <strong>Order Created (${escapeHtml(order.source.toUpperCase())})</strong><br />
        Order ID: <code>${escapeHtml(order.paypal_order_id)}</code> &middot; Amount: <strong>$${Number(order.amount_usd).toFixed(2)}</strong> &middot; Status: <strong>${escapeHtml(order.status)}</strong>
        <p class="muted" style="margin: 0.35rem 0;">${escapeHtml(modeNote)}</p>
        ${
          order.approval_url
            ? `<a class="btn-link btn-primary" href="${escapeHtml(order.approval_url)}">Approve in PayPal</a>`
            : ""
        }
      </div>
    `;
  } else if (att.decision === "HELD") {
    orderHtml = `
      <div class="alert alert-warn">
        <strong>Held for Human Review:</strong> Open the <strong>Approvals</strong> tab to approve or deny this purchase.
      </div>
    `;
  }

  container.innerHTML = `
    <div style="border-top: 1px solid var(--border); padding-top: 0.85rem; margin-top: 0.5rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
        <h3 style="margin:0;">${escapeHtml(att.product_title)} — $${Number(att.price_usd).toFixed(2)} <span class="muted">(${escapeHtml(att.seller)})</span></h3>
        <span class="badge ${decisionBadgeClass(att.decision)}">${escapeHtml(att.decision)} ${att.reason_codes.length ? `(${escapeHtml(att.reason_codes.join(", "))})` : ""}</span>
      </div>
      ${attackNotice}
      ${safeComp}
      ${orderHtml}
      <h4>Guard Rule Checks</h4>
      <ul class="checks-list">${checksHtml}</ul>
      <h4>Seller Risk Score: <strong>${Number(att.risk_score).toFixed(2)}</strong></h4>
      <ul class="muted">${riskHtml}</ul>
    </div>
  `;
}

async function runAgentScenario(requestText, attack = "none") {
  hideAlert();
  if (!state.activeMandate) {
    showAlert("Confirm a mandate first in Step 1 before running the shopper agent.", "warn");
    return;
  }
  document.getElementById("agent-request-input").value = requestText;
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
    await Promise.all([pollActivity(), refreshHoldsBadge()]);
  } catch (err) {
    showAlert(`Agent run failed: ${err.message}`);
  }
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
      listEl.innerHTML = `<div class="empty-state">No held purchases yet. Try the "Fake seller" scenario on the Shop tab.</div>`;
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
          ? `<div class="btn-group" style="margin-top:0.5rem;">
               <button class="btn-ok" type="button" data-approve-id="${escapeHtml(h.id)}">Approve</button>
               <button class="btn-danger" type="button" data-deny-id="${escapeHtml(h.id)}">Deny</button>
             </div>`
          : "";
        return `
          <div class="card">
            <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:0.5rem;">
              <strong>${escapeHtml(h.product_title)} — $${Number(h.price_usd).toFixed(2)} (${escapeHtml(h.seller)})</strong>
              ${statusBadge}
            </div>
            <p class="muted" style="margin:0.35rem 0;">
              Attempt <code>${escapeHtml(h.id)}</code> &middot; Reasons: <code>${escapeHtml(h.reason_codes.join(", "))}</code> &middot; Risk score: <strong>${Number(h.risk_score).toFixed(2)}</strong>
            </p>
            <ul class="muted">${reasons}</ul>
            ${actions}
          </div>
        `;
      })
      .join("");

    listEl.querySelectorAll("[data-approve-id]").forEach((btn) => {
      btn.addEventListener("click", () => handleHoldAction(btn.dataset.approveId, "approve"));
    });
    listEl.querySelectorAll("[data-deny-id]").forEach((btn) => {
      btn.addEventListener("click", () => handleHoldAction(btn.dataset.denyId, "deny"));
    });
  } catch (err) {
    showAlert(`Could not load holds: ${err.message}`);
  }
}

async function handleHoldAction(attemptId, action) {
  hideAlert();
  try {
    const res = await apiJson(`/api/holds/${attemptId}/${action}`, { method: "POST" });
    if (res.paypal_error) {
      showAlert(`Approved hold, but PayPal order failed: ${res.paypal_error}`);
    } else if (res.order && res.order.approval_url) {
      showAlert(`Hold approved! Order ${res.order.paypal_order_id} created — check Merchant tab or click Approve in PayPal.`, "ok");
    }
    await Promise.all([loadHolds(), pollActivity(), loadMerchant()]);
  } catch (err) {
    showAlert(`Hold ${action} failed: ${err.message}`);
  }
}

async function loadMerchant() {
  try {
    const orderData = await apiJson("/api/orders");
    const orders = orderData.orders || [];
    state.orders = orders;

    const ordersEl = document.getElementById("orders-list");
    if (!orders.length) {
      ordersEl.innerHTML = `<div class="empty-state">No orders created yet. Run a "Normal buy" on the Shop tab first.</div>`;
    } else {
      const rowsHtml = orders
        .map((o) => {
          const verifiedBadge = o.verified_mandate
            ? `<span class="badge badge-ok">Verified mandate</span>`
            : `<span class="badge badge-danger">Unverified / Tampered</span>`;
          const sourceBadge = `<span class="badge">${escapeHtml(o.source)}</span>`;
          const actionCell =
            o.status === "CREATED" && o.approval_url
              ? `<a class="btn-link btn-primary" href="${escapeHtml(o.approval_url)}">Approve in PayPal</a>`
              : `<span class="mono">${escapeHtml(o.capture_id || "—")}</span>`;
          return `
            <tr>
              <td><code>${escapeHtml(o.id)}</code><br /><span class="mono">${escapeHtml(o.paypal_order_id)}</span></td>
              <td>${escapeHtml(o.product_title || o.product_id)}<br /><span class="muted">${escapeHtml(o.seller || "")}</span></td>
              <td>$${Number(o.amount_usd).toFixed(2)}</td>
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

    await loadDisputes();
  } catch (err) {
    showAlert(`Could not load merchant orders: ${err.message}`);
  }
}

async function loadDisputes() {
  const disputesEl = document.getElementById("disputes-list");
  try {
    const res = await fetch("/api/disputes");
    if (res.status === 404) {
      state.disputesAvailable = false;
      return;
    }
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    state.disputesAvailable = true;
    const data = await res.json();
    const disputes = data.disputes || [];

    if (!disputes.length) {
      disputesEl.innerHTML = `<div class="empty-state">No disputes yet. Click "Simulate dispute" to test the Dispute Defender.</div>`;
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
          ? `<details style="margin-top:0.5rem;">
               <summary style="cursor:pointer; font-weight:600;">View Evidence Packet (Ledger-only)</summary>
               <pre class="json-box">${escapeHtml(JSON.stringify(d.evidence, null, 2))}</pre>
             </details>`
          : "";
        const replyHtml = d.draft_reply
          ? `<div class="alert ${d.strength === "STRONG" ? "alert-ok" : "alert-warn"}" style="margin-top:0.5rem;">
               <strong>Draft Reply:</strong><br />${escapeHtml(d.draft_reply)}
             </div>`
          : "";
        return `
          <div class="card">
            <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:0.5rem;">
              <div>
                <strong>Dispute <code>${escapeHtml(d.id)}</code></strong> on Order <code>${escapeHtml(d.order_id)}</code> ${sourceBadge}
                <p class="muted" style="margin:0.25rem 0;">Claim: "${escapeHtml(d.claim)}"</p>
              </div>
              <div style="display:flex; align-items:center; gap:0.45rem;">
                <span class="badge ${strengthClass}">Strength: ${escapeHtml(d.strength)}</span>
                <button class="btn-primary" type="button" data-defend-id="${escapeHtml(d.id)}">Build defense</button>
              </div>
            </div>
            ${replyHtml}
            ${packetHtml}
          </div>
        `;
      })
      .join("");

    disputesEl.querySelectorAll("[data-defend-id]").forEach((btn) => {
      btn.addEventListener("click", () => handleBuildDefense(btn.dataset.defendId));
    });
  } catch (err) {
    disputesEl.innerHTML = `<div class="alert alert-danger">Could not load disputes: ${escapeHtml(err.message)}</div>`;
  }
}

async function handleSimulateDispute(unknownOrder = false) {
  hideAlert();
  try {
    const latestOrder = state.orders[0];
    const orderId = unknownOrder || !latestOrder ? "ord_unknown_fake" : latestOrder.id;
    const res = await fetch("/api/disputes/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        order_id: orderId,
        claim: "I did not authorize this AI purchase.",
      }),
    });
    if (res.status === 404) {
      showAlert("Dispute simulation becomes active in Milestone M8.", "warn");
      return;
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not simulate dispute.");
    await Promise.all([loadDisputes(), pollActivity()]);
  } catch (err) {
    showAlert(`Dispute simulation failed: ${err.message}`);
  }
}

async function handleBuildDefense(disputeId) {
  hideAlert();
  try {
    await apiJson(`/api/disputes/${disputeId}/defend`, { method: "POST" });
    await Promise.all([loadDisputes(), pollActivity()]);
  } catch (err) {
    showAlert(`Could not build defense: ${err.message}`);
  }
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
          ? `<button class="btn-danger" type="button" data-tamper-seq="${ev.seq}">Tamper demo</button>`
          : "";
        return `
          <tr>
            <td><strong>#${ev.seq}</strong></td>
            <td><code>${escapeHtml(ev.type)}</code><br /><span class="mono">${escapeHtml(ev.ref_id)}</span></td>
            <td>
              <div class="mono">prev: ${escapeHtml(ev.prev_hash.slice(0, 14))}...</div>
              <div class="mono">hash: ${escapeHtml(ev.hash.slice(0, 14))}...</div>
              <div class="muted">${escapeHtml(ev.ts)}</div>
            </td>
            <td>
              <details>
                <summary style="cursor:pointer;">Body JSON</summary>
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
      btn.addEventListener("click", () => handleTamperDemo(Number(btn.dataset.tamperSeq)));
    });
  } catch (err) {
    showAlert(`Could not load ledger: ${err.message}`);
  }
}

function renderLedgerVerification(v) {
  const banner = document.getElementById("ledger-verify-banner");
  if (v.ok) {
    banner.className = "alert alert-ok";
    banner.innerHTML = `<strong>Chain Intact (Tamper-Evident Check Passed)</strong> — Verified ${v.checked} entries. Head hash: <code class="mono">${escapeHtml(v.head_hash)}</code>`;
  } else {
    banner.className = "alert alert-danger";
    banner.innerHTML = `<strong>TAMPER DETECTED!</strong> Hash verification failed at entry <strong>#${v.first_bad_seq}</strong> (checked ${v.checked} entries).`;
  }
}

async function handleVerifyLedger() {
  hideAlert();
  try {
    const v = await apiJson("/api/ledger/verify");
    renderLedgerVerification(v);
  } catch (err) {
    showAlert(`Verification error: ${err.message}`);
  }
}

async function handleTamperDemo(seq) {
  hideAlert();
  try {
    const res = await apiJson(`/api/dev/tamper/${seq}`, { method: "POST" });
    renderLedgerVerification(res.verification);
    await loadLedger();
  } catch (err) {
    showAlert(`Tamper demo failed: ${err.message}`);
  }
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
      const li = document.createElement("li");
      li.className = "activity-item";
      li.innerHTML = `
        <div><strong>#${ev.seq} ${escapeHtml(ev.type)}</strong></div>
        <div>${escapeHtml(ev.summary)}</div>
        <div class="muted mono">${escapeHtml(ev.ts)}</div>
      `;
      feed.prepend(li);
    }
  } catch {
    // Keep polling quietly without spamming console errors
  }
}

function setupEvents() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  document.getElementById("btn-kill-switch").addEventListener("click", toggleKillSwitch);
  document.getElementById("btn-parse-mandate").addEventListener("click", handleParseMandate);
  document.getElementById("draft-form").addEventListener("submit", handleConfirmMandate);

  document.getElementById("btn-run-agent").addEventListener("click", () => {
    const txt = document.getElementById("agent-request-input").value.trim();
    if (!txt) {
      showAlert("Enter a shopping prompt for the agent first.", "warn");
      return;
    }
    runAgentScenario(txt, "none");
  });

  document.getElementById("btn-scen-normal").addEventListener("click", () =>
    runAgentScenario("get me a black denim jacket", "none")
  );
  document.getElementById("btn-scen-overcap").addEventListener("click", () =>
    runAgentScenario("get me a black leather jacket", "none")
  );
  document.getElementById("btn-scen-fake").addEventListener("click", () =>
    runAgentScenario("buy black winter jacket from cheap-jackets-now.shop", "none")
  );
  document.getElementById("btn-scen-injection").addEventListener("click", () =>
    runAgentScenario("get me a black jacket", "injection")
  );

  document.getElementById("btn-verify-ledger").addEventListener("click", handleVerifyLedger);
  document.getElementById("btn-sim-dispute").addEventListener("click", () => handleSimulateDispute(false));
  document.getElementById("btn-sim-fake-dispute").addEventListener("click", () => handleSimulateDispute(true));
}

async function init() {
  setupEvents();
  try {
    await Promise.all([loadConfig(), loadKillSwitch(), loadActiveMandate(), refreshHoldsBadge()]);
    await pollActivity();
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

  setInterval(() => {
    pollActivity();
    refreshHoldsBadge();
  }, 2000);
}

window.addEventListener("DOMContentLoaded", init);
