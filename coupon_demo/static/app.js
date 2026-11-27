const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

let lastState = null;

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, body = {}) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok || data.ok === false) {
    const err = new Error(data.message || `HTTP ${resp.status}`);
    err.details = data.details || {};
    err.code = data.error;
    throw err;
  }
  return data.result;
}

function showApiResult(value) {
  $("#apiResult").textContent = JSON.stringify(value, null, 2);
}

function showError(err) {
  $("#apiResult").textContent =
    `错误 [${err.code || "error"}]: ${err.message}\n` +
    (err.details && Object.keys(err.details).length
      ? JSON.stringify(err.details, null, 2)
      : "");
}

function collectForm(form) {
  const fd = new FormData(form);
  const body = {};
  for (const [key, raw] of fd.entries()) {
    if (key.startsWith("__")) continue;
    const input = form.elements[key];
    if (input && input.type === "checkbox") {
      body[key] = input.checked;
    } else if (key === "quantity" || key === "start_seq" || key === "count") {
      body[key] = raw === "" ? undefined : Number(raw);
    } else if (key === "holder_names") {
      const names = String(raw).split(",").map((x) => x.trim()).filter(Boolean);
      if (names.length) body[key] = names;
    } else if (raw !== "") {
      body[key] = raw;
    }
  }
  return body;
}

function collectManifests(value, path = "$", out = []) {
  if (!value || typeof value !== "object") return out;
  if (Array.isArray(value)) {
    value.forEach((v, i) => collectManifests(v, `${path}[${i}]`, out));
    return out;
  }
  for (const [k, v] of Object.entries(value)) {
    if (k === "print_manifest_once" || k.endsWith("_manifest_once")) {
      out.push({ label: k.replaceAll("_", " "), path: `${path}.${k}`, codes: v });
    } else if (v && typeof v === "object") {
      collectManifests(v, `${path}.${k}`, out);
    }
  }
  return out;
}

function showManifest(result) {
  const manifests = collectManifests(result);
  if (!manifests.length) return;
  $("#manifestPanel").classList.remove("hidden");
  $("#manifest").innerHTML = manifests.map((m) => `
    <h3 class="small">${esc(m.label)}</h3>
    <table><tbody>
      ${(m.codes || []).map((c) => `
        <tr><td class="manifest-code">${esc(c.ticket_no)}</td>
        <td class="manifest-code">${esc(c.secret)}</td></tr>`).join("")}
    </tbody></table>`).join("");
}

async function refresh() {
  const resp = await fetch("/api/state");
  const data = await resp.json();
  lastState = data.state;
  render(lastState);
}

