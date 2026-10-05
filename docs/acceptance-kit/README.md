# 剪映对照外部验收包

此目录把剩余的外部验收整理成可执行材料。空白表单和预填任务不是测试结果；只有真实执行后填写的数据才可用于关闭验收。

## 独立剪辑体验盲测

- 任务说明：[`blind-editor-tasks.md`](blind-editor-tasks.md)
- 逐任务记录表：[`blind-test-results.csv`](blind-test-results.csv)
- 每位参与者总结：[`participant-summary-template.md`](participant-summary-template.md)
- 多人身份参考标注说明：[`multi-person-identity-annotation.md`](multi-person-identity-annotation.md)
- 多人身份逐帧记录表：[`multi-person-identity-annotations.csv`](multi-person-identity-annotations.csv)
- 固定素材包来源、许可和哈希：`../../output/acceptance/jy-camera-commons-pack-20260926/source-manifest.json`
- 可编辑起始工程：`../../output/acceptance/jy-camera-commons-pack-20260926/web-workflow/cutvoke-six-clip-sample.cutvokepack.zip`
- 任务 1 的未使用素材：`../../output/acceptance/jy-open-license-samples-20260926/afn-naples-crosswalk-safety.webm`。它是美国联邦政府作品，在美国为公有领域；来源和 SHA-256 记录于 `../assets/open-license-qa-media-20260926.json`。主持人应预先将它导入素材库但不放入时间线，保证每位参与者看到相同初始状态。

安排三名没有参与 CutVoke 实现的剪辑用户，使用同一台已安装好应用的机器、相同浏览器和独立重置的起始工程。测试期间不给操作提示；主持人只记录结果。逐人按任务顺序完成后，将每项数据写入 CSV，并各填一份总结。不要在表内记录姓名、邮箱或原始屏幕录制。

## 自然人声 ASR 人工校对

- 逐字校对工作页：[`asr-human-review.html`](asr-human-review.html)
- 当前工作页使用完整 295.644 秒 CC BY 3.0 英语采访，音轨与 SHA-256 固定；预填文本是 105 条 Commons 社区字幕，只作听校草稿，不是正确答案。
- 校对人需完整听完音频，逐字修订并将听不清的位置标注为 `[听不清]`。必须勾选完整听校确认，填写匿名代号和日期，才能下载纯文本逐字稿及独立 JSON 校对记录；测试负责人核对来源哈希后再评分。
- 评分应同时报告语言、口音、人工稿版本、CER/WER、推理速度以及取消/重试与保存/撤销/导出流程。自然人声评分门槛不能由社区字幕暂定分替代。
- 全片来源、原始社区字幕和临时 ASR 对照保存在 `../../output/acceptance/jy-r13-asr-wikimedia-caption-reference-20260926/`；2026-09-27 的 5 分钟基准及其限制记录在 [`LOCAL_ASR.md`](../LOCAL_ASR.md) 与 `../../output/acceptance/jy-r13-asr-full-interview-auto-fallback-20260927/`。

## 发行环境输入模板

项目方可从 [`production-release-inputs.template.json`](production-release-inputs.template.json) 填写非秘密的发布信息。模板不含私钥；签名密钥只应保存在项目方控制的本地密钥库或 CI Secret。

## 正式资源发行环境

JY-R17 生产验收仍需项目方给出真实发布者 ID、HTTPS 目录地址、CDN/托管目标和域名管理责任人。密钥应由项目方在本地密钥库或 CI Secret 中生成，不放入此目录或对话。准备并验证生产发布配置后，记录版本、签名公钥指纹、安装/续传/启用/回滚结果与客户端信任校验结果。

## JY-R20 多人遮挡跟踪本地进度

已实现 SAM2 多提示帧纠正流程：用户可按源素材时间预览并保存后续正向/排除点，任务 API 与本机 worker 传递有序提示帧；输入限制为最多 16 帧、每帧最多 8 点，并校验提示时刻位于片段源素材范围内。现在会从最早提示帧向前、向后传播，并验证全片每帧都返回遮罩；Windows 进度文件遇到短暂共享锁时会重试。

