import { msg, tr, useLocale } from "../i18n";
import { getLocale } from "../i18n/locale";
/** 工程首页（P1 第一阶段）：产品定位 + 最近工程 + 新建工程。
    职责：只做“选/建/改名工程”，进入编辑器后交给 Workspace。
    不用 history 路由（vite.config.ts:10），靠 store.view 状态切换视图。 */

import { useEffect, useState } from "react";
import { Plus, FolderOpen, Pencil, Check, X, RefreshCw, PackageOpen, LayoutTemplate } from "lucide-react";
import { useEditor, showError } from "../store/editor";
import { createProject, loadProjects, renameProject, selectProject } from "../store/actions";
import { openPackage } from "../lib/api";
import { invalidateProject } from "../lib/api";
import { openProjectDirectory } from "../lib/projectDirectories";
import { CacheSettings } from "./CacheSettings";
import { LanguageSelect } from "./LanguageSelect";

const DEFAULT_W = 1920;
const DEFAULT_H = 1080;
const DEFAULT_FPS = 30;

/** 相对时间：把 ISO 串转成「3 分钟前」。 */
function relativeTime(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 60) return tr("刚刚");
  const min = Math.floor(sec / 60);
  if (min < 60) return tr("{0} 分钟前", min);
  const hr = Math.floor(min / 60);
  if (hr < 24) return tr("{0} 小时前", hr);
  const day = Math.floor(hr / 24);
  if (day < 30) return tr("{0} 天前", day);
  return new Date(t).toLocaleDateString(getLocale());
}

/** 无名称工程的稳定回退标签：用 updatedAt 出「未命名工程 MM-DD」。
    刻意不用相对时间（relativeTime），以免 aria-label 定位器随时间漂移。 */
function unnamedLabel(iso: string): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return tr("未命名工程");
  const d = new Date(t);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return tr("未命名工程 {0}-{1}", mm, dd);
}

