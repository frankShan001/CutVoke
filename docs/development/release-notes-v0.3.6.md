# CutVoke v0.3.6 candidate / 候选版（未发布）

## Frozen scope / 冻结范围

This candidate is based on `3bdd35cb409f7a6ea2aff37a8657e2b091f80052`.
Only version metadata and these release notes are added. It deliberately excludes
later project-format migration and thumbnail-seeking changes. It is not a release,
a tag, or a claim that candidate acceptance has passed.

本候选固定在上述提交，仅追加版本元数据和说明，不纳入其后的工程格式迁移及缩略图寻帧变更。
候选准备不等于发布，也不等于已通过完整验收。

## Changes since v0.3.5 / 相对 v0.3.5 的主要变化

- Preserve alpha-marked VP8/VP9 WebM inputs; rebuild corrupt timeline PNG caches.
- Repair short-audio export tails and defer export-path defaults while users edit.
- Improve native audition startup, stale keyframe selection and cancellation handling.
- Add safe human editing takeover, editable generated-source handoffs and lease/revision review.
- Add offline MCPB package validation, practical MCP guides, local audio inspection and editing examples.

- 修复透明 VP8/VP9 WebM 输入与损坏缩略图、波形 PNG 缓存复用。
- 修复短音频导出尾部及导出路径输入过程中被默认值覆盖的问题。
- 改善原生试听启动、过期关键帧选择和取消处理。
- 增加安全人工接管、可编辑生成源码交接与租约/修订审阅。
- 增加离线 MCPB 包校验、MCP 指引、本地音频检查和剪辑示例。

## Known boundaries / 已知边界

- Intermittent audition/keyboard timeouts are not proven fully fixed (#71).
- Split-then-left-trim manual keyframe alignment remains a known risk (#55).
  The newer project-format migration is not included in this frozen candidate.
- Export-window cache recovery may fail closed in frozen packages: export remains
  available, but retries may not reuse completed windows. Do not advertise resume
  support for downloads without per-platform package evidence.
- Long portrait captions have shown horizontal clipping in a Linux render probe;
  browser parity and other platforms remain unverified.
- Real MCP client installation, cross-version migration and Registry publishing
  are separate from offline MCPB validation (#47).
- Native GPU/audio devices, quarantined first launch and subjective artistic or
  listening quality are not established by hosted CI. Existing signing limits remain.

- 间歇性试听/键盘超时尚未全部归因；分割后左裁剪的手动关键帧仍有错位风险。
- 冻结包中的导出窗口缓存恢复可能安全关闭，重试不保证复用；不得宣传未经各平台确认的断点复用。
- Linux 竖屏长字幕实测存在横向裁切，浏览器一致性及其他平台尚未验证。
- 离线 MCPB 校验不代替真实客户端安装、跨版本迁移或 Registry 发布。
- 托管测试不代表实体设备、首次启动、听感或艺术质量验收。

## Candidate acceptance gate / 候选验收门槛

All items below are pending for the new candidate SHA until recorded evidence exists:

- [ ] Complete Python matrix and complete browser suites, with failures and skips disclosed.
- [ ] Windows x64, macOS ARM64, macOS Intel and Linux x64 frozen apps and MCPB checks.
- [ ] Exact source SHA and internal 0.3.6 versions in each artifact; record build IDs and SHA-256.
- [ ] Explicit per-platform export-window cache capability and retained known limitations.
- [ ] Review final evidence and obtain the owner's release decision before tagging/publishing.

上述项目均须针对新候选 SHA 完成，不沿用旧提交结果。关闭程序、备份工程并保留素材目录后再更新。
旧版本和下载保持不变。用户决定发布前，不创建标签或公开 Release。
