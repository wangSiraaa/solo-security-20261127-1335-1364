import { api, qs, downloadJson, downloadText, fmtTime, short, toast, transcriptMarkdown } from '/common.js';

const party = qs('party') === 'B' ? 'B' : 'A';
let sid = qs('sid') || 'present';
let state = null;
let currentPkg = null;
let lastAward = null;
const selectedContext = new Set(['MSG-002', 'MSG-003', 'MSG-004', 'MSG-005', 'MSG-006']);

const $ = (id) => document.getElementById(id);
const simNowInput = $('simNow');

function simAt() {
  const v = simNowInput.value || '2026-11-26T20:08';
  return v.length === 16 ? `${v}:00+08:00` : v;
}

function isoToLocalInput(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '2026-11-26T21:00';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function counterpartUrl() {
  const other = party === 'A' ? 'B' : 'A';
  return `/terminal.html?party=${other}&sid=${encodeURIComponent(sid)}`;
}

async function loadState() {
  state = await api(`/api/state?sid=${encodeURIComponent(sid)}`);
  render();
}

function render() {
  if (!state) return;
  renderTop();
  renderStance();
  renderOrders();
  renderReceipts();
  renderChat();
  renderCitationOptions();
  renderContextPicker();
  renderCorrections();
}

function renderTop() {
  const p = state.parties[party];
  $('partyPill').className = `pill ${party.toLowerCase()}`;
  $('partyPill').textContent = `${party} 终端 · ${p.name}`;
  const presence = state.presence[party]?.status ?? 'online';
  const pp = $('presencePill');
  pp.className = `pill ${presence === 'online' ? 'ok' : 'bad'}`;
  pp.innerHTML = `<span class="dot ${presence}"></span>${presence === 'online' ? '本方在场/在线' : '本方失联/离线'}`;
  $('sidSelect').value = sid;
  $('docCount').textContent = state.corrections.length;
}

function renderStance() {
  const other = party === 'A' ? 'B' : 'A';
  const online = state.presence[party]?.status === 'online';
  const aStance = `
    <h2>甲的隐私边界</h2>
    <div class="note info">普通畅聊只盖<b>双边私记</b>：只有持有本会话模拟共享钥的甲、乙能核对完整性。就算第三方拿到片段和共享钥，也无法证明某个 HMAC 是甲而不是乙制作。</div>
    <div class="note warn">一旦由甲终端生成并交付<b>具名公鉴修订单</b>，它就可被转发、独立验真；交付后不能再向甲承诺“不可转证”。</div>
    <p class="row">
      <a class="button b" href="${counterpartUrl()}" target="_blank" rel="noreferrer">打开乙终端</a>
      <button id="presenceToggle" class="${online ? 'danger' : 'primary'}">${online ? '模拟甲失联' : '恢复甲到场'}</button>
    </p>`;
  const bStance = `
    <h2>乙的订单保护</h2>
    <div class="note info">乙能在本终端核对双边私记和收到状态，但甲缺席时，仲裁不能凭共享私记把普通私聊唯一归给甲。</div>
    <div class="note ok">要保护已接订单：要么等甲到场完成<b>双边会签</b>，要么使用甲发言端事前交付的、第三方可验签的<b>具名修订单</b>，并注意限期接受时间。</div>
    <p class="row">
      <a class="button a" href="${counterpartUrl()}" target="_blank" rel="noreferrer">打开甲终端</a>
      <button id="presenceToggle" class="${online ? 'danger' : 'primary'}">${online ? '模拟乙离线' : '恢复乙到场'}</button>
    </p>`;
  $('stancePanel').innerHTML = party === 'A' ? aStance : bStance;
  $('presenceToggle')?.addEventListener('click', togglePresence);
}

async function togglePresence() {
  const current = state.presence[party]?.status;
  const status = current === 'online' ? 'offline' : 'online';
  await api(`/api/presence?sid=${sid}`, { body: { sid, party, status, at: simAt() } });
  toast(`${party} 已${status === 'online' ? '到场' : '失联'}`);
  loadState();
}

function agreementByIdVersion(ref) {
  const [id, v] = ref.split('@v');
  return state.agreements.find((a) => a.agreementId === id && String(a.version) === v);
}

function renderOrders() {
  $('orders').innerHTML = state.orders
    .map((o) => {
      const ags = state.agreements
        .filter((a) => a.orderId === o.id)
        .sort((x, y) => x.version - y.version)
        .map(
          (a) => `
          <div class="card ${a.status === 'effective' ? 'effective' : 'superseded'}">
            <div class="row" style="justify-content:space-between">
              <b>${a.agreementId}@v${a.version}</b>
              <span class="badge ${a.status === 'effective' ? 'ok' : 'warn'}">${a.status === 'effective' ? '有效' : '已被引用换版'}</span>
            </div>
            <div class="muted small">${a.title}</div>
            <p style="margin:8px 0">${a.clauseText}</p>
            <div class="hash">hash: ${a.contentHash}</div>
            ${a.sourceCorrectionId ? `<div class="small muted">来源订正：${a.sourceCorrectionId}；父哈希：${short(a.parentHash, 12)}</div>` : ''}
          </div>`,
        )
        .join('');
      return `<div class="card"><h3 style="margin-top:0">${o.id} · ${o.title}</h3>
        <div class="kv"><dt>当前版本</dt><dd>${o.currentAgreementId}@v${o.currentVersion}</dd>
        <dt>状态</dt><dd>${o.status}</dd><dt>回单</dt><dd>${o.receiptIds.join('、') || '无'}</dd></div>${ags}</div>`;
    })
    .join('');
}

function renderReceipts() {
  if (!state.receipts.length) {
    $('receipts').innerHTML = '<p class="muted small">暂无回单。</p>';
    return;
  }
  $('receipts').innerHTML = state.receipts
    .map(
      (r) => `
    <div class="card">
      <div class="row" style="justify-content:space-between"><b>${r.id}</b><span class="badge public">甲具名签收</span></div>
      <div>${r.title}</div>
      <div class="muted small">订单 ${r.orderId} · 原始签署时间 ${fmtTime(r.signedAt)}</div>
      <p class="small">${r.text}</p>
      <div class="hash">receiptHash: ${r.receiptHash}</div>
    </div>`,
    )
    .join('');
}

function renderChat() {
  const chat = $('chat');
  const stick = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 90;
  $('chatCount').textContent = `${state.messages.length} 条`;
  const markChecks = new Map((state.pairMarkChecks ?? []).map((x) => [x.id, x.valid]));
  chat.innerHTML = state.messages
    .map((m) => {
      const cls = ['A', 'B', 'SYSTEM', 'TERMINAL'].includes(m.from) ? m.from : 'SYSTEM';
      const badges = [];
      const markValid = markChecks.get(m.id);
      badges.push(
        `<span class="badge ${markValid === false ? 'bad' : 'pair'}" title="${state.session.pairWitness}">${markValid === false ? '私记异常' : '双边私记可核对'} ${short(m.pairMark, 8)}</span>`,
      );
      if (m.kind === 'ack') badges.push('<span class="badge warn">收到≠同意</span>');
      if (m.legalEffect?.includes('late')) badges.push('<span class="badge bad">迟到同意</span>');
      if (m.legalEffect?.includes('effective') || m.kind === 'version-effective') badges.push('<span class="badge ok">已换版</span>');
      if (m.correctionId) badges.push(`<span class="badge public">${m.correctionId}</span>`);
      return `
      <article class="msg ${cls}">
        <div class="meta">
          <span class="from">${m.from === 'SYSTEM' || m.from === 'TERMINAL' ? '终端/规则' : `${m.from} · ${state.parties[m.from]?.name ?? ''}`}</span>
          <span>${fmtTime(m.at)}</span>
          ${badges.join('')}
        </div>
        <div>${m.text}</div>
        ${m.terminalNote ? `<div class="note warn small" style="margin-bottom:0">${m.terminalNote}</div>` : ''}
      </article>`;
    })
    .join('');
  if (stick) chat.scrollTop = chat.scrollHeight;
}

function renderCitationOptions() {
  const select = $('citationSelect');
  const old = select.value || 'AGR-1127-SPEC@v1';
  const sorted = [...state.agreements].sort((a, b) => b.version - a.version);
  select.innerHTML = sorted
    .map((a) => `<option value="${a.agreementId}@v${a.version}" ${a.agreementId === 'AGR-1127-SPEC' && a.version === 1 ? 'selected' : ''}>${a.agreementId}@v${a.version} · ${a.orderId} · ${a.status}</option>`)
    .join('');
  if ([...select.options].some((o) => o.value === old)) select.value = old;
  updateQuoteFromCitation(false);
}

function updateQuoteFromCitation(force = true) {
  const a = agreementByIdVersion($('citationSelect').value);
  if (!a) return;
  if (force || !$('quoteInput').dataset.touched) $('quoteInput').value = a.clauseText;
  validateQuote();
}

function validateQuote() {
  const a = agreementByIdVersion($('citationSelect').value);
  const q = $('quoteInput').value;
  const node = $('citationCheck');
  if (!a || !q) return;
  const ok = a.clauseText.includes(q);
  node.innerHTML = ok ? '<span class="badge ok">引用可还原且哈希已固定</span>' : '<span class="badge bad">引用不是旧约定原文</span>';
  return ok;
}

function renderContextPicker() {
  const box = $('contextPicker');
  const candidateMessages = state.messages.filter((m) => !m.id.startsWith('MSG-007') || true);
  box.innerHTML = candidateMessages
    .map(
      (m) => `
    <label class="check"><input type="checkbox" value="${m.id}" ${selectedContext.has(m.id) ? 'checked' : ''}/>
      <span><b>${m.id}</b> · ${m.from} · ${m.text.slice(0, 72)}${m.text.length > 72 ? '…' : ''}</span>
    </label>`,
    )
    .join('');
  box.querySelectorAll('input').forEach((input) =>
    input.addEventListener('change', () => {
      if (input.checked) selectedContext.add(input.value);
      else selectedContext.delete(input.value);
    }),
  );
}

function approvalSummary(c) {
  if (!c.approvals.length) return '<span class="muted small">尚无签署</span>';
  return c.approvals
    .map(
      (a) => `<span class="badge ${a.decision === 'late-accept' ? 'warn' : 'ok'}">${a.party} ${a.decision} · ${fmtTime(a.at)}</span>`,
    )
    .join(' ');
}

function renderCorrections() {
  const box = $('corrections');
  if (!state.corrections.length) {
    box.innerHTML = '<div class="note info">还没有正式订正。普通聊天不会自动变成承诺。</div>';
    return;
  }
  box.innerHTML = [...state.corrections]
    .reverse()
    .map((c) => {
      const myAccept = c.approvals.some((a) => a.party === party && a.decision === 'accept');
      const canSign = c.status === 'pending' && !myAccept;
      const statusClass = c.status === 'effective' ? 'effective' : c.status === 'late-accepted' ? 'late' : '';
      const statusText = { pending: '待双方接受', effective: '已生效/已换版', 'late-accepted': '已有迟到同意' }[c.status] ?? c.status;
      return `
      <div class="card ${statusClass}">
        <div class="row" style="justify-content:space-between">
          <b>${c.id}</b>
          <span class="badge ${c.status === 'effective' ? 'ok' : c.status === 'late-accepted' ? 'warn' : ''}">${statusText}</span>
        </div>
        <div class="muted small">${c.title} · ${c.kind} · ${c.method === 'joint' ? '双边到场会签' : '具名公鉴'}</div>
        <p>${c.summary}</p>
        <div class="kv">
          <dt>引用替换</dt><dd>${c.citation.agreementId}@v${c.citation.version} / ${c.citation.orderId}<div class="hash">${c.citation.contentHash}</div></dd>
          <dt>上下文</dt><dd>${c.contextMessageIds.join('、')}（${c.contextMessageIds.length} 条，内容已快照）</dd>
          <dt>同版哈希</dt><dd class="hash">${c.docHash}</dd>
          ${c.acceptBy ? `<dt>接受期限</dt><dd>${fmtTime(c.acceptBy)}；当前演练时间 ${fmtTime(simAt())}</dd>` : ''}
          ${c.effectiveVersion ? `<dt>新版</dt><dd>${c.citation.agreementId}@v${c.effectiveVersion}</dd>` : ''}
        </div>
        <h3>引用原文</h3><div class="context-item">${c.citation.quote}</div>
        <h3>签署</h3><div class="row">${approvalSummary(c)}</div>
        <div class="row" style="margin-top:10px">
          ${canSign ? `<button class="primary" data-sign="${c.id}">我是${party}，接受同版并签署</button>` : `<button disabled>${party} 已作有效接受或单据已结束</button>`}
          <button data-copyhash="${c.docHash}" class="ghost">复制哈希</button>
        </div>
        <div class="note warn small">${c.transferWarning}</div>
      </div>`;
    })
    .join('');

  box.querySelectorAll('[data-sign]').forEach((btn) =>
    btn.addEventListener('click', () => signCorrection(btn.dataset.sign)),
  );
  box.querySelectorAll('[data-copyhash]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      await navigator.clipboard.writeText(btn.dataset.copyhash);
      toast('已复制 docHash，可到仲裁席查回');
    }),
  );
}

