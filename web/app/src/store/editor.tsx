/** 编辑器全局状态（Context + useReducer）。UI 组件只消费状态与动作。 */

import {
  createContext,
  useContext,
  useReducer,
  useMemo,
  useRef,
  useEffect,
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
import type { PreparedPreview } from "../lib/previewPreparation";

export type Severity = "idle" | "ok" | "warn" | "err";

export interface Selection {
  trackId: string;
  clipId?: string;
  /** 时间线上的效果对象；存在时 clipId 仍指向效果所属片段。 */
  effectId?: string;
  /** 效果栈下标，用于区分同一片段上相同 effectId 的多个实例。 */
  effectIndex?: number;
  effectKind?: "transition" | "effect";
}

/** 未保存的字幕草稿只用于本地画布预览，不进入工程或版本历史。 */
export interface CaptionDraftPreview {
  projectId: string;
  captionId: string;
  text: string;
  start: Rational;
  end: Rational;
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
  opening: boolean;
  preparedPreview: PreparedPreview | null;
  revision: string;
  serverUp: boolean;
  undoBlocked: boolean;
  redoBlocked: boolean;
  historyHint: string;
  error: ApiFailure | null;
  status: { severity: Severity; text: string };
  selection: Selection | null;
  /** 当前由字幕列表或预览画布选中的字幕；纯 UI 状态，不写入工程。 */
  selectedCaptionId: string | null;
  lastSync: number;
  /** 播放头时间（秒），Player 与时间线标尺共享。 */
  playhead: number;
  /** 标尺或播放头正在拖动；只影响预览调度，不写入工程。 */
  scrubbing: boolean;
  /** 剪映式工具模式：select 选择 / cut 切割。 */
  toolMode: ToolMode;
  /** 磁吸吸附开关（默认开）。 */
  snapEnabled: boolean;
  /** 主视频轨自动贴合：移动片段时按插入顺序压紧主轨；关闭后保留空隙。 */
  mainTrackAutoFitEnabled: boolean;
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
  /** CaptionPanel 的临时画布预览态；保存/放弃/锁定后清空。 */
  captionDraftPreview: CaptionDraftPreview | null;
}

export type EditorAction =
  | { type: "PROJECTS_LOADED"; projects: ProjectListItem[] }
  | { type: "PROJECT_SELECTED"; projectId: string; name?: string }
  | { type: "PROJECT_LOADED"; project: Project }
  | { type: "PREPARED_PREVIEW_SET"; preview: PreparedPreview | null }
  | { type: "PROJECT_OPEN_READY"; projectId: string }
  | { type: "PROJECT_CLOSED" }
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
  | { type: "SCRUBBING_SET"; active: boolean }
  | { type: "TOOL_MODE_SET"; mode: ToolMode }
  | { type: "SNAP_TOGGLE" }
  | { type: "MAIN_TRACK_AUTO_FIT_TOGGLE" }
  | { type: "VIEW_SET"; view: View }
  | { type: "PROJECT_NAME_SET"; name: string }
  | { type: "SYNC_STATE_SET"; syncState: SyncState }
  | { type: "EXPORT_OPEN_SET"; open: boolean }
  | { type: "AGENT_PANEL_OPEN_SET"; open: boolean }
  | { type: "TEMPLATE_OPEN_SET"; open: boolean }
  | { type: "EDIT_LOCK_SET"; lease: EditLease | null }
  | { type: "CAPTION_DRAFT_PREVIEW_SET"; preview: CaptionDraftPreview | null }
  | { type: "CAPTION_SELECTION_SET"; captionId: string | null };

const initialState: EditorState = {
  projects: [],
  currentId: null,
  project: null,
  opening: false,
  preparedPreview: null,
  revision: "",
  serverUp: false,
  undoBlocked: false,
  redoBlocked: true,
  historyHint: "",
  error: null,
  status: { severity: "idle", text: "启动中…" },
  selection: null,
  selectedCaptionId: null,
  lastSync: 0,
  playhead: 0,
  scrubbing: false,
  toolMode: "select",
  snapEnabled: true,
  mainTrackAutoFitEnabled: true,
  view: "home",
  projectName: "",
  syncState: "idle",
  exportOpen: false,
  agentPanelOpen: false,
  templateOpen: false,
  editLock: null,
  captionDraftPreview: null,
};

function sameLeaseIdentity(current: EditLease | null, next: EditLease | null): boolean {
  if (current === next) return true;
  if (!current || !next) return false;
  // expiresAt may move forward on each heartbeat, but the UI only cares whether
  // the active lease/owner changed. Avoid rerendering the whole editor per poll.
  return (
    current.projectId === next.projectId &&
    current.leaseId === next.leaseId &&
    current.owner === next.owner
  );
}

function reconcileSelection(project: Project, selection: Selection | null): Selection | null {
  if (!selection) return null;
  if (!selection.clipId) {
    return project.sequence.tracks.some((track) => track.id === selection.trackId)
      ? selection : null;
  }
  for (const track of project.sequence.tracks) {
    const clip = track.clips.find((item) => item.id === selection.clipId);
    if (!clip) continue;
    if (selection.effectId && !clip.effects?.some((effect) => effect.effectId === selection.effectId)) {
      return null;
    }
    return track.id === selection.trackId ? selection : { ...selection, trackId: track.id };
  }
  return null;
}

function reducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "PROJECTS_LOADED":
      return { ...state, projects: action.projects };
    case "PROJECT_SELECTED":
      return {
        ...state,
        currentId: action.projectId,
        project: null,
        revision: "",
        playhead: 0,
        scrubbing: false,
        error: null,
        opening: true,
        preparedPreview: null,
        projectName: action.name !== undefined ? action.name : state.projectName,
        selection: null,
        selectedCaptionId: null,
        editLock: null,
        captionDraftPreview: null,
      };
    case "PROJECT_LOADED":
      if (action.project.projectId !== state.currentId) return state;
      return {
        ...state,
        project: action.project,
        revision: action.project.revision,
        selection: reconcileSelection(action.project, state.selection),
        selectedCaptionId: (action.project.sequence.captions || []).some(
          (caption) => caption.id === state.selectedCaptionId,
        ) ? state.selectedCaptionId : null,
        lastSync: Date.now(),
        syncState: "saved",
      };
    case "PREPARED_PREVIEW_SET":
      if (action.preview && action.preview.projectId !== state.currentId) return state;
      return { ...state, preparedPreview: action.preview };
    case "PROJECT_OPEN_READY":
      return action.projectId === state.currentId ? { ...state, opening: false } : state;
    case "PROJECT_CLOSED":
      return { ...state, currentId: null, project: null, revision: "", playhead: 0,
        scrubbing: false, preparedPreview: null, opening: false, error: null, editLock: null, view: "home" };
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
    case "SCRUBBING_SET":
      return state.scrubbing === action.active ? state : { ...state, scrubbing: action.active };
    case "TOOL_MODE_SET":
      return { ...state, toolMode: action.mode };
    case "SNAP_TOGGLE":
      return { ...state, snapEnabled: !state.snapEnabled };
    case "MAIN_TRACK_AUTO_FIT_TOGGLE":
      return { ...state, mainTrackAutoFitEnabled: !state.mainTrackAutoFitEnabled };
    case "VIEW_SET":
      return { ...state, view: action.view,
        ...(action.view === "home" ? { preparedPreview: null, opening: false, scrubbing: false } : {}) };
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
      if (sameLeaseIdentity(state.editLock, action.lease)) return state;
      return {
        ...state,
        editLock: action.lease,
        captionDraftPreview: action.lease ? null : state.captionDraftPreview,
      };
    case "CAPTION_DRAFT_PREVIEW_SET":
      return { ...state, captionDraftPreview: action.preview };
    case "CAPTION_SELECTION_SET": {
      const draftCaptionId = state.captionDraftPreview?.captionId;
      if (draftCaptionId && draftCaptionId === state.selectedCaptionId
          && action.captionId !== state.selectedCaptionId) {
        return {
          ...state,
          status: { severity: "warn", text: "当前字幕有未保存的文字或时间修改，请先保存或放弃" },
        };
      }
      return { ...state, selectedCaptionId: action.captionId };
    }
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
const EditingContext = createContext<EditorContextValue | null>(null);

export function EditorProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  useEffect(() => {
    const url = state.preparedPreview?.url;
    return () => { if (url?.startsWith("blob:")) URL.revokeObjectURL(url); };
  }, [state.preparedPreview?.url]);
  const editingState = useRef(state);
  // Playback and polling clocks must not invalidate thousands of asset/clip rows.
  // This snapshot updates for every editing change; event handlers read the live
  // playhead through getLatestState() when they need a clock value.
  if ((Object.keys(state) as (keyof EditorState)[]).some((key) =>
    key !== "playhead" && key !== "lastSync" && state[key] !== editingState.current[key])) {
    editingState.current = state;
  }
  const editingValue = useMemo(() => ({ state: editingState.current, dispatch }), [editingState.current, dispatch]);
  return (
    <EditorContext.Provider value={{ state, dispatch }}>
      <EditingContext.Provider value={editingValue}>{children}</EditingContext.Provider>
    </EditorContext.Provider>
  );
}

/** subscribeToClock=false is for lists/toolbars that do not display a live clock. */
export function useEditor(options?: { subscribeToClock?: boolean }): EditorContextValue {
  const ctx = useContext(options?.subscribeToClock === false ? EditingContext : EditorContext);
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
