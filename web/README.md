# BFSI 看板

React + TypeScript + Vite 前端。它通过本机 Python 服务读取任务与结果，支持新 BV 输入、全量/快速采集、暂停和继续、任务切换，以及结构化 PNG 导出。图表均读取结果 payload；前端不运行采集或语义模型。

## 构建与开发

需要 Node.js 22.12+ 和项目指定版本的 pnpm。在本目录执行：

```sh
pnpm install --frozen-lockfile
pnpm run build
```

构建后从仓库根目录启动 `bfsi`（参见根 README）。后端仅发布 `web/dist/`，不会发布运行目录、原始评论或模型日志。

开发时先在另一个终端启动默认端口 8787 的 Python 服务，再执行：

```sh
pnpm run dev
```

打开终端提示的本机地址。Vite 将 `/api` 代理到 `http://127.0.0.1:8787`，并设置相同的本机 Origin。`pnpm run preview` 只预览静态构建，实际分析功能需要后端；日常使用以后端入口为准。

## 示例与测试

首次打开且没有最近任务时，页面通过 `/api/sample` 展示 `examples/demo/payload.json`。它是明确标注的合成数据，不包含历史真实评论。仓库根目录的离线演示命令可以生成独立演示任务，以验证真实后端与 PNG 导出。

```sh
pnpm run verify:data
pnpm exec playwright install chromium
pnpm run test:browser
```

浏览器测试自动启动本地静态预览，使用合成 API 响应，覆盖指标、代表评论、键盘操作、360–1440px 布局、全量/快速提交、暂停恢复、结果清空、出图按钮与下载链接、无信号及错误恢复。它不会调用 B站或付费模型；真实后端和实际 PNG 由 Python 集成测试验证。输出位于被忽略的 `test-results/`。

如使用已有浏览器，可设置 `BFSI_CHROME_PATH` 为浏览器可执行文件路径；macOS 自动尝试已安装的 Chrome。可用 `BFSI_TEST_URL` 指定待测的前端地址，API 仍由测试拦截。

## 目录

```text
src/main.tsx       看板与分析/任务/出图交互
src/data.ts        payload 类型、字段校验、标签与格式化
src/styles.css     深色终端风格与响应式布局
public/favicon.svg 品牌图标；不包含用户数据
scripts/           合成示例和构建契约检查
tests/             离线浏览器测试
```

`dist/`、`node_modules/`、测试图片、浏览器缓存不纳入 Git。
