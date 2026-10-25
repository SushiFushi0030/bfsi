# 人工合成示例

本目录的30条评论、低位评论ID、BV/OID、视频标题与语义标签均由`scripts/demo.py`构造，不来自真实平台评论，也不是模型真实输出。BV只是格式占位，不应导航或提交真实采集。

- `semantic.json`：内部聚合输入的合成数据，标签人工指定。
- `payload.json`：同一输入经真实指数代码计算的结果。

从仓库根目录执行`python scripts/demo.py --check`核对可重复性。维护者明确需要更新时才使用`--write-examples`；它不会触碰真实任务。`--install`把结果安装为本地可导出的合成run，写入被Git忽略的运行目录。
