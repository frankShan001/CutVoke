# 最小示例

## 完整视频案例

[CHASE THE LIGHT：原始素材与剪辑效果](showcases/chase-the-light/README.md) 包含四宫格原始素材展示、已完成的 V7 成片、两个可导入工程包及制作过程素材。大文件使用 Git LFS；克隆后运行 `git lfs pull`。

## 从零创建最小工程

需要直接跑出一条可编辑短片时，使用 [cutvoke-editor 演示脚本](../skills/cutvoke-editor/scripts/demo.py)：

```bash
python skills/cutvoke-editor/scripts/demo.py --output .artifacts/example-edit
cutvoke serve --data .artifacts/example-edit/data
```

从仓库中已激活的 Python 环境运行，依赖 CutVoke、FFmpeg/ffprobe 和已构建的 Web 编辑器。脚本自行绘制插画与合成音乐，经本机上传及 MCP 剪辑，留下 12 秒 MP4、可编辑数据库、工程包和检查证据；每次使用新输出目录。详见 [技能使用](../docs/user/agent-skill.zh-CN.md) 与 [特效贡献示例](../effects/README.md)。

以下命令创建独立示例工程，不需要下载素材：

```bash
cutvoke project create --name "目录示例" --width 1920 --height 1080 --fps 30
```

给外部 Agent 的示例任务：

> 调用 editor_help 读取工作流程，找到“目录示例”工程，添加一条持续 3 秒的文字“你好，CutVoke”，准备预览并检查实际画面。保持工程可继续手工编辑。

真实素材通过 `media_import` 导入持久素材库；日常测试成片和工程包写入源码目录外，测试报告写入 `.artifacts/acceptance/`。上方明确纳入版本管理的展示案例使用独立目录，不包含运行缓存或本地数据库。