function badge(text, cls = "muted") {
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function statusBadge(status) {
  const map = {
    redeemed: "ok", claimed: "ok", counter_received: "ok",
    at_counter: "ok", independent_verified: "ok", original_batch_secured: "ok",
    quarantined: "bad", reprint_confirmed: "bad", broken: "bad",
    review_hold: "hold", old_state_unverified: "hold",
    printing_interrupted: "hold", handoff_incomplete: "hold",
    receipt_retargeted_under_verification: "hold",
  };
  return badge(status, map[status] || "muted");
}

function renderDecision(d) {
  $("#decision").innerHTML = `
    <h2>服务决定</h2>
    <p><b>优先事项：</b>${esc(d.priority)}　<b>第一站：</b>${esc(d.first_stop)}</p>
    <p>${esc(d.recommendation)}</p>
    <p><b>当前可交割叠：</b>${
      d.sendable_stack_ids.length
        ? d.sendable_stack_ids.map((x) => badge(x, "ok")).join(" ")
        : badge("无；先补齐独立来源/封条/柜台回执并排除重印", "hold")
    }</p>
    <p class="small">未结重印段 ${d.open_reprint_segments} · 活动独立来源 ${d.active_independent_sources} ·
       活动旧备份来源 ${d.active_restored_sources} · 可抢救实名叠 ${d.salvageable_reserved_stacks}</p>`;
}

function renderStacks(state) {
  $("#stacks").innerHTML = state.stacks.length ? state.stacks.map((s) => `
    <div class="stack">
      <div class="stack-head">
        <div class="stack-title">${esc(s.stack_id)} <span class="small">${esc(s.title)}</span></div>
        <div>${statusBadge(s.provenance_status)} ${statusBadge(s.custody_status)} ${statusBadge("封条:" + s.seal_status, s.seal_status === "verified_intact" ? "ok" : "muted")}</div>
      </div>
      <div class="kv">机器 ${esc(s.machine_id)} · 来源 ${esc(s.source_id)} · 段 ${esc(s.segment_id)} ·
          材料 ${esc(s.material_lot_id)} · 印数 ${s.print_count}/${s.planned_count} ·
          当前回执 ${s.active_receipt ? esc(s.active_receipt.receipt_id + " → " + s.active_receipt.counter_id) : "无"}</div>
      <div class="ticket-grid">
        ${s.coupons.map((c) => `
          <div class="ticket">
            <div class="no">${esc(c.ticket_no)}</div>
            <div class="holder">${esc(c.holder_name || "普通券")} · ${statusBadge(c.status)}</div>
            <div class="small">${esc(c.correction_state)}${c.replacement_ticket_no ? " → " + esc(c.replacement_ticket_no) : ""}</div>
            ${c.status === "at_counter" ? `<button data-claim="${esc(c.ticket_no)}" data-counter="${esc(s.active_receipt?.counter_id || "")}">领取该券</button>` : ""}
          </div>`).join("")}
      </div>
    </div>`).join("") : `<p class="small">尚无封包。可点右上角“一键完整演练”。</p>`;

  $$("[data-claim]").forEach((btn) => btn.addEventListener("click", async () => {
    const ticket = btn.dataset.claim;
    const holder = prompt("领取人/预订姓名（可空）", "");
    if (holder === null) return;
    try {
      const result = await api(`/api/coupons/${encodeURIComponent(ticket)}/claim`, {
        holder_name: holder, counter_id: btn.dataset.counter || undefined,
      });
      showApiResult(result);
      await refresh();
    } catch (e) { showError(e); }
  }));
}

function renderReceipts(state) {
  $("#receipts").innerHTML = state.receipts.length ? `<table><thead><tr>
    <th>回执</th><th>柜台</th><th>当前叠</th><th>状态/原叠</th><th>签收</th>
  </tr></thead><tbody>${state.receipts.map((r) => `<tr>
    <td>${esc(r.receipt_id)}</td><td>${esc(r.counter_id)}</td>
    <td><b>${esc(r.stack_id)}</b></td>
    <td>${statusBadge(r.status)}<br><span class="small">${esc(r.original_stack_id || "")}</span></td>
    <td>${esc(r.signed_by)}<br><span class="small">${esc(r.received_at)}</span></td>
  </tr>`).join("")}</tbody></table>` : "<p class='small'>无回执</p>";

  $("#handoffs").innerHTML = state.handoffs.length ? state.handoffs.map((h) => `
    <div class="correction">
      <b>${esc(h.handoff_id)}</b> · ${esc(h.stack_id)} · ${h.complete ? badge("齐全", "ok") : badge("未全", "hold")}
      <div class="small">${esc(h.from_party)} → ${esc(h.to_party)}；材料：${esc((h.documents || []).join("、"))}</div>
      ${(h.missing || []).length ? `<div class="small">缺：${esc(h.missing.join("、"))}</div>` : ""}
    </div>`).join("") : "";
}

function renderRedemptions(state) {
  $("#redemptions").innerHTML = state.redemptions.length ? `<table><thead><tr>
    <th>兑换号</th><th>票号</th><th>柜台</th><th>福利</th><th>时间</th>
  </tr></thead><tbody>${state.redemptions.map((r) => `<tr>
    <td class="small">${esc(r.redemption_id)}</td><td>${esc(r.ticket_no)}</td>
    <td>${esc(r.counter_id)}</td><td>${esc(r.benefit)}</td><td class="small">${esc(r.created_at)}</td>
  </tr>`).join("")}</tbody></table>
  <p class="small">兑换表对暗码哈希建唯一约束；重启 SQLite 后已兑现福利仍不能再发。</p>` : "<p class='small'>尚无兑换</p>";
}

function renderCorrections(state) {
  $("#replacements").innerHTML = state.replacement_orders.length ? `<table><thead><tr>
    <th>换发单</th><th>原券</th><th>状态</th><th>新券</th><th>原因</th>
  </tr></thead><tbody>${state.replacement_orders.map((r) => `<tr>
    <td class="small">${esc(r.replacement_id)}</td>
    <td>${esc(r.original_ticket_no)}</td>
    <td>${statusBadge(r.state, r.state === "replaced" ? "ok" : "hold")}</td>
    <td>${esc(r.new_ticket_no || "待独立来源")}</td>
    <td class="small">${esc(r.reason)}</td>
  </tr>`).join("")}</tbody></table>` : "<p class='small'>无换发单；已兑现福利不会生成换发单。</p>";

  $("#corrections").innerHTML = state.corrections.length ? state.corrections.slice(0, 30).map((c) => `
    <div class="correction ${c.kind.includes("reprint") ? "danger" : ""}">
      <b>${esc(c.kind)}</b> · ${esc(c.aggregate_type)} ${esc(c.aggregate_id)}
      <div>${esc(c.summary)}</div>
      <details><summary class="small">更正前后</summary><pre class="json">${esc(JSON.stringify({ before: c.before, after: c.after }, null, 2))}</pre></details>
    </div>`).join("") : "<p class='small'>尚无更正记录</p>";
}

function renderSources(state) {
  $("#sources").innerHTML = `<table><thead><tr><th>来源</th><th>机器</th><th>类型</th><th>段/游标</th><th>状态</th></tr></thead><tbody>
    ${state.sources.map((s) => `<tr><td class="small">${esc(s.source_id)}</td><td>${esc(s.machine_id)}</td>
      <td>${esc(s.kind)}</td><td class="small">${esc(s.segment_id)}<br>epoch ${s.epoch}, next ${s.next_seq}</td>
      <td>${statusBadge(s.status)}</td></tr>`).join("")}
  </tbody></table>`;
  $("#segments").innerHTML = state.segments.map((g) => `
    <div class="correction ${g.reprint_confirmed ? "danger" : ""}">
      <b>${esc(g.segment_id)}</b> ${statusBadge(g.witness_scope, g.reprint_confirmed ? "bad" : "muted")}
      ${g.reprint_confirmed ? badge("已确认重印", "bad") : ""}
      ${g.print_frozen ? badge("旧段印制冻结", "hold") : ""}
      <div class="small">机器：${esc((g.observed_machines || []).join("、"))} · ${esc(g.witness_note)}</div>
    </div>`).join("");
  $("#witnesses").innerHTML = `<details><summary>签名使用见证 (${state.witnesses.length})</summary>` +
    state.witnesses.map((w) => `<div class="small correction">${esc(w.witness_id)} · ${esc(w.machine_id)} · seq ${w.from_seq}-${w.to_seq} · sig ${w.valid_signature ? "valid" : "invalid"}<br><code>${esc(w.chain_hash.slice(0, 24))}…</code></div>`).join("") +
    `</details>`;
}

function renderMaterials(state) {
  $("#materials").innerHTML = `<table><tbody>${state.materials.map((m) => `<tr>
    <td>${esc(m.lot_id)}</td><td>${esc(m.kind)}</td>
    <td>${m.consumed}/${m.quantity}</td><td class="small">${esc(m.note)}</td></tr>`).join("")}</tbody></table>`;
  $("#audit").innerHTML = `<details><summary>最近审计</summary>` +
    state.audit_events.map((a) => `<div class="small">${esc(a.created_at)} ${esc(a.event_type)} ${esc(a.aggregate_id)}</div>`).join("") +
    `</details>`;
}

function render(state) {
  renderDecision(state.decision);
  renderStacks(state);
  renderReceipts(state);
  renderRedemptions(state);
  renderCorrections(state);
  renderSources(state);
  renderMaterials(state);
}

async function submitForm(form, pathOverride) {
  const path = pathOverride || form.dataset.post;
  const body = collectForm(form);
  const result = await api(path, body);
  showApiResult(result);
  showManifest(result);
  await refresh();
  return result;
}

$$("form[data-post]").forEach((form) => {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await submitForm(form); }
    catch (err) { showError(err); }
  });
});

