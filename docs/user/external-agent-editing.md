# 外部 Agent 剪辑接入

CutVoke 保存可编辑的时间线并执行剪辑。外部 Agent 负责理解需求、规划和视觉验收。编辑器无需大模型服务或生成服务。

可选本地神经网络抠像通过 `local_model_status` 和 `local_matting_*` 工具调用。AI 必须先判断当前模型及客户端是否支持读取真实工具图片；不支持或未知时不要调用视觉抠像流程，改由人工入口操作。界面协助请求采用持久队列与活跃 Agent 的有界等待，不自动唤醒空闲聊天。完整契约见[本机三维、可选模型与 AI 交接](LOCAL_3D_AND_MODELS.md)。

新建工程可指定独立 `directory`。通过 `project_directory` 读取素材、缓存、导出与备份位置；`media_import` 传 `projectId`，导出使用工程 `exports/` 内的绝对路径或相对文件名。移动、备份和旧工程迁移见[工程目录管理](PROJECT_DIRECTORIES.md)。

## 连接

Windows 安装版内置了独立的 MCP 引擎，可直接运行：

```text
%LOCALAPPDATA%\Programs\CutVoke\resources\engine\cutvoke-engine.exe mcp
```

MCP 客户端的 `command` 应填写展开后的绝对路径，`args` 填写 `["mcp"]`。默认与桌面编辑器共用 `~/.cutvoke/data/projects.sqlite`。测试时用 `--data` 指向独立的数据库副本。

服务在 MCP 初始化响应中提供入门 `instructions`。AI 首次接入先调用 `editor_help({})`，即可获取工作顺序、后续帮助入口和验收要求；不需要用户另装 Skill 或讲解接口。再调用 `runtime` 核对数据目录和 `project_list` 查找目标工程。升级不会替换已经运行的 MCP 进程；旧连接需要重新加载，才能看到新增工具。

客户端支持 MCP 文档资源时，可以读取 `cutvoke://guide/overview`、`cutvoke://guide/time`、`cutvoke://guide/editing`、`cutvoke://guide/errors` 和 `cutvoke://commands`。不支持或不自动读取资源的客户端，使用 `editor_help` 工具可以获得相同内容。

具体命令使用 `editor_help({"topic":"command","command":"clip.insert"})` 查询。返回的 `inputSchema` 描述 **payload**，不是 `command_apply` 的外层参数。效果命令再传 `effectId`，即可获取该效果注册表中的精确参数 schema。`commands_list` 默认也返回命令 schema；`commands_list({"includeSchemas":false})` 返回省略 `params` / `inputSchema` 的轻量目录，保留名称、描述、`previewSupported` 和批量命令限制，仍支持 `type` / `query` / `offset` / `limit`。选定命令后再查详情。不修改工程 revision 的命令仍可能录音、写文件或运行模型；`previewSupported` 只说明是否支持预演。HTTP 的 `POST /api/v1/agent/commands_list` 使用相同 JSON 参数和响应。

从剪辑意图开始检索，例如 `commands_list({"query":"字幕","includeSchemas":false,"limit":10})`。目录用于找到操作，不替代选材和剪辑判断。讲解或访谈可继续读取 `editor_help({"topic":"creative","guide":"explainer"})`：先保留观点与原句的素材时间码，检查句子前后语义，再查询 `transcript.delete` 的详情并预演影响；按已有授权提交后，试听剪点并检查画面是否支持所讲内容。节奏与成片质量的复核使用现有 `guide:"review"`；命令成功或预演合法不代表旁白自然、画面有说服力。

`editor_help({"topic":"recipes"})` 列出可执行范例，包括建立可编辑时间线、精确修改字幕和异步导出。范例里的 `<PROJECT>`、`<REVISION>`、`<ASSET>` 等占位符由 AI 用实际查询结果替换；用户只需说明剪辑需求并提供素材访问权限。工程不明确或缺少必要素材时，AI 仍需澄清这些信息。

## 工作顺序

