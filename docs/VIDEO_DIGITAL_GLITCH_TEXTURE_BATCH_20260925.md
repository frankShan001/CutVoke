# 数码故障叠加贴纸批次（2026-09-25）

## 素材

本批由 OpenAI 内置图像生成一次生成 4×4 透明图集，再以固定单元裁切为 16 张 PNG，登记在贴纸库“数码故障”分类。它们是可上时间线并可变换/设时长/加动画的静态叠加贴纸，不计作新的实时画面特效算子或动态贴纸。

- 原始生成图集：[digital-glitch-overlay-texture-sheet-20260925.png](assets/digital-glitch-overlay-texture-sheet-20260925.png)
- 透明底裁切表：[digital-glitch-overlay-texture-contact-sheet-20260925.png](assets/digital-glitch-overlay-texture-contact-sheet-20260925.png)
- 真实画面合成表：[digital-glitch-overlay-texture-preview-contact-sheet-20260925.png](assets/digital-glitch-overlay-texture-preview-contact-sheet-20260925.png)
- 原图哈希、格子坐标、透明像素统计和单项文件哈希：[extraction manifest](assets/digital-glitch-overlay-texture-sheet-20260925.extraction.json)
- 逐项视觉决定及观察：[visual decisions](assets/digital-glitch-texture-visual-decisions-20260925.json)
- 可复跑裁切：[extract_digital_glitch_texture_sheet.py](../scripts/extract_digital_glitch_texture_sheet.py)

16 项包括横向撕裂、RGB 色块错位、扫描光条、数据切片、跳帧残影、像素溶解、分色角芒、纵向同步漂移、棱镜碎片、信号断裂、波形中断、霓虹追踪框、数据斜掠、马赛克跳变、双影错帧和色差分离。

## 验收

- 原图为 1254×1254 RGBA；16 个约 313/314 像素方格均有真实透明与不透明像素；每格可见像素均超过 14,000，离裁切边缘 8px 范围内没有可见像素。
- 为“数码故障”类别生成器加入纹理叠加缩放规则；每项渲染成 320×180 的深到浅渐变预览，检查透明边缘与缩略图识别度。
- 16/16 逐项完成真实应用、变换编辑、保存重开、撤销/重做、预览与 MP4 导出，以及入场/出场动画审计；机器报告位于 `src/cutvoke/assets/sticker_previews/glitch_*.audit.json`。
- 16 项均以逐项观察记录完成人工视觉确认；透明裁切表和真实合成表均可复查。贴纸库每项登记生成来源与 MIT 项目许可字段。
- 内置资源包版本升至 v1.10.0：165 个预设、293 个贴纸、458 项资源、2,577 个登记文件。当前 38 个开放细分类的合格密度缺口为 0。

此批贴纸的逐项审核只说明当前图像、合成预览及机器编辑链路通过；不替代复杂真实摄影素材上的商业观感评估，也不证明外部发布服务、发布者身份或来源许可已独立核验。
