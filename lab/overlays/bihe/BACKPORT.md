# 移植记录 — 碧荷社原样封存分支

- 同名修正：`fix(offline): resolve promised entries without external query`
- 原样沿用：`mojiao/reviewed_resolver.py` 与 `school-promised-modern-2.1.0.json`
- 本分支新增边界：`mojiao/compat_oldid.py:resolve`
- 调用方约定：`caller/legacy-wordid-v1`

主线背书只能覆盖哈希一致的原样组件和词表。旧词号映射、打包组合和教室行为必须由边界复核覆盖；`compat_oldid.py` 一改版，组合背书须重审。
