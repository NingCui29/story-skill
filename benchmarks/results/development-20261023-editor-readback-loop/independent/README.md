# 独立复核记录

本目录只保存审计证据和合成探针；独立审查者未修改生产代码、测试或技能文档，未安装套件、操作真实作品或访问平台网站。

## 本轮已完成

- 共用、规划、写作、审稿、拆文、研究、封面、发布入口及相关专项引用边界抽查；315 条本地 Markdown 锚点均可解析到实际文件标题（`anchor-audit.json`）。这只证明引用可达，不证明文学效果或平台当前政策。
- 研究样本重算、引用与摘录核对、相反证据保留、历史依赖及世界状态歧义表达、工作台恢复分页和归档恢复关键路径静态复核。未新增已确认问题。使用此前全量基线及已有对应回归的覆盖依据，本目录未另外运行这些模块全量。
- 核心字面检索以全量 Python 子串 oracle 做了 266 次断言，涵盖63个合成源、组合字符/罕见字/换行/空白/标点、同文多身份、更新删除、类型过滤和事务回滚，全过（`search-oracle.json`）。断言数不等于 unittest 用例数。
- Native 在途修复复核发现“缓存的单个 handle 后续出现重复匹配仍可能被视为唯一”，已反馈实现方；主线程补充同节点仍连接但 selector 唯一转移及最后 guard 之后字段变化两个边界。最终实现用当前匹配与原 handle 同一性检查、最后 guard 后重取快照解决。独立8场景复验双平台无变化正向和上述三类变化，全部符合预期且零写入（`native_late_races.py` / `native-late-races.json`）。
- 本机隔离 headless Chrome 在临时本地 HTML 上执行生产 Native snapshot、节点同一性及 Chrome current DOM 读取函数，30项通过（`real_dom_probe.py` / `real-dom-result.json`）。覆盖 input当前value、textarea换行、select值、纯文本、contenteditable、属性、空/重复匹配、脱离文档节点、仍连接但匹配转移、ProseMirror空段保留及不支持富文本拒绝，以及焦点在正文/正文子节点接受、其他字段/零匹配/多匹配拒绝。每次运行创建并清除独立临时 profile，没有接触用户profile或联网网站。

这些探针分别验证 Python适配器控制流和生产JavaScript的真实DOM行为；不构成已安装 Playwright、CUA宿主连接、真实七猫/起点网页、平台账号操作或线上保存成功的验收。

`native-late-races.json` 和 `real-dom-result.json` 带运行时生产源码SHA256；最终创建路径如继续调整，应在最终冻结后重新运行两脚本刷新证据，不能把旧 hash 结果冒称最终代码结果。

## 创建路径追加闭环

- Chrome相邻创建问题修复后，独立32场景验证双平台、DOM/listing状态、同ID路由变化正向、各字段填写后跳到既有稿、locator.count读取结束后跳稿和保存按钮标签读取结束后跳稿。全部通过；切换后没有下一次fill/paste/save，既有稿完整对象不变（`chrome_create_probe.py` / `chrome-create-result.json`）。
- Native独立前写入探针发现：在`_fill`内部类型读取返回时换到既有稿，仍可执行下一次Locator.fill，自动保存会实际覆盖既有稿标题/正文；保存按钮标签读取期间换稿也会点击另一稿保存。已留红证据`native-pre-fill-race-red.json`和58场景扩展`native-create-red.json`（24项同根因失败），反馈实现方后修复为实际元素handle动作及动作前guard。
- 修后同一58场景全部转绿，覆盖两个平台、直接新建/popup、普通保存/合成自动保存、各字段完成后的换稿、类型读取/保存标签读取时换稿，以及首个ID分配后的同文档URL变化正向。新建路径没有换稿后的后续写入，旧稿未变（`native_create_probe.py` / `native-create-result.json`）。
- 上述竞态均安排在可观察的读取或已完成动作回调与下一动作之间，不用“写入RPC已开始之后外界任意变化”构造不可兑现的跨页面原子承诺。

最终本目录专项验证已通过，未保留新确认未解决问题。全量回归由主线程串行执行，本目录不宣称其完成状态。
