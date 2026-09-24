# 发布材料包复核验证

2026-09-23，v0.5.11 开发源码。接续[章节材料导出](../material-export/README.md)，补齐材料包移交或再次使用前的只读复核。当前仍处于开发阶段，本轮未发布 GitHub、Release 或 Packages，也未替换日常安装。

`publish-verify-export --book "<绝对书根>" --file "<ZIP绝对路径>" --id "<原清单ID>" --sha256 "<原导出回执SHA-256>"` 同时核对包本身和当前正式稿。预期清单 ID 与外部保存的原始 SHA-256 均为必填；从待查包内部抄录或临时计算的哈希无法证明它是原导出文件。

- `artifact_verified=true` 表示同一次文件读取所得字节与原始哈希一致，ZIP 所属书、清单 ID、固定正文、目录及说明均与本书账本匹配。
- `usable_now=true` 还要求清单仍为 prepared，且本次正式稿、审查证明和登记导出与冻结内容一致。完整但已取消、过期或改稿的包仍能作为历史存档，但不能继续手工填报。
- 验证只读，不修改发布账本的状态或检查时间，也不改变创作 revision。需要持久登记过期状态时另用 `publish-check`。
- 导出与复核使用相同的成员数、单文件、ZIP 大小和展开总量限制。损坏压缩数据、重复或危险成员均给出结构化错误，不在用户书根解压。

## 实际尺寸试用

[独立命令行复核](cli-trial.json)使用上一轮保留的 12 章合成材料包。原始 SHA-256 与预期清单 ID 匹配，`artifact_verified=true`、`usable_now=true`，复核前后发布账本哈希相同；对应的原始[导出与逐字回读记录](../material-export/cli-trial.json)包含 40 个成员及 29,643 字正文。样例不是平台真实稿件或远端发布验收。

## 最终验证

- [全套回归](unit-tests.json)：715 项，698 通过、17 项平台条件跳过，0 失败、0 错误；其中包含本轮 15 项复核专项检查。
- [技能结构验证](skill-validation.json)、[本地文档链接](links.json)和差异格式检查通过。
- [开发 ZIP](package.json)、[归档逐文件核对](archive-check.json)、[npm 本地包](npm-package.json)及[隔离安装](install.json)验证同一最终源码；没有发布到注册表。
- [源码快照](source-manifest.json)记录最终载荷与开发测试文件的哈希，以及回归和打包期间的一致性。

本机为 Intel macOS、Python 3.10.21。Windows／Linux 原生测试本轮未运行；本机跳过项不能当作这些平台已通过。