| 步骤 | 工具与要求 |
| --- | --- |
| 找工程 | `project_list` 按名称搜索；`project_summary` 获取版本和时间线摘要。 |
| 保留原工程 | 需要制作不同版本时，调用 `project_clone`，明确新 ID 和名称。 |
| 找素材 | `media_import` 导入本机文件，返回可直接用于 `clip.insert` 的 `assetId`；`media_list` 查已用/未用素材，`media_inspect` 查尺寸、时长和音视频流。 |
| 看素材 | `preview_frames` 检查素材或工程，每次最多 6 个时间点，直接返回真实 PNG 内容。源音频信息使用 `media_inspect` 和音频分析命令；剪切点的成片混音使用 `preview_audio`，传当前 `expectedRevision`、时间点 `time` 和可选半径 `radius`（默认 0.5 秒，最大 2.5 秒）。返回带 revision 的双声道 WAV 与同一样本的峰值，不能代替语义判断或最终导出试听。 |
| 查编辑参数 | `commands_list` 查具体命令；`resources_list` 搜索预设；`effects_list` 查效果参数和可动画属性。不要猜参数或预设 ID。 |
| 获取编辑权 | `edit_lock` 获取租约，并在长任务中续租。人工界面可查看修改，完成或失败后都释放租约。 |
| 提交编辑 | 支持预演的命令先用 `command_preview`；组合修改用 `edit.batch`，作为一次撤销操作。提交时带 `expectedRevision`、`editLeaseId` 和稳定的 `commandId`。 |
| 验收画面 | 在关键帧、转场前后和字幕显示区间调用 `preview_frames`。图片包含文字与字幕；编辑器连续预览的字幕由可编辑叠层显示。 |
| 预览/导出 | `preview_prepare` + `preview_job` 准备连续预览；`export.enqueue` + `export_job` 执行异步导出。预览间隔约 1 秒查询；导出按 `export_job.pollAfterSeconds` 等待再查询，保留任务 ID；支持取消。 |
| 人工接手 | `project_events` 核对修改；释放租约后人工继续编辑同一工程。`project_package` 打包工程和素材以供交接。 |

## 精确修改一句字幕

先用 `project_lookup` 查出字幕 ID 和当前版本。单条修改使用 `requireUnique: true`；只有 `matchCount=1` 且目标确实符合用户意图时，才能预演和提交。`matchCount` 是 ID、文本、时间条件全部过滤后的总数，`ambiguous` 表示多匹配，两者都在 `limit` 截断前计算。`hasMore` 继续表示还有未返回结果，`limit=1` 不代表唯一。

例如多句字幕都包含“点击保存”时，唯一模式返回 `AMBIGUOUS_MATCH`，不会自行选择首条。根据用户指定的位置补充 `atSeconds` 或 `fromSeconds`/`toSeconds`；位置不明确时先不启用唯一模式列出候选，请用户明确选择，再用所选 `entityId` 和唯一模式复查。零匹配仍返回空列表，应停止并确认目标，不能猜 ID。MCP 使用布尔 `requireUnique`，HTTP GET 使用 `requireUnique=true/false`，Python 客户端使用 `require_unique=True/False`。

例如确认字幕 ID 为 `caption-1`、工程版本为 `8` 后：

```json
{
  "projectId": "my-project",
  "type": "caption.update",
  "payload": {"captionId": "caption-1", "text": "替换后的字幕"},
  "expectedRevision": "8",
  "editLeaseId": "已获取的租约 ID",
  "commandId": "这次修改的唯一 ID"
}
```

以上参数用于 `command_apply`。调用 `command_preview` 时去掉 `commandId` 和 `editLeaseId`，其余参数沿用该工具的规范。不要为一句字幕重新构建整个工程。

## 重试和冲突

- 网络响应丢失时，以同一个 `commandId` 重试同一次修改，不能更换 ID。
- `REVISION_CONFLICT` 表示已有其他修改。重新读取目标和版本，再决定如何编辑；不能覆盖其他人的内容。
- `IDEMPOTENCY_MISMATCH` 表示同一 ID 被用于不同内容。新的修改必须使用新 ID。
- 异步导出绑定提交时的工程快照。后续编辑不会改变已提交任务；重复提交同一命令不会创建第二个导出任务。
- 标准 stdio MCP 入口自动切换到通过检查的新引擎，连接保持，原有预览和导出任务继续完成；`runtime.autoReload` 可查看状态。已经运行的旧入口首次接入此机制需重连一次，Web 服务仍独立重启。完整服务关闭后，预览重新提交可复用磁盘缓存；导出历史保存在数据库中。
- 遇到 `AUTO_UPDATE_PENDING`，等待自动切换后复用同一 `commandId` 重试，无需让用户重启。`ENGINE_CONNECTION_LOST` 表示结果未知，应先读取工程 revision，再决定是否用原 ID 重试；不要换 ID 盲目重做编辑。
- 成功提交才推进版本；导出和查询不占编辑版本，也不进入撤销栈。
- MCP 错误附带 `error.recovery`，包括下一步说明和可调用的帮助入口。错误恢复不会自动提交新编辑或覆盖其他人的修改。

