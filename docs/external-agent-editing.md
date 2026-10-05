# 外部 Agent 剪辑接入

CutVoke 保存可编辑的时间线并执行剪辑。外部 Agent 负责理解需求、规划和视觉验收。编辑器无需大模型服务或生成服务。

## 连接

Windows 安装版内置了独立的 MCP 引擎，可直接运行：

```text
%LOCALAPPDATA%\Programs\CutVoke\resources\engine\cutvoke-engine.exe mcp
```

MCP 客户端的 `command` 应填写展开后的绝对路径，`args` 填写 `["mcp"]`。默认与桌面编辑器共用 `~/.cutvoke/data/projects.sqlite`。测试时用 `--data` 指向独立的数据库副本。

服务在 MCP 初始化响应中提供入门 `instructions`。AI 首次接入先调用 `editor_help({})`，即可获取工作顺序、后续帮助入口和验收要求；不需要用户另装 Skill 或讲解接口。再调用 `runtime` 核对数据目录和 `project_list` 查找目标工程。升级不会替换已经运行的 MCP 进程；旧连接需要重新加载，才能看到新增工具。

客户端支持 MCP 文档资源时，可以读取 `cutvoke://guide/overview`、`cutvoke://guide/time`、`cutvoke://guide/editing`、`cutvoke://guide/errors` 和 `cutvoke://commands`。不支持或不自动读取资源的客户端，使用 `editor_help` 工具可以获得相同内容。

具体命令使用 `editor_help({"topic":"command","command":"clip.insert"})` 查询。返回的 `inputSchema` 描述 **payload**，不是 `command_apply` 的外层参数。效果命令再传 `effectId`，即可获取该效果注册表中的精确参数 schema。`commands_list` 也返回命令 schema；不必一次加载所有命令。

`editor_help({"topic":"recipes"})` 列出可执行范例，包括建立可编辑时间线、精确修改字幕和异步导出。范例里的 `<PROJECT>`、`<REVISION>`、`<ASSET>` 等占位符由 AI 用实际查询结果替换；用户只需说明剪辑需求并提供素材访问权限。工程不明确或缺少必要素材时，AI 仍需澄清这些信息。

## 工作顺序

| 步骤 | 工具与要求 |
| --- | --- |
| 找工程 | `project_list` 按名称搜索；`project_summary` 获取版本和时间线摘要。 |
| 保留原工程 | 需要制作不同版本时，调用 `project_clone`，明确新 ID 和名称。 |
| 找素材 | `media_import` 导入本机文件，返回可直接用于 `clip.insert` 的 `assetId`；`media_list` 查已用/未用素材，`media_inspect` 查尺寸、时长和音视频流。 |
| 看素材 | `preview_frames` 检查素材或工程，每次最多 6 个时间点，直接返回真实 PNG 内容。音频使用 `media_inspect` 和音频分析命令。 |
| 查编辑参数 | `commands_list` 查具体命令；`resources_list` 搜索预设；`effects_list` 查效果参数和可动画属性。不要猜参数或预设 ID。 |
| 获取编辑权 | `edit_lock` 获取租约，并在长任务中续租。人工界面可查看修改，完成或失败后都释放租约。 |
| 提交编辑 | 支持预演的命令先用 `command_preview`；组合修改用 `edit.batch`，作为一次撤销操作。提交时带 `expectedRevision`、`editLeaseId` 和稳定的 `commandId`。 |
| 验收画面 | 在关键帧、转场前后和字幕显示区间调用 `preview_frames`。图片包含文字与字幕；编辑器连续预览的字幕由可编辑叠层显示。 |
| 预览/导出 | `preview_prepare` + `preview_job` 准备连续预览；`export.enqueue` + `export_job` 执行异步导出。间隔约 1 秒查询，保留任务 ID；支持取消。 |
| 人工接手 | `project_events` 核对修改；释放租约后人工继续编辑同一工程。`project_package` 打包工程和素材以供交接。 |

## 精确修改一句字幕

先用 `project_lookup` 查出字幕 ID 和当前版本，再预演和提交。例如确认字幕 ID 为 `caption-1`、工程版本为 `8` 后：

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
- 预览任务由当前服务进程持有。服务重启后重新提交可复用磁盘缓存；导出历史保存在数据库中。
- 成功提交才推进版本；导出和查询不占编辑版本，也不进入撤销栈。
- MCP 错误附带 `error.recovery`，包括下一步说明和可调用的帮助入口。错误恢复不会自动提交新编辑或覆盖其他人的修改。

## 可复验的真实流程

`scripts/verify_agent_editor.py` 使用独立的 stdio MCP 客户端，复制工程库后导入真实图片与音乐，完成转场、关键帧、标题、字幕、画面检查、连续预览、异步导出和工程打包，并检查原工程库未变化。`--engine` 可指定安装版引擎，`--evidence` 指定一个新的验收目录。
