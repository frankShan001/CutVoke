# 独立工程目录

当前源码版的新工程默认使用一个独立目录，工程列表所在的 `projects.sqlite` 只记录这些工程的位置。旧工程仍可从原库打开，不会在升级时自动复制大量素材。

```text
我的工程/
  project.cutvoke       # 工程入口和格式版本
  project.sqlite        # 时间线、撤销/重做、事件、素材账本和任务记录
  media/                # 工程自己的素材副本、LUT、录音及生成素材
  cache/                # 预览、代理、波形等可重建缓存
  exports/              # 成片、封面、字幕和交接包
  backups/              # 初始迁移快照及制作归档
```

## 新建、打开和移动

首页新建工程时可填写**尚不存在的绝对目录**，留空则在工程库旁的 `projects/` 下自动建目录。素材导入到当前工程；Agent 将外部素材加入时间线时，也会复制到工程内。文件按内容指纹去重，同一工程多次使用同一素材只保存一份；不同工程各保留独立副本。内置资源库可共用，实际用于工程的源文件会随工程收集。

导出填写 `video.mp4` 这样的文件名即可，后端会保存到当前工程的 `exports/`。独立工程的输出路径须位于自己的 `exports/` 或 `backups/` 内；工程归档默认写入 `backups/`。工程面板显示当前目录。

**移动或备份前，先关闭 CutVoke 及操作该工程的 Agent/服务**，然后复制或移动整个目录。请一并保留 SQLite 的 `-wal`、`-shm` 文件（正常关闭后通常会自动合并/移除）。在首页“打开工程目录”填写新目录或 `project.cutvoke` 的路径；工程会更新内部素材、历史和任务引用。新机器无需原工程库。

同一个工程 ID 不同时注册两个在线副本。若恢复同机备份，请先将旧目录移走，再打开恢复的目录。注册目录离线时会提示重新打开，不会悄悄回退到旧库里的过期快照。需要同时编辑两个版本时使用“复制工程”。

完整备份包含入口、数据库、素材和导出；`cache/` 可以在应用关闭后省略，之后会重新生成。`backups/initial.sqlite` 只是数据库快照，不替代整个工程的备份。

## 旧工程迁移

在当前工程面板点击**预览迁移到独立目录**，先查看引用素材和保留历史所需文件数、大小、缺失项，再选择一个新目录并迁移。旧库未记录“未使用素材属于哪个工程”，因此不能自动判断全局素材库里未入时间线的文件归属；需要的未用素材可在迁移后导入该工程。

迁移会：

1. 备份原工程库，复制当前工程及历史、制作版本、导出任务引用的文件。
2. 对复制内容校验，并在复制前后核对工程与素材是否变化。
3. 保留撤销/重做、活动记录与任务数据，最后切换工程列表索引。

原库、原素材和已有导出始终保留，不自动清理。缺失素材、有效编辑租约、未完成后台任务或过期预览会阻止切换；失败的目标目录会标为未完成，不能当作完整工程打开。

迁移后、尚未继续编辑时，可在面板点击**回退迁移**。继续编辑后会拒绝回退，避免丢失新修改；此时保留工程目录进行备份或恢复。回退只恢复索引，已复制的目录也会保留。

桌面设置的“更换整个工程库位置”仍可使用：库内的独立目录会随库复制并更新索引，库外自定义工程目录保持各自的位置。

## CLI / HTTP / MCP

```powershell
cutvoke project create --project demo --name "我的工程" --data "E:\CutVoke\library.sqlite" --directory "E:\Videos\我的工程"
cutvoke serve --data "E:\Videos\我的工程\project.cutvoke"
cutvoke export --data "E:\Videos\我的工程\project.cutvoke" --project demo --out video.mp4
```

HTTP：`GET /api/v1/projects/{id}/directory` 读取目录；`POST .../directory/plan` 预览迁移，`POST .../directory/migrate` 提交预览返回的 `token` 与可选 `directory`；`POST .../directory/rollback` 回退。`POST /api/v1/project-directories/open` 传 `{ "path": "绝对目录或工程入口" }` 打开工程。`POST /api/v1/projects` 支持可选 `directory`。

素材上传、列表、重新链接、LUT 与预览缓存请求应指定 `projectId`；浏览器界面自动附带。MCP 的 `create_project` 支持 `directory`，`project_directory` 提供 `info/plan/migrate/rollback`，`project_directory_open` 打开已有目录。Agent 的 `media_import` 应传 `projectId`，后续输出优先使用该工程目录中的路径。

本地工程目录格式版本为 1。移动时保留目录内相对布局，不要单独移动数据库或只修改入口中的 `rootPath`。