async function signCorrection(id) {
  const c = state.corrections.find((x) => x.id === id);
  try {
    await api(`/api/approvals?sid=${sid}`, {
      body: {
        sid,
        correctionId: id,
        party,
        at: simAt(),
        channel: c.method === 'joint' ? 'joint-presence' : 'named-terminal',
      },
    });
    toast('签署已记录');
    await loadState();
  } catch (err) {
    toast(err.message);
  }
}

function collectContext() {
  return [...document.querySelectorAll('#contextPicker input:checked')].map((i) => i.value);
}

async function createCorrection() {
  const ref = $('citationSelect').value;
  const [agreementId, v] = ref.split('@v');
  const contextMessageIds = collectContext();
  if (!contextMessageIds.length) return toast('至少选择一条上下文消息');
  const kind = $('crKind').value;
  const method = $('crMethod').value;
  const body = {
    sid,
    at: simAt(),
    kind,
    method,
    agreementId,
    version: Number(v),
    quote: $('quoteInput').value,
    contextMessageIds,
    summary: $('summaryInput').value,
    proposedBy: party,
    acceptBy: method === 'named' ? deadlineIso() : null,
  };
  if (kind === 'spec') {
    body.changes = { clauseText: $('clauseInput').value, title: '交片规格订正（引用换版）' };
  } else {
    const due = $('newDueInput').value.length === 16 ? `${$('newDueInput').value}:00+08:00` : $('newDueInput').value;
    body.changes = {
      clauseText: $('rescheduleClauseInput').value,
      terms: { dueAt: due },
      schedule: { dueAt: due, claimedBasis: $('basisInput').value },
      basis: $('basisInput').value,
      title: '同版改期订正单',
    };
    body.title = '同版改期订正单';
  }
  try {
    const c = await api('/api/corrections?sid=' + sid, { body });
    toast(`已生成 ${c.correction.id} 并由 ${party} 签署`);
    document.querySelector('[data-tab="docs"]').click();
    await loadState();
  } catch (err) {
    toast(err.message);
  }
}

