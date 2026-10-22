# 内置素材制作输入

这里保留原始图集及对应的提取记录（`*.extraction.json`，少量旧记录命名为 `*-extraction-*.json`）。记录包含生成来源、裁切坐标、源图及输出 SHA-256；已有旧版图集用于解释历史裁切和审核结果，不能仅因文件名带旧日期而删除。

| 查找内容 | 文件名关键词 |
| --- | --- |
| 基础图标、标注贴纸 | `basic-*`、`annotation-*`、`video-annotation-*` |
| 食物、旅行、宠物、教育、庆祝贴纸 | `food-*`、`travel-*`、`pet-*`、`education-*`、`celebration-*` |
| 光效、氛围、人物叠加、纹理背景 | `*-overlay-*`、`*-texture-*`、`person-fx-*`、`video-background-*` |

制作工具在 `tools/assets/`：提取脚本读取这里的源图，将新提取报告写到 `.artifacts/assets/reports/`，联系图/预览写到 `.artifacts/reviews/`；注册脚本读取这里已归档的提取记录。更改内置成品和正式记录前，检查透明边缘、裁切安全区、实际渲染与来源许可。只将通过审核且有必要保留的记录归档回来。

提取 JSON 保留原始字节，其中旧路径通过 [来源索引](../provenance.json) 的 `legacyPath` 定位。配图与审核拼图不在此目录。