- 验证：`tests/test_person_cutout.py` 20 项通过；`web/app/e2e/person-cutout.spec.ts` 2 项通过；当前全仓库 pytest 为 227 项、504 个子测试通过；最新 Playwright 全量 76/76 通过，Web TypeScript/生产构建通过。
- 最新真实本机管线：在 12 秒首提示后生成 725 帧、24.17 秒 ProRes 4444 透明 MOV，保留 AAC 音频；1920×1080、30 fps，ffprobe 确认 `yuva444p12le`，FFmpeg 全片解码通过。任务统计跟踪 558/725 帧、丢失 167 帧。
- 视觉复核显示 10 秒反向样本只有 692 个 Alpha≥128 像素，仍是小块遮罩；这些数值不能证明身份正确。当前只关闭双向传播代码与技术输出链路，素材逐帧身份真值、身份切换率和遮挡后恢复率尚未验收，JY-R20 仍未通过。
- 最新复核记录：[`bidirectional-acceptance.json`](../../output/acceptance/jy-r20-person-multicross-20260927-bidirectional/bidirectional-acceptance.json)、[`midpoint-frame-contact.png`](../../output/acceptance/jy-r20-person-multicross-20260927-bidirectional/midpoint-frame-contact.png)。早期单向运行仍保存在 `../../output/acceptance/jy-r20-person-multicross-20260926/`。试验素材来自 [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Scramble_Crossing_at_Robinson_Road_in_Singapore_-_September_2022.webm)，标注作者与 CC BY 4.0 许可；素材和转码仅用于本地验收，不进入产品资源包。
- 另有本机候选预标注材料：[`JY-R20 候选包说明`](../../output/acceptance/jy-r20-person-identities-singapore-20260927/README.md)、[`A/B 逐秒预标注 CSV`](../../output/acceptance/jy-r20-person-identities-singapore-20260927/candidate-ab-preannotation.csv)、[`模型对照图`](../../output/acceptance/jy-r20-person-identities-singapore-20260927/candidate-ab-model-comparison.png)和[`诊断数据`](../../output/acceptance/jy-r20-person-identities-singapore-20260927/candidate-evaluation.json)。这些记录来自 Codex AI 预标注，不是独立真值；15–16 秒 B 的身份尚未确认，身份准确率仍未评分。
- 关闭验收仍需按 [`multi-person-identity-annotation.md`](multi-person-identity-annotation.md) 对可区分目标逐帧建立独立真值，并统计身份切换、遮挡后恢复率和丢失区间。

### 逐帧标注工作页

- 打开 [`multi-person-identity-review.html`](multi-person-identity-review.html)，载入 `jy-r20-person-multicross-20260926/singapore-scramble-crossing-1080p.webm`，可核对页面显示的 SHA-256 是否匹配来源清单。
- 导入 [`AI 候选 CSV`](../../output/acceptance/jy-r20-person-identities-singapore-20260927/candidate-ab-preannotation.csv)。工作页会将候选 `B-candidate` 映射为模板目标 `B`，便于定位后逐帧修订。
- 请由未参与 CutVoke 实现的复核者独立查看视频，填写新的匿名代号；每次保存前勾选已核对确认。浏览器导出的 CSV 只包含人工复核代号保存的行，未核实的 AI 候选仍标为 `codex-ai-preannotation`，不会作为真值导出。
- 工作页只在浏览器本机处理视频和草稿。独立身份、完整帧覆盖、遮挡起止和跟踪结果仍须由评测负责人核验；工作页本身、勾选确认或一份不完整 CSV 都不代表 JY-R20 通过。
- 为避免把大型视频整体读入浏览器内存，超过 512 MiB 时会跳过 SHA-256 和自动草稿保存；完成标注后请及时导出，并由主持人核对源视频。

## 状态边界

本机工程和测试用例不等于外部用户体验结果。没有真实 ASR 人工校订稿、真实多人遮挡身份标注、三位独立剪辑用户数据及正式发行环境时，对应项仍保持“部分实现/未验收”。
