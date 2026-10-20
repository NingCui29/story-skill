# 写作工作台 · Windows 独立应用

面向 Windows 10/11 x64。客户端使用 PySide6 / Qt WebEngine 桌面窗口，内置 Chromium 和 Python；使用者无需另装 Python、技能或浏览器扩展。完整创作技能在所用助手中另行安装。

已发布 **0.1.9**，内嵌 Story Skill **0.6.19**。原生 Windows CI 已完成构建、安装、窗口、安全探针、完整载荷／源码核对和卸载；实际下载核验范围见 [v0.6.19 发布记录](../../docs/releases/v0.6.19.md)。Windows 10/11 用户环境的人工验收仍按实际证据记录；安装程序未做代码签名。

## 下载与安装

本版正式附件如下，下载后先核对同名校验文件：

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

GitHub Actions 的 `Build Windows desktop candidate` 可对指定源码构建安装程序和便携 ZIP。构建流程覆盖运行时、CLI、安装、原生窗口、外观、合成更新进度、完整 ZIP／安装目录及源码／许可清单核对和卸载，安装程序由 Inno Setup 6 与 `installer.iss` 构建。

手动触发的 `draft_release_id` 默认值为 `0`，常规构建不上传 Release。仅在主仓库显式指定已有草稿 ID，且原生构建验收通过后，独立发布任务才会核对草稿的版本标签与确切目标源提交，上传四份指定 Windows 文件；该任务不创建或公开 Release。Actions 产物、草稿上传与正式分发分别核对。

## 验收范围

本版原生验收在 GitHub Windows runner 完成：安装后的运行时／安全探针、书架与明暗主题、合成下载进度和卸载已核对；ZIP 安全路径、CRC、完整 manifest、实际安装目录、源码与随包许可清单均逐文件核验。原生 CI 完整读取包体与本机报告／服务端摘要／公开请求核对的范围见[发布记录](../../docs/releases/v0.6.19.md)，不将摘要或 Range 核对写成已在本机完整回下载。

在未安装 Python 的 Windows 10/11 用户环境中，首次启动、中文及空格目录、重复启动、添加和打开作品、候选保存重开、版本对照、下载取消、关闭提示、服务重启与非管理员使用仍按实际人工验收分别登记。合成更新进度不证明真实网络下载或升级安装完成。

随包 `ThirdPartyLicenses` 保留上游 Python 分发包的许可文件，并补充与 Qt / PySide 6.10.3 对应的 LGPL/GPL 完整文本及官方 Qt WebEngine 第三方归因清单。离线入口为 `ThirdPartyLicenses/Qt/QtWebEngine/index.html`；[来源与逐文件清单](https://github.com/NingCui29/story-skill/blob/main/desktop/windows/licenses/README.md)记录版本和核对范围，各第三方组件保留自己的许可条款。

Mac 使用 `desktop/macos` 的原生 Swift 客户端。两端都不在安装或更新时迁移真实作品、采用候选或自动停止编辑服务。
