# 从开发阶段目录到功能目录

发布副本从已完成的`production`出发，并显式纳入它仍依赖的历史源码。旧目录不移动、不重命名；运行数据不导入公开仓库。

| 原位置（相对旧项目） | 发布副本 | 处理 |
| --- | --- | --- |
| `production/bfsi/collector.py` | `src/bfsi/collection/collector.py` | 包导入替代历史文件动态加载；运行目录独立 |
| `task 01/programmatic_validation/fetch_first_page.py` | `src/bfsi/collection/http_client.py` | 提取HTTP/WBI运行所需函数，移除旧任务执行入口与样本常量 |
| `production/bfsi/browser_capture.py` | `src/bfsi/collection/browser_capture.py` | 保留文件桥协议，移除旧布局探测与个人路径 |
| `production/bfsi/cleaner.py` | `src/bfsi/transform/cleaner.py` | 显式包导入，来源锚点与写入范围指向私有数据目录 |
| `task 03/transformer.py` | `src/bfsi/transform/transformer.py` | 保留转换逻辑 |
| `schema v0.1/validate_schema_examples.py` | `src/bfsi/transform/validation.py` | 保留Schema/业务/raw验证；剥离旧历史验收入口 |
| `schema v0.1/*schema_v0.1.json` | `schemas/`和`src/bfsi/resources/` | 冻结契约字节一致 |
| `production/bfsi/semantic.py` | `src/bfsi/semantic/runner.py` | 保留批次和恢复；CLI通过显式配置/PATH查找，兼容现有macOS应用捆绑CLI |
| `production/bfsi/resources/*` | `src/bfsi/resources/` | 保留Prompt与语义契约 |
| `production/bfsi/indices.py` | `src/bfsi/index/calculator.py` | 资源改随包读取；payload来源改稳定包内标识并保留实际字节哈希 |
| `task 06/scripts/index_math.py` | `src/bfsi/index/math.py` | 原始字节复制，公式未改 |
| `task 06/target_aliases_v0.1.json` | `src/bfsi/resources/target_aliases_v0.1.json` | 只保留通用宏观词，删除特定创作者别名 |
| `production/bfsi/jobs.py`、`server.py` | `src/bfsi/server/` | 独立运行目录、`python -m bfsi.server`入口 |
| `production/bfsi/poster.py` | `src/bfsi/reporting/poster.py` | 保留结构化出图与指纹缓存；加入合成标识/通用时区 |
| `production/dashboard` | `web/` | 保留最新React界面；移除旧数据文件和任务说明 |
| `production/tests` | `tests/` | imports与路径修复；真样本依赖替换为合成fixture，无缺样本跳过 |
| 根目录启动器 | `scripts/setup.sh`、`scripts/start.sh`、根Mac启动器 | 安装与启动分离，无个人机器路径 |

Task02字段调查、Task04历史验收、Task05真实请求/响应、Task06真实报表、Task07旧静态入口、`production/evidence`与`production/runs`留在私有原项目，未作为公开资源复制。公开历史说明止于此映射，不把阶段文件夹继续用作运行依赖。

## 回滚与老任务

新副本不会重写原项目，原启动器仍使用原production。旧run里的相对路径与冻结哈希绑定旧布局，不能直接搬入新`var/runs`后当作兼容任务续跑。需要查看或继续旧任务时使用原项目；新仓库创建新任务。

迁移前已创建完整私有压缩快照与逐文件SHA256清单，具体本机位置在交付说明中，未写入公开仓库。恢复时先解压到独立空目录并校验清单，再决定是否替换；禁止盲目覆盖原项目。新发布副本可独立保留或移除，不影响旧项目。