function deadlineIso() {
  const v = $('acceptByInput').value;
  return v.length === 16 ? `${v}:00+08:00` : v;
}

async function sendMessage(text, asParty = party, kind = 'talk') {
  if (asParty !== party) {
    toast(`该脚本应由 ${asParty} 终端发送；已为你打开对应终端`);
    window.open(counterpartUrl(), '_blank', 'noopener');
    return;
  }
  if (!text?.trim()) return toast('请输入消息');
  await api(`/api/messages?sid=${sid}`, { body: { sid, from: party, text, kind, at: simAt() } });
  $('messageInput').value = '';
  await loadState();
}

async function buildPackage() {
  const context = collectContext();
  const body = {
    sid,
    preparedBy: party,
    at: simAt(),
    title: $('pkgTitle').value,
    includePairVerifier: $('pkgPairVerifier').checked,
    receiptIds: $('includeReceipts').checked ? state.receipts.map((r) => r.id) : [],
    claims: [
      { type: 'attribute-pair-messages', targetParty: 'A' },
      { type: 'enforce-all-included-corrections' },
      { type: 'retrieve-signed-reschedule' },
      { type: 'unstarted-reschedule', orderId: 'ORD-1127' },
    ],
  };
  if (!$('pkgAllMessages').checked) body.messageIds = context;
  currentPkg = await api('/api/packages?sid=' + sid, { body });
  $('downloadPkgBtn').disabled = false;
  $('arbitratePkgBtn').disabled = false;
  $('pkgResult').innerHTML = `<div class="note ok">已生成 <b>${currentPkg.packageId}</b><br>清单哈希 <code>${short(currentPkg.manifest.contentsHash, 18)}</code><br><a href="/arbiter.html?packageId=${currentPkg.packageId}" target="_blank">在仲裁席打开此包 →</a></div>`;
  toast('证据包已生成');
}

