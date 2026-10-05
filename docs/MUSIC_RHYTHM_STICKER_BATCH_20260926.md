# 音乐节奏贴纸图集（2026-09-26）

## 交付

为视频编辑器新增“音乐节奏”贴纸类目。使用 ImageGen 生成一张 4×4 真透明图集，裁切为 16 张独立 RGBA 贴纸；每张可搜索、预览、调整、放入独立叠加轨，并进入离线资源包。

- 原始图集：[`docs/assets/music-rhythm-overlay-atlas-20260926.png`](assets/music-rhythm-overlay-atlas-20260926.png)
- 透明裁切接触表：[`docs/assets/music-rhythm-overlay-contact-20260926.png`](assets/music-rhythm-overlay-contact-20260926.png)
- 实际画面合成接触表：[`docs/assets/music-rhythm-review/sticker-音乐节奏-rendered.png`](assets/music-rhythm-review/sticker-音乐节奏-rendered.png)
- 透明图形接触表：[`docs/assets/music-rhythm-review/sticker-音乐节奏.png`](assets/music-rhythm-review/sticker-音乐节奏.png)
- 网格坐标、裁切框和逐项 SHA-256：[`docs/assets/music-rhythm-overlay-atlas-20260926.extraction.json`](assets/music-rhythm-overlay-atlas-20260926.extraction.json)
- 哈希绑定的视觉复核：[`docs/assets/music-rhythm-visual-review-20260926.json`](assets/music-rhythm-visual-review-20260926.json)
- 可复跑的裁切与登记：[`scripts/extract_music_rhythm_atlas.py`](../scripts/extract_music_rhythm_atlas.py)、[`scripts/register_music_rhythm_atlas.py`](../scripts/register_music_rhythm_atlas.py)
- 源贴图：`src/cutvoke/assets/stickers/audiofx_*.png`

## 16 项清单

青珊瑚波形光带、紫色低音脉冲环、金色弧形均衡器、霓虹音符轨迹、彩虹频谱光环、青色点阵声波、珊瑚节奏波形、珍珠声波弧线、薄荷节拍光束、蓝紫频谱徽章、琥珀唱片光环、洋红麦克风光晕、星光节奏曲线、蜜桃歌词聚光、青金声能螺旋、彩虹立体声波形。

## 生成提示词

初始生成：

```text
Use case: standalone transparent overlay stickers for a desktop video editor, suitable for music videos, lyric edits, and social clips.
Asset type: one square image atlas of exactly 16 distinct stickers, arranged in a perfectly even 4 by 4 grid, in reading order. Every sticker must be centered in its own cell, at the same visual scale, with generous transparent padding and an empty gutter to make exact grid cropping safe.
Background: genuine transparent alpha across the entire canvas; no checkerboard, no colored or white background.
Subjects, in reading order:
1. flowing cyan-to-coral audio waveform ribbon with a few tiny beat sparks;
2. violet concentric bass pulse rings with a crisp center;
3. arched warm-gold equalizer bars with a few glints;
4. graceful cluster of floating cyan and magenta music-note symbols with no words;
5. circular rainbow audio-spectrum halo, clean ring silhouette;
6. dotted aqua sound-wave trail bending gently upward;
7. coral heartbeat-style rhythm line with a small glow, no medical icon;
8. pearl-white acoustic contour arcs with soft violet accents;
9. mint-green radial beat burst with short musical pulse strokes;
10. blue-violet frequency bars in a compact circular badge;
11. amber vinyl-record orbit rings and tiny light flecks, no record body;
12. elegant magenta microphone-shaped light aura made only of luminous strokes, no stand or text;
13. scattered star-shaped beat sparks linked by a thin cyan rhythm curve;
14. soft peach and lilac lyric spotlight ellipse with a delicate waveform edge;
15. teal-and-gold sound-energy spiral made from a few clean ribbon lines;
16. compact multicolor stereo waveform stack with luminous peaks.
Style: polished editorial motion-graphics overlays, clean vector-like luminous strokes with subtle dimensional glow, coherent premium palette, sharp crop-safe silhouettes, no opaque shadows beyond the art.
Constraints: preserve actual alpha transparency; exactly one distinct subject in each of 16 equal cells; no touching or overlapping neighboring cells; no borders or dividers; no labels or numerals.
Avoid: photographs, scenes, people, instruments as solid objects, UI panels, backgrounds, checkerboard, grain, speckles, text, logos, watermarks, grid lines, frame edges, extra objects, repeated designs.
```

图集修整提示词：

```text
Edit the supplied image atlas only to improve crop-safe spacing. Preserve the same exact sixteen music-visualization overlay designs, their reading order, colors, shapes, glow style, and the exact square 4 by 4 grid layout. Scale every complete sticker artwork uniformly down to about 68 percent of its current size and center it within its own original grid cell, so each sprite and every glow stays fully inside the central 68 percent of that cell with generous genuine transparent alpha margins on all four sides. Keep all 16 cells, no missing or added elements, no touching cell borders, no neighboring-cell overlap. Preserve the actual transparent background and the original canvas aspect ratio. Do not add any grid lines, dividers, checkerboard, background, labels, text, grain, or extra particles.
```

## 处理与验收

- 图集尺寸为 1254×1254，真实 Alpha 范围 0–254；按四等分网格及每格的图形边界裁切，裁切安全边距 16 px，再缩放至 320×320 并留透明内边距。
- `scripts/generate_sticker_previews.py --prefix cutvoke.sticker.audiofx_` 为 16/16 项生成带底图 MP4 预览，均可见。
- `scripts/audit_sticker_library.py --prefix cutvoke.sticker.audiofx_` 的插入、变换编辑、保存重开、撤销/重做、动画、预览和 MP4 导出机器审计 16/16 通过。
- 每项均检查透明裁切表和深浅渐变合成预览；逐项观察及素材哈希保存在 `music-rhythm-visual-review-20260926.json` 和对应 `.visual.json` 侧车文件。
- 浏览器回归核对分类显示 16 项、素材卡预览、来源详情、独立贴纸轨插入和资源哈希。
- 内置资源包更新为 v1.22.0，共 604 项资源和 3,379 个文件；完整来源/许可字段与文件哈希清单通过构建检查。

素材为项目内部原创 AI 生成内容，不单独授权再分发。预览为中性渐变合成，不代表独立用户评估或复杂实拍下的商业观感验收。
