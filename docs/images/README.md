# 正式文档配图

[readme/](readme/README.md) 保存中英文 README 使用的真实编辑器截图、品牌横幅及截图来源记录。

只收录被正式文档使用、具有明确用途的图片及必要来源说明。素材制作原图放在 [resources/source](../../resources/README.md)，临时截图和测试输出写入 `.artifacts/`，个人历史对比图与开发记录保存在仓库外。

[social-card.png](social-card.png)（1280×640）是 GitHub Pages 文档站使用的分享图，也可作为仓库 Social preview 上传。文件名不用 preview：仓库图片检查会拒绝文件名中含 review 的图，而 preview 含有该子串。它不是未改动的编辑器截图：左侧复用英文 README 的 [editor-overview.png](readme/en/editor-overview.png)，右侧是指定标题。在仓库根目录执行 `python site/render_social_preview.py` 重新生成（需要 Pillow；提交件使用 DejaVu Sans 渲染）。

`social-card.png` (1280×640) is the share image for the GitHub Pages site. The filename avoids `preview` because the repository image check rejects `review` inside a `docs/images` filename. It composites the English editor overview; it is not an unmodified capture. Regenerate it from the repository root with `python site/render_social_preview.py`.