async function arbitrateCurrentPackage() {
  if (!currentPkg) return;
  const award = await api('/api/arbitrate', { body: { package: currentPkg } });
  lastAward = award;
  downloadJson(award, `${award.awardId}.json`);
  $('pkgResult').insertAdjacentHTML(
    'beforeend',
    `<div class="note ok" style="margin-top:8px">仲裁已出具：<a href="/arbiter.html?awardId=${encodeURIComponent(award.awardId)}" target="_blank">${award.awardId}</a>；JSON 已开始下载。</div>`,
  );
  toast('判定 JSON 已下载，可在仲裁席链接查看具体判定');
}

async function querySigned() {
  const q = $('queryInput').value.trim();
  if (!q) return toast('请输入编号或哈希');
  let pkg = currentPkg;
  if (!pkg) {
    pkg = await api('/api/packages?sid=' + sid, {
      body: { sid, preparedBy: party, includePairVerifier: false, receiptIds: state.receipts.map((r) => r.id) },
    });
  }
  const results = await api('/api/retrieve', { body: { package: pkg, query: q } });
  $('queryResult').innerHTML = results.length
    ? results
        .map(
          (r) => `<div class="card ${r.verified ? 'effective' : ''}">
        <div class="row" style="justify-content:space-between"><b>${r.type} · ${r.id}</b><span class="badge ${r.verified ? 'ok' : 'bad'}">${r.verified ? '验签通过' : '验签失败'}</span></div>
        <div class="muted small">${r.title ?? ''} · ${r.effective ? '生效/具事实效力' : '仅可查来源或未生效'}</div>
        <div class="hash">${r.hash}</div>
        <pre>${JSON.stringify(r.exactContent, null, 2)}</pre>
      </div>`,
        )
        .join('')
    : '<div class="note warn">没有按该编号/哈希查回签署内容。</div>';
}

