# 脚本入口

| 用途 | 入口 |
| --- | --- |
| 启动桌面客户端 | 根目录 `start-cutvoke.cmd` |
| 启动浏览器编辑器 | 根目录 `start-cutvoke.cmd --web` |
| 连接源码 MCP | `dev/start-cutvoke-mcp.cmd` |
| 重启本机 Web 服务 | `dev/restart-cutvoke-web.cmd` |
| 重启并检查旧 MCP 进程 | `dev/restart-cutvoke.cmd` |
| 构建 Windows 安装包 | `build/build-cutvoke-desktop.cmd` |
| 从正式母版生成或校验应用图标 | `assets/build_brand_icons.py` / `assets/build_brand_icons.py --check` |
| 构建及验收 Windows 免安装便携 ZIP | [Windows portable Actions](../.github/workflows/windows-portable.yml)；[固定依赖与验收说明](../docs/development/windows-portable.md) |
| 构建及验收 macOS arm64/x64 与 Linux x64 原生发行包 | [Native packages Actions](../.github/workflows/unix-packages.yml)；[固定依赖与实际产物验收](../docs/development/native-packages.md) |
| 检查代码分层 | `verify/check_layering.py` |
| 检查应用/引擎目录及过时导入、构建路径 | `verify/check_repository_layout.py` |
| 检查资源分类、文档图片链接与生成物误提交 | `verify/check_asset_layout.py` |
| 实测工程渲染耗时（支持隔离空缓存） | `verify/benchmark_render.py --help` |
| 检查社区效果清单与贡献材料 | `verify/validate_effect_contributions.py` |
| 检查内置资源包 | `build/build_builtin_resource_pack.py --check` |

Python 工具可直接执行，不依赖当前工作目录定位源码。生成文件统一放到 `.artifacts/`。`assets/` 中部分工具会改写资源目录和清单，执行前先查看其用途和参数；视觉审核与性能检查集中在 `verify/`。

素材制作输入在 [resources/source/materials](../resources/source/materials/README.md)，品牌母版在 [resources/source/brand](../resources/source/brand/README.md)。新的提取报告和审核预览输出到 `.artifacts/`；注册工具读取与当前素材对应的提取记录。只将产品所需的来源和校验元数据纳入仓库，个人审核过程保存在仓库外，详见[收录范围](../docs/development/repository-content.md)。

独立 wheel 交付时，将 `dev/start-cutvoke-wheel.cmd` **复制到 wheel 旁边**，保持文件名 `start-cutvoke-wheel.cmd`；它按所在目录寻找安装包，无需源码仓库。

可复制的 [剪辑技能演示脚本](../skills/cutvoke-editor/scripts/demo.py) 放在技能包内，以便随技能一起使用。它只生成隔离样例，经正常 Web 上传与 MCP 编辑完成导出；使用前运行 `--help`，并选择新输出目录。特效贡献的开发与 PR 规则见 [贡献指南](../effects/README.md)。
