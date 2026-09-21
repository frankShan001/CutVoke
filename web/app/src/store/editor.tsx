/** 编辑器全局状态（Context + useReducer）。UI 组件只消费状态与动作。 */

import {
  createContext,
  useContext,
  useReducer,
  type ReactNode,
  type Dispatch,
} from "react";
import {
  ApiFailure,
  createProject as apiCreate,
  exportProject as apiExport,
  getProject,
  listEvents,
  listProjects,
  newCommandId,
  sendCommand,
} from "../lib/api";
import { secsToRational } from "../lib/rational";
import type { ProjectListItem } from "../lib/api";
import type { EditLease } from "../lib/api";
import type { ExportResult, Project, Rational } from "../types/api";

export type Severity = "idle" | "ok" | "warn" | "err";

export interface Selection {
  trackId: string;
  clipId?: string;
}

export type ToolMode = "select" | "cut";

/** 顶层视图：工程首页 / 编辑工作区（vite 不用 history 路由，靠组件内状态切换）。 */
export type View = "home" | "editor";

/** 与后端的保存态镜像：saving=正在提交，saved=已落库，error=提交失败。 */
export type SyncState = "idle" | "saving" | "saved" | "error";

export interface EditorState {
  projects: ProjectListItem[];
  currentId: string | null;
  project: Project | null;
  revision: string;
  serverUp: boolean;
  undoBlocked: boolean;
  redoBlocked: boolean;
  historyHint: string;
  error: ApiFailure | null;
  status: { severity: Severity; text: string };
  selection: Selection | null;
  lastSync: number;
  /** 播放头时间（秒），Player 与时间线标尺共享。 */
  playhead: number;
  /** 剪映式工具模式：select 选择 / cut 切割。 */
  toolMode: ToolMode;
  /** 磁吸吸附开关（默认开）。 */
  snapEnabled: boolean;
  /** 当前顶层视图（首页 / 编辑器）。 */
  view: View;
  /** 当前工程的人类可读名称（空则回退 projectId）。 */
  projectName: string;
  /** 与后端的保存态镜像（驱动状态栏“保存中… / 已保存 / 保存失败”）。 */
  syncState: SyncState;
  /** 统一导出抽屉是否打开。 */
  exportOpen: boolean;
  /** Agent 活动抽屉（D10）是否打开。 */
  agentPanelOpen: boolean;
  /** J12 模板库抽屉是否打开。 */
  templateOpen: boolean;
  /** 当前 Agent 编辑租约；存在时整个工作区只读但仍可观察工程变化。 */
  editLock: EditLease | null;
}

export type EditorAction =
  | { type: "PROJECTS_LOADED"; projects: ProjectListItem[] }
  | { type: "PROJECT_SELECTED"; projectId: string; name?: string }
  | { type: "PROJECT_LOADED"; project: Project }
  | { type: "REVISION_SET"; revision: string }
  | { type: "SVC_UP"; up: boolean }
  | { type: "UNDO_SET"; blocked: boolean }
  | { type: "REDO_SET"; blocked: boolean }
  | { type: "HISTORY_HINT"; hint: string }
  | { type: "ERROR_SET"; error: ApiFailure | null }
  | { type: "STATUS_SET"; severity: Severity; text: string }
  | { type: "SELECTION_SET"; selection: Selection | null }
  | { type: "SYNC" }
  | { type: "RESET_SELECTION_FOR"; trackId: string | null }
  | { type: "PLAYHEAD_SET"; t: number }
  | { type: "TOOL_MODE_SET"; mode: ToolMode }
  | { type: "SNAP_TOGGLE" }
  | { type: "VIEW_SET"; view: View }
  | { type: "PROJECT_NAME_SET"; name: string }
  | { type: "SYNC_STATE_SET"; syncState: SyncState }
  | { type: "EXPORT_OPEN_SET"; open: boolean }
  | { type: "AGENT_PANEL_OPEN_SET"; open: boolean }
  | { type: "TEMPLATE_OPEN_SET"; open: boolean }
  | { type: "EDIT_LOCK_SET"; lease: EditLease | null };

const initialState: EditorState = {
  projects: [],
  currentId: null,
  project: null,
  revision: "",
  serverUp: false,
  undoBlocked: false,
  redoBlocked: true,
  historyHint: "",
  error: null,
  status: { severity: "idle", text: "启动中…" },
  selection: null,
  lastSync: 0,
  playhead: 0,
  toolMode: "select",
  snapEnabled: true,
  view: "home",
  projectName: "",
  syncState: "idle",
  exportOpen: false,
  agentPanelOpen: false,
  templateOpen: false,
  editLock: null,
};

function reducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "PROJECTS_LOADED":
      return { ...state, projects: action.projects };
    case "PROJECT_SELECTED":
      return {
        ...state,
        currentId: action.projectId,
        projectName: action.name !== undefined ? action.name : state.projectName,
        editLock: null,
      };
    case "PROJECT_LOADED":
      return {
        ...state,
        project: action.project,
        revision: action.project.revision,
        lastSync: Date.now(),
        syncState: "saved",
      };
    case "REVISION_SET":
      return { ...state, revision: action.revision };
    case "SVC_UP":
      return { ...state, serverUp: action.up };
    case "UNDO_SET":
      return { ...state, undoBlocked: action.blocked };
    case "REDO_SET":
      return { ...state, redoBlocked: action.blocked };
    case "HISTORY_HINT":
      return { ...state, historyHint: action.hint };
    case "ERROR_SET":
      return { ...state, error: action.error };
    case "STATUS_SET":
      return { ...state, status: { severity: action.severity, text: action.text } };
    case "SELECTION_SET":
      return { ...state, selection: action.selection };
    case "SYNC":
      return { ...state, lastSync: Date.now() };
    case "PLAYHEAD_SET":
      return { ...state, playhead: action.t };
    case "TOOL_MODE_SET":
      return { ...state, toolMode: action.mode };
    case "SNAP_TOGGLE":
      return { ...state, snapEnabled: !state.snapEnabled };
    case "VIEW_SET":
      return { ...state, view: action.view };
    case "PROJECT_NAME_SET":
      return { ...state, projectName: action.name };
    case "SYNC_STATE_SET":
      return { ...state, syncState: action.syncState };
    case "EXPORT_OPEN_SET":
      return { ...state, exportOpen: action.open };
    case "AGENT_PANEL_OPEN_SET":
      return { ...state, agentPanelOpen: action.open };
    case "TEMPLATE_OPEN_SET":
      return { ...state, templateOpen: action.open };
    case "EDIT_LOCK_SET":
      return { ...state, editLock: action.lease };
    case "RESET_SELECTION_FOR":
      if (!action.trackId) {
        return { ...state, selection: null };
      }
      if (state.selection && state.selection.trackId === action.trackId) {
        return { ...state, selection: null };
      }
      return state;
    default:
      return state;
  }
}

interface EditorContextValue {
  state: EditorState;
  dispatch: Dispatch<EditorAction>;
}

const EditorContext = createContext<EditorContextValue | null>(null);

export function EditorProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <EditorContext.Provider value={{ state, dispatch }}>
      {children}
    </EditorContext.Provider>
  );
}

export function useEditor(): EditorContextValue {
  const ctx = useContext(EditorContext);
  if (!ctx) throw new Error("useEditor must be used within EditorProvider");
  return ctx;
}

// ---------------------------------------------------------------------------
// 动作辅助（供组件调用）。结构错误统一落入 state.error。
// ---------------------------------------------------------------------------

function fail(err: unknown): ApiFailure {
  return err instanceof ApiFailure ? err : new ApiFailure({
    status: 0,
    code: "NETWORK",
    message: err instanceof Error ? err.message : "网络错误",
    retryable: true,
  });
}

/** 渲染错误浮窗 + 状态栏。 */
export function showError(dispatch: Dispatch<EditorAction>, err: unknown) {
  const f = fail(err);
  dispatch({ type: "ERROR_SET", error: f });
  dispatch({ type: "STATUS_SET", severity: "err", text: `${f.code} — ${f.message}` });
  return f;
}

export const selectors = {
  videoTracks: (p: Project | null) => (p?.sequence.tracks || []).filter((t) => t.kind === "video"),
  audioTracks: (p: Project | null) => (p?.sequence.tracks || []).filter((t) => t.kind === "audio"),
  trackById: (p: Project | null, id: string) =>
    (p?.sequence.tracks || []).find((t) => t.id === id),
  /** 全部轨道（视频优先，供拖拽跨轨目标选择）。 */
  allTracks: (p: Project | null) => p?.sequence.tracks || [],
};

/** 将秒输入解析为有理数，出错抛出本地错误（由调用方捕获展示）。 */
export function parseRationalOrThrow(input: string | number): Rational {
  try {
    return secsToRational(input);
  } catch (e) {
    throw new ApiFailure({
      status: 400,
      code: "INVALID_ARGUMENT",
      message: e instanceof Error ? e.message : "时间格式错误",
    });
  }
}

export { apiExport, apiCreate, getProject, listEvents, listProjects, newCommandId, sendCommand };
export type { ExportResult, Project, ProjectListItem };
