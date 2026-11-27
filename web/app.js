import {
  DOC_LABELS,
  addDocsToBook,
  auditRelease,
  canonicalStringify,
  createStation,
  issueApproval,
  issuePlanDocuments,
  makeDocNumber,
  redeemDocument,
  applyRedemption,
  validateRecords,
  validateTemplate,
} from '/src/core.mjs';
import {
  baselineV1,
  cloneJson,
  declassifiedV2,
  makeFixedSupplementRule,
  seedApprovalForm,
  seedRecords,
  templatePresets,
} from '/src/presets.mjs';

const $ = (id) => document.getElementById(id);

function freshState() {
  return {
    records: cloneJson(seedRecords),
    template: cloneJson(baselineV1),
    approvals: [],
    issuedDocs: [],
    stations: {
      'public-desk': createStation({
        id: 'public-desk',
        name: '栖霞社区公开站',
        acceptedTemplateVersions: ['1.0.0'],
      }),
      'priority-group': createStation({
        id: 'priority-group',
        name: '限定收件组离线站',
        acceptedTemplateVersions: ['1.0.0', '2.0.0'],
      }),
    },
    selectedStationId: 'public-desk',
    audit: null,
    timeline: [],
  };
}

let state = freshState();

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function docLabel(type) {
  return DOC_LABELS[type] ?? type;
}

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function addTimeline(title, detail = '', tone = '') {
  state.timeline.unshift({ at: nowTime(), title, detail, tone });
  state.timeline = state.timeline.slice(0, 18);
  renderTimeline();
}

function renderTimeline() {
  $('timeline').innerHTML = state.timeline.length
    ? state.timeline.map((e) => `
      <div class="timeline-item ${esc(e.tone)}">
        <span class="timeline-time">${esc(e.at)}</span>
        <strong>${esc(e.title)}</strong>
        ${e.detail ? `<div>${esc(e.detail)}</div>` : ''}
      </div>`).join('')
    : '<div class="empty">尚未执行步骤。</div>';
}

function syncStaticControls() {
  $('templateEditor').value = pretty(state.template);
  $('recordsEditor').value = pretty(state.records);
  $('templatePreset').value = 'baselineV1';
  $('recipientGroup').value = 'public-desk';
  $('stationSelect').value = state.selectedStationId;
  $('seatPresent').checked = true;

  $('approvalId').value = seedApprovalForm.approvalId;
  $('approvalFields').value = seedApprovalForm.fieldsText;
  $('approvalTemplateId').value = seedApprovalForm.templateId;
  $('approvalTemplateVersion').value = seedApprovalForm.templateVersion;
  $('approvalRecipientGroup').value = seedApprovalForm.recipientGroup;
  $('approvalNote').value = seedApprovalForm.note;
}

function readTemplateFromEditor() {
  try {
    const parsed = JSON.parse($('templateEditor').value);
    const check = validateTemplate(parsed);
    if (!check.ok) {
      $('templateValidation').className = 'validation error';
      $('templateValidation').textContent = check.errors.join('；');
      addTimeline('模板被拒绝', check.errors.join('；'), 'danger');
      return null;
    }
    $('templateValidation').className = 'validation ok';
    $('templateValidation').textContent = '模板合法：只包含允许的数据节点。';
    return parsed;
  } catch (error) {
    $('templateValidation').className = 'validation error';
    $('templateValidation').textContent = `JSON 解析失败：${error.message}`;
    return null;
  }
}

function readRecordsFromEditor() {
  try {
    const parsed = JSON.parse($('recordsEditor').value);
    const check = validateRecords(parsed);
    if (!check.ok) {
      $('recordsValidation').className = 'validation error';
      $('recordsValidation').textContent = check.errors.join('；');
      return null;
    }
    $('recordsValidation').className = 'validation ok';
    $('recordsValidation').textContent = `记录合法：${parsed.length} 条。`;
    return parsed;
  } catch (error) {
    $('recordsValidation').className = 'validation error';
    $('recordsValidation').textContent = `JSON 解析失败：${error.message}`;
    return null;
  }
}

async function runAudit({ announce = false } = {}) {
  const template = readTemplateFromEditor();
  const records = readRecordsFromEditor();
  if (!template || !records) return null;

  state.template = template;
  state.records = records;
  const recipientGroup = $('recipientGroup').value;
  state.selectedStationId = $('stationSelect').value;

  state.audit = auditRelease({
    template,
    records,
    approvals: state.approvals,
    recipientGroup,
    ignoreApprovals: !$('seatPresent').checked,
  });

  renderAudit();
  renderHall();
  if (announce) addTimeline('发行前检查完成', `收件组=${recipientGroup}；请查看馆内审计结果。`, 'ok');
  return state.audit;
}

