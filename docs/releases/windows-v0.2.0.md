# Windows 工作台 0.2.0

Windows 桌面层改为 C# / WPF / .NET 10 / WebView2，作品服务继续使用 Story Skill 0.6.22 / Python。旧 Qt 桌面实现及依赖已移除，源码、构建产物及安装目录加入 Qt 系列与 PHP 禁用检查。

## 下载与安装

- [EXE 安装包](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.0/story-workbench-0.2.0-windows-x86_64-setup.exe)
- [同名 SHA256 文件](https://github.com/NingCui29/story-skill/releases/download/windows-v0.2.0/story-workbench-0.2.0-windows-x86_64-setup.exe.sha256)
- [安装与升级说明](../../desktop/windows/README.md)

安装包大小为 299,411,997 字节，SHA256 为 `72289c4b08aabbfe6de83408e3414fa2a548c4dd4f801a1fb5234ccde98507ce`。安装包内置 .NET、Python 和微软签名的 WebView2 离线安装程序；应用安装器本身未做代码签名。

本次独立发行保留 Story Skill `v0.6.22` 的 latest 入口。Windows 0.2.0 使用上述固定链接手动安装，当前应用内检查不会发现该独立标签。

## 构建来源与验收

EXE 构建来源：[a875f3b1c3598b715cf291e863ab807116ac61e0](https://github.com/NingCui29/story-skill/commit/a875f3b1c3598b715cf291e863ab807116ac61e0)。[Windows 构建记录](https://github.com/NingCui29/story-skill/actions/runs/39945818527) 已成功完成：

- C# 合约测试 28/28，零跳过。
- Windows Server 2025 runner 中的当前用户安装、中文与空格路径、原生窗口启动及卸载。
- 浅色、深色、天空蓝主题；原生 WebView2 中的隐藏文稿、输入法组合、保存状态与导出保护。
- 安装与便携载荷的 1,124 个文件逐项验证，作品备份及恢复探针、禁用组件检查。
- 官方 WebView2 离线运行时的完整摘要和微软有效签名。

发行标签还包含下载文档及 Packages 发布标签过滤；可执行代码、依赖、安装脚本和作品运行时与上述构建一致。随包 README 保留该构建提交的快照，不能将发行标签的全部文件宣称为安装包原始构建输入。Release 附件中的 `release-source-equivalence.json` 记录两者差异。

## 验证范围

更新进度和编辑保护使用明确标记的合成测试数据；不代表真实网络升级或用户稿件已被验证。缺少 WebView2 时首次补装、真实旧版覆盖升级及 Windows 10 各版本未在本次 runner 单独实测。

源码回归基线 `7625b6d` 的 Linux、Windows Python 3.10/3.12 三组均完成 2,488 项测试（存在平台跳过）。后续 `e7b8052` 的 Windows Python 3.10 全量执行出现两项 Chrome worker 时序相关失败，该结果保留；相关业务与测试未在 C# 迁移中修改。最终安装器使用 Python 3.12，以上桌面与发行验收全部通过。
