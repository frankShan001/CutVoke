# 项目目录规范

```text
CutVoke/
  apps/
    editor/                 浏览器和桌面共用的 React 编辑器
    desktop/                Electron 窗口、系统集成、安装配置
  src/cutvoke/
    domain/                 工程模型、有理数时间、关键帧、约束
    editing/                编辑服务、命令 schema、模板、效果配方
    rendering/              预览、导出、GPU、效果算法、语音与人物处理
    storage/                数据库、素材目录、缓存、资源包、工程归档
    interfaces/             CLI、HTTP、MCP 和外部客户端
    catalogs/               随包发布的内置资源清单（JSON）
    assets/                 软件运行所需的内置成品素材
  tests/                    与后端职责对应的回归测试
  tools/                    开发、构建、验证和资源制作工具
  effects/                  社区效果与配方
  examples/                 可复现示例和可编辑演示工程
  resources/source/         制作源文件及来源说明
  skills/                   可独立复制的外部 Agent 工作流
  docs/                     使用、开发和正式配图
```

编辑器的开发工具和浏览器测试分别在 `apps/editor/scripts/` 和 `apps/editor/e2e/`；桌面测试与平台配置放在 `apps/desktop/`。它们依赖各自应用的开发环境，因此与 Python 引擎测试分开。`tests/` 按 `domain`、`editing`、`rendering`、`storage`、`interfaces`、`integration` 分组，浏览器测试的隔离服务在 `tests/support/`。

`src/cutvoke/runtime.py` 负责源码/安装包身份和更新检查，`paths.py` 统一定位包内数据。顶层 `main.py`、`client.py`、`mcp_supervisor.py` 仅作为原有公开入口的兼容转发，实际实现位于 `interfaces/`。各业务目录通过明确的包导入协作；`domain` 不依赖其他业务目录，业务模块不得反向依赖 CLI、HTTP 或 MCP。

## 从哪里开始

| 要修改的行为 | 首选位置 |
| --- | --- |
| 时间轴、素材卡片、预览交互、界面翻译 | `apps/editor/src/` |
| 首次启动、目录选择、桌面窗口、安装器 | `apps/desktop/` |
| 工程与片段字段、时间精度、关键帧和合法性 | `src/cutvoke/domain/` |
| 拖动/裁切/套用效果等命令、事务和撤销重做 | `src/cutvoke/editing/service.py` |
| 效果参数定义与算法、即时预览、导出 | `src/cutvoke/rendering/` |
| 工程数据库、文件迁移、资源包与缓存 | `src/cutvoke/storage/` |
| Agent 工具、HTTP 路由、CLI 命令 | `src/cutvoke/interfaces/` |

剪辑写操作始终通过 `editing.service.EditService`。目录拆分不改变工程 JSON、数据库格式、命令名称、撤销记录或对外 HTTP/MCP 协议。

## 原目录对应关系

| 原位置 | 新位置 |
| --- | --- |
| `web/app/` | `apps/editor/` |
| `desktop/` | `apps/desktop/` |
| `scripts/` | `tools/` |
| `src/cutvoke/core/*.py` | 按职责分入五个业务目录，见上方职责表 |
| `src/cutvoke/core/builtin_*.json` | `src/cutvoke/catalogs/` |
| `tests/test_*.py` | `tests/<职责>/test_*.py` |

更新开发环境中的启动命令、IDE 测试配置和自定义脚本后，运行 `python -m pip install -e .` 刷新控制台入口。`python -m cutvoke`、`cutvoke`、MCP 工具名和根目录 `start-cutvoke.cmd` 保持可用。外部代码若直接导入旧的内部 `cutvoke.core.*`，需按模块职责更新；公开顶层模型与服务导出保留。

新的内置资源包使用 `catalogs/` 存放清单，因此资源包版本升级到 `1.40.0`；原素材和审核侧文件不重新编码、不重新审批。安装和读取已有 `core/` 格式的旧资源包仍受支持。资源来源记录中的旧路径由 `resources/source/provenance.json` 解析，不依赖个人开发档案。

## 验证与构建

先激活项目虚拟环境并安装开发依赖，浏览器测试的 `python` 也应来自该环境。

```text
python -m pip install -e ".[dev]"
python tools/verify/check_repository_layout.py
python tools/verify/check_layering.py
python -m pytest -q
```

在 `apps/editor/` 运行 `npm ci`、`npm run build`、`npm run test:i18n` 和 `npm run test:e2e`。在 `apps/desktop/` 运行桌面测试和 Electron 打包命令。浏览器和桌面共用编译到 `.artifacts/web/` 的编辑器页面，安装包内的静态页面位置仍为 `cutvoke/web/`，它是发布数据目录。

Windows 完整构建入口为 `tools/build/build-cutvoke-desktop.cmd`。源码/wheel 候选包通过 `python tools/build/prepare_candidate.py --output <新目录>` 构建和检查；独立安装验证用 `tools/verify/verify_delivery_wheel.py`。macOS/Linux 与 Windows 便携版的 CI 入口分别为 `.github/workflows/unix-packages.yml` 和 `.github/workflows/windows-portable.yml`。平台包需要在对应系统验收；在 Windows 构建成功不代表已验证其他系统。

