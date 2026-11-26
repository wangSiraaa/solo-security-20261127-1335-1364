const $ = (sel) => document.querySelector(sel);

const STATION_LABEL = {
  "old-station": "旧站",
  "new-station": "新站/备站",
};

let lastState = null;
let lastMatrix = null;
let lastPlan = null;

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  const data = await resp.json();
  if (!resp.ok || data.ok === false) {
    const detail = data.message || resp.statusText;
    showMessage(`${data.error || "error"}: ${detail}`, true);
    throw new Error(detail);
  }
  showMessage("", false);
  return data;
}

let messageTimer = null;
function showMessage(text, isError) {
  const box = $("#message");
  box.hidden = !text;
  box.textContent = text;
  box.className = `message${isError ? " error" : ""}`;
  clearTimeout(messageTimer);
  if (text && !isError) messageTimer = setTimeout(() => (box.hidden = true), 2500);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

async function refresh() {
  const [state, matrix, plan] = await Promise.all([
    api("GET", "/api/state"),
    api("GET", "/api/matrix"),
    api("GET", "/api/plan"),
  ]);
  lastState = state;
  lastMatrix = matrix;
  lastPlan = plan;
  renderAuthority();
  renderPlan();
  renderOrders();
  renderCabinetActions();
  renderTodos();
}

function renderAuthority() {
  const tick = lastState.tick;
  const cards = Object.values(lastState.authority)
    .map((a) => `
      <div class="auth-card ${a.station === "new-station" ? "new" : "old"}">
        <h3>
          <span>${a.cabinet_label} <code>${a.cabinet}</code></span>
          <span class="badge ${a.station === "new-station" ? "new" : "old"}">G${a.generation}</span>
        </h3>
        <div><strong>${a.station_label}</strong> 可签</div>
        <div class="muted">${esc(a.basis_label)}</div>
        <div class="muted">当前第 ${tick} 拍</div>
      </div>`).join("");
  $("#authority").innerHTML = `<div class="auth-grid">${cards}</div>
    <p class="muted">镜像声明：${esc(lastState.restored_image?.text_claim || "无")}；该字段不参与授权推导。</p>`;
}

function renderPlan() {
  const p = lastPlan;
  const selected = p.plans[p.selected_boundary === "early-per-cabinet" ? "early_per_cabinet" : "old_dual_then_takeback"];
  $("#plan").innerHTML = `
    <div class="plan-selected">当前选择：${esc(selected?.title || p.selected_boundary)}</div>
    <div>${esc(p.selection_reason)}</div>
    <ul>${(selected?.notes || []).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
    <div class="muted" style="margin-top:8px">
      ${p.boundaries.map((b) => `${b.cabinet}: ${esc(b.action)} / 最早新代第${b.earliest_new_authority_tick}拍`).join("；")}
    </div>`;
}

function stationPill(item, station) {
  const v = item.stations[station];
  let text;
  let cls = "no";
  if (v.can_complete_now) {
    text = "可领且可办结"; cls = "yes";
  } else if (v.can_collect_half_now) {
    text = "当前可取可及柜的半张"; cls = "yes";
  } else if (v.can_resume_half) {
    text = "持有半张，等待补其余柜"; cls = "yes";
  } else if (v.can_claim) {
    text = "可领（线路待恢复）"; cls = "yes";
  } else if (v.common_authority) {
    text = "资格共有但条件不足";
  } else {
    text = "无当前完整资格";
  }
  return `<div class="station-pill ${cls}"><strong>${STATION_LABEL[station]}</strong>：${text}</div>`;
}

function renderOrders() {
  $("#orders").innerHTML = lastMatrix.orders.map((o) => `
    <div class="order">
      <div class="order-head">
        <div>
          <div class="order-title">${esc(o.order_id)} · ${esc(o.title)}</div>
          <div class="order-meta">
            柜：${o.required_labels.join("、")}｜交付：${esc(o.delivery_site)}｜
            材料齐：第${o.material_ready_tick}拍｜期限：第${o.deadline_tick}拍｜
            共同签发站：<strong>${esc(o.common_station_label)}</strong>
          </div>
        </div>
        <span class="badge ${esc(o.status)}">${esc(o.status)}${o.overdue ? " / overdue" : ""}</span>
      </div>
      <div class="station-grid">
        ${stationPill(o, "new-station")}
        ${stationPill(o, "old-station")}
      </div>
      <div class="button-row">
        <button class="mini" data-order-action="claim" data-id="${o.order_id}">模拟领单</button>
        <button class="mini primary" data-order-action="issue" data-id="${o.order_id}" data-station="new-station">新站签发 / 补条</button>
        <button class="mini" data-order-action="issue" data-id="${o.order_id}" data-station="old-station">旧站签发 / 补条</button>
      </div>
    </div>`).join("") || '<p class="muted">暂无单据。</p>';
}

function renderCabinetActions() {
  $("#cabinet-actions").innerHTML = lastState.cabinets.map((c) => `
    <div class="cabinet-line">
      <strong>${c.label} ${c.code}</strong>
      <span class="muted">switch=${c.switch_state}, internal=${c.internal_online}, newRoute=${c.new_route_online}</span>
      <div class="button-row" style="margin-top:8px">
        <button class="mini" data-switch="${c.code}:deliver">仅送达换代令</button>
        <button class="mini" data-switch="${c.code}:ack">确认持久停用</button>
        <button class="mini primary" data-switch="${c.code}:succeed">一步换代成功</button>
        <button class="mini" data-route="${c.code}:new:true">新址线路上线</button>
        <button class="mini danger" data-route="${c.code}:new:false">新址线路断开</button>
      </div>
    </div>`).join("");
}

function renderTodos() {
  const todos = lastState.todos.filter((t) => t.status === "open");
  $("#todos").innerHTML = todos.length
    ? todos.map((t) => `<div class="todo">#${t.id} ${esc(t.order_id || "")} 缺 ${esc(t.cabinet || "")}：${esc(t.detail)}</div>`).join("")
    : '<p class="muted">没有未结待办。联签缺柜时会在这里留下半张回条待办。</p>';
}

function renderReplay(result) {
  const lines = [];
  lines.push(`剧本：${result.case || "empty"} / checks=${result.checks?.filter((c) => c.ok).length || 0}/${result.checks?.length || 0}`);
  for (const f of result.frames) {
    lines.push(`\n-- 第${f.tick}拍 | ${f.label} --`);
    for (const n of f.notes || []) lines.push(`  · ${n}`);
    for (const [code, a] of Object.entries(f.authority)) {
      lines.push(`  ${code}: ${a.station_label} G${a.generation} [${a.basis_label}]`);
    }
    for (const o of f.orders || []) {
      const flags = [];
      if (o.new_can_complete) flags.push("新站可办结");
      else if (o.new_can_half) flags.push("新站可取半张");
      else if (o.new_can_resume) flags.push("新站半张待补");
      else if (o.new_can_claim) flags.push("新站可领");
      if (o.old_can_complete) flags.push("旧站可办结");
      else if (o.old_can_half) flags.push("旧站可取半张");
      else if (o.old_can_resume) flags.push("旧站半张待补");
      else if (o.old_can_claim) flags.push("旧站可领");
      lines.push(`  ${o.order_id} [${o.required.join(",")}] ${o.status}, 共同=${o.common_station_label || "分裂"} | ${flags.join("、") || "无可办站"}`);
    }
    for (const a of f.attempts || []) {
      lines.push(`  动作${a.ok ? "OK" : "ERR"}: ${a.label}${a.ok ? "" : ` -> ${a.code}: ${a.message}`}`);
    }
    for (const t of f.open_todos || []) lines.push(`  TODO #${t.id} ${t.order_id} 缺${t.cabinet}: ${t.detail}`);
  }
  $("#replay-output").textContent = lines.join("\n");
}

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  try {
    if (btn.dataset.action === "reset") {
      await api("POST", "/api/reset", { scenario: $("#scenario").value });
      await refresh();
    } else if (btn.dataset.action === "advance") {
      await api("POST", "/api/clock/advance", { beats: 1 });
      await refresh();
    } else if (btn.dataset.action === "restart") {
      await api("POST", "/api/admin/restart", {});
      await refresh();
    } else if (btn.dataset.orderAction) {
      const { orderAction, id, station } = btn.dataset;
      const body = station ? { station } : {};
      const result = await api("POST", `/api/orders/${encodeURIComponent(id)}/${orderAction}`, body);
      if (orderAction === "issue" && result.voucher?.status === "partial") {
        showMessage("已留下半张回条和待办；线路/代次恢复后再次点击同一站的签发按钮。", false);
      }
      await refresh();
    } else if (btn.dataset.switch) {
      const [code, action] = btn.dataset.switch.split(":");
      await api("POST", `/api/switch/${code}/${action}`, {});
      await refresh();
    } else if (btn.dataset.route) {
      const [code, route, online] = btn.dataset.route.split(":");
      await api("POST", `/api/cabinets/${code}/route`, { route, online: online === "true" });
      await refresh();
    } else if (btn.dataset.planApply !== undefined) {
      await api("POST", "/api/plan/apply", {});
      await refresh();
    } else if (btn.dataset.replay) {
      const kind = btn.dataset.replay;
      $("#replay-output").textContent = "回放运行中……";
      const result = await api("GET", `/api/replay?case=${encodeURIComponent(kind)}`);
      renderReplay(result);
    }
  } catch (err) {
    await refresh().catch(() => {});
  }
});

refresh().catch((err) => showMessage(err.message, true));
