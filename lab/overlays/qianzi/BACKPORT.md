# 移植记录 — 千字社旧词号分支

- 同名修正：`fix(offline): resolve promised entries without external query`
- 主线原实现：`mojiao/reviewed_resolver.py`
- 本分支实际被调用实现：`mojiao/native_legacy.py:resolve`
- 调用方约定：`caller/legacy-wordid-v1`，教室启动器只传四位旧词号。
- 词表：`school-promised-legacy-1.4.0.json`，并可加载带 `legacy_id` 的独立新版词表。

合并记录和补丁标题只用于检索，不能作为代码身份。复核台必须记录实际入口、模块哈希、词表哈希与断网/接收柜行为。
