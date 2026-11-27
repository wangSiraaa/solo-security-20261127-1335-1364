import { api, qs, downloadJson, downloadText, fmtTime, short, toast } from '/common.js';

const $ = (id) => document.getElementById(id);
let pkg = null;
let award = null;

function resultTone(text = '') {
  if (/驳回|不能|不成立|失败|未通过|否定|不得/.test(text)) return 'bad';
  if (/有效|成立|通过|可查回|认定有效|来源成立/.test(text)) return 'ok';
  return 'warn';
}

async function loadPackage(nextPkg, autoArbitrate = true) {
  pkg = nextPkg;
  $('packageSummary').hidden = false;
  $('queryPanel').hidden = false;
  $('packageSummary').innerHTML = `
    <h2>证据包 ${pkg.packageId}</h2>
    <div class="kv">
      <dt>案由</dt><dd>${pkg.caseCaption}</dd>
      <dt>提交方</dt><dd>${pkg.preparedBy} / ${pkg.preparedByTerminal}</dd>
      <dt>生成时间</dt><dd>${fmtTime(pkg.generatedAt)}</dd>
      <dt>清单哈希</dt><dd class="hash">${pkg.manifest?.contentsHash ?? '(缺)'}</dd>
      <dt>材料数</dt><dd>${pkg.messages?.length ?? 0} 条消息；${pkg.corrections?.length ?? 0} 份订正；${pkg.receipts?.length ?? 0} 份回单；${pkg.agreements?.length ?? 0} 份约定</dd>
      <dt>双边钥</dt><dd>${pkg.session?.pairVerifier?.sharedSecret ? '包内附共享模拟钥，可验 HMAC，但不能唯一归属' : '未附共享钥，仅双方可核对'}</dd>
    </div>
    <div class="row" style="margin-top:12px">
      <button id="rerunBtn" class="primary">重新开庭判定</button>
      <button id="downloadPkg">下载证据包</button>
    </div>`;
  $('rerunBtn').addEventListener('click', runArbitration);
  $('downloadPkg').addEventListener('click', () => downloadJson(pkg, `${pkg.packageId}.json`));
  if (autoArbitrate) await runArbitration();
}

async function runArbitration() {
  $('loadStatus').textContent = '正在核验清单、HMAC、公鉴署名、引用哈希与上下文…';
  try {
    award = await api('/api/arbitrate', { body: { package: pkg } });
    window.history.replaceState(null, '', `/arbiter.html?awardId=${encodeURIComponent(award.awardId)}`);
    renderAward();
    $('loadStatus').textContent = `已出具 ${award.awardId}`;
  } catch (err) {
    $('loadStatus').textContent = err.message;
    toast(err.message);
  }
}

function renderAward() {
  $('awardPanel').hidden = false;
  const integrity = award.packageIntegrity;
  const scope = award.scope;
  $('award').innerHTML = `
    <div class="note ${integrity.valid ? 'ok' : 'bad'}">证据包清单完整性：${integrity.valid ? '通过，材料未在封包后变动' : `未通过：expected ${short(integrity.expected, 16)} / actual ${short(integrity.actual, 16)}`}</div>
    <div class="note info">${award.messageFindings.conclusion}</div>
    <h3>主文 / 具体判定</h3>
    ${award.holdings
      .map(
        (h) => `
      <article class="holding">
        <h3><span>${h.index}. ${h.title}</span><span class="result ${resultTone(h.result)}">${h.result}</span></h3>
        <p>${h.text}</p>
        ${h.details?.length ? `<ul>${h.details.map((d) => `<li>${d}</li>`).join('')}</ul>` : ''}
      </article>`,
      )
      .join('')}
    <h3>订单版本与相同字句隔离</h3>
    <div class="card">
      ${scope.orderStates
        .map(
          (o) => `<p><b>${o.orderId}</b> ${o.title}<br><span class="muted small">版本：${o.latestAgreementId}@v${o.latestVersion}；生效订正：${o.effectiveCorrectionIds.join('、') || '无'}${o.unchangedDespiteIdenticalWords ? '；<b>虽有相同字句，仍保持无关订单不被覆盖</b>' : ''}</span></p>`,
        )
        .join('')}
      ${scope.identicalWordWarnings.length ? `<ul>${scope.identicalWordWarnings.map((w) => `<li>${w.correctionId} 的“${short(w.quotedFrom, 24)}”不得覆盖 ${w.identicalTextAlsoAppearsIn}（${w.otherOrderId}）。</li>`).join('')}</ul>` : ''}
      <div class="note ${scope.unstartedBasis.supported ? 'warn' : 'bad'}">${scope.unstartedBasis.ruling}</div>
    </div>
    <h3>仲裁庭命令/确认事项</h3>
    <ul>${award.specificOrders.map((x) => `<li>${x}</li>`).join('')}</ul>
    <details style="margin-top:18px"><summary class="muted">查看裁决 JSON / Markdown 原文</summary><pre id="awardRaw">${JSON.stringify(award, null, 2).replace(/</g, '&lt;')}</pre></details>
  `;
  $('exportAwardJson').onclick = () => downloadJson(award, `${award.awardId}.json`);
  $('exportAwardMd').onclick = () => downloadText(award.markdown, `${award.awardId}.md`);
}

