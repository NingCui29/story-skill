# Python 会话初始化恢复的独立复核

本目录为另一代理对 owner 实现的只读复核，未改生产文件。全部调用在临时书根执行，没有浏览器或平台操作。

独立基线复现了五个失败场景：并发 attach 可各自成功，留下两个 live 会话；首次 session 写入前/后和 lease 写入前失败会令后续 status/attach 遇到 FileNotFoundError；首次 lease 已原子发布再抛错则留下无句柄的 live 会话。原始结果在 `concurrent-before.json` 与 `recovery-before.json`。

修后原样重放及边界共 **14 场景通过**：并发只成功一个连接；四种首次写入失败均可重新连接；含 outbox、claimed、response、health、challenge、lease、未知文件和伪 stage 名称的八类不完整目录均继续拒绝，原文件不改；真实本地队列的已领取 create 在等待及超时后仍按活动会话处理，拒绝重复 attach、保留请求证据，并且不重发已领取操作。

并发探针在首次“尚无连接”的实际检查点暂停后启动第二 attach。新版将该内部检查改名 `_worker_status`，探针只随方法名移动同一检查点，没有模拟锁的成功结果。该独立并发探针使用同进程两线程；owner 另负责跨进程覆盖。未执行原生 Windows。最终源码哈希及结构化结果见 `summary.json`。
