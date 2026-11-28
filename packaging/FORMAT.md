# 虚构练习档案袋

abc_legacy_bag.json 中每一层的 ref 选择教学钥。教学钥是字符串 solo-demo-public-keys/ 与 ref 拼接后所得 UTF-8 字节的 SHA-256 原始 32 字节。全部训练钥来自公开规则，限此练习使用。nonce_hex 是 12 字节十六进制，ciphertext_b64 是 AES-GCM 密文与 16 字节标签合在一起的 Base64 编码。meta 的规范 JSON 字节作为附加验证数据。

规范 JSON 为递归按键排序、无分隔符空白、Unicode 不转义的 UTF-8 序列，普通对象分隔符逗号和英文冒号。如该层是封套，解出 body_ref、body_version、body_digest。body_digest 是完整 body 对象的规范 JSON 字节的 SHA-256 小写十六进制值。封套内的正文摘要描述它实际锁住的内层。新封套采用原案号与卷标并改用新封套钥；若业务选择重新密封正文，新内层必须使用 v2 与 B/6。生成任何新练习袋所用 nonce 需为对应新测试钥的不同值。

提供的文件均为任意可替换的合成输入。修复工程的下载或存档时不要把训练钥当作字幕内容。