function badgeForStatus(status) {
  const map = {
    'release-invariant': ['invariant', '静态保证一致'],
    'release-pair-equivalent': ['pair', '本对恰好一致'],
    'release-declassified': ['declassified', '核准后可显差异'],
    'hold-divergent': ['divergent', '扣留：私有分叉'],
    'hold-unproven': ['unproven', '扣留：保证不足'],
    'eval-error': ['error', '求值失败'],
  };
  const [cls, label] = map[status] ?? ['neutral', status];
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function renderComparison() {
  const audit = state.audit;
  if (!audit) {
    $('comparisonTable').innerHTML = '<div class="empty">尚未运行检查。</div>';
    return;
  }
  if (!audit.ok) {
    $('comparisonTable').innerHTML = `
      <div class="timeline-item danger"><strong>输入/模板校验失败</strong>
        <div>${esc([...(audit.templateErrors ?? []), ...(audit.recordErrors ?? [])].join('；'))}</div>
      </div>`;
    return;
  }

  const rows = audit.comparisons.map((c) => {
    const record = state.records.find((r) => r.id === c.recordId);
    const pub = record?.public ?? {};
    const sameAs = '';
    return `
      <tr>
        <td><strong>${esc(c.recordId)}</strong></td>
        <td title="${esc(c.publicKey)}"><code>baseFee=${esc(pub.baseFee)}, copies=${esc(pub.copies)}, formCode=${esc(pub.formCode)}</code></td>
        <td class="num"><strong>${c.summary.count}</strong></td>
        <td class="num"><strong>${c.summary.totalAmount}</strong></td>
        <td>${esc(JSON.stringify(c.summary.kinds))}</td>
        <td class="num">${c.summary.errorCount} ${c.summary.errorCount ? esc(JSON.stringify(c.summary.errorCodes)) : ''}</td>
        <td>${c.heldSlots.length ? esc(c.heldSlots.join(', ')) : '<span class="badge invariant">无</span>'}</td>
      </tr>`;
  }).join('');

  const fingerprints = new Set(audit.comparisons.map((c) => canonicalStringify({
    count: c.summary.count,
    totalAmount: c.summary.totalAmount,
    kinds: c.summary.kinds,
    errors: c.summary.errorCodes,
  })));
  const equal = fingerprints.size === 1;
  const hasDeclassified = audit.classes.some((cls) => cls.slots.some((s) => s.status === 'release-declassified'));
  const banner = hasDeclassified
    ? '<div class="timeline-item warn"><strong>该收件组存在经核准的去密差异。</strong> 对其他收件组，同一些槽位仍会被扣留。</div>'
    : equal
      ? '<div class="timeline-item ok"><strong>公共投影一致：</strong>两份记录的公开张数、金额、种类和错误回单摘要相同（不含公开案号/签章元数据）。</div>'
      : '<div class="timeline-item danger"><strong>公共投影不同：</strong>若没有核准标记，这不应进入公共端。</div>';

  $('comparisonTable').innerHTML = `
    ${banner}
    <div class="table-wrap">
      <table>
        <thead><tr><th>记录</th><th>相同公开输入(节选)</th><th>张数</th><th>总金额</th><th>单据种类</th><th>错误回单</th><th>扣留槽位</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderSlotAudit() {
  const audit = state.audit;
  if (!audit?.ok) {
    $('slotAudit').innerHTML = '<div class="empty">修复 JSON 后运行发行前检查。</div>';
    return;
  }

  $('slotAudit').innerHTML = audit.classes.map((cls) => `
    <div>
      <p class="hint">等价类公开输入指纹：<code>${esc(cls.publicKey.slice(0, 96))}${cls.publicKey.length > 96 ? '…' : ''}</code>；私有变体数=${cls.privateVariantCount}</p>
      ${cls.slots.map((slot) => `
        <div class="slot-card ${esc(slot.status)}">
          <div class="slot-title">
            <strong>${esc(slot.ruleId)}</strong>
            ${badgeForStatus(slot.status)}
          </div>
          <div class="slot-reason">${esc(slot.reason)}</div>
          <div class="hint">静态判定：${slot.staticInvariant ? '✅ ' : '⚠️ '}${esc(slot.staticReason ?? '')}</div>
          <div class="dep-list">reads: ${esc(slot.deps.join(', ') || '(none)')}</div>
          ${slot.approvalId ? `<div class="hint">适用核准：<strong>${esc(slot.approvalId)}</strong></div>` : ''}
          <div class="table-wrap">
            <table>
              <thead><tr><th>记录</th><th>该槽张数</th><th>金额合计</th><th>种类</th><th>错误码</th></tr></thead>
              <tbody>
                ${Object.entries(slot.observations).map(([id, obs]) => `
                  <tr>
                    <td>${esc(id)}</td>
                    ${obs.error ? `<td colspan="4" class="badge error">${esc(obs.error)}</td>` : `
                      <td class="num">${obs.count}</td>
                      <td class="num">${obs.totalAmount}</td>
                      <td>${esc(JSON.stringify(obs.kinds))}</td>
                      <td>${esc(JSON.stringify(obs.errorCodes))}</td>`}
                  </tr>`).join('')}
              </tbody>
            </table>
          </div>
        </div>`).join('')}
    </div>`).join('');
}

function findIssuedDoc(recordId, ruleId, copy) {
  const recipientGroup = $('recipientGroup')?.value ?? state.audit?.recipientGroup;
  const number = makeDocNumber({ template: state.template, recordId, ruleId, copy, recipientGroup });
  return state.issuedDocs.find((d) => d.docNumber === number)
    ?? state.issuedDocs.find((d) =>
      d.recordId === recordId
      && d.ruleId === ruleId
      && d.copy === copy
      && (!recipientGroup || d.recipientGroup === recipientGroup))
    ?? state.issuedDocs.find((d) => d.recordId === recordId && d.ruleId === ruleId && d.copy === copy)
    ?? null;
}

function docCard(doc, { privateDoc = false, showStationActions = false, redeemed = false } = {}) {
  if (!doc) return '';
  const type = doc.docType;
  const number = doc.docNumber ?? '(尚未签发编号)';
  return `
    <div class="doc-card ${privateDoc ? 'private-doc' : 'public-doc'} ${redeemed ? 'redeemed' : ''}">
      <div class="doc-title">${esc(docLabel(type))} <code>${esc(type)}</code></div>
      <div>金额：<strong>${esc(doc.amount ?? 0)}</strong>${doc.code !== null && doc.code !== undefined ? `｜回单代码：<strong>${esc(doc.code)}</strong>` : ''}</div>
      <div class="doc-meta">${esc(number)}</div>
      ${doc.templateVersion ? `<div class="doc-meta">v${esc(doc.templateVersion)}｜收件组=${esc(doc.recipientGroup)}${doc.approvalId ? `｜核准=${esc(doc.approvalId)}` : ''}</div>` : ''}
      <div class="doc-actions">
        ${doc.docNumber ? `<button class="tiny" data-action="present-doc" data-doc-number="${esc(doc.docNumber)}">客户出示此单</button>` : ''}
        ${showStationActions && doc.docNumber ? `<button class="tiny primary" data-action="redeem-number" data-doc-number="${esc(doc.docNumber)}">在本站直接兑换</button>` : ''}
        ${!showStationActions && doc.docNumber ? `<button class="tiny" data-action="transfer-doc" data-doc-number="${esc(doc.docNumber)}">拷入当前选中站</button>` : ''}
      </div>
    </div>`;
}

function renderHallRecords() {
  const audit = state.audit;
  if (!audit?.ok) {
    $('hallRecords').innerHTML = '<div class="empty">检查成功后显示逐笔产物。</div>';
    return;
  }

  $('hallRecords').innerHTML = `<div class="record-grid">${state.records.map((record) => {
    const plan = audit.plans[record.id];
    return `
      <div class="record-box">
        <h4><span>${esc(record.id)}</span><span class="badge neutral">受托端</span></h4>
        <details><summary class="hint">查看完整记录（含私有字段）</summary>
          <pre class="doc-meta">${esc(pretty(record))}</pre>
        </details>

        <h4>🔒 私有产物</h4>
        ${plan.privateArtifacts.length ? plan.privateArtifacts.map((p) => docCard(p, { privateDoc: true })).join('') : '<div class="empty">无私有产物</div>'}

        <h4>📤 已获准公开投影</h4>
        ${plan.release.length ? plan.release.map((sheet) => {
          const issued = findIssuedDoc(record.id, sheet.ruleId, sheet.copy);
          return issued ? docCard(issued) : docCard(sheet);
        }).join('') : '<div class="empty">没有可公开单据</div>'}

        <h4>⛔ 扣留/回馆槽位</h4>
        ${plan.holds.length ? plan.holds.map((h) => `
          <div class="timeline-item danger">
            <strong>${esc(h.ruleId)}</strong><br>${esc(h.reason)}
          </div>`).join('') : '<div class="empty">无扣留</div>'}
      </div>`;
  }).join('')}</div>`;
}

function renderApprovals() {
  $('approvalList').innerHTML = state.approvals.length
    ? state.approvals.map((a) => `
      <div class="approval-item">
        <strong>${esc(a.approvalId)}</strong> <span class="badge declassified">已签发</span>
        <div>字段：${(a.fields ?? []).map((f) => `<code>${esc(f)}</code>`).join(' ')}</div>
        <div>范围：模板 <code>${esc(a.templateId)}@${esc(a.templateVersion)}</code>｜收件组 <code>${esc(a.recipientGroup)}</code></div>
        <div class="doc-meta">${esc(a.issuedAt)}｜seal ${esc(String(a.seal ?? '').slice(0, 18))}…</div>
        ${a.note ? `<div class="hint">${esc(a.note)}</div>` : ''}
      </div>`).join('')
    : '<div class="empty">尚无独立核准单。</div>';
}

function renderIssuedRegistry() {
  if (!state.issuedDocs.length) {
    $('issuedRegistry').innerHTML = '<div class="empty">尚未实际签发单据。运行检查后点击“实际签发可放行单据”。</div>';
    return;
  }
  $('issuedRegistry').innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>单号/种类</th><th>记录</th><th>版本/收件组</th><th class="num">金额</th><th>核准</th><th>操作</th></tr></thead>
      <tbody>
        ${state.issuedDocs.map((d) => `
          <tr>
            <td><strong>${esc(docLabel(d.docType))}</strong><div class="doc-meta">${esc(d.docNumber)}</div></td>
            <td>${esc(d.recordId)}</td>
            <td><code>${esc(d.templateVersion)}</code><br><span class="doc-meta">${esc(d.recipientGroup)}</span></td>
            <td class="num">${esc(d.amount)}</td>
            <td>${d.approvalId ? `<span class="badge declassified">${esc(d.approvalId)}</span>` : '—'}</td>
            <td>
              <button class="tiny" data-action="present-doc" data-doc-number="${esc(d.docNumber)}">出示</button>
              <button class="tiny" data-action="transfer-doc" data-doc-number="${esc(d.docNumber)}">拷入选中站</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table></div>`;
}

function currentStation() {
  state.selectedStationId = $('stationSelect').value;
  return state.stations[state.selectedStationId];
}

function renderStation() {
  const station = currentStation();
  $('stationPolicy').innerHTML = `
    <div><strong>${esc(station.name)}</strong>（<code>${esc(station.id)}</code>） · 离线模式：${station.offline ? '是' : '否'}</div>
    <div class="mini-actions" style="margin-top:.45rem">
      <label style="flex:1">认可模板版本（逗号分隔）
        <input id="versionInput" type="text" value="${esc(station.acceptedTemplateVersions.join(', '))}" />
      </label>
      <button data-action="update-versions">更新版本策略</button>
    </div>`;

  const docs = Object.values(station.book).sort((a, b) => a.docNumber.localeCompare(b.docNumber));
  $('stationDocs').innerHTML = docs.length
    ? docs.map((d) => docCard(d, {
        showStationActions: true,
        redeemed: Boolean(station.redeemed[d.docNumber]),
      })).join('')
    : '<div class="empty">离线底册为空。馆内签发后，用“拷入”或 U 盘包装入。</div>';

  const logs = station.log;
  $('redemptionLog').innerHTML = logs.length
    ? logs.map((e) => `
      <div class="log-event ${esc(e.result)}">
        <div><span class="log-code">${esc(e.code)}</span> · ${esc(e.result === 'redeemed' ? '兑现成功' : '拒绝兑现')}</div>
        <div class="doc-meta">${esc(e.at)}｜${esc(e.docNumber ?? '(无单号)')}｜v${esc(e.templateVersion ?? '?')}｜${esc(e.recordId ?? '')}</div>
        <div>${esc(e.reason)}</div>
      </div>`).join('')
    : '<div class="empty">暂无兑现记录。</div>';
}

function renderAudit() {
  renderComparison();
  renderSlotAudit();
  renderHallRecords();
}

function renderAll() {
  renderTimeline();
  renderApprovals();
  renderAudit();
  renderIssuedRegistry();
  renderStation();
}

async function handleAction(action, dataset = {}) {
  switch (action) {
    case 'reset-all':
      state = freshState();
      syncStaticControls();
      state.audit = auditRelease({
        template: state.template,
        records: state.records,
        recipientGroup: 'public-desk',
      });
      renderAll();
      addTimeline('已重置', '载入两条自造记录和 v1 基线模板。', 'ok');
      break;

    case 'step-baseline':
      await stepBaseline();
      break;
    case 'step-issue-v1':
      await stepIssueV1();
      break;
    case 'step-approval-v2':
      await stepApprovalV2();
      break;
    case 'step-supplement':
      await stepSupplement();
      break;
    case 'step-restore':
      await stepRestore();
      break;
    case 'step-old-book':
      await stepOldBook();
      break;
    case 'run-full-scenario':
      await runFullScenario();
      break;

    case 'apply-template': {
      const t = readTemplateFromEditor();
      if (t) {
        state.template = t;
        $('templatePreset').value = 'custom';
        await runAudit({ announce: true });
      }
      break;
    }

    case 'apply-records': {
      const r = readRecordsFromEditor();
      if (r) {
        state.records = r;
        await runAudit({ announce: true });
      }
      break;
    }

    case 'toggle-supplement': {
      const t = readTemplateFromEditor();
      if (!t) break;
      const idx = t.rules.findIndex((r) => r.id === 'supplement');
      if (idx >= 0) t.rules.splice(idx, 1);
      else t.rules.push(makeFixedSupplementRule());
      state.template = t;
      $('templateEditor').value = pretty(t);
      $('templatePreset').value = 'custom';
      await runAudit();
      addTimeline(idx >= 0 ? '已移除补件分叉' : '已添加只给 A 的补件分叉', '无适用核准时，该槽位会在公共端整组扣留。', 'warn');
      break;
    }

    case 'fill-approval-v2':
      $('approvalId').value = seedApprovalForm.approvalId;
      $('approvalFields').value = seedApprovalForm.fieldsText;
      $('approvalTemplateId').value = seedApprovalForm.templateId;
      $('approvalTemplateVersion').value = seedApprovalForm.templateVersion;
      $('approvalRecipientGroup').value = seedApprovalForm.recipientGroup;
      $('approvalNote').value = seedApprovalForm.note;
      break;

    case 'issue-approval':
      await issueApprovalFromForm();
      break;

    case 'run-audit':
      await runAudit({ announce: true });
      break;

    case 'issue-docs':
      await issueCurrentDocs();
      break;

    case 'transfer-group-to-station': {
      const station = currentStation();
      const docs = state.issuedDocs.filter((d) => d.recipientGroup === station.id);
      state.stations[station.id] = addDocsToBook(station, docs);
      renderStation();
      addTimeline('离线底册已更新', `${station.name} 装入 ${docs.length} 张 ${station.id} 单据。`, 'ok');
      break;
    }

    case 'transfer-doc': {
      const station = currentStation();
      const doc = state.issuedDocs.find((d) => d.docNumber === dataset.docNumber)
        ?? Object.values(station.book).find((d) => d.docNumber === dataset.docNumber);
      if (!doc) break;
      state.stations[station.id] = addDocsToBook(station, [doc]);
      renderStation();
      addTimeline('单据已拷入离线底册', `${doc.docNumber} → ${station.name}`, 'ok');
      break;
    }

    case 'present-doc': {
      const doc = state.issuedDocs.find((d) => d.docNumber === dataset.docNumber)
        ?? Object.values(state.stations[state.selectedStationId]?.book ?? {}).find((d) => d.docNumber === dataset.docNumber)
        ?? Object.values(state.stations).flatMap((s) => Object.values(s.book)).find((d) => d.docNumber === dataset.docNumber);
      if (!doc) break;
      $('stationSelect').value = doc.recipientGroup;
      state.selectedStationId = doc.recipientGroup;
      $('presentedEditor').value = pretty(doc);
      renderStation();
      break;
    }

    case 'redeem-number': {
      const station = currentStation();
      const doc = station.book[dataset.docNumber];
      if (!doc) break;
      $('presentedEditor').value = pretty(doc);
      await redeemPresented();
      break;
    }

    case 'redeem-doc':
      await redeemPresented();
      break;

    case 'clear-presented':
      $('presentedEditor').value = '';
      break;

    case 'toggle-public-view':
      document.body.classList.toggle('public-view');
      break;

    case 'load-v1-book':
      loadV1BookForSelectedStation();
      break;

    case 'update-versions':
    case 'accept-current-versions': {
      const station = currentStation();
      const input = $('versionInput');
      const text = input ? input.value : station.acceptedTemplateVersions.join(',');
      station.acceptedTemplateVersions = text.split(',').map((x) => x.trim()).filter(Boolean);
      renderStation();
      addTimeline('站点版本策略已更新', station.acceptedTemplateVersions.join(', '), 'ok');
      break;
    }

    case 'export-package': {
      const station = currentStation();
      const pkg = {
        format: 'offline-book/v1',
        stationId: station.id,
        exportedAt: new Date().toISOString(),
        acceptedTemplateVersions: station.acceptedTemplateVersions,
        docs: Object.values(station.book),
      };
      $('packageEditor').value = pretty(pkg);
      addTimeline('已生成离线介质包', `含 ${pkg.docs.length} 个编号。`, 'ok');
      break;
    }

    case 'import-package': {
      const station = currentStation();
      try {
        const pkg = JSON.parse($('packageEditor').value);
        const docs = Array.isArray(pkg) ? pkg : pkg.docs;
        if (!Array.isArray(docs)) throw new Error('包内没有 docs 数组');
        state.stations[station.id] = addDocsToBook(station, docs);
        if (!Array.isArray(pkg) && Array.isArray(pkg.acceptedTemplateVersions)) {
          state.stations[station.id].acceptedTemplateVersions = pkg.acceptedTemplateVersions;
        }
        renderStation();
        addTimeline('离线包已导入', `${docs.length} 张单进入 ${station.name} 底册。`, 'ok');
      } catch (error) {
        addTimeline('离线包导入失败', error.message, 'danger');
      }
      break;
    }

    default:
      console.warn('未知动作', action);
  }
}

async function issueApprovalFromForm() {
  const fields = $('approvalFields').value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean);
  const form = {
    approvalId: $('approvalId').value.trim(),
    fields,
    templateId: $('approvalTemplateId').value.trim(),
    templateVersion: $('approvalTemplateVersion').value.trim(),
    recipientGroup: $('approvalRecipientGroup').value,
    note: $('approvalNote').value.trim(),
  };
  if (!form.approvalId || !form.templateId || !form.templateVersion || fields.length === 0) {
    addTimeline('核准单未签发', '核准单号、字段、模板 ID/版本和收件组均必填。', 'danger');
    return;
  }
  const approval = await issueApproval(form);
  state.approvals = state.approvals.filter((a) => a.approvalId !== approval.approvalId);
  state.approvals.push(approval);
  renderApprovals();
  await runAudit();
  addTimeline('独立核准席已签发决定', `${approval.approvalId}：${fields.join(', ')} → ${form.templateId}@${form.templateVersion} / ${form.recipientGroup}`, 'ok');
}

async function issueCurrentDocs() {
  const audit = state.audit ?? await runAudit();
  if (!audit?.ok) return [];
  const recipientGroup = $('recipientGroup').value;
  const docs = await issuePlanDocuments({
    audit,
    records: state.records,
    template: state.template,
    recipientGroup,
  });

  let added = 0;
  let existed = 0;
  const conflicts = [];
  for (const doc of docs) {
    const idx = state.issuedDocs.findIndex((d) => d.docNumber === doc.docNumber);
    if (idx === -1) {
      state.issuedDocs.push(doc);
      added += 1;
    } else {
      const old = state.issuedDocs[idx];
      const { seal: _s1, issuedAt: _i1, ...oldPayload } = old;
      const { seal: _s2, issuedAt: _i2, ...newPayload } = doc;
      if (canonicalStringify(oldPayload) === canonicalStringify(newPayload)) existed += 1;
      else conflicts.push(doc.docNumber);
    }
  }
  renderIssuedRegistry();
  renderHall();
  addTimeline(
    '公开投影已实际签发',
    `新增 ${added} 张，已存在 ${existed} 张${conflicts.length ? `；编号内容冲突 ${conflicts.length} 个，请升模板版本` : ''}。`,
    conflicts.length ? 'danger' : 'ok',
  );
  return docs;
}

async function redeemPresented() {
  const station = currentStation();
  let presented;
  try {
    presented = JSON.parse($('presentedEditor').value);
  } catch (error) {
    addTimeline('无法兑换', `出示物 JSON 无效：${error.message}`, 'danger');
    return null;
  }
  const result = await redeemDocument(station, presented);
  state.stations[station.id] = applyRedemption(station, result);
  renderStation();
  addTimeline(
    result.ok ? '站点兑现成功' : '站点拒绝兑现',
    `${result.event.docNumber ?? '(无单号)'} → ${result.code}：${result.reason}`,
    result.ok ? 'ok' : 'danger',
  );
  return result;
}

function loadV1BookForSelectedStation({ silent = false } = {}) {
  const station = currentStation();
  const v1Docs = state.issuedDocs.filter((d) => d.templateVersion === '1.0.0' && d.recipientGroup === station.id);
  station.book = {};
  state.stations[station.id] = addDocsToBook(station, v1Docs);
  state.stations[station.id].acceptedTemplateVersions = ['1.0.0'];
  renderStation();
  if (!silent) addTimeline('站点只认可上一版编号', `${station.name} 的底册仅含 ${v1Docs.length} 个 v1 编号。`, 'warn');
  return state.stations[station.id];
}

function loadPreset(key) {
  state.template = cloneJson(templatePresets[key]);
  $('templatePreset').value = key;
  $('templateEditor').value = pretty(state.template);
  $('templateValidation').textContent = '';
  $('templateValidation').className = 'validation';
}

async function stepBaseline() {
  state = freshState();
  syncStaticControls();
  loadPreset('baselineV1');
  $('recipientGroup').value = 'public-desk';
  state.audit = auditRelease({ template: state.template, records: state.records, recipientGroup: 'public-desk' });
  renderAll();
  const c = state.audit.comparisons;
  addTimeline('步骤 1：双记录 v1 比对', `A/B 均为 ${c[0].summary.count} 张、金额 ${c[0].summary.totalAmount}、错误回单 ${c[0].summary.errorCount}。`, 'ok');
}

async function stepIssueV1() {
  loadPreset('baselineV1');
  $('recipientGroup').value = 'public-desk';
  // 重放本步骤时给公开站一个干净的兑现状态，避免把“重复兑现”误读成办理失败。
  state.stations['public-desk'] = createStation({
    id: 'public-desk',
    name: '栖霞社区公开站',
    acceptedTemplateVersions: ['1.0.0'],
  });
  await runAudit();
  await issueCurrentDocs();

  // 同时给限定收件组签发一套 v1 编号；步骤 6 会让该站只退回这套旧底册。
  $('recipientGroup').value = 'priority-group';
  await issueCurrentDocs();
  $('recipientGroup').value = 'public-desk';

  const station = state.stations['public-desk'];
  const docs = state.issuedDocs.filter((d) => d.templateVersion === '1.0.0' && d.recipientGroup === 'public-desk');
  state.stations['public-desk'] = addDocsToBook(station, docs);
  $('stationSelect').value = 'public-desk';
  const feeA = docs.find((d) => d.recordId === 'CASE-A' && d.docType === 'PAYMENT_ORDER');
  $('presentedEditor').value = pretty(feeA);
  await redeemPresented();
  addTimeline('步骤 2：合格站办理', '私有条件两枝同形，v1 缴费单已在社区站成功兑现。', 'ok');
}

async function stepApprovalV2() {
  loadPreset('declassifiedV2');
  $('recipientGroup').value = 'priority-group';
  $('seatPresent').checked = true;
  $('approvalId').value = seedApprovalForm.approvalId;
  $('approvalFields').value = seedApprovalForm.fieldsText;
  $('approvalTemplateId').value = seedApprovalForm.templateId;
  $('approvalTemplateVersion').value = seedApprovalForm.templateVersion;
  $('approvalRecipientGroup').value = 'priority-group';
  await issueApprovalFromForm();
  await issueCurrentDocs();

  state.stations['priority-group'] = createStation({
    id: 'priority-group',
    name: '限定收件组离线站',
    acceptedTemplateVersions: ['1.0.0', '2.0.0'],
  });
  const docs = state.issuedDocs.filter((d) => d.templateVersion === '2.0.0' && d.recipientGroup === 'priority-group');
  const station = state.stations['priority-group'];
  state.stations['priority-group'] = addDocsToBook(station, docs);
  $('stationSelect').value = 'priority-group';
  state.selectedStationId = 'priority-group';
  const feeB = docs.find((d) => d.recordId === 'CASE-B' && d.docType === 'PAYMENT_ORDER');
  $('presentedEditor').value = pretty(feeB);
  renderStation();
  await redeemPresented();
  addTimeline('步骤 3：限定收件组去密', '核准单只覆盖 private.hardship + 2.0.0 + priority-group；B 的 80 元单可在该组兑现。', 'ok');
}

async function stepSupplement() {
  loadPreset('baselineV1');
  const t = state.template;
  t.rules.push(makeFixedSupplementRule());
  $('templateEditor').value = pretty(t);
  $('templatePreset').value = 'custom';
  $('recipientGroup').value = 'public-desk';
  $('seatPresent').checked = false; // 独立核准席缺席：不承认任何核准单
  await runAudit();
  await issueCurrentDocs();
  addTimeline('步骤 4：补件回条只出现在 A 的私有候选中', '核准席缺席；supplement 槽被整组扣留，公共 A/B 张数仍相同。', 'danger');
}

async function stepRestore() {
  loadPreset('baselineV1');
  $('recipientGroup').value = 'public-desk';
  $('seatPresent').checked = true;
  await runAudit();
  addTimeline('步骤 5：已移除补件分叉', '两边公共投影恢复一致。', 'ok');
}

async function stepOldBook() {
  loadPreset('declassifiedV2');
  $('seatPresent').checked = true;

  // 限定组站：A 的 v2 缴费金额仍是 120，与 v1 完全相同，但编号/版本是新的。
  $('recipientGroup').value = 'priority-group';
  await runAudit();
  await issueCurrentDocs();
  $('stationSelect').value = 'priority-group';
  state.selectedStationId = 'priority-group';
  loadV1BookForSelectedStation({ silent: true });

  const v2FeeA = state.issuedDocs.find((d) =>
    d.templateVersion === '2.0.0'
    && d.recipientGroup === 'priority-group'
    && d.recordId === 'CASE-A'
    && d.docType === 'PAYMENT_ORDER');
  $('presentedEditor').value = pretty(v2FeeA);
  renderStation();
  await redeemPresented();

  // 公开社区站：无核准的金额槽被扣留，但正确的 v2 受理回执也不能越过旧编号底册。
  $('recipientGroup').value = 'public-desk';
  await runAudit();
  await issueCurrentDocs();
  $('stationSelect').value = 'public-desk';
  state.selectedStationId = 'public-desk';
  loadV1BookForSelectedStation({ silent: true });

  const v2Acceptance = state.issuedDocs.find((d) =>
    d.templateVersion === '2.0.0'
    && d.recipientGroup === 'public-desk'
    && d.recordId === 'CASE-A'
    && d.docType === 'ACCEPTANCE_RECEIPT');
  $('presentedEditor').value = pretty(v2Acceptance);
  renderStation();
  await redeemPresented();

  addTimeline(
    '步骤 6：正确投影也不能越出离线底册',
    'priority-group 的 120 元 v2 缴费单和 public-desk 的 v2 受理回执均签章正确，但站点只认可上一版 v1 编号，因此都留下拒绝兑现记录。',
    'warn',
  );
}

async function runFullScenario() {
  await stepBaseline();
  await stepIssueV1();
  await stepApprovalV2();
  await stepSupplement();
  await stepRestore();
  await stepOldBook();
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  event.preventDefault();
  handleAction(button.dataset.action, button.dataset).catch((error) => {
    console.error(error);
    addTimeline('操作异常', error.message, 'danger');
  });
});

$('templatePreset').addEventListener('change', async (event) => {
  const key = event.target.value;
  if (key === 'custom') return;
  loadPreset(key);
  await runAudit();
});

$('stationSelect').addEventListener('change', () => {
  state.selectedStationId = $('stationSelect').value;
  renderStation();
});

$('recipientGroup').addEventListener('change', () => {
  runAudit();
});

$('seatPresent').addEventListener('change', () => {
  runAudit();
});

// 启动：先放一个已审计的 v1 状态，但不自动签发。
state.audit = auditRelease({ template: state.template, records: state.records, recipientGroup: 'public-desk' });
syncStaticControls();
renderAll();
addTimeline('界面就绪', '可点击“一键完整演示”，或从第 1 步开始。', 'ok');