async function query() {
  const q = $('queryInput').value.trim();
  if (!q || !pkg) return toast('请先载入证据包并输入编号/哈希');
  const results = await api('/api/retrieve', { body: { package: pkg, query: q } });
  $('queryResults').innerHTML = results.length
    ? results
        .map(
          (r) => `<div class="card ${r.verified ? 'effective' : ''}">
            <div class="row" style="justify-content:space-between;gap:10px">
              <b>${r.type} · ${r.id}</b>
              <span class="badge ${r.verified ? 'ok' : 'bad'}">${r.verified ? '第三方公钥验签通过' : '验签失败/未验签'}</span>
            </div>
            <div class="muted small">${r.title ?? ''}</div>
            <div class="hash">${r.hash}</div>
            <p><b>状态：</b>${r.effective ? '生效或具相应事实效力' : '未生效；但不影响已验证的出处结论'}</p>
            <pre>${JSON.stringify(r.exactContent, null, 2)}</pre>
          </div>`,
        )
        .join('')
    : '<div class="note warn">未查回匹配的签署内容。</div>';
}

$('fileInput').addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  try {
    await loadPackage(JSON.parse(text));
  } catch (err) {
    toast('文件不是有效证据包 JSON：' + err.message);
  }
});
$('fetchPackageBtn').addEventListener('click', async () => {
  const id = $('packageIdInput').value.trim() || qs('packageId');
  if (!id) return toast('请输入 packageId');
  try {
    const p = await api(`/api/packages/${encodeURIComponent(id)}`);
    await loadPackage(p);
  } catch (err) {
    toast(err.message);
  }
});
document.querySelectorAll('.demo-pkg').forEach((btn) =>
  btn.addEventListener('click', async () => {
    try {
      $('loadStatus').textContent = `正在读取 ${btn.dataset.file} …`;
      const res = await fetch(btn.dataset.file);
      if (!res.ok) throw new Error('演示证据包尚未生成；请先运行 npm run demo');
      const p = await res.json();
      $('packageIdInput').value = p.packageId;
      await loadPackage(p);
    } catch (err) {
      $('loadStatus').textContent = err.message;
      toast(err.message);
    }
  }),
);
$('queryBtn').addEventListener('click', query);
$('queryInput').addEventListener('keydown', (e) => e.key === 'Enter' && query());

(async function init() {
  const awardId = qs('awardId');
  if (awardId) {
    try {
      award = await api(`/api/awards/${encodeURIComponent(awardId)}`);
      $('awardPanel').hidden = false;
      renderAward();
    } catch (err) {
      $('loadStatus').textContent = err.message;
    }
  }

  const id = qs('packageId');
  if (id) {
    $('packageIdInput').value = id;
    try {
      const p = await api(`/api/packages/${encodeURIComponent(id)}`);
      await loadPackage(p);
    } catch (err) {
      $('loadStatus').innerHTML = `不能从运行中会话读取 <code>${id}</code>：${err.message}<br>若你持有的是下载的 JSON，请直接上传。`;
    }
  }
  const stored = sessionStorage.getItem('latest-award');
  if (stored && !id) {
    try {
      award = JSON.parse(stored);
      // We do not have package necessarily; award can be shown minimally.
      $('awardPanel').hidden = false;
      $('award').innerHTML = `<div class="note ok">已从终端转入最近裁决：${award.awardId}</div><pre>${award.markdown.replace(/</g, '&lt;')}</pre>`;
      $('exportAwardJson').onclick = () => downloadJson(award, `${award.awardId}.json`);
      $('exportAwardMd').onclick = () => downloadText(award.markdown, `${award.awardId}.md`);
    } catch {}
  }
})();
