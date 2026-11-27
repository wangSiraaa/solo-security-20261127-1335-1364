"""资料保管单迁移台（可本地重放）。

领域对应关系：
- 解释字典/解释器  -> Interpreter（v1 旧版 / v2 新版）
- 资料保管单        -> CustodyOrder（绑定解释版本、合同版本快照、交接记录、签章）
- 接收库            -> ReadOnlyReceivingStore（仅追加哈希链日志）
- 便携许可          -> PortablePermit（路线 B：旧解释器退场前签出）
- 语义分歧证据      -> EvidenceBundle（站内封存，开发者决定由受托站保存）
"""

__version__ = "1.0.0"
