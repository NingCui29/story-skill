# 写作工作台 · Windows 独立应用

面向 Windows 10/11 x64。客户端使用 PySide6 / Qt WebEngine 桌面窗口，内置 Chromium 和 Python；使用者无需另装 Python、技能或浏览器扩展。完整创作技能在所用助手中另行安装。

本轮发布版本为 **0.1.9**，内嵌 Story Skill **0.6.19**。发布验收正在进行，最终构建、安装、窗口、卸载及附件回下载待核验，见 [v0.6.19 发布记录](../../docs/releases/v0.6.19.md)。Windows 10/11 用户环境的人工验收仍按实际证据记录；安装程序未做代码签名。

## 下载与安装

本版正式附件目标如下，链接待发布后可用：

- [安装程序 story-workbench-0.1.9-windows-x86_64-setup.exe](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64-setup.exe)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64-setup.exe.sha256)。核对后运行安装向导，为当前用户安装并创建开始菜单快捷方式，可选创建桌面快捷方式；系统应用列表支持卸载。
- [便携 ZIP](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64.zip)及[校验文件](https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.9-windows-x86_64.zip.sha256)。完整解压到可写目录，双击 `StoryWorkbench.exe`；保留同目录的 `_internal`、`runtime`、`Scripts` 等运行文件。

升级前保存桌面与外部浏览器全部页面编辑，退出旧客户端，再安装或替换。打开后若复用旧服务，应在保留编辑后按服务管理流程更新；客户端不自动结束作品服务。

## 使用

书架与编辑页在应用标签中打开。点击“复制作品目录”选择目录，再将路径粘贴到书架添加栏。下载使用系统保存面板。重复打开作品复用原标签，关闭、退出、重新载入和跳转时核对全部标签及隐藏文稿；保存中或状态不明时阻止关闭，未保存文字可返回保存或导出完整 ZIP。保存继续生成候选，正式采用沿用审稿流程。

天空蓝界面集中书架搜索与筛选，低频操作放在“管理作品”。书架和编辑页顶栏的“外观”支持浅色、深色与跟随系统，记住选择并保留未保存文字。书架可新建、移出、重新定位、删除至系统回收站、整书备份及恢复；编辑页提供恢复中心、保存全部候选、两稿对照和下一章任务。[操作与边界](../../docs/工作台需求与进展.md)

“检查更新”在后台查询 GitHub 最新正式发布并比较 Windows x64 安装包版本。下载显示百分比、大小、速度、预计剩余时间和 SHA256 校验状态，支持取消与重试；通过核验后，启动安装向导前检查全部标签编辑状态，便携包打开目录供手工替换。安装交给系统后提示保存、安装与重新打开，不将下载完成视为升级完成。缺少完整性信息、网络失败、超时或没有对应包时明确提示。[更新流程](../../docs/工作台更新流程.md)

平台草稿支持配置、预览、受控保存与回读，实际保存需当前助手连接既有已登录 Chrome。番茄已有指定合成稿的完整链路证据；七猫、起点、多卷与真实分页待实页验收，不执行提交审核或定时发布。[平台草稿说明](../../skills/story-skill-publish/references/platform-drafts.md)

## 构建

在 Windows x64 的完整 Python 3.12 环境中，从仓库根目录运行：

```powershell
python -m venv .venv-desktop
.venv-desktop\Scripts\python -m pip install -r desktop/windows/requirements-build.txt
.venv-desktop\Scripts\python scripts/build_desktop_windows.py --output dist/story-workbench-windows-x86_64
```

GitHub Actions 的 `Build Windows desktop candidate` 可对指定源码构建安装程序和便携 ZIP。构建流程覆盖运行时、CLI、安装、原生窗口、外观、更新进度和卸载检查，安装程序由 Inno Setup 6 与 `installer.iss` 构建。Actions 构建产物与正式 Release 附件分别核对，旧候选结果不作为本版通过依据。

## 验收范围

本版待核验最终构建、第三方许可、安装／卸载、原生窗口、运行时与附件回下载。在未安装 Python 的 Windows 10/11 用户环境中，首次启动、中文及空格目录、重复启动、添加和打开作品、候选保存重开、版本对照、下载取消、关闭提示、服务重启与非管理员使用仍按实际人工验收分别登记。随包应保留 Qt、Python 及 Qt WebEngine 第三方许可声明。

Mac 使用 `desktop/macos` 的原生 Swift 客户端。两端都不在安装或更新时迁移真实作品、采用候选或自动停止编辑服务。