$$("form[data-post-dynamic]").forEach((form) => {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const path = form.elements.__action_path.value;
    try { await submitForm(form, path); }
    catch (err) { showError(err); }
  });
});

$("#retargetForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = collectForm(e.currentTarget);
  const id = body.receipt_id;
  delete body.receipt_id;
  try {
    const result = await api(`/api/receipts/${encodeURIComponent(id)}/retarget`, body);
    showApiResult(result); await refresh();
  } catch (err) { showError(err); }
});

$("#claimForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = collectForm(e.currentTarget);
  const ticket = body.ticket_no;
  const legacy = body.legacy === true;
  delete body.ticket_no;
  delete body.legacy;
  try {
    const suffix = legacy ? "/legacy-claim" : "/claim";
    if (legacy) delete body.counter_id;
    const result = await api(`/api/coupons/${encodeURIComponent(ticket)}${suffix}`, body);
    showApiResult(result); await refresh();
  } catch (err) { showError(err); }
});

$("#redeemForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  const body = collectForm(form);
  const ticket = body.ticket_no;
  try {
    const result = await api("/api/redeem", body);
    $("#redeemResult").innerHTML = `<div class="ok">✓ ${esc(result.message)}<br>${esc(result.redemption_id)} · ${esc(result.benefit)}</div>`;
    form.elements.secret.value = "";
    showApiResult(result);
    await refresh();
  } catch (err) {
    $("#redeemResult").innerHTML = `<div class="err">× ${esc(err.message)}</div>`;
    showError(err);
    form.elements.secret.value = "";
  }
});

