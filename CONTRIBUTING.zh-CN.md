# 参与 CutVoke 开发

[English](CONTRIBUTING.md) · **简体中文**

可认领的工作与验收标准见[社区任务](docs/development/community-tasks.md)。安全问题请先读[安全报告说明](SECURITY.md)，不要在公开 Issue 中披露漏洞细节。

感谢你愿意改进 CutVoke。提交代码前，请先说明你遇到的问题以及希望得到的行为。较大的功能最好先开 Issue，避免实现方向重复。

## 本地环境

如果只是分享 AI 剪辑中产生的效果配方，使用[本地特效模块流程](docs/user/effect-contributing.zh-CN.md#边剪辑边创作无需开发环境)，无需安装开发环境。导出 ZIP 排除所有用户工程内容；不要附加用户素材、文字、截图或工程包。以下步骤用于源码与引擎开发。

后端需要 Python 3.10+、FFmpeg 和 ffprobe。Web 编辑器需要 Node.js 20+。

```bash
git clone https://github.com/frankShan001/CutVoke.git
cd CutVoke

python -m venv .venv
# Linux / macOS：source .venv/bin/activate
# Windows PowerShell：.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

cd apps/editor
npm ci
npm run build
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
python tools/verify/check_layering.py
python -m pytest -q
```

前端改动至少运行：

```bash
cd apps/editor
npm run typecheck
npm run test:i18n
npm run build
npm run test:e2e
npm run test:e2e:regressions
```

浏览器测试会启动临时工程库并生成小型测试素材，不会读写个人工程。Windows 本地使用已安装的 Chrome；CI 会安装 Chromium。Linux 云环境可设置 `CUTVOKE_E2E_BROWSER=/usr/bin/chromium` 使用已有浏览器。只改后端且不影响编辑流程时，可以只运行后端检查。

防回归子集会重复三次检查菜单关闭、防抖阈值前后、取消预览准备、慢解码短片、效果保存期间的控件状态、跨迟到确认的蒙版手势和五种视口宽度下的德语工具栏边界。验证时序时要控制计时器和所有竞争的就绪响应，不能假设自动化操作必定在防抖期限内完成；表单定位应限定到目标编辑区。固定外壳不能被原生焦点或显示元素操作滚动；布局检查应覆盖工具栏子项，而不只检查外框。独立执行的效果命令会生成新实例 ID，等价比较要保留引用关系、效果顺序、参数和关键帧目标。相关 PR 与 main 推送均运行原生打包验收；结论应绑定精确 SHA，浏览器和四个平台全部结束后才能称该变更已验证。

桌面打包依赖变更后，在 `apps/desktop/` 中运行 `npm run test:build-deps`，验证下载器的代理接入仍然可用。打包工具的传递依赖覆盖集中在 `apps/desktop/package.json` 的 `overrides`；调整覆盖时同时核对调用接口和安全审计。

发布 Python wheel 前必须先运行 `npm run build`。打包脚本会把 `.artifacts/web` 放入安装包；若找不到已构建的页面，打包会报错，避免发布只有接口、没有编辑器的安装包。

如果改动涉及渲染，请使用真实媒体做一次最小验证，并在 PR 中写明输入、命令和结果。不要只用“命令成功退出”代替画面或音频检查。

## 代码约定

CutVoke 有几条不能绕过的边界：

1. 工程时间使用 `Rational`。浮点数只应出现在界面显示或生成 FFmpeg 参数的边缘位置。
2. 所有工程写入都经过 `EditService.execute`。HTTP、MCP、CLI 和 Web 不应各自实现一套编辑逻辑。
3. 写命令需要 revision 和 command ID。并发冲突应返回错误，重试不应重复执行。
4. SQLite 是跨进程的权威状态。不要依赖某个长期运行进程里的旧内存副本。
5. 预览和导出应尽量复用同一套合成语义。新增效果时要同时考虑两条路径。
6. Agent 批量修改应使用编辑租约；租约期间页面保持可观察，但人工操作只读。

核心模块不能反向依赖 CLI、HTTP 或 MCP 接口层。`tools/verify/check_layering.py` 会检查这条规则。

源码入口见[目录地图](docs/development/project-layout.md)：编辑器与桌面壳分别位于 `apps/editor`、`apps/desktop`；Python 引擎按 `domain`、`editing`、`rendering`、`storage`、`interfaces` 分工；对应测试按同名目录组织。提交前运行 `python tools/verify/check_repository_layout.py`，避免重新引入旧路径或漏掉构建引用。

## 新增编辑命令

- 在 `EditService` 中实现并注册处理器。
- 为输入提供明确的 schema 和错误信息。
- 更新序列化、撤销/重做和持久化逻辑。
- 确认同一命令能从需要支持的接口到达。
- 覆盖成功、非法输入、版本冲突和重复提交。

不要为单个界面添加只能从该界面调用的隐藏写路径。

## 新增效果

社区效果的目录、清单、运行样例、验证材料和 PR 审核要求见 [特效贡献指南](effects/README.md)（[中文](docs/user/effect-contributing.zh-CN.md)）。当前样例复用已有渲染器，新增算法仍需实现和测试，不支持清单执行任意外部代码。

优先扩展效果注册表和声明式参数，而不是在命令处理器中加入专用分支。提交时请说明：

- 效果 ID、适用对象和参数范围；
- 使用的 FFmpeg filter 或其他实现；
- 预览与导出的验证方式；
- 外部素材、字体、滤镜或二进制的来源与许可。

CutVoke 调用系统 FFmpeg，并不意味着任何 FFmpeg 构建都可以随软件一起分发。若 PR 加入二进制或第三方素材，请提供准确的许可证和来源链接。

## 文档

界面翻译的消息键、占位符、RTL、效果元数据与验证方法见 [翻译贡献说明](docs/development/translations.zh-CN.md)（[English](docs/development/translations.md)）。新增界面文案时同步维护所有语言；不要翻译工程内容或 API 标识。

文档应描述当前代码能做什么，不写内部验收编号、阶段汇报、测试数量或尚未确认的路线图。尽量使用短句和可执行示例。功能变化后，同步更新 README、命令帮助或接口 schema 中真正受影响的部分。

## Pull Request

PR 请保持范围清楚，并包含：

- 问题和改动摘要；
- 实际运行过的验证命令；
- 对工程格式、兼容性或许可的影响；
- 涉及界面或渲染时的截图、短视频或其他可检查结果。

提交信息建议使用简短的 Conventional Commit 格式，例如 `fix: keep export jobs on their queued revision`。一条提交只描述一个可以独立理解的改动。

## 文件存放

遵守 [项目目录说明](docs/development/project-layout.md)。新增工具按用途放入 `tools/` 的对应目录，临时渲染、日志和报告统一写入 `.artifacts/`。测试工程使用临时数据库；用户工程、原始素材与成片保存在源码仓库之外。

可复现的内置素材制作输入与来源记录放在 `resources/source/`，正式文档配图放在 `docs/images/`。个人开发日志、一次性验收报告和审核截图保存在仓库外。运行时需要的资源元数据及第三方许可随产品保留。遵循[公开仓库收录范围](docs/development/repository-content.md)，提交前运行 `python tools/verify/check_asset_layout.py`。

安装、已测版本与故障排查见[安装说明](docs/user/installation.md)；源码/wheel 的候选包校验与 Windows 验收边界见[预发布流程](docs/development/prerelease.md)。
