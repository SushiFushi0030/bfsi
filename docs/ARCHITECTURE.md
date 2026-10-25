# 架构与模块边界

应用由 Python 标准库 HTTP 服务、文件任务系统和 React 看板组成。原有 Flask/数据库依赖不存在，不为迁移另加服务。`pyproject.toml` 安装 `src/bfsi`，前端使用独立 `web/package.json` 和锁文件。

| 层 | 职责 | 持久化 / 边界 |
| --- | --- | --- |
| Collection | 校验 BV/域名、验证 BV/OID 映射、主楼/置顶/回复分页、节流、检查点 | 原件、SHA256、来源清单、采集范围 |
| Transform | 原文保留、关系还原、不同评论与 Observation 分离、隔离不完整来源 | Standard Schema、业务关系、raw证据校验 |
| Semantic | 视频+父链上下文、10条批次、输出枚举和ID校验、最多2批并发 | 冻结Prompt/Schema/输入/模型，保留尝试记录 |
| Index | 不同评论去重、精确分数、分布、对象共现、代表评论 | 无网络、无写文件；返回JSON payload |
| Jobs | 单worker、暂停/续跑、同任务身份、只发布完成结果 | 每个run独立；latest只在成功发布后更新 |
| Reporting | 数据驱动排版、文本换行、截断标注、缓存指纹 | PNG与manifest，拒绝覆盖不一致结果 |
| HTTP / Web | 本机API、状态轮询、看板、导出预览与下载 | Host/Origin检查，静态服务范围仅web/dist |

## BV → 看板

1. `POST /api/analyze` 校验输入和模式，创建唯一 run ID。
2. 解析公开视频页面中的 BV/OID，保留映射证据后才采集。
3. 先处理主分页，再补齐普通和置顶根评论的回复树。恢复时验证已存页指纹。
4. Transformer 还原父/根/祖先关系；隔离、未覆盖位置和未映射评论计入部分范围说明。
5. Standard 字节、上下文和配置冻结。批次完整校验成功后，才计入可复用结果。
6. 全部有效输入完成语义分析后，真实公式计算 payload，原子发布 latest。
7. Web读取结果；点击导出将同一 payload 送入本地 PNG 排版器。

## API

| 方法 | 路径 | 内容 |
| --- | --- | --- |
| GET | `/api/health` | 服务能力和活动任务 |
| GET | `/api/latest` | 最近成功run或null |
| GET | `/api/sample` | 公开合成payload |
| GET | `/api/runs` | 本地保存的任务 |
| POST | `/api/analyze` | `{"input":"BV…","mode":"full","limit":null}` 或quick+10–200 |
| GET | `/api/runs/<id>` | 任务状态 |
| POST | `/api/runs/<id>/pause`、`/resume` | 保存检查点或继续 |
| GET | `/api/runs/<id>/result` | 已成功/部分完成的结果 |
| POST | `/api/runs/<id>/export` | 创建/验证PNG，返回预览和下载URL |
| GET | `/api/runs/<id>/poster?download=1` | PNG下载；省略参数则预览 |

POST 使用 JSON 对象；暂停、恢复、导出也发送 `{}`。服务拒绝跨站提交。开发时 Vite 将 `/api` 代理到后端；这仍是本地应用，没有远程部署的身份认证/多租户设计。

## 资源与私有数据

`schemas/` 提供可读的契约副本，运行时使用随Python包安装的 `resources/*.json`；测试保证两份字节一致。Prompt与通用别名也随包安装。界面和演示文件来自源码仓库，因此本轮使用完整仓库 + 常规包安装；单独安装wheel不包含完整前端。

`BFSI_DATA_DIR` 默认是根目录 `var/`。其中 `runs/<run_id>/` 包含 raw、Standard、semantic、payload、exports、状态和检查点。原件与模型输出保留是为了本地恢复和可追溯，不能因“Schema已去掉账号字段”就公开此目录。建议单独备份。
