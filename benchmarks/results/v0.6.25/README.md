# v0.6.25 验收索引

本轮分发为 Story Skill 0.6.25、Intel Mac 0.1.37／build 38、Windows 0.2.4。[主发行说明](../../../docs/releases/v0.6.25.md)与[Windows 说明](../../../docs/releases/windows-v0.2.4.md)记录功能、安装方式和验证限制。

版本分支：[version-0.6.25](https://github.com/NingCui29/story-skill/tree/version-0.6.25)。

实际构建源码：`45fe93530a3e07975ad5728a7d575ef23f8ae3d5`。最终标签源码：`b344e15dbcfb2c10f70f8d97ad706b9afdf092be`。两份测试期望／测试进程总时限修正以外的115个实际构建输入一致，见[等价性核验](build-source-equivalence.json)和[冻结构建输入](build-inputs.json)。原始失败记录保留，不改写为成功。

| 范围 | 结果与证据 |
| --- | --- |
| 最终源码 CI | 最终源码 CI 三组任务全部通过；Linux / Python 3.10：3060 项，通过 3012、跳过 48；Windows / Python 3.10：3060 项，通过 2989、跳过 71；Windows / Python 3.12：3060 项，通过 2989、跳过 71。[运行 40181492924](https://github.com/NingCui29/story-skill/actions/runs/40181492924)；[完整回执](release/source-ci.json) |
| 技能与插件 | 69个载荷文件、8技能的[固定源码核验](fixed-source-verification.json)；[Codex 隔离安装与发现](codex-plugin-verification.json) |
| 固定标签安装 | [标签、隔离安装、CLI与HTTP核验](release/remote-tag-install.json) |
| Mac | 20模块、1681普通文件和9个链接；[完整ZIP／DMG核验](macos-complete-inventory.json)、[原生合成场景](macos-native-acceptance.json)、[视觉检查](macos-visual-review.json) |
| Windows | C#39／39、1125文件、0.2.3→0.2.4升级；[原生构建](windows-native-build.json)、[分发](windows-distribution-verification.json)、[安装生命周期](windows-installation-lifecycle.json)、[升级](windows-previous-upgrade.json) |
| 公开下载 | [主发行10附件完整匿名回下载](release/public-downloads.json)；[Windows两个校验文件完整下载、两个二进制首尾各64KiB采样](release/windows-public-downloads.json)。Windows完整二进制摘要依据原生完整核验和GitHub服务端记录 |
| Packages | [工作流实际registry HTTP回下载](release/packages-ci.json)；[本机对工作流导出tarball的独立核验](release/registry-independent-download.json)。后者不是第二次本机registry HTTP下载 |
| 历史基线 | [实际构建源码本地全量回归](local-full-tests-build-source.json)保留一条旧UI文案断言失败；最终通过结论使用上述最终源码CI |

所有本轮作品与界面验收使用隔离合成材料，不代表真实作者作品整批保存、发布或多平台实页全量验收。Mac使用临时本地签名，未公证，Apple Silicon未验证；Windows工作台及安装程序未签名。安装包验收不等于本机日常应用或技能已更新。
