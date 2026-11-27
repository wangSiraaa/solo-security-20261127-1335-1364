const roleCache = new Map();
let activeTab = 'desk';
let aloneResult = null;

const $ = (selector) => document.querySelector(selector);
const roleView = $('#role-view');
const exportLink = $('#export-link');
const viewTitle = $('#view-title');

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'content-type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok && !body.code) throw new Error(body.error || `请求失败 ${res.status}`);
  return { ok: res.ok, status: res.status, body };
}

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function dt(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}
function dayTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const date = d.toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit' });
  const time = d.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false });
  return `${date} ${time}`;
}

const STATUS_META = {
  PAUSED: ['暂停', 'pause'],
  LIFTED: ['已解除', 'ok'],
  LIFTED_DIFFERENT_CUT: ['解除：不同剪辑版', 'ok'],
  NO_PAUSE_REQUIRED: ['无需暂停', 'ok'],
  ACTIVE: ['有效/已送交', 'active'],
  READY_TO_DISPATCH: ['待送交', 'pending'],
  WITHDRAWN_DIFFERENT_CUT: ['已撤回：不同剪辑版', 'neutral'],
  PENDING_CORRECTION_RECHECK: ['待订正复办', 'pending'],
  PLAYABLE: ['可播放', 'play'],
  PROCEED_FIRST_GRANT: ['先授予，继续', 'proceed'],
  PAUSE: ['暂停本场', 'pause'],
  SCHEDULED: ['已排期', 'neutral'],
  AWAIT_SAME_EDITION_WITNESS: ['待同版见证，未送台', 'warn'],
  PLAYABLE_AFTER_RECHECK: ['复办后可播放', 'play'],
  PAUSED_FOR_RIGHTS_COLLISION: ['撞期待暂停', 'pause'],
};

function badge(status) {
  const [label, cls] = STATUS_META[status] || [status || '—', 'neutral'];
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

const REASON_TEXT = {
  SOLD_SHOW_IN_DOUBLE_EXCLUSIVE_OVERLAP_LATER_GRANT: '已售场落入双重独占交集，且为后授予方',
  IN_OVERLAP_BUT_FIRST_GRANT_PRIORITY: '虽在交集内，但属先授予方，按约定优先继续',
  OUTSIDE_MUTUAL_EXCLUSIVE_OVERLAP: '不在两份独占期交集内',
  TERRITORY_DISJOINT: '使用地域不重合',
  NOT_BOTH_EXCLUSIVE: '并非两份独占许可同时冲突',
  EXCLUSIVE_WINDOWS_DO_NOT_OVERLAP: '独占期不重合',
  OUTSIDE_EXHIBITION_WINDOW: '超出普通放映权期限',
  DIFFERENT_CUT: '回执订正为不同剪辑版，同版基础消灭',
  EXCLUSIVITY_SHORTENED_BEFORE_EVENING: '独占期已缩短到晚场前',
};
const reason = (code) => REASON_TEXT[code] || code || '—';

function setNotice(type, html) {
  $('#action-result').innerHTML = `<div class="notice ${type}">${html}</div>`;
}

async function loadOverview() {
  const { body } = await api('/api/overview');
  $('#counts').innerHTML = `
    <div class="count"><b>${body.counts.catalogA}</b><span>甲馆目录</span></div>
    <div class="count"><b>${body.counts.catalogB}</b><span>乙馆目录</span></div>
    <div class="count"><b>${body.counts.deskReleases}</b><span>排映台发布包</span></div>
    <div class="count"><b>${body.counts.workOrders}</b><span>排映工单</span></div>
    <div class="count"><b>${body.counts.corrections}</b><span>订正/合同修订</span></div>
    <div class="count"><b>${body.counts.activeSessions}</b><span>活动一次性会话</span></div>
  `;
  $('#events').innerHTML = body.events.slice().reverse().map((e) => `
    <div class="event"><time>${dayTime(e.at)}</time><b>${esc(e.scope)}</b> · ${esc(e.action)}${e.detail?.woId ? ` · <code>${esc(e.detail.woId)}</code>` : ''}${e.detail?.pairId ? ` · <code>${esc(e.detail.pairId)}</code>` : ''}</div>
  `).join('');
}

async function loadTab(tab = activeTab) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tab));
  exportLink.href = `/api/export/${tab}`;
  const titles = {
    desk: '联合排映台：只见确认同版作品、公开片段、最小放映字段和工单',
    'archive-a': '东屿资料馆：本馆原册、完整合同与电子放映单',
    'archive-b': '西潮影像馆：本馆原册、完整合同与电子放映单',
    trust: '本地卖方回执/一次性会话岗：可见封签与订正，不见两馆放映单',
    governance: '岗位导出矩阵与凭案号单独复查结果',
  };
  viewTitle.textContent = titles[tab];
  roleView.innerHTML = '<p class="muted">正在读取本岗导出视图…</p>';
  const { body } = await api(`/api/role/${tab}`);
  roleCache.set(tab, body);
  if (tab === 'desk') roleView.innerHTML = renderDesk(body);
  if (tab === 'archive-a' || tab === 'archive-b') roleView.innerHTML = renderArchive(body);
  if (tab === 'trust') roleView.innerHTML = renderTrust(body);
  if (tab === 'governance') roleView.innerHTML = renderGovernance(body);
  if (tab === 'archive-a' || tab === 'archive-b') bindArchiveImport(tab);
}

