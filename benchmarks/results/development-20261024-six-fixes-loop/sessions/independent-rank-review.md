# 扫榜惰性容器与外来命名空间独立复核

独立复核使用真实隔离 Chromium 的 DOMParser 对照生产解析器，全部输入来自临时本地 HTML。没有访问实际平台，也没有修改生产代码或测试文件。

首次复核确认 `_foreign_self_closing` 仅追溯标签名而缺少命名空间状态，遗漏 HTML breakout 后的上下文变化：`<svg><p>…</p><template/>` 及 MathML、div、font、hidden custom 变体中，浏览器只有可见书卡100，解析器却加入惰性书卡900。另三例 SVG/MathML 的合法非 HTML 元素被误判为 integration point 而拒绝。原23场景结果保留于 [首次红灯](independent-rank-red.json)。

作者加入真实 namespace 与 start-tag breakout 后，原23场景通过；同一修复面的 end-tag `</p>`、`</br>` 仍留下四个隐藏内容误接受，见 [第二次红灯](independent-rank-namespace-red.json)。作者随后统一 start/end breakout 路径。

最终 **27/27 场景通过**：11个正常静态/foreign结构与 Chromium 结果一致；16个有歧义的惰性 HTML 自闭合输入明确拒绝，并要求重新导出完整结束标签。隐藏书卡误录为0。原模板作用域、hidden属性、正常SVG/MathML self-close、nested integration，以及命名空间同名元素均保留对应正向覆盖。

生产源码 SHA256：`7d323fd5130d0f956641a58d929c542db95852a6ce603d3203fd93496525c1ab`。详见 [最终结果](independent-rank-result.json)、[可重跑探针](independent-rank-probe.py)。

结论仅针对本次修复的惰性容器、命名空间和breakout契约；这不是完整 HTML5 tree builder、任意畸形HTML、CSS可见性、动态页面或真实站点验收。当前复核范围内无未解决的已确认问题。
