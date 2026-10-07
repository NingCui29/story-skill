# v0.6.12 工程与分发验收

本目录记录2026-10-07新版本的工程检查与分发回执，采用隔离合成书库，不包含真实小说、私人书架或账号凭据。

固定提交的Linux及两组Windows CI均已成功，最终分发结果见[发布记录](../../../docs/releases/v0.6.12.md)。本地测试、实际页面显示、固定标签与跨平台CI、公开附件回下载、GitHub Packages回下载分别验收；安装文件完整不等于宿主模型实际发现、路由或文学质量验证。

[本地套件摘要](packages-local.json) · [npm构建](npm-package.json) · [隔离安装](install-local.json) · [CLI冒烟](smoke.json) · [中文事务](chinese.json) · [长篇历史修订](long.json) · [迁移](migration.json) · [桌面构建](desktop-build.json) · [桌面验收](desktop-verification.json) · [GitHub About](github-about.json)。

完整本地回归的[首轮统计](unit-tests-first.json)与[失败记录](unit-tests-first-failures.txt)保留；安装升级测试的macOS临时目录别名断言修正后，[22项安装回归](install-suite-final.json)全部通过。正式发布提交的三组完整CI另行记录，不能把定点重验改称本机整套重跑。

[发布提交CI](release/ci-final.json) · [远端固定标签隔离安装](release/remote-install.json) · [技能格式](skill-validation.json) · [指令计数](tokens.md) · [容量](scaling.json) · [工程核对](verification.json)。

[六附件公开回下载](release/release.json)与本地构建字节、GitHub摘要和三份校验文件一致；回下载不使用认证。

GitHub Packages自动发布及注册表回下载成功；本机通过工作流artifact独立复核载荷、包装、摘要和CLI，见[工作流回执](release/packages-receipt.json)与[独立核验](release/packages-independent.json)。

## Claude 桌面导入修正 r2

原桌面ZIP含9个 `SKILL.md`，用户实际上传时被拒绝；原有格式、字节与CLI检查没有发现这个导入约束，历史回执继续保留。修正版将8个内嵌入口及其Markdown引用改为 `GUIDE.md`，整个ZIP只含一个 `story-skill/SKILL.md`，运行时及标准套件未变。

[修正与公开回下载回执](release/claude-desktop-r2.json)记录47项本地打包回归、独立包结构/资源检查和r2两附件的公开下载摘要。实际Claude界面导入仍待复验；原发布提交的跨平台CI数字不作为本次修正版CI结论。
