// 微型数据规则发布器核心。
// 规则树只有五种节点：int / field / cmp / if / doc。
// 本文件不访问网络、文件系统或子进程；浏览器与 Node 测试共用同一实现。

export const NODE_TYPES = new Set(['int', 'field', 'cmp', 'if', 'doc']);

export const COMPARE_OPS = new Set(['eq', 'ne', 'lt', 'le', 'gt', 'ge']);

export const AUDIENCES = new Set(['public', 'hall']);

export const DOC_LABELS = {
  ACCEPTANCE_RECEIPT: '受理回执',
  PAYMENT_ORDER: '缴费单',
  SUPPLEMENT_NOTICE: '公共补件回条',
  ERROR_RECEIPT: '错误回单',
  HALL_REVIEW_NOTE: '馆内复核单（私有）',
  HALL_CORRECTION_NOTE: '馆内校对单（私有）',
  DECLASSIFIED_NOTICE: '去密结果通知',
};

const OP_FN = {
  eq: (a, b) => a === b,
  ne: (a, b) => a !== b,
  lt: (a, b) => a < b,
  le: (a, b) => a <= b,
  gt: (a, b) => a > b,
  ge: (a, b) => a >= b,
};

export function isPlainData(value) {
  if (value === null || typeof value !== 'object') return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function pathJoin(path, key) {
  return path ? `${path}.${key}` : key;
}

export function validateNode(node, path = '$') {
  const errors = [];
  if (node === null || typeof node !== 'object' || Array.isArray(node)) {
    return [`${path}: 节点必须是 JSON 对象`];
  }
  if (!NODE_TYPES.has(node.t)) {
    errors.push(`${path}: 非法节点类型 ${JSON.stringify(node.t)}；只允许 int/field/cmp/if/doc`);
    return errors;
  }

  switch (node.t) {
    case 'int':
      if (!Number.isInteger(node.value)) errors.push(`${path}.value: 必须是整数`);
      break;

    case 'field':
      if (node.scope !== 'public' && node.scope !== 'private') {
        errors.push(`${path}.scope: 必须是 public 或 private`);
      }
      if (typeof node.name !== 'string' || !node.name.trim()) {
        errors.push(`${path}.name: 必须是非空字段名`);
      }
      break;

    case 'cmp':
      if (!COMPARE_OPS.has(node.op)) errors.push(`${path}.op: 不支持的比较算子 ${JSON.stringify(node.op)}`);
      errors.push(...validateNode(node.left, pathJoin(path, 'left')));
      errors.push(...validateNode(node.right, pathJoin(path, 'right')));
      break;

    case 'if':
      if (node.cond?.t !== 'cmp') errors.push(`${path}.cond: 条件选择的 cond 必须是 cmp 比较节点`);
      errors.push(...validateNode(node.cond, pathJoin(path, 'cond')));
      if (node.then !== null && node.then !== undefined) {
        errors.push(...validateNode(node.then, pathJoin(path, 'then')));
      }
      if (node.else !== null && node.else !== undefined) {
        errors.push(...validateNode(node.else, pathJoin(path, 'else')));
      }
      break;

    case 'doc':
      if (typeof node.docType !== 'string' || !node.docType.trim()) {
        errors.push(`${path}.docType: 必须是非空单据种类`);
      }
      if (node.amount !== undefined) errors.push(...validateNode(node.amount, pathJoin(path, 'amount')));
      if (node.copies !== undefined) errors.push(...validateNode(node.copies, pathJoin(path, 'copies')));
      if (node.code !== undefined) errors.push(...validateNode(node.code, pathJoin(path, 'code')));
      break;
  }
  return errors;
}

export function validateTemplate(template) {
  const errors = [];
  if (!isPlainData(template)) return { ok: false, errors: ['模板必须是 JSON 对象'] };
  if (typeof template.id !== 'string' || !template.id.trim()) errors.push('template.id 必须非空');
  if (typeof template.version !== 'string' || !template.version.trim()) errors.push('template.version 必须非空');
  if (!Array.isArray(template.rules) || template.rules.length === 0) {
    errors.push('template.rules 必须是非空数组');
    return { ok: false, errors };
  }

  const ids = new Set();
  template.rules.forEach((rule, i) => {
    const p = `rules[${i}]`;
    if (!isPlainData(rule)) {
      errors.push(`${p} 必须是对象`);
      return;
    }
    if (typeof rule.id !== 'string' || !rule.id.trim()) errors.push(`${p}.id 必须非空`);
    else if (ids.has(rule.id)) errors.push(`${p}.id 重复: ${rule.id}`);
    else ids.add(rule.id);

    if (rule.audience !== undefined && !AUDIENCES.has(rule.audience)) {
      errors.push(`${p}.audience 只能是 public 或 hall`);
    }
    errors.push(...validateNode(rule.node, `${p}.node`));
  });

  return { ok: errors.length === 0, errors };
}

export function validateRecords(records) {
  const errors = [];
  if (!Array.isArray(records) || records.length === 0) return { ok: false, errors: ['记录必须是非空数组'] };
  const ids = new Set();
  records.forEach((r, i) => {
    const p = `records[${i}]`;
    if (!isPlainData(r)) return errors.push(`${p} 必须是对象`);
    if (typeof r.id !== 'string' || !r.id.trim()) errors.push(`${p}.id 必须非空`);
    else if (ids.has(r.id)) errors.push(`${p}.id 重复`);
    else ids.add(r.id);
    for (const scope of ['public', 'private']) {
      if (!isPlainData(r[scope])) errors.push(`${p}.${scope} 必须是对象`);
      else {
        for (const [k, v] of Object.entries(r[scope])) {
          if (!Number.isInteger(v)) errors.push(`${p}.${scope}.${k} 必须是整数`);
        }
      }
    }
  });
  return { ok: errors.length === 0, errors };
}

function readField(node, record) {
  const bucket = record?.[node.scope];
  if (!isPlainData(bucket) || !Object.prototype.hasOwnProperty.call(bucket, node.name)) {
    throw new Error(`缺少${node.scope === 'private' ? '私有' : '公开'}字段 ${node.name}`);
  }
  const value = bucket[node.name];
  if (!Number.isInteger(value)) throw new Error(`字段 ${node.scope}.${node.name} 不是整数`);
  return value;
}

// 同步解释器。任何非数据节点都会在 validateNode 阶段被拒。
export function evalNode(node, record) {
  if (node === null || node === undefined) return null;
  switch (node.t) {
    case 'int':
      if (!Number.isInteger(node.value)) throw new Error('int 节点不是整数');
      return node.value;

    case 'field':
      return readField(node, record);

    case 'cmp': {
      const a = evalNode(node.left, record);
      const b = evalNode(node.right, record);
      if (typeof a !== 'number' || typeof b !== 'number') throw new Error('比较两端必须是整数');
      const fn = OP_FN[node.op];
      if (!fn) throw new Error(`不支持的比较算子 ${node.op}`);
      return fn(a, b);
    }

    case 'if': {
      const cond = evalNode(node.cond, record);
      if (typeof cond !== 'boolean') throw new Error('条件节点必须求出布尔值');
      const chosen = cond ? node.then : node.else;
      return chosen === undefined ? null : evalNode(chosen, record);
    }

    case 'doc': {
      const amount = node.amount === undefined ? 0 : evalNode(node.amount, record);
      const copies = node.copies === undefined ? 1 : evalNode(node.copies, record);
      const code = node.code === undefined ? null : evalNode(node.code, record);
      if (!Number.isInteger(amount)) throw new Error('单据金额不是整数');
      if (!Number.isInteger(copies) || copies < 0) throw new Error('单据张数必须是非负整数');
      if (code !== null && !Number.isInteger(code)) throw new Error('错误回单代码不是整数');
      return {
        t: 'doc',
        docType: node.docType,
        amount,
        copies,
        code,
      };
    }

    default:
      throw new Error(`拒绝执行非法节点类型: ${JSON.stringify(node?.t)}`);
  }
}

export function collectFieldReads(node, out = []) {
  if (!node || typeof node !== 'object') return [...new Set(out)];
  if (node.t === 'field') {
    out.push(`${node.scope}.${node.name}`);
  }
  for (const key of ['left', 'right', 'cond', 'then', 'else', 'amount', 'copies', 'code']) {
    if (node[key] && typeof node[key] === 'object') collectFieldReads(node[key], out);
  }
  return [...new Set(out)];
}

export function canonicalStringify(value) {
  const seen = new WeakSet();
  return JSON.stringify(value, (key, val) => {
    if (typeof val === 'bigint') return val.toString();
    if (val && typeof val === 'object') {
      if (seen.has(val)) throw new Error('不能规范化循环对象');
      seen.add(val);
      if (!Array.isArray(val)) {
        return Object.fromEntries(Object.entries(val).sort(([a], [b]) => a.localeCompare(b)));
      }
    }
    return val;
  });
}

export function publicKey(record) {
  return canonicalStringify(record.public ?? {});
}

export function privateKey(record) {
  return canonicalStringify(record.private ?? {});
}

// 把一张 doc 规格展开成实际张数。copy 从 1 开始。
export function expandSheets(spec, ruleId) {
  const sheets = [];
  for (let copy = 1; copy <= spec.copies; copy += 1) {
    sheets.push({
      ruleId,
      copy,
      docType: spec.docType,
      amount: spec.amount,
      code: spec.code,
    });
  }
  return sheets;
}

export function summarizeSheets(sheets) {
  const list = Array.isArray(sheets) ? sheets : [];
  const kinds = {};
  const errors = [];
  let totalAmount = 0;
  for (const s of list) {
    kinds[s.docType] = (kinds[s.docType] ?? 0) + 1;
    totalAmount += Number.isInteger(s.amount) ? s.amount : 0;
    if (s.docType === 'ERROR_RECEIPT') errors.push(s.code);
  }
  return {
    count: list.length,
    totalAmount,
    kinds,
    errorCount: errors.length,
    errorCodes: errors,
  };
}

// 公共观察指纹：不含单号、时间、签章等每次签发元数据。
// 这些元数据是否可关联由离线底册/兑换策略单独控制。
export function observationFingerprint(sheets) {
  const obs = (sheets ?? []).map((s) => ({
    docType: s.docType,
    amount: s.amount,
    code: s.code,
    copy: s.copy,
  }));
  return canonicalStringify(obs);
}

/*
 * 对固定公开输入做抽象可见性判断。
 *
 * 返回 { invariant:true, shape } 表示：无论 private 字段取什么整数，
 * 该表达式的公共可观察形状都相同。返回 invariant:false 时给出原因和私有依赖。
 *
 * 这是保守但可执行的判定：只覆盖本微型规则语言，不声称能判定任意程序。
 */
export function publicShape(node, publicInput = {}) {
  if (node === null || node === undefined) {
    return { invariant: true, shape: { kind: 'null' }, deps: [] };
  }

  const deps = collectFieldReads(node).filter((f) => f.startsWith('private.'));

  switch (node.t) {
    case 'int':
      return { invariant: true, shape: { kind: 'int', value: node.value }, deps: [] };

    case 'field':
      if (node.scope === 'public') {
        const v = publicInput?.[node.name];
        if (!Number.isInteger(v)) {
          return { invariant: false, reason: `公开字段 ${node.name} 缺失或不是整数`, deps: [] };
        }
        return { invariant: true, shape: { kind: 'int', value: v }, deps: [] };
      }
      return { invariant: false, reason: `私有字段 ${node.name} 直接影响观察`, deps };

    case 'cmp': {
      const left = publicShape(node.left, publicInput);
      const right = publicShape(node.right, publicInput);

      if (
        left.invariant && right.invariant &&
        left.shape.kind === 'int' && right.shape.kind === 'int'
      ) {
        return {
          invariant: true,
          shape: { kind: 'bool', value: OP_FN[node.op](left.shape.value, right.shape.value) },
          deps: [],
        };
      }

      // 恒等比较：x eq x / x le x / x ge x 恒真；ne/lt/gt 恒假，即使 x 读私有字段。
      if (canonicalStringify(node.left) === canonicalStringify(node.right)) {
        const alwaysTrue = node.op === 'eq' || node.op === 'le' || node.op === 'ge';
        return { invariant: true, shape: { kind: 'bool', value: alwaysTrue }, deps };
      }

      return {
        invariant: false,
        reason: '比较结果可能随私有字段变化',
        deps,
      };
    }

    case 'if': {
      const cond = publicShape(node.cond, publicInput);
      if (cond.invariant && cond.shape.kind === 'bool') {
        return publicShape(cond.shape.value ? node.then : node.else, publicInput);
      }

      const thenShape = publicShape(node.then, publicInput);
      const elseShape = publicShape(node.else, publicInput);
      if (
        thenShape.invariant && elseShape.invariant &&
        canonicalStringify(thenShape.shape) === canonicalStringify(elseShape.shape)
      ) {
        return {
          invariant: true,
          shape: thenShape.shape,
          deps: [...new Set([...(thenShape.deps ?? []), ...(elseShape.deps ?? [])])],
          reason: '私有条件的两个分支给出完全相同的公共形状',
        };
      }

      return {
        invariant: false,
        reason: '私有条件的两个分支可能产生不同种类、张数、金额或错误回单',
        deps,
      };
    }

    case 'doc': {
      const amount = node.amount === undefined
        ? { invariant: true, shape: { kind: 'int', value: 0 } }
        : publicShape(node.amount, publicInput);
      const copies = node.copies === undefined
        ? { invariant: true, shape: { kind: 'int', value: 1 } }
        : publicShape(node.copies, publicInput);
      const code = node.code === undefined
        ? { invariant: true, shape: { kind: 'int', value: null } }
        : publicShape(node.code, publicInput);

      if (!amount.invariant || !copies.invariant || !code.invariant) {
        return { invariant: false, reason: '单据金额、张数或错误代码可能随私有字段变化', deps };
      }
      if (copies.shape.kind !== 'int' || copies.shape.value < 0) {
        return { invariant: false, reason: '张数形状非法', deps };
      }

      return {
        invariant: true,
        shape: {
          kind: 'doc',
          docType: node.docType,
          amount: amount.shape.value,
          copies: copies.shape.value,
          code: code.shape.kind === 'int' ? code.shape.value : null,
        },
        deps: [],
      };
    }

    default:
      return { invariant: false, reason: `非法节点 ${JSON.stringify(node?.t)}`, deps };
  }
}

export function findApplicableApproval(approvals, { template, recipientGroup, privateDeps, ignoreApprovals = false }) {
  if (ignoreApprovals) return null;
  const deps = new Set(privateDeps ?? []);
  return (approvals ?? []).find((a) => {
    if (a?.status && a.status !== 'issued') return false;
    if (a?.templateId !== template.id || a?.templateVersion !== template.version) return false;
    if (a?.recipientGroup !== recipientGroup) return false;
    const fields = new Set(a.fields ?? []);
    return [...deps].every((d) => fields.has(d));
  }) ?? null;
}

function evaluateRuleForRecord(rule, record) {
  try {
    const value = evalNode(rule.node, record);
    if (value === null || value === undefined) return { ok: true, sheets: [] };
    if (value?.t !== 'doc') {
      return { ok: false, error: '规则根节点必须求出 doc 或空枝' };
    }
    return { ok: true, sheets: expandSheets(value, rule.id) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

/*
 * 发行前审计。
 *
 * 对公开输入相同的记录按 ruleId 比较完整单据观察。未通过的公共槽位：
 * - 有覆盖全部私有依赖、模板版本和收件组的核准单 => declassified，可对该组发行；
 * - 否则 hold，并从该等价类的所有记录中扣留同一槽位，避免只给一个分支添单。
 *
 * audience=hall 的规则只进 privateArtifacts，永不进入公共计划。
 */
export function auditRelease({
  template,
  records,
  approvals = [],
  recipientGroup = 'public-desk',
  ignoreApprovals = false,
}) {
  const tplCheck = validateTemplate(template);
  const recCheck = validateRecords(records);
  if (!tplCheck.ok || !recCheck.ok) {
    return {
      ok: false,
      templateErrors: tplCheck.errors,
      recordErrors: recCheck.errors,
      classes: [],
      plans: {},
    };
  }

  const groups = new Map();
  for (const record of records) {
    const key = publicKey(record);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }

  const plans = {};
  const classes = [];

  for (const [key, groupRecords] of groups) {
    const privateVariants = new Set(groupRecords.map(privateKey));
    const classResult = {
      publicKey: key,
      recordIds: groupRecords.map((r) => r.id),
      privateVariantCount: privateVariants.size,
      slots: [],
    };

    for (const record of groupRecords) {
      plans[record.id] = plans[record.id] ?? {
        release: [],
        holds: [],
        privateArtifacts: [],
        evalErrors: [],
        recipientGroup,
      };
    }

    for (const rule of template.rules) {
      if (rule.audience === 'hall') {
        for (const record of groupRecords) {
          const result = evaluateRuleForRecord(rule, record);
          if (result.ok) {
            plans[record.id].privateArtifacts.push(...result.sheets);
          } else {
            plans[record.id].evalErrors.push({ ruleId: rule.id, error: result.error, audience: 'hall' });
          }
        }
        continue;
      }

      const byRecord = {};
      let evalError = null;
      for (const record of groupRecords) {
        const result = evaluateRuleForRecord(rule, record);
        if (!result.ok) {
          evalError = result.error;
          byRecord[record.id] = { error: result.error };
          plans[record.id].evalErrors.push({ ruleId: rule.id, error: result.error, audience: 'public' });
        } else {
          byRecord[record.id] = result.sheets;
        }
      }

      const deps = collectFieldReads(rule.node);
      const privateDeps = deps.filter((d) => d.startsWith('private.'));
      const firstPublic = groupRecords[0].public;
      const shape = publicShape(rule.node, firstPublic);
      const fingerprints = new Set(
        groupRecords.map((r) => evalError ? 'EVAL_ERROR' : observationFingerprint(byRecord[r.id])),
      );
      const pairEqual = !evalError && fingerprints.size === 1;
      const approval = findApplicableApproval(approvals, {
        template,
        recipientGroup,
        privateDeps,
        ignoreApprovals,
      });

      let status;
      let reason;
      if (evalError) {
        status = 'eval-error';
        reason = `求值失败，失败关闭并回馆处理：${evalError}`;
      } else if (pairEqual && shape.invariant) {
        status = 'release-invariant';
        reason = shape.reason ?? '抽象判定：公共形状不依赖私有字段';
      } else if (pairEqual && privateVariants.size >= 2) {
        status = 'release-pair-equivalent';
        reason = '本批不同私有值的实际观察恰好相同；可由受托端逐笔签发，但不是所有私有取值的通用安全证明';
      } else if (approval) {
        status = 'release-declassified';
        reason = `核准单 ${approval.approvalId} 已指定字段/模板版本/收件组，可对 ${recipientGroup} 放行该私有依赖槽位`;
      } else if (pairEqual) {
        status = 'hold-unproven';
        reason = '观察相同，但缺少不同私有值对照且静态形状不能保证一致；保留在信任边界内，或取得范围匹配的核准';
      } else {
        status = 'hold-divergent';
        reason = ignoreApprovals
          ? '核准席缺席：公共观察随未公开字段变化，扣留该槽位且不向外显示差异'
          : '公共观察随未公开字段变化，且没有覆盖字段、模板版本和收件组的核准单';
      }

      const slot = {
        ruleId: rule.id,
        status,
        reason,
        deps,
        privateDeps,
        staticInvariant: shape.invariant,
        staticReason: shape.invariant ? (shape.reason ?? '静态公共形状不变') : shape.reason,
        approvalId: approval?.approvalId ?? null,
        pairEqual,
        observations: Object.fromEntries(
          Object.entries(byRecord).map(([id, sheets]) => [
            id,
            Array.isArray(sheets) ? summarizeSheets(sheets) : { error: sheets.error },
          ]),
        ),
      };
      classResult.slots.push(slot);

      const canRelease = status === 'release-invariant'
        || status === 'release-pair-equivalent'
        || status === 'release-declassified';

      for (const record of groupRecords) {
        if (canRelease && !evalError) {
          for (const sheet of byRecord[record.id]) {
            plans[record.id].release.push({ ...sheet, declassification: approval?.approvalId ?? null });
          }
        } else {
          plans[record.id].holds.push({
            ruleId: rule.id,
            status,
            reason,
            approvalId: approval?.approvalId ?? null,
          });
        }
      }
    }

    classes.push(classResult);
  }

  const comparisons = records.map((r) => ({
    recordId: r.id,
    publicKey: publicKey(r),
    privateKey: privateKey(r),
    summary: summarizeSheets(plans[r.id].release),
    heldSlots: plans[r.id].holds.map((h) => h.ruleId),
    privateCount: plans[r.id].privateArtifacts.length,
  }));

  return { ok: true, classes, plans, comparisons, recipientGroup };
}

export async function sha256Hex(value) {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.subtle) throw new Error('当前运行时没有 crypto.subtle，无法模拟签章');
  const bytes = new TextEncoder().encode(canonicalStringify(value));
  const digest = await cryptoApi.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function sealPayload(payload) {
  return sha256Hex(payload);
}

// 先移除可能存在的旧 seal，再对规范化 payload 计算模拟签章。
export async function sealDocument(doc) {
  const { seal: _oldSeal, ...payload } = doc;
  return { ...doc, seal: await sha256Hex(payload) };
}

export async function verifyDocumentSeal(doc) {
  if (!doc || typeof doc.seal !== 'string') return false;
  const { seal, ...payload } = doc;
  const expected = await sha256Hex(payload);
  return expected === seal;
}

export function makeDocNumber({ template, recordId, ruleId, copy, recipientGroup = '' }) {
  const slug = (s) => String(s).replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 24) || 'x';
  const groupPart = recipientGroup ? `${slug(recipientGroup)}-` : '';
  return `GN-${slug(template.id)}-${slug(template.version)}-${groupPart}${slug(recordId)}-${slug(ruleId)}-${copy}`;
}

export async function issuePlanDocuments({ audit, records, template, recipientGroup, issuedAt = new Date().toISOString() }) {
  const issued = [];
  for (const record of records) {
    const plan = audit.plans[record.id];
    if (!plan) continue;
    for (const sheet of plan.release) {
      const docNumber = makeDocNumber({
        template,
        recordId: record.id,
        ruleId: sheet.ruleId,
        copy: sheet.copy,
        recipientGroup,
      });
      const payload = {
        docNumber,
        recordId: record.id,
        templateId: template.id,
        templateVersion: template.version,
        ruleId: sheet.ruleId,
        copy: sheet.copy,
        docType: sheet.docType,
        amount: sheet.amount,
        code: sheet.code,
        recipientGroup,
        approvalId: sheet.declassification ?? null,
        issuedAt,
        issuer: 'trusted-hall',
      };
      issued.push(await sealDocument(payload));
    }
  }
  return issued;
}

export function createStation({
  id = 'public-desk',
  name = '公开办事台/社区站',
  acceptedTemplateVersions = ['1.0.0'],
} = {}) {
  return {
    id,
    name,
    offline: true,
    acceptedTemplateVersions: [...acceptedTemplateVersions],
    book: {},
    redeemed: {},
    log: [],
  };
}

export function addDocsToBook(station, docs) {
  const next = structuredClone(station);
  for (const doc of docs) {
    next.book[doc.docNumber] = structuredClone(doc);
  }
  return next;
}

export async function redeemDocument(station, presented, at = new Date().toISOString()) {
  const baseEvent = {
    at,
    stationId: station.id,
    stationName: station.name,
    docNumber: presented?.docNumber ?? null,
    templateVersion: presented?.templateVersion ?? null,
    recordId: presented?.recordId ?? null,
    docType: presented?.docType ?? null,
    amount: presented?.amount ?? null,
  };

  const reject = (code, reason) => ({
    ok: false,
    code,
    reason,
    event: { ...baseEvent, result: 'rejected', code, reason },
  });

  if (!isPlainData(presented) || typeof presented.docNumber !== 'string') {
    return reject('MALFORMED_DOCUMENT', '出示物不是有效单据数据');
  }

  const known = station.book[presented.docNumber];
  if (!known) {
    return reject('UNKNOWN_DOC_NUMBER', '单据编号不在本站离线签发底册；投影即使正确也不能兑换');
  }

  let sealOk = false;
  try {
    sealOk = await verifyDocumentSeal(presented);
  } catch {
    sealOk = false;
  }
  if (!sealOk) return reject('BAD_SEAL', '单据签章校验失败');

  const { seal: _pSeal, ...presentedPayload } = presented;
  const { seal: _kSeal, ...knownPayload } = known;
  if (canonicalStringify(presentedPayload) !== canonicalStringify(knownPayload)) {
    return reject('PROJECTION_MISMATCH', '投影内容与离线底册不一致');
  }

  if (presented.recipientGroup && station.id !== presented.recipientGroup) {
    return reject('RECIPIENT_GROUP_MISMATCH', `单据限定收件组 ${presented.recipientGroup}，本站是 ${station.id}`);
  }

  if (!station.acceptedTemplateVersions.includes(presented.templateVersion)) {
    return reject(
      'VERSION_NOT_ACCEPTED',
      `本站只认可版本 ${station.acceptedTemplateVersions.join(', ')} 的单据编号`,
    );
  }

  if (station.redeemed[presented.docNumber]) {
    return reject('ALREADY_REDEEMED', `该单已于 ${station.redeemed[presented.docNumber].at} 兑现`);
  }

  return {
    ok: true,
    code: 'REDEEMED',
    reason: '编号、签章、版本与收件组均匹配',
    event: { ...baseEvent, result: 'redeemed', code: 'REDEEMED', reason: '兑现成功' },
  };
}

export function applyRedemption(station, result) {
  const next = structuredClone(station);
  next.log.unshift(result.event);
  if (result.ok) {
    next.redeemed[result.event.docNumber] = { at: result.event.at, amount: result.event.amount };
  }
  return next;
}

export function issueApproval(form, at = new Date().toISOString()) {
  const approval = {
    status: 'issued',
    approvalId: form.approvalId,
    kind: 'declassification-decision',
    fields: [...(form.fields ?? [])],
    templateId: form.templateId,
    templateVersion: form.templateVersion,
    recipientGroup: form.recipientGroup,
    issuedBy: 'independent-approval-seat',
    issuedAt: at,
    note: form.note ?? '',
  };
  return sealDocument(approval);
}
