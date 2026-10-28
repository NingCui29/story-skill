# 独立实际试用评估

结论：能够从所给合成快照走通“去重与模式分析 → 经营对象抽样 → 填写候选和假设 → 刷新证据模板 → 阅读原文 → 回填证据 → 可读规划交接”。本次共形成 2 个候选方向、5 条文本验证记录，最终 `research-check` 返回成功；不是首遍无错误。

## 实际范围

使用本地开发版 `skills/story-skill` 共享 CLI 与任务指定 Python 3.12。按顺序读取研究技能、共同约束、结构化扫榜、研究交接、选题与阅读验证、拆文入口及深读规范。未读取开发者测试、其他代理任务或其结论，未查看 CLI 实现源码。所有研究产物在本目录；没有写正文、开小说工程、修改仓库代码/技能/测试、调用真实平台或发布。

输入全部是原创合成开发材料，来源域名 example.invalid 只是占位声明；没有将其当作可联网的真实榜单。a/b/c/d 四份短文本均全读，但它们各自只有开篇片段，不能称读过全书。研究包中的局部覆盖保留了这一区别。

## 本轮得到的证据

- 5 条观测去重为 4 本，a 重复被折叠；经营 3 本、邻里 2 本、远行 1 本，d 的标签未知。模式分析找到“开局”前缀与冒号结构各 3 本，都是样本字段事实。
- `rank-sample --tag 经营 --limit 3` 取得 a、b、c。它们是该合成组的全部命中阅读对象，没有真实市场代表性。
- a 有明确赔偿成本以及药房老板的开门委托，支持“出现新的信任与合作邀请”的局部解释；长期经济合作和委托履行未知。
- b 的原受助者没有回报，后来通过与不同伙伴协商投入建立合作；因此原假设为混合，新假设“互补投入与明确分工形成合作”获得局部支持。
- c 的资金求援未兑现，段尾仅有自救，反证只限于“这段已经换来合作”；没有反驳全书未来存在合作的可能。它对互补合作机制则证据不足。
- 两个候选分别是“共享厨房的小单承诺”“旧影院的分时合伙”。人工报告按证据强弱建议先细化第二方向的合作机制，但两者保持候选，未代作者作商业成功或最终采用判断。

## 走通时遇到的问题

1. **结论枚举不够可发现。** 初读研究交接只给中文类别，模板 `conclusion=null`，`research-verify --help` 未列枚举。首次填写自然猜测的 `supported/refuted` 被拒绝，实际错误明确列出 `supports/contradicts/mixed/insufficient`。按错误提示纠正后成功，失败命令与原输入均保留。失败没有创建输出目录。这是一个可恢复的小阻碍，不能报告成零错误首遍通过。开发者随后告知补充文档；本轮恢复依据仍是实际错误输出。
2. **生成文档有轻微标点累加。** 正常中文字段自带句号时，模板继续追加句号或分号，读回可见“。。”“。；”。内容完整，但路径、身份、重复提示与标点共同增加阅读负担。独立 `analysis-report.md` 提供了较简洁的综合交接。
3. **一次操作者路径笔误。** 我在纠正 JSON 的 shell 调用中拼错临时工作目录，进程创建失败；换回正确目录后执行。该失败与 Story Skill 无关，未影响原材料，已单列到 execution.json。

## 工具核验与尚未验证的范围

最终 decision 保留 2 个方向、5 条证据；每条都有本地原文/报告 SHA-256，`quote_checked=true`。本轮确实观察到正向字面匹配与研究检查成功；没有另做引文伪造、文件变更、重复回填等负向实验，因此不据此声称所有拒绝分支都经过实测。

机械核验不证明我的解释必然成立。A 的第三方动机没有明说、B 的让利与新合伙未明示因果、C 的老人资金状况仅为人物说法，这些限度均随交接保留。作者素材和优势没有提供，已写为待补，未编造；经营领域实务、长程结构、真正目标读者和平台结果均未验证。

字数报告把 c=0 算作真实零值、d=null 算作未知，机械行为符合输入；但 c 已有非空开篇，因此本轮只确认原字段为 0，不把它升级为全书实际字数事实。这也体现了“输入结构合法”和“来源语义真实”之间的区别。

## 最终产物

- `01-aggregate/report.md`：去重、标签和标题模式。
- `02-sample/sample.md`：经营阅读对象及来源。
- `analysis-report.md`：局部文本论证、反例、两个候选与取舍条件。
- `07-final-handoff/decision.json` 与 `07-final-handoff/planning-candidate.md`：供后续规划读取的候选交接；全部仍为候选。
- `evidence-filled.json`：本轮成功使用的 5 条证据；`evidence-attempt1.json` 保留错误枚举的首次输入。
- `execution.json` 与 `logs/`：完整 CLI 参数、时间、退出码、标准输出/错误、输入哈希和问题记录。

## 实际 CLI 命令顺序

所有命令的解释器、工具绝对路径和工作目录见 execution.json。每个产生目录的调用使用新子目录，初始及回填各版本均保留。

1. `story.py rank-check --input ../inputs/snapshot.json` — exit 0
2. `story.py rank-aggregate --input ../inputs/snapshot.json --output-dir 01-aggregate` — exit 0
3. `story.py rank-sample --input ../inputs/snapshot.json --group 65df9d06b8af8e2e6a50 --tag 经营 --limit 3 --output-dir 02-sample` — exit 0
4. `story.py research-init --sample 02-sample/sample.json --output-dir 03-research-initial` — exit 0
5. `story.py research-render --input decision-authored.json --output-dir 04-research-authored` — exit 0
6. `story.py research-verify --help` — exit 0
7. `story.py research-verify --input 04-research-authored/decision.json --evidence evidence-filled.json --output-dir 05-verified` — exit 2
8. `story.py research-verify --input 04-research-authored/decision.json --evidence evidence-filled.json --output-dir 06-verified` — exit 0
9. `story.py research-check --input 06-verified/decision.json` — exit 0
10. `story.py research-render --input 06-verified/decision.json --output-dir 07-final-handoff` — exit 0
