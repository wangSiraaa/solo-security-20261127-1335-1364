// 名册：仅追加的哈希链截止页。旧页只可被后续页引用，永不可改写。
// 每页由分库签名（本地受理依据）；独立见证人只对自己持有旧页的延长链作证；
// 跨分库并账产生双签合并页。
import {
  signJson,
  verifySignedJson,
  fingerprint,
  publicFromPrivate,
} from './crypto.mjs';
import { canonical, digestJson, pageId, nowIso, randomId } from './util.mjs';

const ENTRY_TYPES = new Set(['add', 'rotate', 'revoke']);

// 去掉随页附属物后的待签主体
export function pageBody(page) {
  const { signature, attestations, intakeReceipts, mergeProof, ...body } = page;
  return body;
}

function assertEntry(entry) {
  if (!ENTRY_TYPES.has(entry.type)) throw new Error(`未知名册条目类型: ${entry.type}`);
  if (!entry.name || typeof entry.name !== 'string') throw new Error('条目缺少收件名 name');
  if (!entry.signSpki || !entry.encSpki) throw new Error('条目缺少钥匙材料');
  if (entry.type !== 'revoke' && !entry.materialsKinds) throw new Error('条目缺少允收材料');
  if (entry.type === 'revoke' && entry.materialsKinds) throw new Error('撤销条目不携带允收材料');
}

export class Ledger {
  constructor(vaultId, signPrivatePkcs8, pages = []) {
    this.vaultId = vaultId;
    this.signPrivatePkcs8 = signPrivatePkcs8;
    this.pages = []; // 仅本库自己链上的页（含已双签并入的合并页）
    this.index = new Map(); // pageId -> page（本库链页）
    this.loose = new Map(); // 离线收存的外部页（验签通过但不在本库链上）
    this.vaultKeys = new Map();
    this.witnessKeys = new Map();
    for (const p of pages) this.ingest(p);
  }

  // ---------- 本地建页与受理 ----------
  // 本库续写一页：authorVault 签名即“本地见过并受理”的依据。
  append(entries, opts = {}) {
    for (const e of entries) assertEntry(e);
    const tip = this.tip;
    const page = {
      v: 1,
      branchId: this.branchId,
      seq: tip ? tip.seq + 1 : 0,
      prev: tip ? pageId(tip) : null,
      authorVault: this.vaultId,
      entries: entries.map((e) => ({ ...e, entryId: e.entryId ?? randomId('ent') })),
      ts: nowIso(),
    };
    if (opts.resolution) page.resolution = { resolves: opts.resolution };
    page.signature = signJson(this.signPrivatePkcs8, pageBody(page));
    page.attestations = [];
    page.intakeReceipts = [];
    this.pages.push(page);
    this.index.set(pageId(page), page);
    return page;
  }

  // 独立见证席：只在该页是自己所持旧页(basePageId)的延长时才签。
  // scopeNames 限定本次作证覆盖的收件名；不在范围内的条目不得援引该见证。
  attest(page, witnessId, witnessPrivatePkcs8, basePageId, scopeNames = null) {
    const id = pageId(page);
    if (!this.index.has(id)) throw new Error('见证前名册须先受理该页');
    if (!this.extendsFrom(page, basePageId)) {
      throw new Error(
        `见证席 ${witnessId} 拒签：页 ${id.slice(0, 10)} 接不到旧页 ${basePageId.slice(0, 10)}，属于另一条链`,
      );
    }
    const names = scopeNames ?? page.entries.map((e) => e.name);
    const signature = signJson(witnessPrivatePkcs8, {
      witnessId,
      basePageId,
      pageId: id,
      names: [...names].sort(),
      body: pageBody(page),
    });
    page.attestations.push({ witnessId, basePageId, names: [...names].sort(), signature });
    return signature;
  }

  // 分库签收一页：只表示本地见过，不构成跨库共同认可。
  markIntake(page, vaultId, signPrivatePkcs8) {
    const id = pageId(page);
    if (!this.index.has(id)) throw new Error('签收前须先 ingest 该页');
    const signature = signJson(signPrivatePkcs8, { kind: 'intake', vaultId, pageId: id });
    page.intakeReceipts.push({ vaultId, signature });
  }

  // 离线拿回来的页：完整验签后挂到本地链上（链断则报错交由 reconcile 处理）。
  ingest(page) {
    const id = pageId(page);
    if (this.index.has(id)) return page;
    this.#verifyPageSignatures(page);
    // 链连接（合并页允许两个 prev；前页可在本库链或已验签的 loose 区）
    const prevs = page.mergeProof ? page.prev : [page.prev];
    for (const prev of prevs) {
      if (prev !== null && !this.lookupPage(prev)) {
        const err = new Error(`页 ${id.slice(0, 10)} 的前页缺失，链断开（离线分叉，需对账）`);
        err.code = 'DISCONNECTED';
        err.page = page;
        throw err;
      }
    }
    if (page.mergeProof) this.#verifyMerge(page);
    this.index.set(id, page);
    this.pages.push(page);
    if (!this.branchId) this.branchId = page.branchId;
    return page;
  }

