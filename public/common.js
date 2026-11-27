export async function api(path, options = {}) {
  const res = await fetch(path, {
    method: options.method ?? (options.body ? 'POST' : 'GET'),
    headers: options.body ? { 'content-type': 'application/json' } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
}

export function qs(name) {
  return new URLSearchParams(location.search).get(name);
}

export function el(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html.trim();
  return tpl.content.firstElementChild;
}

export function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' });
  downloadBlob(blob, filename);
}

export function downloadText(text, filename, type = 'text/markdown;charset=utf-8') {
  downloadBlob(new Blob([text], { type }), filename);
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function fmtTime(s) {
  if (!s) return '';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function short(h, n = 10) {
  if (!h) return '';
  return h.length > n + 8 ? `${h.slice(0, n)}…${h.slice(-6)}` : h;
}

let toastTimer;
export function toast(message) {
  let node = document.querySelector('.toast');
  if (!node) {
    node = document.createElement('div');
    node.className = 'toast';
    document.body.appendChild(node);
  }
  node.textContent = message;
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 3600);
}

export function transcriptMarkdown(state, party) {
  const p = state.parties[party];
  const lines = [];
  lines.push(`# ${p.name} 终端聊天记录导出`);
  lines.push('');
  lines.push(`- 导出终端：${party} / ${p.name}（${p.role}）`);
  lines.push(`- 会话：${state.session.id}`);
  lines.push(`- 导出时间：${new Date().toISOString()}`);
  lines.push(`- 终端规则：${state.session.formalRuleVersion}`);
  lines.push('');
  lines.push('> 普通消息仅附双边共享私记；“我收到了”不自动同意改规格。唯一对外归属以具名署名/共同会签为准。');
  lines.push('');
  for (const m of state.messages) {
    lines.push(`## ${m.id} · ${m.from} · ${fmtTime(m.at)}`);
    lines.push('');
    lines.push(m.text);
    lines.push('');
    lines.push(`- 类型：\`${m.kind}\`；私记：\`${short(m.pairMark, 16)}\``);
    if (m.legalEffect) lines.push(`- 效力标记：${m.legalEffect}`);
    if (m.terminalNote) lines.push(`- 终端提示：${m.terminalNote}`);
    if (m.correctionId) lines.push(`- 关联订正：${m.correctionId}`);
    lines.push('');
  }
  lines.push('# 当前订单/约定');
  lines.push('');
  for (const o of state.orders) lines.push(`- ${o.id} ${o.title}：当前 ${o.currentAgreementId}@v${o.currentVersion}`);
  lines.push('');
  for (const a of state.agreements) {
    lines.push(`## ${a.agreementId}@v${a.version} (${a.status})`);
    lines.push('');
    lines.push(`- order: ${a.orderId}`);
    lines.push(`- hash: \`${a.contentHash}\``);
    lines.push(`- source: ${a.sourceCorrectionId ?? 'seed'}`);
    lines.push('');
    lines.push(a.clauseText);
    lines.push('');
  }
  return lines.join('\n');
}
