# 运行、恢复与开发

使用仓库根README中的安装步骤。Python依赖记录在`pyproject.toml`及固定版本`requirements.txt`；前端版本由`web/pnpm-lock.yaml`固定。首次需要联网安装依赖，已安装后合成演示完全离线。

## 常见操作

```bash
source .venv/bin/activate
python -m bfsi.server --no-open --port 8787
# 或使用已安装的 bfsi 命令，参数相同
```

默认会打开浏览器；`--no-open`关闭自动打开。若8787已被旧服务占用，关闭相应旧窗口或明确使用另一端口，避免误看原静态看板。前端开发代理固定指向8787，若更换后端端口需同步`web/vite.config.ts`。

`BFSI_DATA_DIR`可指向仓库外的私有目录。相同目录不可由多个服务并发管理；本轮为单进程应用，任务序列化只在本服务进程中生效。

## 暂停与恢复

页面“暂停分析”会停止新请求/模型批次派发，等待在途批次保存后才变为暂停。继续时校验已保存分页、Standard和模型输入配置，再复用确认成功的批次。

Ctrl+C会保存并等待在途任务退出。异常重启后，未完成任务标记为可继续；已发布成功/部分结果保持固定。如需重新采集已经发布的视频，创建新任务。不要手改检查点、模型元数据或冻结产物。

全量结束要求分页实际结束及数据完整性通过，无法取得、来源异常或隔离情况会明确变为部分范围。访问被拒绝时不会切换代理或绕过验证码。

## 图片

点击“导出结果图”后，预览、下载与磁盘文件绑定同一个run。图片位于`var/runs/<run_id>/exports/result_poster.png`，同目录manifest用于本地内容/指纹核对。默认1080px宽，高度按内容增长；中文字体路径通过`BFSI_POSTER_FONT`配置，时间通过`BFSI_TIMEZONE`（默认UTC）配置。

真实图片可能包含评论原文，导出动作不自动授权公开分享。README和仓库图片只使用人工合成例子。

## 浏览器文件桥（高级）

`python -m bfsi.collection.browser_capture --help`提供独立接入器。它消费外部浏览器正常导航取得的JSON文档封装，使用现有采集器产生的分页URL，并复用同一Cleaner及后续流程。它不会读取Cookie、不自动获得Chrome登录态，也不内置浏览器控制器。

输入需要已验证的视频映射文件、公开WBI图片配置，以及外部驱动读写的私有bridge目录。`requests/`记录待导航URL，驱动向`responses/`提交同URL、文档原文、捕获时间和transport标识。文件桥保存receipt，区分“浏览器文档UTF-8表示”与“HTTP原始传输字节”。完成Standard冻结后，run以暂停的semantic阶段出现在任务列表，由页面继续分析。协议细节以`BrowserBridge.request`为准；对应离线测试使用合成驱动。

公开副本没有搬入原浏览器的运行证据或真实配置，不承诺在未提供外部驱动时自动采集登录态评论。正常“开始分析”仍走独立HTTP路径。

## 排错

| 现象 | 检查 |
| --- | --- |
| 看板构建不存在 | 运行`pnpm --dir web run build` |
| Codex CLI不存在 / 模型不可用 | 检查`codex --version`、登录状态、`BFSI_CODEX_CLI`与账号模型权限 |
| JSON/模型事件验证失败 | 任务保留失败尝试；检查本地记录，勿上传真实内容；CLI协议变化可能需适配 |
| 中文字体不存在 | 安装Noto CJK或指定`BFSI_POSTER_FONT` |
| 页面显示部分采集 | 读取范围警告，不用平台总数替代实际成功范围 |
| Git忽略规则未排除已有文件 | 忽略只影响未追踪文件；检查Git索引并按发布说明处理 |
| macOS安装成功但找不到bfsi包 | 运行`.venv/bin/python scripts/check_install.py`检查安装；setup已采用常规包安装并包含资源检查；手动安装时使用`pip install .` |