  // 宽松接收：链断的分叉页收进 loose 区，不污染本库链（验签仍必须通过）。
  ingestLoose(page) {
    const id = pageId(page);
    if (this.index.has(id)) return page;
    if (this.loose.has(id)) return page;
    this.#verifyPageSignatures(page);
    this.loose.set(id, page);
    return page;
  }

  #verifyPageSignatures(page) {
    const id = pageId(page);
    const authorKey = this.#authorKeyFor(page);
    if (!authorKey || !verifySignedJson(authorKey, pageBody(page), page.signature)) {
      throw new Error(`页 ${id.slice(0, 10)} 作者签名无效`);
    }
    for (const a of page.attestations ?? []) {
      const wk = this.witnessKeys?.get(a.witnessId);
      if (!wk || !verifySignedJson(wk, {
        witnessId: a.witnessId,
        basePageId: a.basePageId,
        pageId: id,
        names: a.names,
        body: pageBody(page),
      }, a.signature)) {
        throw new Error(`页 ${id.slice(0, 10)} 见证签名无效: ${a.witnessId}`);
      }
    }
  }

  // ---------- 链上查询 ----------
  get tip() {
    // 线性本库的末页（合并后仍为一条链）
    return this.pages[this.pages.length - 1] ?? null;
  }

  get genesis() {
    return this.pages.find((p) => p.prev === null && !p.mergeProof) ?? null;
  }

  extendsFrom(page, ancestorId) {
    let cur = page;
    const guard = new Set();
    while (cur) {
      const cid = pageId(cur);
      if (cid === ancestorId) return true;
      if (guard.has(cid)) return false;
      guard.add(cid);
      if (cur.mergeProof) return cur.prev.some((p) => p === ancestorId);
      if (cur.prev === null) return false;
      cur = this.lookupPage(cur.prev);
      if (!cur) return false;
    }
    return false;
  }

  // 沿线性链（合并页沿两条 parent 回溯）收集所有页
  chainPages(startPage = this.tip) {
    const out = [];
    const seen = new Set();
    const walk = (p) => {
      if (!p) return;
      const id = pageId(p);
      if (seen.has(id)) return;
      seen.add(id);
      out.push(p);
      if (p.mergeProof) p.prev.forEach((pid) => walk(this.lookupPage(pid)));
      else if (p.prev) walk(this.lookupPage(p.prev));
    };
    walk(startPage);
    return out;
  }

  // 本库链页与离线收存页统一按 id 取
  lookupPage(id) {
    return this.index.get(id) ?? this.loose.get(id) ?? null;
  }

  // ---------- 视图：把链折叠成当前名册 ----------
  // 规则：撤销永久黏住；同页之前的 rotate 被后续取代；分叉单侧新地址标记 conflict。
  view() {
    return new RosterView(this.chainPages());
  }

  // ---------- 离线对账 ----------
  // 与另一分库的链碰头：找共有最后一页，或产出双方分别可验真的分歧。
  reconcile(other) {
    // 只比较两条库链各自的页；loose 收存的对方页仅用于按 id 取页验签，
    // 不能让“我见过你的页”把对方链误算成共同链。
    const myChain = this.chainPages();
    const myIds = new Set(myChain.map(pageId));
    const shared = other
      .chainPages()
      .filter((p) => myIds.has(pageId(p)))
      .sort((a, b) => a.seq - b.seq);
    const commonBase = shared[shared.length - 1] ?? null;

    if (!commonBase) {
      return {
        kind: 'no-common-ancestor',
        // 双方各拿自己的创世页与签名，彼此独立验真
        myGenesis: this.genesis,
        theirGenesis: other.genesis,
      };
    }
    const baseId = pageId(commonBase);
    const mineOnly = this.chainPages()
      .filter((p) => this.extendsFrom(p, baseId) && pageId(p) !== baseId)
      .sort((a, b) => a.seq - b.seq);
    const theirsOnly = other
      .chainPages()
      .filter((p) => other.extendsFrom(p, baseId) && pageId(p) !== baseId)
      .sort((a, b) => a.seq - b.seq);
    if (theirsOnly.length === 0 && mineOnly.length === 0) {
      return { kind: 'in-sync', commonBase };
    }
    return {
      kind: 'fork',
      commonBase,
      commonBaseId: baseId,
      mineOnly,
      theirsOnly,
      // 每页都带作者签名，两库可分别用对方分库公钥独立验真
      verifiableByBoth: true,
    };
  }

  // 碰头后并账：撤销取并集（永久）；互不冲突的新地址合并；同名新钥冲突保留双分支待裁决。
  merge(other, theirVaultId, theirSignPublicSpki) {
    const report = this.reconcile(other);
    if (report.kind === 'in-sync') return { report, mergedPage: null };
    if (report.kind === 'no-common-ancestor') {
      throw new Error('无共有旧页，拒绝并账（各自验真创世页后人工处置）');
    }
    // 双方各自独立验真对方分叉页
    for (const p of report.theirsOnly) {
      if (!verifySignedJson(theirSignPublicSpki, pageBody(p), p.signature)) {
        throw new Error(`对方页 ${pageId(p).slice(0, 10)} 验签失败，拒绝并账`);
      }
      this.ingestLoose(p);
    }
    const myView = new RosterView(report.mineOnly, report.commonBase);
    const theirView = new RosterView(report.theirsOnly, report.commonBase);

    const mergedEntries = [];
    const conflicts = [];
    // 记录每个被搬运条目在原分支上的依据：仅本地签收的条目即便进了合并页也不得升级为共同认可
    const entryGrounding = new Map();
    const recordGrounding = (view) => {
      for (const name of view.names()) {
        const rec = view.lookup(name);
        entryGrounding.set(name, {
          attested: (rec.attestations ?? []).length > 0,
          originPageId: rec.pageId,
        });
      }
    };
    recordGrounding(myView);
    recordGrounding(theirView);
    const allNames = new Set([...myView.names(), ...theirView.names()]);
    for (const name of allNames) {
      const a = myView.lookup(name);
      const b = theirView.lookup(name);
      if (a && !b) mergedEntries.push(this.#ground(a.lastEntry, entryGrounding.get(name)));
      else if (b && !a) mergedEntries.push(this.#ground(b.lastEntry, entryGrounding.get(name)));
      else if (a && b) {
        // 撤销并集：任一分支撤了，合并后即撤，共同旧页也救不回来
        if (a.status === 'revoked' || b.status === 'revoked') {
          const live = a.status === 'revoked' ? b : a;
          if (live.status !== 'revoked') {
            mergedEntries.push({
              type: 'revoke',
              name,
              signSpki: live.lastEntry.signSpki,
              encSpki: live.lastEntry.encSpki,
              reason: '并账撤销并集',
              entryId: randomId('ent'),
            });
          }
          continue;
        }
        const sameKey =
          a.lastEntry.signSpki === b.lastEntry.signSpki &&
          a.lastEntry.encSpki === b.lastEntry.encSpki;
        if (sameKey) mergedEntries.push(a.lastEntry);
        else conflicts.push({ name, branchA: a.lastEntry, branchB: b.lastEntry });
      }
    }

    const tips = [pageId(this.tip), pageId(other.tip)];
    const page = {
      v: 1,
      branchId: this.branchId,
      seq: this.tip.seq + 1,
      prev: tips,
      mergeProof: {
        commonBaseId: report.commonBaseId,
        mergedFrom: [this.vaultId, theirVaultId],
        conflicts: conflicts.map((c) => ({
          name: c.name,
          aSignFp: fingerprint(c.branchA.signSpki),
          bSignFp: fingerprint(c.branchB.signSpki),
        })),
      },
      authorVault: this.vaultId,
      entries: mergedEntries,
      ts: nowIso(),
    };
    page.signature = signJson(this.signPrivatePkcs8, pageBody(page));
    page.attestations = [];
    page.intakeReceipts = [];
    this.pages.push(page);
    this.index.set(pageId(page), page);
    return { report, mergedPage: page, conflicts };
  }

  // 双签合并页：对方分库对同一合并页补签，才构成双方共同认可。
  countersignMerge(mergedPage, theirSignPrivatePkcs8) {
    if (!mergedPage.mergeProof) throw new Error('仅合并页可双签');
    const sig = signJson(theirSignPrivatePkcs8, {
      kind: 'merge-countersign',
      pageId: pageId(mergedPage),
      body: pageBody(mergedPage),
    });
    mergedPage.mergeProof.countersignatures ??= [];
    mergedPage.mergeProof.countersignatures.push({
      vaultId: mergedPage.mergeProof.mergedFrom[1],
      signature: sig,
    });
    return sig;
  }

  #verifyMerge(page) {
    const proof = page.mergeProof;
    if (!Array.isArray(page.prev) || page.prev.length !== 2) throw new Error('合并页必须有两个前页');
    const sigs = proof.countersignatures ?? [];
    if (sigs.length < 1) throw new Error('合并页缺少对方分库双签');
    const otherVault = proof.mergedFrom[1];
    const otherKey = this.vaultKeys?.get(otherVault);
    if (!otherKey) return; // 未配置对方公钥时只做结构校验
    const ok = sigs.some(
      (s) =>
        s.vaultId === otherVault &&
        verifySignedJson(otherKey, {
          kind: 'merge-countersign',
          pageId: pageId(page),
          body: pageBody(page),
        }, s.signature),
    );
    if (!ok) throw new Error('合并页双签验真失败');
  }

  // 把分支条目的原始依据（是否有独立见证）盖在合并条目上，防止合并页凭空升级
  #ground(entry, grounding) {
    if (!grounding) return entry;
    return { ...entry, groundedByWitness: grounding.attested, originPageId: grounding.originPageId };
  }

  #authorKeyFor(page) {
    if (page.authorVault === this.vaultId) {
      return publicFromPrivate(this.signPrivatePkcs8);
    }
    return this.vaultKeys?.get(page.authorVault) ?? null;
  }

  // 裁决同名冲突：追加一条决定页，明确选中的条目；由独立见证席见证后冲突解除。
  resolveConflict(name, chosenEntry) {
    assertEntry(chosenEntry);
    const entry = { ...chosenEntry, type: chosenEntry.type === 'add' ? 'rotate' : chosenEntry.type };
    return this.append([entry], { resolution: name });
  }

  toJSON() {
    return { vaultId: this.vaultId, pages: this.pages };
  }
}

