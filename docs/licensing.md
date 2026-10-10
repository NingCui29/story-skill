# 许可证与分发

2026-10-10 起，当前源码的八个技能许可证统一为仓库根 [LICENSE](../LICENSE) 的完整原文：《Story Skill Non-Commercial Use and Commercial Licensing Agreement》。非商业用途可免费使用；商业用途须在开始前取得另行商业授权并按约定付费，具体以许可证原文为准。

根许可证中的 `Commercial Licensor` 和 `Commercial Licensing Contact` 仍为占位文字，授权主体和联系方式尚未填写。

## v0.6.16 的构建与分发

| 文件或分发 | 许可证位置与标注 |
|---|---|
| 仓库源码 | 根 `LICENSE` 与八份 `skills/*/LICENSE` 原文字节一致 |
| 标准技能 ZIP | 八个技能目录各自携带完整 `LICENSE` |
| Claude 桌面技能 ZIP | 顶层及内嵌八技能携带同一完整 `LICENSE` |
| npm 技能内容包 | 从输入 ZIP 的技能许可证生成包装；自定义条款标为 `SEE LICENSE IN LICENSE`，包根 `LICENSE` 复制核心技能的完整原文 |
| 独立 Mac 客户端 | `Resources/StorySkill-LICENSE` 复制仓库根许可证 |

npm 构建和核验按输入归档的实际许可证选择包装；八技能的许可须一致，未知条款不自动标为 MIT。自定义标识与包根许可证文件遵循 [npm 许可证字段说明](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/#license)。当前打包器登记历史 MIT 和本次自定义协议的完整正文指纹；以后填写授权主体、联系方式或修改条款时，需要同步更新许可识别及验证。本次协议随v0.6.16分发，源码构建应结合提交与文件摘要辨认，不能仅凭版本号视为已发布附件。

## 历史分发与单独许可的材料

这次更新没有替换历史 Release 附件或 npm 已发布版本。[v0.6.14](releases/v0.6.14.md#分发中的许可证) 与 [v0.6.15](releases/v0.6.15.md#分发中的许可证) 的许可证表保留当时的实际分发记录：标准、Claude 桌面及 npm 技能包携带 MIT，独立 Mac 客户端携带根自定义条款。以相应归档中的完整许可证为准。

根许可证第 6 节保留此前依法获得的 MIT 或其他许可证权利；此次同步不追溯变更这些权利。历史 MIT 归档仍生成原有 npm 包装字节，可继续核验已有分发包。

[schema 1 历史运行时测试夹具](../tests/fixtures/README.md) 保留单独的 [MIT 许可证](../tests/fixtures/LICENSE)。[真实作品来源](../benchmarks/analysis-real/sources/README.md) 和其他单独标明许可的材料继续按各自来源与条款处理。

根许可证第 5 节规定，项目不会仅因用户使用软件而取得其输入或输出的权利；商业内容创作仍受第 2 节商业授权要求约束。
