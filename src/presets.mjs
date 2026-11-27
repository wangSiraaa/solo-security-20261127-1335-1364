// 自造演示数据。所有规则字段均为整数，避免引入字符串表达式或算术节点。

export const seedRecords = [
  {
    id: 'CASE-A',
    public: {
      formCode: 7,
      baseFee: 120,
      copies: 1,
    },
    private: {
      reviewLevel: 5,
      correction: 0,
      hardship: 2,
    },
  },
  {
    id: 'CASE-B',
    public: {
      formCode: 7,
      baseFee: 120,
      copies: 1,
    },
    private: {
      reviewLevel: 2,
      correction: 7,
      hardship: 9,
    },
  },
];

const acceptanceRule = {
  id: 'acceptance',
  audience: 'public',
  node: {
    t: 'doc',
    docType: 'ACCEPTANCE_RECEIPT',
    amount: { t: 'int', value: 0 },
  },
};

const publicFeeInvariantRule = {
  id: 'fee',
  audience: 'public',
  node: {
    t: 'doc',
    docType: 'PAYMENT_ORDER',
    amount: {
      t: 'if',
      cond: {
        t: 'cmp',
        op: 'gt',
        left: { t: 'field', scope: 'private', name: 'reviewLevel' },
        right: { t: 'int', value: 3 },
      },
      // 条件读私有字段，但两个分支的公共形状完全相同：保证不可区分。
      then: { t: 'field', scope: 'public', name: 'baseFee' },
      else: { t: 'field', scope: 'public', name: 'baseFee' },
    },
    copies: { t: 'field', scope: 'public', name: 'copies' },
  },
};

const publicValidationRule = {
  id: 'public-validation',
  audience: 'public',
  node: {
    t: 'if',
    cond: {
      t: 'cmp',
      op: 'lt',
      left: { t: 'field', scope: 'public', name: 'baseFee' },
      right: { t: 'int', value: 0 },
    },
    then: {
      t: 'doc',
      docType: 'ERROR_RECEIPT',
      amount: { t: 'int', value: 0 },
      code: { t: 'int', value: 4001 },
    },
    else: null,
  },
};

const privateProofRule = {
  id: 'private-proof',
  audience: 'hall',
  node: {
    t: 'if',
    cond: {
      t: 'cmp',
      op: 'ge',
      left: { t: 'field', scope: 'private', name: 'reviewLevel' },
      right: { t: 'int', value: 4 },
    },
    then: {
      t: 'doc',
      docType: 'HALL_REVIEW_NOTE',
      amount: { t: 'field', scope: 'private', name: 'reviewLevel' },
    },
    else: {
      t: 'doc',
      docType: 'HALL_CORRECTION_NOTE',
      amount: { t: 'field', scope: 'private', name: 'correction' },
    },
  },
};

export const baselineV1 = {
  id: 'community-fee',
  version: '1.0.0',
  description: '基线版：含私有复核条件，但公共金额/种类/张数/错误回单不变',
  rules: [
    acceptanceRule,
    publicFeeInvariantRule,
    publicValidationRule,
    privateProofRule,
  ],
};

// 这个版本在极端私有取值下可能分叉；A/B 两条自造记录恰好都得到 120。
// 它用来说明“本对记录恰好相同”不等于可把规则安全下放给所有私有取值。
export const coincidenceV11 = {
  id: 'community-fee',
  version: '1.1.0-accidental',
  description: '偶然同额版：A/B 恰好同额，但静态抽象判定不保证所有私有值一致',
  rules: [
    acceptanceRule,
    {
      id: 'fee',
      audience: 'public',
      node: {
        t: 'doc',
        docType: 'PAYMENT_ORDER',
        amount: {
          t: 'if',
          cond: {
            t: 'cmp',
            op: 'gt',
            left: { t: 'field', scope: 'private', name: 'reviewLevel' },
            right: { t: 'int', value: 3 },
          },
          then: {
            t: 'if',
            cond: {
              t: 'cmp',
              op: 'gt',
              left: { t: 'field', scope: 'private', name: 'reviewLevel' },
              right: { t: 'int', value: 900 },
            },
            then: { t: 'int', value: 0 },
            else: { t: 'int', value: 120 },
          },
          else: { t: 'int', value: 120 },
        },
        copies: { t: 'int', value: 1 },
      },
    },
    publicValidationRule,
    privateProofRule,
  ],
};

export const declassifiedV2 = {
  id: 'community-fee',
  version: '2.0.0',
  description: '另一版本：困难等级进入金额；须有指定 private.hardship / 2.0.0 / 收件组核准',
  rules: [
    acceptanceRule,
    {
      id: 'fee',
      audience: 'public',
      node: {
        t: 'doc',
        docType: 'PAYMENT_ORDER',
        amount: {
          t: 'if',
          cond: {
            t: 'cmp',
            op: 'ge',
            left: { t: 'field', scope: 'private', name: 'hardship' },
            right: { t: 'int', value: 5 },
          },
          then: { t: 'int', value: 80 },
          else: { t: 'int', value: 120 },
        },
        copies: { t: 'int', value: 1 },
      },
    },
    publicValidationRule,
    privateProofRule,
  ],
};

// 使用固定 id 的补件规则，便于测试和“移除同一条”。
export function makeFixedSupplementRule() {
  return {
    id: 'supplement',
    audience: 'public',
    node: {
      t: 'if',
      cond: {
        t: 'cmp',
        op: 'ge',
        left: { t: 'field', scope: 'private', name: 'reviewLevel' },
        right: { t: 'int', value: 4 },
      },
      then: {
        t: 'doc',
        docType: 'SUPPLEMENT_NOTICE',
        amount: { t: 'int', value: 0 },
      },
      else: null,
    },
  };
}

export const errorForkV3 = {
  id: 'community-fee',
  version: '3.0.0-error-fork',
  description: '错误回单分叉版：私有 correction 决定是否给错误回单；无核准则必须扣留',
  rules: [
    acceptanceRule,
    publicFeeInvariantRule,
    {
      id: 'private-error',
      audience: 'public',
      node: {
        t: 'if',
        cond: {
          t: 'cmp',
          op: 'ne',
          left: { t: 'field', scope: 'private', name: 'correction' },
          right: { t: 'int', value: 0 },
        },
        then: {
          t: 'doc',
          docType: 'ERROR_RECEIPT',
          amount: { t: 'int', value: 0 },
          code: { t: 'int', value: 7788 },
        },
        else: null,
      },
    },
    privateProofRule,
  ],
};

export const templatePresets = {
  baselineV1,
  coincidenceV11,
  declassifiedV2,
  errorForkV3,
};

export const seedApprovalForm = {
  approvalId: 'APR-HARDSHIP-V2-PRIORITY',
  fieldsText: 'private.hardship',
  templateId: declassifiedV2.id,
  templateVersion: declassifiedV2.version,
  recipientGroup: 'priority-group',
  note: '模拟独立核准席：仅核准困难等级字段在 2.0.0 模板、限定收件组中形成公开金额差异。',
};

export function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}
