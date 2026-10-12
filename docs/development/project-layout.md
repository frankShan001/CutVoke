# 项目目录规范

## 源码

| 目录 | 内容 |
| --- | --- |
| `src/cutvoke/` | Python 引擎、CLI、MCP、协议与内置资源 |
| `web/app/` | 编辑器、前端配置、浏览器测试 |
| `desktop/` | 桌面窗口、内置服务管理、安装包配置 |
| `tests/` | 后端回归测试和隔离测试服务 |
| `scripts/dev/` | 开发启动、重启、可选模型准备 |
| `scripts/build/` | 安装包和资源包构建 |
| `scripts/verify/` | 校验、验收、性能测试、视觉审核 |
| `scripts/assets/` | 资源生成、裁剪、注册与审核结果应用 |
| `scripts/maintenance/` | 备份、路径迁移与维护 |
| `docs/user/` | 当前使用文档 |
| `docs/development/` | 架构与开发规范 |
| `docs/assets/` | 资源来源和审核材料 |
| `docs/history/` | 按日期归档的开发记录 |
| `examples/` | 小型可复现示例 |

根目录保留说明、许可证、依赖配置与 `start-cutvoke.cmd`。默认打开桌面客户端，`--web` 打开浏览器编辑器，`--mcp` 提供 stdio MCP。

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
  data/missing/         已有缺失引用的位置标记，不生成替代素材
  exports/              成片
  packages/             可编辑 cvkpkg
  projects/             保留的制作说明与源文档
  backups/              数据库和迁移前原文件备份
```

预览缓存使用应用设置中选择的目录。`CUTVOKE_DATA` / `--data` 可指定其他工程库。数据目录可以在其他盘，但必须让桌面、Web 和 MCP 指向同一份工程库。

## 旧目录迁移

```powershell
# 只查看迁移计划
.\.venv\Scripts\python.exe scripts\maintenance\migrate_workspace_data.py

# 复制、验证、备份并更新工程引用
.\.venv\Scripts\python.exe scripts\maintenance\migrate_workspace_data.py --apply
```

工具仅迁移指定源码目录下 `output` 和 `本地开发` 的文件引用。先核对目标文件 SHA256，再以一个数据库事务更新工程、撤销历史、素材库、导出快照和命令记录。检测到版本变化、编辑租约或未完成导出时拒绝写入。已有缺失文件会单独报告。

迁移工具保留原目录，并在备份中生成 `archive-plan.json`；确认 `result.json` 的 `verified` 为 true 后，维护者可按该清单将原目录移入备份。原文件和数据库都可用于恢复；恢复前关闭使用此工程库的服务，再使用备份配套的数据库和原目录，避免把新旧工程状态混用。