// 名册视图：从一组页（可含共同基准页）折叠出每个收件名的当前状态。
export class RosterView {
  constructor(pages, basePage = null, conflictedNames = new Set()) {
    this.basePage = basePage;
    this.byName = new Map();
    this.conflictedNames = new Set(conflictedNames);
    const ordered = [...pages].sort((a, b) => a.seq - b.seq);
    if (basePage) this.#apply(basePage);
    for (const p of ordered) this.#apply(p);
    // 合并页自带的冲突清单：同名新钥未裁决前，即便某分支条目已上链也只能暂扣；
    // 后续带 resolution 的决定页把该名移出冲突集。
    for (const p of ordered) {
      for (const c of p.mergeProof?.conflicts ?? []) this.conflictedNames.add(c.name);
      if (p.resolution?.resolves) this.conflictedNames.delete(p.resolution.resolves);
    }
  }

  #apply(page) {
    const make = (e, status) => ({
      name: e.name,
      status,
      lastEntry: e,
      history: [...(this.byName.get(e.name)?.history ?? []), e],
      // 只采纳显式覆盖该收件名的见证
      attestations: (page.attestations ?? []).filter((a) => a.names.includes(e.name)),
      intakeReceipts: page.intakeReceipts ?? [],
      pageId: pageId(page),
      merged: Boolean(page.mergeProof),
    });
    for (const e of page.entries ?? []) {
      const cur = this.byName.get(e.name);
      if (e.type === 'add') {
        if (cur?.status === 'revoked') {
          // 撤销永久黏住：共同旧页之后任何同名 add 都不能让被撤钥复活
          continue;
        }
        this.byName.set(e.name, make(e, 'active'));
      } else if (e.type === 'rotate') {
        if (cur?.status === 'revoked') continue;
        this.byName.set(e.name, make(e, 'active'));
      } else if (e.type === 'revoke') {
        this.byName.set(e.name, make(e, 'revoked'));
      }
    }
  }

  names() {
    return this.byName.keys();
  }

  lookup(name) {
    return this.byName.get(name) ?? null;
  }

  // 寄送台决策：该收件名当前可否按此材料寄往。
  // 分叉单侧、仅有本地签收而无见证/双签依据的新地址一律暂扣。
  routingDecision(name, materialKind) {
    const rec = this.byName.get(name);
    if (!rec) return { name, decision: 'hold', reason: '名册无此收件名' };
    if (rec.status === 'revoked') {
      return { name, decision: 'block', reason: '收件钥已明文撤销，共同旧页亦不可使其恢复' };
    }
    if (this.conflictedNames.has(name)) {
      return { name, decision: 'hold', reason: '同名新钥存在双分支冲突，裁决前一律暂扣' };
    }
    const kinds = rec.lastEntry.materialsKinds ?? [];
    if (!kinds.includes(materialKind)) {
      return { name, decision: 'hold', reason: `当前钥不允收材料 ${materialKind}` };
    }
    const grounded =
      rec.merged && rec.lastEntry.groundedByWitness !== false
        ? true // 合并页条目：默认共同认可，除非显式记录原分支无见证
        : (rec.attestations && rec.attestations.length > 0);
    if (!grounded) {
      return { name, decision: 'hold', reason: '仅本地受理/签收，缺独立见证或双签合并依据，暂扣待对账' };
    }
    return { name, decision: 'send', reason: '共同认可且在任，允收该材料', entry: rec.lastEntry };
  }
}
