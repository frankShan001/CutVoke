/** 工程首页（P1 第一阶段）：产品定位 + 最近工程 + 新建工程。
    职责：只做“选/建/改名工程”，进入编辑器后交给 Workspace。
    不用 history 路由（vite.config.ts:10），靠 store.view 状态切换视图。 */

import { useEffect, useState } from "react";
import { Clapperboard, Plus, FolderOpen, Pencil, Check, X, RefreshCw, PackageOpen, LayoutTemplate } from "lucide-react";
import { useEditor, showError } from "../store/editor";
import { createProject, loadProjects, renameProject, selectProject } from "../store/actions";
import { openPackage } from "../lib/api";

const DEFAULT_W = 1920;
const DEFAULT_H = 1080;
const DEFAULT_FPS = 30;

/** 相对时间：把 ISO 串转成「3 分钟前」。 */
function relativeTime(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 60) return "刚刚";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(t).toLocaleDateString("zh-CN");
}

/** 无名称工程的稳定回退标签：用 updatedAt 出「未命名工程 MM-DD」。
    刻意不用相对时间（relativeTime），以免 aria-label 定位器随时间漂移。 */
function unnamedLabel(iso: string): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return "未命名工程";
  const d = new Date(t);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `未命名工程 ${mm}-${dd}`;
}

export function HomeView() {
  const { state, dispatch } = useEditor();
  const [name, setName] = useState("");
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
      width: DEFAULT_W,
      height: DEFAULT_H,
      fps: DEFAULT_FPS,
    });
    setBusy(false);
    if (pid) {
      setName("");
      dispatch({ type: "PROJECT_NAME_SET", name: trimmed || unnamedLabel(new Date().toISOString()) });
      dispatch({ type: "VIEW_SET", view: "editor" });
    }
  };

  const handleOpenPackage = async () => {
    const trimmed = packPath.trim();
    if (!trimmed) {
      showError(dispatch, { status: 400, code: "INVALID_ARGUMENT", message: "请填写资源包路径" });
      return;
    }
    setPackBusy(true);
    setPackWarnings([]);
    try {
      const res = await openPackage(trimmed);
      setPackWarnings(res.warnings || []);
      await loadProjects(dispatch);
      dispatch({ type: "STATUS_SET", severity: "ok", text: `已解包导入工程：${res.name || res.projectId}` });
      setPackPath("");
    } catch (err) {
      showError(dispatch, err);
    } finally {
      setPackBusy(false);
    }
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
          <Clapperboard size={22} />
          <span className="home__brand-name">CutVoke</span>
        </div>
        <h1 className="home__title">在浏览器里剪片，交给 Agent 精修</h1>
        <p className="home__subtitle">
          本地优先的视频编辑器：拖入素材、在时间线上直接剪，所有编辑都是可继续精修的时间线对象。
        </p>
      </header>

      <section className="home__create" aria-label="新建工程">
        <div className="home__create-row">
          <input
            className="cv-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
            }}
            placeholder="给这个工程起个名字"
            aria-label="新工程名称"
          />
          <button
            className="cv-btn cv-btn--primary cv-btn--md"
            onClick={() => void handleCreate()}
            disabled={busy}
            aria-label="创建工程"
          >
            <Plus size={14} />
            {busy ? "创建中…" : "创建工程"}
          </button>
          <button
            className="cv-btn cv-btn--md"
            onClick={() => dispatch({ type: "TEMPLATE_OPEN_SET", open: true })}
            aria-label="从模板新建"
            title="从模板新建：选模板后按画幅建工程并套用"
          >
            <LayoutTemplate size={14} />
            从模板新建
          </button>
        </div>
        <p className="cv-hint">名称留空时由后端分配 ID，可稍后重命名；或选一个模板快速搭好结构。</p>
      </section>

      <section className="home__create" aria-label="打开资源包">
        <div className="home__create-row">
          <input
            className="cv-input"
            value={packPath}
            onChange={(e) => setPackPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleOpenPackage();
            }}
            placeholder="资源包路径，如：D:\交接\project.cutvokepack.zip"
            aria-label="资源包路径"
          />
          <button
            className="cv-btn cv-btn--md"
            onClick={() => void handleOpenPackage()}
            disabled={packBusy}
            aria-label="解包打开"
          >
            <PackageOpen size={14} />
            {packBusy ? "解包中…" : "解包打开"}
          </button>
        </div>
        <p className="cv-hint">
          打开他人交接的 .cutvokepack 资源包：解包素材并导入为新工程，可继续编辑。
        </p>
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
          <h2 className="home__section-title">最近工程</h2>
          <button className="cv-btn cv-btn--ghost cv-btn--sm" onClick={() => void loadProjects(dispatch)}>
            <RefreshCw size={13} />
            刷新
          </button>
        </div>

        {state.projects.length === 0 ? (
          <div className="home__empty">
            <FolderOpen size={20} />
            <p>还没有工程。在上方输入名称创建你的第一个工程，然后拖入素材开始剪辑。</p>
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
                    aria-label={`打开工程 ${label}`}
                    title={`打开 ${label}`}
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
                        aria-label="工程名称"
                      />
                      <button
                        className="cv-btn cv-btn--ghost cv-btn--sm"
                        onClick={() => void commitRename(p.id)}
                        aria-label="确认重命名"
                      >
                        <Check size={14} />
                      </button>
                      <button
                        className="cv-btn cv-btn--ghost cv-btn--sm"
                        onClick={() => setEditingId(null)}
                        aria-label="取消重命名"
                      >
                        <X size={14} />
                      </button>
                    </span>
                  ) : (
                    <button
                      className="cv-btn cv-btn--ghost cv-btn--sm"
                      onClick={() => startRename(p.id, label)}
                      aria-label={`重命名工程 ${label}`}
                      title="重命名"
                    >
                      <Pencil size={13} />
                      重命名
                    </button>
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
