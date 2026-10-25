# 许可状态与选择

本次迁移没有在原项目中发现项目自有的 `LICENSE`。仓库所有者尚未授权选择开源许可，因此发布副本没有自动增加 MIT、Apache 或 GPL 许可，也没有把项目声明为“已开源”。公开展示源码与授予他人复用许可是两个决定。GitHub 官方说明，无许可时适用默认版权规则；公开仓库仍受 GitHub 条款中查看和 fork 权利的约定。[GitHub：Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

## 所有者可以怎样选

| 目的 | 可考虑的许可 | 需要接受的主要条件 |
| --- | --- | --- |
| 作品集展示后允许他人广泛复用，条款简短 | MIT | 允许商业使用、修改和再分发；再分发时保留版权与许可声明。 |
| 广泛复用，同时希望明确专利授权条款 | Apache-2.0 | 保留许可、版权和相关 NOTICE，标明修改；包含明确的贡献者专利授权及终止条件。 |
| 希望向他人分发的衍生版本继续提供相应源代码并使用相同许可 | GPL-3.0 | 分发受许可覆盖的软件/修改版须遵守对应源代码及同许可条件；具体边界以许可正文为准。 |

以上为选择方向，未替所有者作决定。MIT 可作为允许自由复用的作品集项目的简洁选项；如果目前只希望面试官阅读展示，可以先保持未授权复用的状态，并在 README 中说明。

MIT 条件参见 [GitHub Choose a License：MIT](https://choosealicense.com/licenses/mit/)；Apache 专利与再分发条件参见 [Apache License 2.0 正文，第 3–4 节](https://www.apache.org/licenses/LICENSE-2.0)；GPL 概览参见 [GitHub Choose a License：GPL-3.0](https://choosealicense.com/licenses/gpl-3.0/)，正式条件参见 [GNU GPL v3 正文](https://www.gnu.org/licenses/gpl-3.0.html)。

## 决定后再做的操作

1. 确认代码、文档、图形等内容的权利归属，以及是否存在其他贡献者或外部来源的限制。
2. 由所有者明确选择许可，在根目录添加对应完整许可正文，填写真实的版权持有人信息。
3. 同步 README 与包清单的许可说明，保留第三方原有的版权、许可与必要 NOTICE。
4. 逐项核对依赖和未来复制进来的代码；本项目所选许可不会改变第三方依赖本身的许可。

项目的软件许可不自动覆盖 B站评论、平台商标、用户头像、视频封面或模型服务条款。发布副本仅提供合成示例，未搬入真实评论数据。以上资料核对日期：2026-10-25。
