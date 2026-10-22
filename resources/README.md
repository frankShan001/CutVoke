# 资源制作源文件

此目录保存可追溯、可用于重新制作内置资源的输入与说明，不是用户工程素材库。

| 位置 | 内容 |
| --- | --- |
| [source/materials/](source/materials/README.md) | 原始贴纸/纹理图集、提取坐标、来源、许可原文和输出校验值 |
| [source/brand/](source/brand/README.md) | 已选定的 CV 图标母版、生成记录和平台导出清单 |

应用直接使用的成品位于 `src/cutvoke/assets/`；正式文档配图见 [docs/images](../docs/images/README.md)。

不要在这里保存素材下载缓存、裁切试验、旧成品备份或用户原片。它们应保存在 `.artifacts/` 或仓库外。保留来源说明不等于授予额外授权，具体许可按各资源记录执行。

当前源文件校验值和已发布素材中的旧来源路径见 [来源索引](source/provenance.json)。索引仅收录仍在使用的制作源文件和文档资源，不依赖个人开发档案。目录规则与提取流程见 [项目目录说明](../docs/development/project-layout.md)。
