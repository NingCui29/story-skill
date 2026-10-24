# v0.6.24 发布验收

版本：Story Skill 0.6.24、Intel Mac 0.1.29 / build 30、Windows C# 0.2.3。

最终源码 `d3b934fe8a8b58a93b97673a21d293b0a476392f`；实际构建源 `d2de5f49c5528eac4796683ea152a06a438ae8fc`。打包后只调整两份测试文件的六项旧断言，113个实际构建输入的字节、Git模式和对象一致。原始构建记录保留实际来源。

| 检查 | 本轮证据 |
|---|---|
| 完整三环境源码 CI | [source-ci](release/source-ci.json) |
| 初始失败记录 | [本地2735项、6项旧断言失败](local-full-tests-initial.json) · [断言修正与31项定向验证](test-expectations-fix.json) |
| 源码与构建对应 | [固定输入](build-inputs.json) · [最终源码等价证明](build-source-equivalence.json) · [发布源](release/source.json) |
| 三种技能包与本地 npm | [构建与校验汇总](local-package-verification.json) · [npm](npm-local.json) |
| Codex 隔离实际安装与八技能发现 | [Codex 回执](codex-plugin-verification.json) |
| Mac 原生与分发 | [原生验收](macos-native-acceptance.json) · [20运行时与安全探针](macos-payload-probe.json) · [1680文件／9链接完整比对](macos-complete-inventory.json) · [目视核对](macos-visual-review.json) |
| Windows 原生和安装 | [构建汇总](windows-native-build.json) · [39项C#测试](windows-csharp-tests.json) · [1125文件安装后比对](windows-distribution-verification.json) · [安装生命周期](windows-installation-lifecycle.json) · [0.2.2升级](windows-previous-upgrade.json) |
| 远端固定 tag 实际安装 | [隔离安装、CLI和HTTP](release/remote-tag-install.json) |
| 公开发行与附件 | [两个Release](release/published-releases.json) · [主发行匿名完整下载](release/public-downloads.json) · [Windows下载范围](release/windows-public-downloads.json) |
| GitHub Packages | [实际发布和注册表回下载](release/packages-ci.json) · [下载导出包独立复核](release/registry-independent-download.json) |

Windows客户端源码和随包README在原生checkout使用CRLF，独立比对保留两端原始摘要，并且只允许Git换行转换；20个运行时文件逐字节一致。主发行10附件完整匿名下载；Windows4附件中的校验文件完整下载，两个二进制匿名首尾采样并对应原生完整验证与GitHub摘要。

所有场景使用隔离合成书稿。新软件测试不构成番茄真实章节同步成功，未修改作者作品、平台稿件、本机已安装应用或用户技能；原始失败记录不作为通过证据。Mac为临时签名、非Apple公证，仅Intel原生验收；Windows安装程序未签名。

## 完整源码测试结果

| 环境 | 测试数 | 实际结果 |
|---|---|---|
| test (ubuntu-latest, 3.10) | 2730 | OK (skipped=46) |
| test (windows-latest, 3.10) | 2730 | OK (skipped=70) |
| test (windows-latest, 3.12) | 2730 | OK (skipped=70) |

各环境跳过项保留在原始日志中，不计为已执行通过。完整步骤还包括技术约束、指令预算、三种技能包构建和CLI／中文事务／长篇修订验收。
