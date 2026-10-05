# 天气与氛围叠加贴图（2026-09-26）

按用户建议使用内置 ImageGen 一次生成 4×4 RGBA 贴图图集，再逐格提取。原始图集为 [`docs/assets/weather-atmosphere-overlay-atlas-20260926.png`](assets/weather-atmosphere-overlay-atlas-20260926.png)，裁切与 SHA-256 清单（含完整生成提示词）为 [`docs/assets/weather-atmosphere-overlay-atlas-20260926.extraction.json`](assets/weather-atmosphere-overlay-atlas-20260926.extraction.json)，透明底联系表为 [`docs/assets/weather-atmosphere-overlay-contact-20260926.png`](assets/weather-atmosphere-overlay-contact-20260926.png)。提取和登记脚本分别为 `scripts/extract_weather_atmosphere_atlas.py` 与 `scripts/register_weather_atmosphere_atlas.py`。

本批包括细雨、密集雨幕、镜头水滴、飘雪、雪花、低空薄雾、体积阳光、暖色窗光、尘埃、樱花、秋叶、萤火、泡泡、余烬和风中草籽，共 15 项。右下第 16 格生成了大面积高不透明度的青绿色底板，叠到实拍视频会遮挡/染色画面，因此没有入库；排除原因保存在提取记录中。

素材位于“贴纸 / 特效贴图”，保留 ImageGen 生成的 Alpha 通道，支持搜索、预览、独立叠加轨、变换、保存、撤销/重做和导出。来源标为项目内部原创 AI 生成，不单独授权再分发。机器工作流审计与逐项透明素材/合成画面复核完成后，结果再计入合格内容；这批贴图本身不证明在所有真实摄影素材上的商业观感。

15/15 通过应用、变换、保存重开、撤销/重做、入出场、预览和 MP4 导出一致性机器审计，并按当前 PNG/预览哈希记录逐项视觉决定。浏览器 E2E 实测分类显示 47/420、来源展开、卡片预览和贴图插入独立叠加轨。资源包升级到 v1.23.0：619 项资源、3,469 个登记文件；离线 ZIP 为 46,391,111 字节；`effect_coverage.py` 报告 189 项合格预设、420 项合格贴纸、609 项合格内容、候选 0、密度缺口 0。合成审核用的是深浅渐变画面，不是原生相机素材。
