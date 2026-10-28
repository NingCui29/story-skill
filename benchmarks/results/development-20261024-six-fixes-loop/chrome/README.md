# Chrome 执行器循环复核 · 2026-10-24

本轮保留工作树既有改动，重点复核前轮按钮 href、逐动作 DOM 状态、同页创建管理缓存，以及同 ID 合法别名与状态查询后换稿两处 Chrome 回归。另检查读取快照、段落焦点、超时单次重读、创建不重发、关闭自建标签、worker 队列与健康检查邻域。

## 确认并修复

**原始 href 未变化时，有效目的地仍可变化。** 页面 `<base>` 从 `/draft/` 变为 `/publish/` 后，`href="next"` 与按钮文案都没有变化，上一版本仍点击了新建/保存链接。`red-effective-destination.log` 的两个失败用例记录了各 1 次实际 click，并且旧实现返回成功；测试开始时的完整源码保存在 `before-bridge.mjs`。

现在初次与最终按钮 DOM 快照同时记录原始 href 与按实际 `document.baseURI` 解析的 `resolvedHref`。两者任一发生变化即停止后续点击，解析失败也不点击。Popup 创建也使用实际解析结果取得其 query。无 href 的普通按钮、稳定空/相对/绝对链接、有效目的地不变的无关 base 变化、带 query 的相对 popup 链接均有正常流程对照。

修复候选验证中，空 href/fragment 链接遇到同 ID 的配置内编辑器别名切换曾被严格有效 URL 比较误拒，红灯为 `red-same-document-alias.log`。独立复核随后又指出 Unicode 空格被 `trim()` 当作空白，但 URL 解析会把 NBSP/U+3000 保留为编码路径；`red-unicode-self-link.log` 复现了配置内 `/edit/99`→`/modify/99` 时错误点击保存。该阶段源码保存在 `before-unicode-self-link.mjs`。

最终只用 URL 解析结果判断同文档链接：save 已绑定 ID，两次当前地址均匹配同作品同草稿的配置模板，两次有效链接均恰等按各自当前文档 URL 解析结果，且分别清除 fragment 后目标地址必须等于当前文档地址，才放行别名切换。真正的 ASCII 空白/C0/fragment 保持兼容；Unicode 编码路径、一般相对 `next`、同时改变 base 的别名切换不享有此例外。两处候选问题均在最终冻结前修正。

## 验证

- 新增 **6 个 Node 顶层测试**，包含 **39 个实际创建/保存场景**，另有无 baseURI 和无 ownerDocument 的回调检查；参数数不加入顶层测试数。
- 最终组件回归：**166 driver + 94 worker = 260 项通过，零失败/跳过**，见 `component-green.log`。全仓库 unittest 中 Node 仅计为两个包装测试。
- `effective_destination_dom.py` 将生产回调原样序列化到隔离的本地 Chrome 页面，**18/18 项通过**。真实 anchor.click 监听器记录实际 href 并 preventDefault，未导航到网站；包含相对路径、query、空 href、fragment、根相对、绝对、协议相对、无 href、Unicode 空白、ASCII/C0、编码、base host、默认 document base 与无效链接。结果绑定最终生产哈希。
- 独立复核 Python 会话修复 **15/15 场景通过**，见 `independent-session-result.json`：可捕获中断前/后发布、替换 session/lease 的保留现场、无关活动会话不改动、初始化残留可精确退役、未知/已认领队列仍阻断。未修改 Python 生产模块或其测试。

精确命令与文件哈希见 `validation.json`。此处使用离线 CUA 契约和隔离本地 DOM；没有访问真实平台/账号/作品，没有安装或发布，也不声称任意 UI RPC 开始后的变化具有原子保证。完整稳定源码回归和跨模块验包由根线程执行。