export function HomeView() {
  useLocale();
  const { state, dispatch } = useEditor();
  const [name, setName] = useState("");
  const [directory, setDirectory] = useState("");
  const [openDirectory, setOpenDirectory] = useState("");
  const [directoryBusy, setDirectoryBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");

  // 解包打开资源包（J10 异机交接：接收者在本机打开交接包继续编辑）
  const [packPath, setPackPath] = useState("");
  const [packBusy, setPackBusy] = useState(false);
  const [packWarnings, setPackWarnings] = useState<string[]>([]);

  useEffect(() => {
    void loadProjects(dispatch);
  }, [dispatch]);

  const enterEditor = (projectId: string, projectName: string) => {
    dispatch({ type: "PROJECT_NAME_SET", name: projectName });
    dispatch({ type: "VIEW_SET", view: "editor" });
    void selectProject(dispatch, projectId, projectName);
  };

  const handleCreate = async () => {
    const trimmed = name.trim();
    setBusy(true);
    const pid = await createProject(dispatch, {
      projectId: trimmed || undefined,
      name: trimmed || undefined,
      directory: directory.trim() || undefined,
      width: DEFAULT_W,
      height: DEFAULT_H,
      fps: DEFAULT_FPS,
    });
    setBusy(false);
    if (pid) {
      setName("");
      setDirectory("");
      dispatch({ type: "PROJECT_NAME_SET", name: trimmed || unnamedLabel(new Date().toISOString()) });
      dispatch({ type: "VIEW_SET", view: "editor" });
    }
  };

  const handleOpenPackage = async () => {
    const trimmed = packPath.trim();
    if (!trimmed) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: tr("请填写资源包路径") });
      return;
    }
    setPackBusy(true);
    setPackWarnings([]);
    try {
      const res = await openPackage(trimmed);
      setPackWarnings(res.warnings || []);
      await loadProjects(dispatch);
      dispatch({ type: "STATUS_SET", severity: "ok", text: msg("已解包导入工程：{0}", res.name || res.projectId) });
      setPackPath("");
    } catch (err) {
      showError(dispatch, err);
    } finally {
      setPackBusy(false);
    }
  };

  const handleOpenDirectory = async () => {
    if (!openDirectory.trim()) return;
    setDirectoryBusy(true);
    try {
      const result = await openProjectDirectory(openDirectory.trim());
      invalidateProject(result.projectId);
      await loadProjects(dispatch);
      enterEditor(result.projectId, result.name || result.projectId);
      setOpenDirectory("");
    } catch (error) { showError(dispatch, error); }
    finally { setDirectoryBusy(false); }
  };

  const startRename = (id: string, current: string) => {
    setEditingId(id);
    setEditingName(current);
  };

  const commitRename = async (id: string) => {
    const trimmed = editingName.trim();
    setEditingId(null);
    if (trimmed) await renameProject(dispatch, id, trimmed);
  };

  return (
    <div className="home">
      <header className="home__hero">
        <div className="home__brand">
          <img src="/brand/icon-128.png" width="36" height="36" alt="" aria-hidden="true" draggable={false} />
          <span className="home__brand-name">CutVoke</span>
          <CacheSettings />
          <LanguageSelect />
        </div>
        <h1 className="home__title">{tr("本地剪片，交给 Agent 精修")}</h1>
        <p className="home__subtitle">{tr("本地优先的视频编辑器：拖入素材、在时间线上直接剪，所有编辑都是可继续精修的时间线对象。")}</p>
      </header>

      <section className="home__create" aria-label={tr("新建工程")}>
        <div className="home__create-row">
          <input
            className="cv-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
            }}
            placeholder={tr("给这个工程起个名字")}
            aria-label={tr("新工程名称")}
          />
          <button
            className="cv-btn cv-btn--primary cv-btn--md"
            onClick={() => void handleCreate()}
            disabled={busy}
            aria-label={tr("创建工程")}
          >
            <Plus size={14} />
            {busy ? tr("创建中…") : tr("创建工程")}
          </button>
          <button
            className="cv-btn cv-btn--md"
            onClick={() => dispatch({ type: "TEMPLATE_OPEN_SET", open: true })}
            aria-label={tr("从模板新建")}
            title={tr("从模板新建：选模板后按画幅建工程并套用")}
          >
            <LayoutTemplate size={14} />{tr("从模板新建")}</button>
        </div>
        <input className="cv-input" style={{ marginTop: 8, width: "100%" }} value={directory} disabled={busy}
          onChange={event => setDirectory(event.target.value)} aria-label={tr("新工程目录")}
          placeholder={tr("新工程目录（留空使用默认位置，请填写尚不存在的目录）")} />
        <p className="cv-hint">{tr("每个新工程独立保存素材、工程数据、缓存和导出。")}</p>
      </section>

      <section className="home__create" aria-label={tr("打开工程目录")}>
        <div className="home__create-row">
          <input className="cv-input" value={openDirectory} disabled={directoryBusy}
            onChange={event => setOpenDirectory(event.target.value)}
            onKeyDown={event => { if (event.key === "Enter") void handleOpenDirectory(); }}
            aria-label={tr("工程目录路径")} placeholder={tr("工程目录或 project.cutvoke 文件路径")} />
          {window.cutvokeDesktop && <button className="cv-btn cv-btn--md" disabled={directoryBusy}
            onClick={async () => { const path = await window.cutvokeDesktop?.chooseProjectDirectory(); if (path) setOpenDirectory(path); }}>{tr("浏览")}</button>}
          <button className="cv-btn cv-btn--md" disabled={directoryBusy || !openDirectory.trim()} onClick={() => void handleOpenDirectory()}>
            <FolderOpen size={14} />{tr("打开工程目录")}</button>
        </div>
      </section>

      <section className="home__create" aria-label={tr("打开资源包")}>
        <div className="home__create-row">
          <input
            className="cv-input"
            value={packPath}
            onChange={(e) => setPackPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleOpenPackage();
            }}
            placeholder={tr("资源包路径，如：D:\\交接\\project.cutvokepack.zip")}
            aria-label={tr("资源包路径")}
          />
          <button
            className="cv-btn cv-btn--md"
            onClick={() => void handleOpenPackage()}
            disabled={packBusy}
            aria-label={tr("解包打开")}
          >
            <PackageOpen size={14} />
            {packBusy ? tr("解包中…") : tr("解包打开")}
          </button>
        </div>
        <p className="cv-hint">{tr("打开他人交接的 .cutvokepack 资源包：解包素材并导入为新工程，可继续编辑。")}</p>
        {packWarnings.length > 0 && (
          <div className="home__pack-warnings" aria-live="polite">
            {packWarnings.map((w) => (
              <div key={w} className="cv-hint">{w}</div>
            ))}
          </div>
        )}
      </section>

      <section className="home__recent">
        <div className="home__recent-head">
          <h2 className="home__section-title">{tr("最近工程")}</h2>
          <button className="cv-btn cv-btn--ghost cv-btn--sm" onClick={() => void loadProjects(dispatch)}>
            <RefreshCw size={13} />{tr("刷新")}</button>
        </div>

        {state.projects.length === 0 ? (
          <div className="home__empty">
            <FolderOpen size={20} />
            <p>{tr("还没有工程。在上方输入名称创建你的第一个工程，然后拖入素材开始剪辑。")}</p>
          </div>
        ) : (
          <ul className="home__list">
            {state.projects.map((p) => {
              const editing = editingId === p.id;
              const label = p.name || unnamedLabel(p.updatedAt);
              return (
                <li key={p.id} className="home__item">
                  <button
                    className="home__item-main"
                    onClick={() => enterEditor(p.id, label)}
                    aria-label={tr("打开工程 {0}", label)}
                    title={tr("打开 {0}", label)}
                  >
                    <span className="home__item-icon">
                      <FolderOpen size={15} />
                    </span>
                    <span className="home__item-text">
                      <span className="home__item-name">{label}</span>
                      <span className="home__item-meta">
                        {relativeTime(p.updatedAt) || "—"}
                      </span>
                    </span>
                  </button>

                  {editing ? (
                    <span className="home__item-rename">
                      <input
                        className="cv-input"
                        value={editingName}
                        autoFocus
                        onChange={(e) => setEditingName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void commitRename(p.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        aria-label={tr("工程名称")}
                      />
                      <button
                        className="cv-btn cv-btn--ghost cv-btn--sm"
                        onClick={() => void commitRename(p.id)}
                        aria-label={tr("确认重命名")}
                      >
                        <Check size={14} />
                      </button>
                      <button
                        className="cv-btn cv-btn--ghost cv-btn--sm"
                        onClick={() => setEditingId(null)}
                        aria-label={tr("取消重命名")}
                      >
                        <X size={14} />
                      </button>
                    </span>
                  ) : (
                    <button
                      className="cv-btn cv-btn--ghost cv-btn--sm"
                      onClick={() => startRename(p.id, label)}
                      aria-label={tr("重命名工程 {0}", label)}
                      title={tr("重命名")}
                    >
                      <Pencil size={13} />{tr("重命名")}</button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
