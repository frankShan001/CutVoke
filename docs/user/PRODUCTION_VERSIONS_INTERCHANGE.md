# 制作版本、多机位与工程互换

这三个入口位于左侧“当前工程”面板中，展开对应区域即可使用。它们操作保存的结构化时间线；人工操作、外部 Agent、保存和撤销共用命令服务。

## 画布与制作版本

“画布与制作版本”可以切换活动序列、修改实际宽高与帧率，或把当前完整剪辑复制为另一个可编辑版本。画布支持 2–8192 的偶数尺寸，帧率支持 1–240。构图有三种：完整保留画面（contain）、填满并居中裁切（cover）、拉伸（stretch，保持旧工程的默认行为）。位置与像素字号随新画布缩放；字幕的默认字号继续由画布自动决定。修改帧率保留绝对时间，不会改变原有剪辑速度。

命令为 `sequence.configure`、`sequence.duplicate`、`sequence.switch`。复制版本会重新生成内部对象 ID 并更新关联引用，包括嵌套序列。版本存入同一工程，画布修改、复制与切换均可撤销。

填写“批量输出路径前缀”后，“全部版本排队导出”会为各序列提交独立导出任务，后缀中包含序列 ID。每个任务冻结对应序列的真实画布、帧率、构图和时间线；排队后再编辑不会改变已提交任务。命令 `export.enqueue` 和 HTTP `POST /projects/{id}/exports` 均支持 `sequenceId`，无需改变编辑器的活动序列。基础导出面板可查看、取消已提交的任务。

## 多机位

先把机位放在各自视频轨上，再“建立多机位组”。“在播放头切换机位”保存明确的时间切点，播放头前的选择不会改变；同一时间再次切换会替换该切点。正常预览与最终导出共用选择表达式，机位原片不会被切碎，原来的片段时钟与效果历史保留。未加入机位组的贴纸、字幕和其它画面层继续参与合成。节目音频固定来自初始机位，不随画面切点反复换音。

“更新机位监看”读取各机位在当前播放头的真实合成帧，用于逐帧检查。它是用户触发的同步帧监看，不宣称多路实时视频播放。未覆盖该时刻的机位会返回缺帧提示。

“按音频波形同步”比较机位实际源音频的 RMS 能量波形，以归一化互相关估计偏移。当前接受每个机位一个连续普通 1x 片段，需至少两秒非静音素材，最多分析前 60 秒，默认搜索 ±10 秒、上限 ±30 秒，时间分辨率为 10 毫秒。目标机位应先同步再添加效果或关键帧。低置信度或重复波形导致歧义时，命令会拒绝修改，提示人工对齐。

同步结果包括 `method`、`lagSeconds`、`confidence`、`ambiguityMargin` 和实际平移/裁掉前缀的秒数，存入工程并返回给调用方。超出时间线零点的目标前缀会相应裁去，源入点一并移动。同步本身可撤销。该方法不修复长时间录制的时钟漂移；同步后仍应检查头尾画面和声音。

命令为 `multicam.create`、`multicam.switch {trackId,time}`、`multicam.sync`、`multicam.remove`。切点时间持久保存为有理数，旧的省略 `time` 的全局切换命令仍可使用。机位监看为 HTTP `GET /projects/{id}/multicam-monitor?trackId=...&time=...`。

## 工程互换

`project.interchangeExport` 返回标准文档正文和结构化 `diagnostics`，可选写到绝对 `outPath`；写文件时同时保留 `.diagnostics.json`。Web 可下载文档，或读取本地文件正文后通过 `project.interchangeImport` 导入为新序列。导入可撤销，原有序列保留。文件只引用素材，不打包媒体；丢失的本地媒体会返回重新链接提示。

目前明确支持以下子集：

| 格式 | 支持内容 | 主要限制 |
| --- | --- | --- |
| OTIO JSON | Timeline / Stack 下的平面视频、音频轨；Clip / Gap；本地 ExternalReference；源入点、时长、轨道间相对时间 | 不映射转场、嵌套栈、效果、关键帧、变速、字幕与音频总线；画布与可见/静音状态包含 CutVoke metadata 扩展 |
| CMX 3600 EDL | 单视频轨的 C 剪切、帧对齐源/记录时码、SOURCE FILE 本地素材注释 | 只支持 1–60 的整数非丢帧时码，最多 999 个事件；无额外音轨/层/特效；没有 FPS 注释时明确报告按 30 fps 解释 |
| FCPXML 1.10 | 单一主故事线的平面 asset-clip、gap、本地 media-rep、分数秒时长与画布格式；真实探测并声明单路单声道/立体声嵌入音频 | 不支持连接片段、次级故事线、多机位、复合、调速、效果和字幕；多音频流/多声道、无法探测的源明确返回诊断并拒绝输出 |
| AAF | 当前不支持 | 返回 `UNSUPPORTED_FORMAT`；不生成改扩展名的假 AAF 文件 |

默认“拒绝有损互换”开启。出现未映射的内容时不会生成不完整输出或提交导入；关闭后允许显式导出/导入支持的部分，同时保留每个遗漏项目的路径、代码与说明。FCPXML 导出会通过 ffprobe 核验每个唯一源的嵌入音频，以 `hasAudio`、`audioSources`、`audioChannels`、`audioRate` 声明实际音轨。无音轨源明确标为 `hasAudio="0"`；无法可靠探测、多个音频流或不支持的声道布局会返回 `FCPXML_EMBEDDED_AUDIO_UNVERIFIED`，即使关闭有损限制也不生成可能错误静音的文档。带 48 kHz 立体声 AAC 的真实 MP4 已验证声明与导入后可听输出。

基础 OTIO 输出已用真实 OpenTimelineIO 0.18.1 解析校验；EDL/FCPXML 做了本项目支持子集的导出再导入验证，尚不等于已在各商业编辑器逐一人工验收。

实现依据：[ASWF OTIO 序列化字段](https://opentimelineio.readthedocs.io/en/v0.18.1/tutorials/otio-serialized-schema-only-fields.html)、[Apple FCPXML 文档结构](https://developer.apple.com/documentation/professional-video-applications/creating-fcpxml-documents)、[Apple 素材及嵌入音频声明示例](https://developer.apple.com/documentation/professional-video-applications/describing-final-cut-pro-items-in-fcpxml)、[OTIO 格式适配说明](https://opentimelineio.readthedocs.io/en/v0.15/tutorials/adapters.html)。
