# 浏览器修复的独立交叉复核

此部分由非浏览器修复者另行构造时序与多次会话场景，使用公开 adapter/driver 接口配合离线假 DOM。未编辑两份浏览器生产模块或浏览器测试；发现的问题交给对应 owner 修复。

## 新实现中确认并修复的窗口

Chrome 新增“动作前读取当前草稿状态”后，如果状态 DOM 回调取得旧快照、返回期间页面已经切到另一稿，驱动曾继续执行下一次 `fill`，再在动作后发现 ID 不同。独立探针中，七猫与起点的已有稿 88 章号都被从 88 写成 12，虽然最终返回 identity_mismatch，但错误写入已经发生。

- `chrome-cross-red.json` 保存这一阶段的 16 场景结果；`chrome-cross-probe.json` 是同一红阶段的原始输出，不能作最终通过依据。
- Chrome owner 修复为：草稿状态快照不重试；在它之后、实际操作之前再次核对当前 URL 所含的已绑定 book/remote，并把当前 URL 交给按钮/焦点检查。支持同 ID 已配置 newdraft/modifydraft 迁移，拒绝其他稿、作品或未知路由。
- 同一独立探针重放为 `chrome-cross-green.json`：**16 场景全部通过**。4 个同 ID 合法迁移通过；12 个不同目标/作品/未知路由在首次 fill 前拒绝，原稿字段全部保持不变。`chrome-cross-summary.json` 绑定最终生产 SHA256。
- 测试覆盖状态取样前与取样后两个时点；不是把任意 UI RPC 发起后的导航声称为可原子阻止。

## Native 与 same_page 正反复验

`native-cross-probe.py` / `native-cross-result.json`：**28 场景通过**。七猫与起点的新建、保存分别覆盖无 href、空 href 与稳定相对 href；真实 guard 读取之后 href 改变时不得点击，稳定值正常通过。另在 Native 当前状态读取之后改变创建地址，空 ID 迁移在首次写前拒绝；正常首次 ID 分配且地址匹配仍通过。绑定当前 Native 模块哈希。

`chrome-same-page-probe.mjs` / `chrome-same-page-result.json`：**2 个完整流程通过**，分别为实时目录与模拟 SPA 缓存目录。每个流程连续执行创建第12章→目录查找回读→创建第13章→目录查找回读；创建数/保存数均恰为2，使用两个不同的管理页承接 same_page 新建，两稿正文分别保持正确。最后仅关闭本会话自建标签，预先存在的外部标签保留。该探针独立于 owner 新增的单次成功用例。

当前交叉检查范围内没有已确认未解决项。真实本地 Chrome DOM、持久草稿 journal 与完整回归由根线程另行汇总；这里的假 DOM 场景不冒充真实平台验收，也不重复计入 unittest 总数。
