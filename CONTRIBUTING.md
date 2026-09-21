# 参与 CutVoke 开发

感谢你愿意改进 CutVoke。提交代码前，请先说明你遇到的问题以及希望得到的行为。较大的功能最好先开 Issue，避免实现方向重复。

## 本地环境

后端需要 Python 3.10+、FFmpeg 和 ffprobe。Web 编辑器需要 Node.js 20+。

```bash
git clone https://github.com/frankShan001/CutVoke.git
cd CutVoke

python -m venv .venv
python -m pip install -e ".[dev]"

cd web/app
npm ci
cd ../..
```

先运行环境检查：

```bash
cutvoke doctor --json
```

## 提交前检查

后端改动至少运行：

```bash
python -m compileall -q src
python scripts/check_layering.py
```

前端改动至少运行：

```bash
cd web/app
npm run typecheck
npm run build
```

如果改动涉及渲染，请使用真实媒体做一次最小验证，并在 PR 中写明输入、命令和结果。不要只用“命令成功退出”代替画面或音频检查。

## 代码约定

CutVoke 有几条不能绕过的边界：

1. 工程时间使用 `Rational`。浮点数只应出现在界面显示或生成 FFmpeg 参数的边缘位置。
2. 所有工程写入都经过 `EditService.execute`。HTTP、MCP、CLI 和 Web 不应各自实现一套编辑逻辑。
3. 写命令需要 revision 和 command ID。并发冲突应返回错误，重试不应重复执行。
4. SQLite 是跨进程的权威状态。不要依赖某个长期运行进程里的旧内存副本。
5. 预览和导出应尽量复用同一套合成语义。新增效果时要同时考虑两条路径。
6. Agent 批量修改应使用编辑租约；租约期间页面保持可观察，但人工操作只读。

核心模块不能反向依赖 CLI、HTTP 或 MCP 接口层。`scripts/check_layering.py` 会检查这条规则。

## 新增编辑命令

- 在 `EditService` 中实现并注册处理器。
- 为输入提供明确的 schema 和错误信息。
- 更新序列化、撤销/重做和持久化逻辑。
- 确认同一命令能从需要支持的接口到达。
- 覆盖成功、非法输入、版本冲突和重复提交。

不要为单个界面添加只能从该界面调用的隐藏写路径。

## 新增效果

优先扩展效果注册表和声明式参数，而不是在命令处理器中加入专用分支。提交时请说明：

- 效果 ID、适用对象和参数范围；
- 使用的 FFmpeg filter 或其他实现；
- 预览与导出的验证方式；
- 外部素材、字体、滤镜或二进制的来源与许可。

CutVoke 调用系统 FFmpeg，并不意味着任何 FFmpeg 构建都可以随软件一起分发。若 PR 加入二进制或第三方素材，请提供准确的许可证和来源链接。

## 文档

文档应描述当前代码能做什么，不写内部验收编号、阶段汇报、测试数量或尚未确认的路线图。尽量使用短句和可执行示例。功能变化后，同步更新 README、命令帮助或接口 schema 中真正受影响的部分。

## Pull Request

PR 请保持范围清楚，并包含：

- 问题和改动摘要；
- 实际运行过的验证命令；
- 对工程格式、兼容性或许可的影响；
- 涉及界面或渲染时的截图、短视频或其他可检查结果。

提交信息建议使用简短的 Conventional Commit 格式，例如 `fix: keep export jobs on their queued revision`。一条提交只描述一个可以独立理解的改动。