function bindArchiveImport(tab) {
  const side = tab === 'archive-a' ? 'a' : 'b';
  const message = $('#import-message');
  $('#catalog-file')?.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const catalog = JSON.parse(await file.text());
      const { body } = await api(`/api/archives/${side}/import-catalog`, { method: 'POST', body: { catalog } });
      roleCache.set(tab, body);
      roleView.innerHTML = renderArchive(body);
      bindArchiveImport(tab);
      message.textContent = `已从 ${file.name} 导入 ${body.originalCatalog.length} 行。`;
    } catch (error) {
      if (message) message.textContent = `导入失败：${error.message}`;
    }
  });
  $('#catalog-sample')?.addEventListener('click', async () => {
    const { body } = await api(`/api/archives/${side}/import-sample`, { method: 'POST' });
    roleCache.set(tab, body);
    roleView.innerHTML = renderArchive(body);
    bindArchiveImport(tab);
    const msg = $('#import-message');
    if (msg) msg.textContent = '内置样例目录已重新导入本岗。';
    loadOverview();
  });
}

function decisionTable(decisions) {
  if (!decisions?.length) return '<p class="muted">本轮没有可核查的放映场。</p>';
  return `
    <div class="table-wrap"><table>
      <thead><tr><th>放映场</th><th>馆别</th><th>时间</th><th>已售</th><th>双重独占交集</th><th>顺位</th><th>结论</th><th>规则原因</th></tr></thead>
      <tbody>${decisions.map((d) => `
        <tr>
          <td><code>${esc(d.screeningId)}</code><div class="muted">${esc(d.sheetId || '')}</div></td>
          <td>${d.archive === 'a' ? '东屿' : d.archive === 'b' ? '西潮' : '—'}</td>
          <td>${dayTime(d.start)}–${new Date(d.end).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit' })}</td>
          <td class="num">${d.sold ?? '—'}</td>
          <td>${d.inMutualExclusiveOverlap === true ? badge('PAUSE') : d.inMutualExclusiveOverlap === false ? badge('PLAYABLE') : '—'}</td>
          <td>${d.priority === 'FIRST_GRANT' ? '先授予' : d.priority === 'LATER_GRANT' ? '后授予' : '—'}</td>
          <td>${badge(d.decision)}</td>
          <td>${esc(reason(d.reasonCode))}</td>
        </tr>`).join('')}
      </tbody>
    </table></div>`;
}

function renderDesk(d) {
  const wos = d.workOrders.slice().reverse();
  return `
    <div class="notice info"><b>排映台受案规则：</b>${esc(d.protocol.collisionRule)} ${esc(d.protocol.priorityRule)}</div>

    <h3 class="section-title">必须处理的工单（${wos.length}）</h3>
    ${wos.map((wo) => `
      <section class="wo">
        <div class="wo-head">
          <h4>${esc(wo.id)}</h4>
          ${badge(wo.status)}
          <span class="muted">发布包 <code>${esc(wo.releaseId)}</code> · 复办 ${wo.rounds.length} 轮</span>
        </div>
        <p>${esc(wo.currentSummary || '')}</p>
        <div class="pill-row">${(wo.targetScreeningIds || []).map((id) => `<span class="badge pause">暂停 ${esc(id)}</span>`).join('') || '<span class="badge ok">当前无暂停场次</span>'}</div>
        ${wo.rounds.slice().reverse().map((r) => `
          <div class="round">
            <h5>第 ${r.round} 轮 · ${esc(r.trigger || '核查')} · ${dt(r.at)}</h5>
            ${r.reason ? `<p class="muted">${esc(r.reason)}</p>` : ''}
            ${r.evaluation?.exclusiveOverlap ? `<p class="muted">双重独占交集：${dt(r.evaluation.exclusiveOverlap.start)} 至 ${dt(r.evaluation.exclusiveOverlap.end)}</p>` : ''}
            ${decisionTable(r.evaluation?.screeningDecisions || r.screeningDecisions)}
          </div>`).join('')}
      </section>
    `).join('')}

    <h3 class="section-title">已接收的同版发布包（${d.confirmedReleases.length}）</h3>
    <div class="grid-2">
      ${d.confirmedReleases.map((r) => `
        <article class="item-card">
          <h4>${esc(r.agreedTitle || r.releaseId)}</h4>
          <div class="pill-row">${badge(r.status)}<span class="badge neutral">见证 ${esc(r.witnessId)}</span></div>
          <p class="muted">发布包 <code>${esc(r.releaseId)}</code>；不含未命中的近似名目录。</p>
          <dl class="kv">
            ${r.publicFragments.map((f) => `
              <dt>${f.archive === 'a' ? '甲馆片段' : '乙馆片段'}</dt>
              <dd><code>${esc(f.licenseId)}</code><br>${esc(f.territoryName)}<br>独占：${dt(f.exclusiveStart)} → ${dt(f.exclusiveEnd)}<br><span class="muted">${esc(f.allowedPublicText)}</span></dd>
            `).join('')}
          </dl>
        </article>`).join('')}
    </div>

    <h3 class="section-title">最小放映字段（${d.minimizedScreeningSubmissions.length}）</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>放映场</th><th>发布包</th><th>馆别</th><th>放映单</th><th>开始</th><th>结束</th><th>已售</th></tr></thead>
      <tbody>${d.minimizedScreeningSubmissions.map((s) => `
        <tr><td><code>${esc(s.screeningId)}</code></td><td><code>${esc(s.releaseId)}</code></td><td>${s.archive === 'a' ? '东屿' : '西潮'}</td><td><code>${esc(s.sheetId)}</code></td><td>${dt(s.start)}</td><td>${dt(s.end)}</td><td class="num">${s.sold}</td></tr>
      `).join('')}</tbody>
    </table></div>
  `;
}

function renderArchive(d) {
  const side = d.post === 'archive-a' ? 'a' : 'b';
  return `
    <div class="notice ok"><b>${esc(d.postName)}本地导出：</b>本岗可导出自己的原目录、完整合同和放映单；系统投影不包含对馆目录。</div>
    <div class="import-bar">
      <label class="button small">分别导入：选择本馆目录 JSON
        <input id="catalog-file" type="file" accept="application/json,.json" hidden />
      </label>
      <button class="small" id="catalog-sample">重新导入内置${side === 'a' ? '甲' : '乙'}馆样例目录</button>
      <span id="import-message" class="muted">导入只写入本岗原册，不会发送给排映台。</span>
    </div>

    <h3 class="section-title">原目录与当前回执</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>本馆目录号</th><th>作品</th><th>别名</th><th>当前回执</th></tr></thead>
      <tbody>${d.originalCatalog.map((c) => `
        <tr><td><code>${esc(c.id)}</code></td><td><b>${esc(c.title)}</b></td><td>${(c.aliases || []).map(esc).join('<br>') || '—'}</td><td><code>${esc(c.receiptId)}</code></td></tr>
      `).join('')}</tbody>
    </table></div>

    <h3 class="section-title">本馆回执（持执文本不含内部版次封签）</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>回执号</th><th>别名</th><th>状态</th><th>被哪张订正回执替代</th><th>说明</th></tr></thead>
      <tbody>${d.ownReceipts.map((r) => `
        <tr><td><code>${esc(r.id)}</code></td><td>${esc(r.alias)}</td><td>${badge(r.status)}</td><td>${r.supersededBy ? `<code>${esc(r.supersededBy)}</code>` : '—'}</td><td class="muted">${esc(r.statement)}</td></tr>
      `).join('')}</tbody>
    </table></div>

    <h3 class="section-title">完整许可合同（仅本馆可单独导出）</h3>
    <div class="grid-2">
      ${d.originalLicenses.map((l) => `
        <article class="item-card">
          <h4><code>${esc(l.id)}</code></h4>
          <dl class="kv">
            <dt>地域</dt><dd>${esc(l.territoryName)} (<code>${esc(l.territoryId)}</code>)</dd>
            <dt>放映权</dt><dd>${dt(l.exhibitionStart)} → ${dt(l.exhibitionEnd)}</dd>
            <dt>独占期</dt><dd>${l.exclusive ? `${dt(l.exclusiveStart)} → ${dt(l.exclusiveEnd)}` : '非独占'}</dd>
            <dt>授予时间</dt><dd>${dt(l.grantedAt)}</dd>
            <dt>私有费用</dt><dd><b>${esc(l.feePrivate)}</b></dd>
            <dt>内部备注</dt><dd>${esc(l.internalNote)}</dd>
            <dt>准许公开</dt><dd class="muted">${esc(l.publicText)}</dd>
          </dl>
        </article>`).join('')}
    </div>

    <h3 class="section-title">本地电子放映单</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>放映单号</th><th>场次号</th><th>节目</th><th>影厅</th><th>时间</th><th>售票/容量</th><th>状态</th><th>排映台工单</th></tr></thead>
      <tbody>${d.electronicScreeningSheets.map((s) => `
        <tr>
          <td><code>${esc(s.sheetId)}</code></td>
          <td><code>${esc(s.id)}</code></td>
          <td><code>${esc(s.workKey)}</code></td>
          <td>${esc(s.hall)}</td>
          <td>${dayTime(s.start)}–${new Date(s.end).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit' })}</td>
          <td class="num">${s.sold}/${s.capacity}</td>
          <td>${badge(s.holdStatus)}</td>
          <td>${s.deskWorkOrderId ? `<code>${esc(s.deskWorkOrderId)}</code>` : '—'}</td>
        </tr>`).join('')}
      </tbody>
    </table></div>

    <h3 class="section-title">本馆比对确认记录</h3>
    ${d.ownConfirmations.length ? `<div class="table-wrap"><table><tbody>${d.ownConfirmations.map((c) => `
      <tr><td>${dt(c.confirmedAt || c.acknowledgedAt)}</td><td><code>${esc(c.pairId)}</code></td><td>${esc(c.result)}</td><td class="muted">${esc(c.sessionPurpose || '')}</td></tr>
    `).join('')}</tbody></table></div>` : '<p class="muted">暂无。</p>'}
  `;
}

function renderTrust(d) {
  return `
    <div class="notice ${d.activeOneTimeSessions.length ? 'warn' : 'ok'}">
      ${d.activeOneTimeSessions.length ? '仍有活动会话；散会前可看到一次性句柄。' : '当前无活动会话：一次性 session、提交句柄和目录行映射已删除。'}
    </div>

    <h3 class="section-title">回执台账（受托岗可见内部版次封签；持回执的馆方导出不可见）</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>回执号</th><th>馆别</th><th>别名</th><th>内部封签</th><th>状态</th><th>订正单</th></tr></thead>
      <tbody>${d.receiptLedgerIncludingInternalLock.map((r) => `
        <tr><td><code>${esc(r.id)}</code></td><td>${r.archive === 'a' ? '东屿' : '西潮'}</td><td>${esc(r.alias)}</td><td><code>${esc(r.lock)}</code></td><td>${badge(r.status)}</td><td>${r.correctionOrderId ? `<code>${esc(r.correctionOrderId)}</code>` : '—'}</td></tr>
      `).join('')}</tbody>
    </table></div>

    <h3 class="section-title">同版见证</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>见证号</th><th>节目键</th><th>状态</th><th>签发时间</th><th>说明</th></tr></thead>
      <tbody>${d.issuedWitnesses.map((w) => `
        <tr><td><code>${esc(w.id)}</code></td><td><code>${esc(w.workKey)}</code></td><td>${badge(w.status)}</td><td>${dt(w.issuedAt)}</td><td class="muted">${esc(w.statement)}${w.supersededByCorrection ? ` 已由 <code>${esc(w.supersededByCorrection)}</code> 替代。` : ''}</td></tr>
      `).join('')}</tbody>
    </table></div>

    <h3 class="section-title">改名/合同订正台账</h3>
    ${d.correctionLedger.length ? d.correctionLedger.map((c) => `
      <article class="item-card"><h4><code>${esc(c.id)}</code> · ${esc(c.type)}</h4><p class="muted">${esc(c.reason || c.note || '')}</p><pre class="pre">${esc(JSON.stringify(c, null, 2))}</pre></article>
    `).join('') : '<p class="muted">暂无订正。</p>'}
  `;
}

function renderGovernance(d) {
  return `
    <div class="notice info"><b>${esc(d.choice)}</b><br>${esc(d.rejectedAlternative)}</div>
    ${aloneResult ? `
      <div class="notice ${aloneResult.ok ? 'ok' : 'warn'}">
        <b>凭案号单独复算结果：</b><code>${esc(aloneResult.body.caseNumber)}</code><br>
        ${esc(aloneResult.body.reason)}
      </div>` : ''}
    <h3 class="section-title">各岗位最终可单独导出的资料</h3>
    <div class="matrix">
      ${d.exportMatrix.map((row) => `
        <article class="item-card">
          <h4>${esc(row.postName)} <code>${esc(row.post)}</code></h4>
          <div class="grid-2">
            <div><b>可以单独导出</b><ul>${row.aloneCanExport.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>
            <div><b>不能单独还原</b><ul>${row.aloneCannotReconstruct.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>
          </div>
        </article>
      `).join('')}
    </div>
    <h3 class="section-title">命令行导出物</h3>
    <p class="muted">运行 <code>npm test</code> 会在 <code>exports/</code> 生成四岗 JSON、放映单 CSV、导出矩阵和三轮排映台快照。</p>
  `;
}

document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => loadTab(btn.dataset.tab));
});

$('#btn-refresh').addEventListener('click', async () => {
  roleCache.clear();
  await Promise.all([loadOverview(), loadTab(activeTab)]);
});

$('#btn-prepare').addEventListener('click', async () => {
  try {
    const { body } = await api('/api/demo/reset-and-prepare', { method: 'POST' });
    setNotice('ok', `已重置并完成初始比对：签发见证 ${body.comparison.closeSummary.issuedWitnessIds.join('、')}；排映台生成 ${body.adjudications.length} 份核查结果。`);
    roleCache.clear();
    await Promise.all([loadOverview(), loadTab(activeTab)]);
  } catch (e) {
    setNotice('danger', esc(e.message));
  }
});

$('#btn-cut').addEventListener('click', async () => {
  try {
    const { body } = await api('/api/demo/correct-cut-and-replay', { method: 'POST' });
    setNotice('ok', `订正单 <code>${body.correction.id}</code> 已把回执改为 <code>${body.correction.newLock}</code>；新一次性会话无同版见证，原工单 <code>${body.workOrder.id}</code> 第 ${body.workOrder.rounds.length} 轮复办为 <b>${body.workOrder.status}</b>，原暂停场已解除。`);
    roleCache.clear();
    await Promise.all([loadOverview(), loadTab('desk')]);
    document.querySelector('.tab[data-tab="desk"]').click();
  } catch (e) {
    setNotice('danger', `${esc(e.message)}。请先点击“重置并跑初始比对/排映”。`);
  }
});

$('#btn-shorten').addEventListener('click', async () => {
  try {
    const { body } = await api('/api/demo/shorten-sp-and-replay', { method: 'POST' });
    const decisions = body.workOrder.rounds.at(-1).evaluation.screeningDecisions;
    const pm = decisions.find((d) => d.screeningId === 'SCR-A-SP-PM');
    const eve = decisions.find((d) => d.screeningId === 'SCR-A-SP-EVE');
    setNotice('ok', `合同修订 <code>${body.amendment.id}</code> 后复办 <code>${body.workOrder.id}</code>：下午场 <b>${badge(pm.decision)}</b>，晚场 <b>${badge(eve.decision)}</b>。`);
    roleCache.clear();
    await Promise.all([loadOverview(), loadTab('desk')]);
    document.querySelector('.tab[data-tab="desk"]').click();
  } catch (e) {
    setNotice('danger', `${esc(e.message)}。请先点击“重置并跑初始比对/排映”。`);
  }
});

$('#btn-alone').addEventListener('click', async () => {
  const result = await api('/api/work-orders/WO-HB-1212/recheck-alone', { method: 'POST' });
  aloneResult = result;
  await loadTab('governance');
  setNotice('warn', '接口返回 <code>409 NEEDS_BOTH_PARTIES</code>：可凭案号读旧卷，但不能仅凭案号重算同版；方案 B 必须两馆再次配合。');
});

(async function init() {
  try {
    await api('/api/demo/reset-and-prepare', { method: 'POST' });
    await Promise.all([loadOverview(), loadTab('desk')]);
    setNotice('info', '已自动载入种子并跑通初始流程。可依次点击上方三个订正/复查按钮。');
  } catch (e) {
    setNotice('danger', `初始化失败：${esc(e.message)}`);
  }
})();