## 可复现的隔离演示

重度剪辑的音轨检查、光效融合、实例操作与转场对比见 [重度剪辑说明](HEAVY_EDITING.md)。

运行 `python skills/cutvoke-editor/scripts/demo.py --output .artifacts/agent-demo`。
脚本生成原创测试图片与音乐，经独立 stdio MCP 导入、编辑、预览、导出和打包，
不读取个人工程库。`--engine` 可以指定安装版引擎；完整参数见 `--help`。
运行后使用 `cutvoke serve --data .artifacts/agent-demo/data` 打开同一演示工程，
检查人工接手、文字修改和保存。详细输出保留在该临时目录。


创作型任务可先用 `creative_workflow({"operation":"list","query":"旅行","locale":"zh-CN"})` 找配方，再取单张详情和 plan/review/repair schema。审片记录须附当前 projectId/revision/sequenceId、实际成片或抽帧证据；局部修复使用 `preview-repair`，显式提交后重新生成证据。详见[可执行创作闭环](../../skills/cutvoke-editor/references/creative-workflow.md)。此入口不自动判断艺术质量，不打开证据文件，也不替代事实与原意核对。

### 导出轮询等待提示

`export_job` 的状态查询和取消响应都包含 `pollAfterSeconds`：`queued` / `running` 为正数秒（当前为 `1`），`succeeded` / `failed` / `cancelled` / `interrupted` 为 `null`。`null` 表示停止轮询，不是立即重试。成功时读取 `result.output_path`，其他终态检查 `error`；始终设置有界超时并保留 `jobId`。该字段只提示下次查询间隔，不是预计完成时间。

此字段是向后兼容的响应扩展，适用于 MCP 和 HTTP agent 接口、内存队列和 SQLite 账本中的任务（包括旧任务）。`export.enqueue` 的回执保持不变；收到 `jobId` 后查询 `export_job` 获取当前状态和等待提示。

## 人工提前接管租约

编辑器显示 Agent 的租约持有者和剩余秒数。点击「接管编辑」，核对后确认，
即可结束当前观察到的指定租约；取消不会发送接管请求。接管保留已提交的修改，
刷新工程成功后才恢复人工编辑。失败时保持只读并允许重试；如果租约已被替换，
界面显示新持有者，必须重新确认，不能撤销替换后的租约。

本地编辑器调用 `POST /api/v1/projects/{projectId}/edit-lock/takeover`，
请求体为 `{"leaseId":"当前观察到的租约 ID"}`。响应包含 `status`、`takenOver`、
`locked` 和 `lease`。重复接管已经结束的租约不会重复写入审计记录。
审计使用 `edit.lease.takenOver` 事件，记录本地编辑器 `human/web-editor`、
原持有者、租约 ID、时间和接管时的 revision；它不是剪辑，不推进 revision、
不增加撤销历史，也不能在活动抽屉中「撤销到此」。按 revision 读取事件时，
接管事件可在相同 revision 再次返回，客户端应按 `eventId` 去重。

Agent 必须在每次写入中携带取得的 `editLeaseId`。失效、过期或被替换的 token
会得到不可自动重试的 `EDIT_LEASE_INVALID`：重新读取工程并等待新的人工交接，再取得新租约后
重新规划操作；不要原样重发旧 token。续租以及使用旧 ID 再次 acquire 同样失败。
已经成功提交的相同 `commandId`/请求仍可重放原回执，不产生新写入。
校验覆盖 SQLite 最终写入事务，包括字幕导入、重命名和旧工程库的全局路径迁移维护命令。
既有不携带 token 的人工操作继续兼容；actor 是现有本地接口的来源标记，
此功能不增加用户认证，也不提供轨道级并行租约或租约变更回顾。