/* Events */
$('sidSelect').addEventListener('change', () => {
  sid = $('sidSelect').value;
  history.replaceState(null, '', `/terminal.html?party=${party}&sid=${sid}`);
  currentPkg = null;
  loadState();
});
$('resetBtn').addEventListener('click', async () => {
  await api('/api/reset', { body: { sid, scenario: sid } });
  toast('已重置为脚本初始轮次');
  loadState();
});
$('sendBtn').addEventListener('click', () => sendMessage($('messageInput').value));
$('messageInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) sendMessage($('messageInput').value);
});
document.querySelectorAll('[data-quick]').forEach((btn) =>
  btn.addEventListener('click', () => {
    const kind = btn.dataset.quick;
    if (kind === 'proposal') {
      $('messageInput').value = '明天交片规格改一下：先出 1920×1080、25fps、H.264 MP4 审片版，4K ProRes 母版往后放。';
      sendMessage($('messageInput').value, 'A', 'proposal-talk');
    } else if (kind === 'ack') {
      $('messageInput').value = '我收到了。';
      sendMessage($('messageInput').value, 'B', 'ack');
    } else {
      $('messageInput').value = '这些私聊就我们俩能核对就行，别将来截一段就拿去对外说是我正式改单。';
      sendMessage($('messageInput').value, 'A');
    }
  }),
);
document.querySelectorAll('[data-advance]').forEach(() =>
  document.querySelector('[data-advance]').addEventListener('click', () => {
    const d = new Date(simAt());
    d.setMinutes(d.getMinutes() + 1);
    simNowInput.value = isoToLocalInput(d.toISOString());
  }),
);
$('lateTimeBtn').addEventListener('click', () => (simNowInput.value = '2026-11-26T21:14'));
$('citationSelect').addEventListener('change', () => updateQuoteFromCitation(true));
$('quoteInput').addEventListener('input', () => {
  $('quoteInput').dataset.touched = 'true';
  validateQuote();
});
$('fillQuoteBtn').addEventListener('click', () => {
  const a = agreementByIdVersion($('citationSelect').value);
  $('quoteInput').value = a.clauseText;
  validateQuote();
});
$('fillCommonQuoteBtn').addEventListener('click', () => {
  const a = agreementByIdVersion($('citationSelect').value);
  const m = a.clauseText.match(/交片规格：.*?交片。/);
  $('quoteInput').value = m ? m[0] : a.clauseText;
  validateQuote();
});
$('crKind').addEventListener('change', () => {
  const reschedule = $('crKind').value === 'reschedule';
  $('specFields').hidden = reschedule;
  $('rescheduleFields').hidden = !reschedule;
  $('summaryInput').value = reschedule ? '双方接受同版改期；改期所依据的开工状态由回单另行证明。' : '调整次日交片规格，并明确母版交付时间。';
});
$('createCrBtn').addEventListener('click', createCorrection);
document.querySelectorAll('.tabs [data-tab]').forEach((btn) =>
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tabs [data-tab]').forEach((b) => b.classList.toggle('active', b === btn));
    const tab = btn.dataset.tab;
    document.querySelectorAll('.tabpane').forEach((p) => p.classList.toggle('active', p.dataset.pane === tab));
  }),
);
$('exportJson').addEventListener('click', async () => {
  const transcript = await api('/api/transcript?sid=' + sid, {
    body: { sid, party, includePairVerifier: $('includePairSecretExport').checked },
  });
  downloadJson(transcript, `${sid}-${party}-terminal-transcript.json`);
  toast('JSON 已取出');
});
$('exportMd').addEventListener('click', () => {
  downloadText(transcriptMarkdown(state, party), `${sid}-${party}-terminal-transcript.md`);
  toast('Markdown 已取出');
});
$('buildPkgBtn').addEventListener('click', buildPackage);
$('downloadPkgBtn').addEventListener('click', () => currentPkg && downloadJson(currentPkg, currentPkg.packageId + '.json'));
$('arbitratePkgBtn').addEventListener('click', arbitrateCurrentPackage);
$('queryBtn').addEventListener('click', querySigned);
$('queryInput').addEventListener('keydown', (e) => e.key === 'Enter' && querySigned());

loadState().catch((err) => toast(err.message));
setInterval(() => loadState().catch(() => {}), 1800);
