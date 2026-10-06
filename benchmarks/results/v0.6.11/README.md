# v0.6.11 工程与分发验收

2026-10-06（北京时间）。本目录保存本次源码的本地工程验证与远端分发回执；公开记录中的个人路径已通用化，不包含真实小说正文或私有书架截图。

本地完整回归为1364项：1347通过、17项原生Windows条件跳过，无失败或错误。Node相关用例已执行。发布包装调整另有40项定点测试通过。

本次使用合成作品验证CLI、长篇历史修订、三类schema 1迁移与四种容量组合，不改动真实小说。旧开发阶段记录保留各自日期、哈希和原有结论，不改称本版全量验证。

固定提交、远端CI、Release附件、固定标签隔离安装与GitHub Packages分别核验，最终状态见[发布记录](../../../docs/releases/v0.6.11.md)。

[工程验证汇总](verification.json) · [完整单测](unit-tests.json) · [CLI冒烟](smoke.json) · [中文事务](chinese.json) · [长篇历史修订](long.json) · [容量](scaling.json) · [迁移](migration.json) · [指令测量](tokens.md) · [ZIP清单](package.json)。

本地[npm构建](npm-package.json)与[独立核验](npm-verify.json)通过：49个载荷与2个包装文件。客户端[构建记录](desktop-build.json)与[解压／签名／运行核验](desktop-verification.json)分别记录Intel实验附件的范围。

远端分发证据：[最终CI](release/ci-final.json) · [Release附件](release/release.json) · [固定标签隔离安装](release/remote-install.json) · [Packages发布](release/packages-receipt.json) · [Packages独立复核](release/packages-independent.json)。