$("#witnessForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = collectForm(e.currentTarget);
  const segment = body.segment_id;
  delete body.segment_id;
  try {
    const result = await api(`/api/segments/${encodeURIComponent(segment)}/witness`, body);
    showApiResult(result); await refresh();
  } catch (err) { showError(err); }
});

$("#confirmWitnesses").addEventListener("click", async () => {
  const segment = $("#confirmSegment").value;
  try {
    const result = await api(`/api/segments/${encodeURIComponent(segment)}/confirm-reprint`, {});
    showApiResult(result); await refresh();
  } catch (err) { showError(err); }
});

$("#reseedForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = collectForm(e.currentTarget);
  const sourceId = body.source_id;
  delete body.source_id;
  try {
    const result = await api(`/api/sources/${encodeURIComponent(sourceId)}/reseed`, body);
    showApiResult(result); await refresh();
  } catch (err) { showError(err); }
});

$("#seedDemo").addEventListener("click", async () => {
  try {
    const result = await api("/api/demo/seed", { reset: true });
    showApiResult(result);
    showManifest(result);
    await refresh();
  } catch (err) { showError(err); }
});

$("#refresh").addEventListener("click", refresh);
$("#hideManifest").addEventListener("click", () => $("#manifestPanel").classList.add("hidden"));

async function quickAction(action) {
  try {
    let result;
    if (action === "sharedBackup") {
      result = await api("/api/backups", { backup_id: "BACKUP-1367", seed: "fixed-demo-backup-secret-state", segment_id: "SEG-OLD-SHARED" });
    } else if (action === "restoreA") {
      result = await api("/api/backups/restore", { backup_id: "BACKUP-1367", machine_id: "printer-a", source_id: "SRC-A-OLD" });
    } else if (action === "restoreB") {
      result = await api("/api/backups/restore", { backup_id: "BACKUP-1367", machine_id: "printer-b", source_id: "SRC-B-OLD" });
    }
    showApiResult(result);
    await refresh();
  } catch (err) { showError(err); }
}
$$("[data-action]").forEach((btn) => btn.addEventListener("click", () => quickAction(btn.dataset.action)));

refresh().catch(showError);