已有源码服务和 MCP 连接需要重启/重连才能加载新模块。普通源码启动入口仍为根目录 `start-cutvoke.cmd`，用户工程和缓存位置由应用设置决定，不随源码目录迁移。

## 源码

| 目录 | 内容 |
| --- | --- |
| `src/cutvoke/` | Python 引擎、CLI、MCP、协议与内置资源 |
| `apps/editor/` | 编辑器、前端配置、浏览器测试 |
| `apps/desktop/` | 桌面窗口、内置服务管理、安装包配置 |
| `tests/` | 后端回归测试和隔离测试服务 |
| `tools/dev/` | 开发启动、重启、可选模型准备 |
| `tools/build/` | 安装包和资源包构建 |
| `tools/verify/` | 校验、验收、性能测试、视觉审核 |
| `tools/assets/` | 资源生成、裁剪、注册与审核结果应用 |
| `docs/user/` | 当前使用文档 |
| `docs/development/` | 架构与开发规范 |
| `resources/source/materials/` | 内置素材的原始图集、裁切坐标、来源与提取记录 |
| `resources/source/brand/` | 品牌母版、生成提示词和平台图标校验清单 |
| `docs/images/` | 正式文档使用的截图、插图及其来源说明 |
| `examples/` | 小型可复现示例 |
| `skills/cutvoke-editor/` | 可复制的外部 AI 剪辑工作流、按需参考和隔离演示脚本 |
| `effects/<类型>/<效果 ID>/` | 社区贡献清单、来源/许可证、真实预览；不搬移已有内置资源 |
| `effects/recipes/<内容 ID>/` | 普通 AI 剪辑导出的纯配方模块，只接受四个程序生成文件，不收录用户素材或文字 |

根目录保留说明、许可证、依赖配置与 `start-cutvoke.cmd`。默认打开桌面客户端，`--web` 打开浏览器编辑器，`--mcp` 提供 stdio MCP。

## 素材与审核资料

从 [资源入口](../../resources/README.md) 查找制作源文件；从 [文档配图入口](../images/README.md) 查找正式截图。应用实际使用的成品仍位于 `src/cutvoke/assets/`，平台图标位于 `apps/desktop/assets/brand/` 和 `apps/editor/public/brand/`。移动制作源文件不改变用户工程或内置成品素材。

源图集与正式成品都需要保留：源图用于重新裁切及追溯来源，成品供安装包直接使用。不要将旧成品备份、裁切试验、缓存或每次运行的截图提交到这些目录。提取工具的新报告写入 `.artifacts/assets/reports/`，视觉检查输出写入 `.artifacts/reviews/`；确认后只更新当前素材所需的提取坐标、来源、许可及校验记录。注册脚本读取对应的正式提取记录，不会自动采用临时报告。个人审核过程保存在仓库外。

[来源索引](../../resources/source/provenance.json) 只记录当前制作源文件、正式文档资源及原始来源路径。随包发布的资源资格、许可和校验元数据由运行时使用，保留原始字节。个人日志、历史截图和阶段报告不作为构建输入；详见[收录范围](repository-content.md)。

提交前运行 `python tools/verify/check_asset_layout.py`，检查分类、来源校验值、文档链接及个人记录/生成物误提交。此检查已接入 CI。

## 生成文件

所有可重新生成的开发产物写入 `.artifacts/`：

```text
.artifacts/
  web/                 Vite 构建页面
  build/               wheel / PyInstaller / 下载的打包工具
  desktop/             Electron 打包中间件和 win-unpacked
  test-results/        浏览器失败诊断
  acceptance/          验收报告、测试工程、临时渲染
  activation/          重启检查记录
```

`.artifacts/` 不提交。清理该目录后，需重新构建编辑器或客户端；运行中的验证任务应先结束。`dist/` 只放用于交付的安装程序、wheel、源码发布包及其必要附属文件，不放解包客户端或测试环境。

仓库里的 `.venv`、`node_modules` 是可重建的开发依赖，工具索引和编辑器配置属于本机元数据，均不作为交付内容。

## 用户数据

默认位置是 `%USERPROFILE%\.cutvoke`，与安装版和 MCP 共用现有工程库：

```text
.cutvoke/
  data/projects.sqlite  工程、历史、素材目录和导出账本
  data/media/           持久素材
  data/effect-modules/   无用户内容的本地特效配方；shares/ 为本地贡献 ZIP
  data/missing/         已有缺失引用的位置标记，不生成替代素材
  exports/              成片
  packages/             可编辑 cvkpkg
  projects/             保留的制作说明与源文档
  backups/              数据库和迁移前原文件备份
```

预览缓存使用应用设置中选择的目录。`CUTVOKE_DATA` / `--data` 可指定其他工程库。数据目录可以在其他盘，但必须让桌面、Web 和 MCP 指向同一份工程库。
