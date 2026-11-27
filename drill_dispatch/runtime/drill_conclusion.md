# 值班发件安全演练结论

## 演练结论（给操作员）

见下方各项计数与动作；正式域在封存点之后零写入，所有演练动作均发生在 drill/ 替身柜，回条带演练域标记。

## 正式柜封存前计数

```json
{
  "vault-main": {
    "dispatched": 3,
    "seq": 5
  }
}
```

## 演练结束时两域计数

```json
{
  "drill": {
    "shadow-vault-1": {
      "dispatched": 1,
      "seq": 1
    },
    "shadow-vault-2": {
      "dispatched": 1,
      "seq": 1
    },
    "shadow-vault-3": {
      "dispatched": 1,
      "seq": 1
    },
    "shadow-vault-4": {
      "dispatched": 1,
      "seq": 1
    }
  },
  "prod": {
    "vault-main": {
      "dispatched": 3,
      "seq": 5
    }
  }
}
```

## 可验证范围

1) 历史回条能且只能证明正式域当次实际领出的物件与领用组；
2) 替身柜可受理改过的工单，但新收条带 drill 域标记，不能冒充历史见证；
3) 超时/崩溃重放由幂等键与因果日志保证不重复发件，可凭台账逐条验链；
4) 本结论不回答正式作业当时未做的新操作——那超出历史回条的见证范围；
5) 缺回执时作业挂 UNKNOWN，须人工核查台账，禁止臆测重发。

## 正式柜实际办理动作

```json
[
  {
    "idem_key": "JOB-P1:take:e0a8973215f87407",
    "kind": "take",
    "receipt_id": "prod-vault-main-take-000001",
    "seq": 1,
    "ts": "2026-11-27T02:47:28.097404+00:00"
  },
  {
    "idem_key": "JOB-P2:take:ddfb7da595e146cd",
    "kind": "take",
    "receipt_id": "prod-vault-main-take-000002",
    "seq": 2,
    "ts": "2026-11-27T02:47:28.114338+00:00"
  },
  {
    "idem_key": "manual-return-ITEM-A",
    "kind": "return",
    "receipt_id": "prod-vault-main-return-000003",
    "seq": 3,
    "ts": "2026-11-27T02:47:28.131952+00:00"
  },
  {
    "idem_key": "JOB-P3:take:9069d48186d67722",
    "kind": "take",
    "receipt_id": "prod-vault-main-take-000004",
    "seq": 4,
    "ts": "2026-11-27T02:47:28.140529+00:00"
  },
  {
    "idem_key": "manual-return-ITEM-A-2",
    "kind": "return",
    "receipt_id": "prod-vault-main-return-000005",
    "seq": 5,
    "ts": "2026-11-27T02:47:28.160223+00:00"
  }
]
```

## 证据完整性

```json
{
  "problems": []
}
```
