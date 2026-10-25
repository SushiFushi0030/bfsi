# 数据契约导航

本项目使用 JSON Schema Draft 2020-12。契约位于根`schemas/`，相同字节随Python包放在`src/bfsi/resources/`；自动测试防止镜像漂移。

| 契约 | 输入 / 输出 | 约束 |
| --- | --- | --- |
| [`source_file_manifest_schema_v0.1.json`](../schemas/source_file_manifest_schema_v0.1.json) | 采集器输出的文件来源清单 | raw相对路径、端点、来源角色、run、接收时间、SHA256 |
| [`standard_comment_schema_v0.1.json`](../schemas/standard_comment_schema_v0.1.json) | Cleaner输出，语义分析输入 | `core`唯一评论+一个或多个`observations`，不把重复Observation计为新评论 |
| [`simplified_semantic_schema_v0.1.json`](../schemas/simplified_semantic_schema_v0.1.json) | 模型每条输出 | 固定标签、targets、confidence、summary，禁止额外模型字段 |

Schema中保留的历史Task描述是契约来源记录，不是运行依赖。迁移没有重写冻结Schema；Transformer业务验证与raw校验也需要通过，单纯通过JSON结构校验不等于数据可信。

## Standard

顶层为`schema_version`、`ingestion_mode`、`file_manifest`、`comments`。每个评论由以下两部分组成：

- `core`：`comment_id`、`video_bvid`、`video_oid`、`root_comment_id`、`parent_comment_id`、`depth`、`raw_text`、`publish_time_raw`、`published_at`。ID使用十进制字符串；根/父缺省以null表示。
- `observations[]`：`source_endpoint`、`source_role`、`source_run`、`observed_at`、时间精度、`raw_file`、`raw_object_path`，以及当时的点赞/回复数、置顶、表情、提及、链接和图片状态。

同一评论可从主分页、置顶、回复树或内嵌预览多次出现，每次保留来源。父链缺失、跨视频或来源冲突不能通过构造内容修补，隔离情况必须计入部分范围说明。原文不会被HTML反转义、裁剪或改写为模型摘要。

标准化不复制完整账号对象，但评论原文、@提及、链接、ID及关系仍可能识别人，不是公开匿名数据。发布示例不从真实Standard脱敏而来，全部人工构造。

## Semantic

模型输出的10个字段：`comment_id`、`relevance`、`targets`、`market_stance`、`emotion`、`action_intent`、`creator_attitude`、`sarcasm`、`confidence`、`summary`。其中情绪为多标签数组，对象包含`raw_name`、可空`normalized_name`和`target_type`。

Python语义模块另附上Standard原文、视频/父链上下文、来源指针、Observation数及批次来源，形成内部semantic dataset。它包含模型输入和用户原文，默认只在私有运行目录留存。模型标签和原文不能互相冒充。

Prompt位于[`bulldozer_prompt_v0.1.md`](../src/bfsi/resources/bulldozer_prompt_v0.1.md)。发布版保留分类规则，改写了原Prompt中可能来自真实评论的边界例句为人工构造句；Prompt指纹因此变化。新任务冻结新指纹，不把旧模型输出伪装为新Prompt的结果。

## Dashboard payload

当前payload由Python构造，前端在`web/src/data.ts`做类型和字段校验，尚未单独冻结为JSON Schema。主要包含：

| 字段 | 内容 |
| --- | --- |
| `video` | 标题、BV/OID、不同评论数、Observation数 |
| `headline` | BFSI、覆盖率、分歧、无信号标识与未舍入值 |
| `distributions` / `distribution_percentages` | 固定枚举分布 |
| `auxiliary_metrics` | 相关性、交易意图等辅助比例 |
| `target_indices` / `top_financial_topics` | 对象共现近似 |
| `representative_comments` | 有来源的代表评论与上下文 |
| `analysis` | run、模式、范围、完成时间、模型、覆盖与警告 |
| `source` / `methodology` | 输入与资源哈希、计算口径 |

合成示例见[`examples/demo/`](../examples/demo/)，`analysis.synthetic=true`且正文明确标注。海报导出要求已持久化的payload与run身份/哈希一致，不接受随意传入旧视频结果。
