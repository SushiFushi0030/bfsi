# GitHub公开发布步骤

正式仓库为 [SushiFushi0030/bfsi](https://github.com/SushiFushi0030/bfsi)。以下为后续维护和重新发布时的参考步骤。只把本发布仓库加入Git，不要对包含旧Task、生产数据或备份的父目录执行`git add`。

## 1. 检查准备结果

```bash
source .venv/bin/activate
python scripts/check_public.py
python scripts/demo.py --check
python -B -m unittest discover -s tests -v
pnpm --dir web run build
pnpm --dir web run verify:data
```

阅读`docs/LICENSING.md`并决定许可；本轮未替你添加LICENSE。确认示例图片、README内容、贡献者署名和第三方素材的权利范围。

## 2. 查看将要提交的文件

正式仓库使用`main`。仅在准备全新仓库且尚未初始化时运行第一条命令：

```bash
git init -b main
git add .
python scripts/check_public.py
git diff --cached --stat
git diff --cached --check
git status --short
```

扫描器同时检查工作文件和整个Git索引，能发现“曾暂存秘密，后来只在工作区删除”这类情况。它是有限规则检查，不能替代人工判断图片、原文身份信息及历史提交。这个发布副本从干净历史开始，不导入原目录的历史。

确认后由所有者设置自己希望公开的Git姓名和邮箱。可使用GitHub提供的noreply邮箱，避免暴露个人邮箱；不要直接复制其他人的提交身份。

```bash
git config user.name '你希望公开的署名'
git config user.email '你选择的公开或GitHub noreply邮箱'
git commit -m 'Prepare BFSI portfolio repository'
```

## 3. 创建远端并推送（由所有者主动执行）

在GitHub网页创建空仓库，例如`bfsi`，不要另行初始化README、gitignore或许可文件，避免与本地冲突。选择公开之前再确认上一步的许可及安全检查。复制仓库页面显示的真实远端地址，替换下面占位值：

```bash
git remote add origin '你的GitHub仓库URL'
git remote -v
git push -u origin main
```

这些命令依据[GitHub官方本地源码导入说明](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)。已经存在远端时先用`git remote -v`核对，不要重复添加origin。

首次推送后检查Actions结果、README图像和Mermaid展示；将仓库简介设为“B站财经评论区情绪观察器：采集、语义分析、BFSI与可分享看板”。可将仓库置顶至个人主页。不要宣称尚未建立的准确率、市场预测能力或远端CI通过结果。

GitHub提供的[secret scanning](https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning)可作为额外防线，但不能保证发现评论身份数据。若误提交凭证，应撤销/轮换受影响凭证，并按实际情况清理历史，单独新增gitignore不能移除已泄露内容。
