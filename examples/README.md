# 最小示例

以下命令创建独立示例工程，不需要下载素材：

```bash
cutvoke project create --name "目录示例" --width 1920 --height 1080 --fps 30
```

给外部 Agent 的示例任务：

> 调用 editor_help 读取工作流程，找到“目录示例”工程，添加一条持续 3 秒的文字“你好，CutVoke”，准备预览并检查实际画面。保持工程可继续手工编辑。

真实素材通过 `media_import` 导入持久素材库；测试成片和工程包写入源码目录外，测试报告写入 `.artifacts/acceptance/`。
