# 维护分支移植与复核台报告

## 结论

只能沿用其明确覆盖、且实际调用路径的模块/词表/契约哈希完全一致的背书；同名补丁或合并记录不转移信任。

- 千字社走原生重写：不能照签 END-MAIN-1396；重验后只签发仅覆盖 maint/qianzi-native 指定提交的 END-QIANZI-BRANCH-1396。
- 碧荷社走原样封存：END-MAIN-1396 仅能通过 CARRY-BIHE-VERBATIM-1396 沿用于哈希一致的 reviewed_resolver.py 与 2.1.0 词表；compat 边界和组合包由 END-BIHE-COMPOSITION-1396 覆盖，边界改版须重审。
- 学校交付选择：XIAN-GE-ROOM-01 安装千字社原生分支包，因为它保住四位旧词号，并允许词表独立更新。
- 碧荷封存路线复核包：`/workspace/dist/bihe-sealed-review-1.0.0`（含原样继承与组合边界两张背书）

## 正向交付

- 教室：`XIAN-GE-ROOM-01`（虚构·弦歌小学一号机房）
- 包：`/workspace/dist/qianzi-school-1.0.0`
- 压缩包：`/workspace/dist/qianzi-school-1.0.0.tar.gz`
- 压缩包 SHA-256：`9e8a32952dab2b2216061d0caf632e0b060cec56edb1bddfed08eee1b3a326a2`
- 断服务离线验收：`通过`
- 接收柜新事件数：`0`

复核者可运行：

```sh
cd /workspace/classrooms/xian-ge-room-01
python3 verify.py --human
./ww lookup 1001 --human
```

## 交付后独立词表更新

- 新版词表 SHA-256：`0369626dc29e8e874c723861a7b2701dedb238cbff0147a0a9a2ad46db68a594`
- 旧词号 1001-1003 仍通过：`True`
- 新增旧词号 1004 状态：`ok`
- 新版专用 `W-NEW-017` 在旧词号契约下状态：`contract_error`

这显示“加载器/旧词号契约”与“新版独立词表内容”是两类不同承诺；未来词表内容不被旧背书自动覆盖，须记录哈希并重新跑行为探针。

## 反向演练

1. **偷换实际入口实现**：REFUSE_OLD_ENDORSEMENT_SIGN_AS_IS
   - 功能探针通过：`True`；但实际模块哈希与背书哈希不一致，旧背书不能照签。
2. **来历真实但撤走目标词表**：FAIL_OFFLINE_DELIVERY_OLD_ID_COVERAGE
   - 原始压缩包来历仍真实，但安装副本的旧词号探针失败；来源证明不能代替离线覆盖验收。
3. **本地答案正确、兼容层投递接收柜**：FUNCTIONAL_PASS_BUT_REFUSE_NO_EGRESS_OR_NON_DISCLOSURE_ENDORSEMENT
   - 本地输出全部正确：`True`；返回值还都自称无外传：`True`
   - 接收柜新增事件：`3`
   - 因此不能签署“不外传”。

## 账本

- 哈希链校验：`ok=True, records=8, head=9cfe6e98e92d05223b01933a17b9f825f935c4bbb7bab06e0d6a005be202a250`
- 柜登记：`/workspace/review-desk/cabinets/registry.json`
- 单独查柜：`python3 -m reviewdesk.cli cabinet query --all`
