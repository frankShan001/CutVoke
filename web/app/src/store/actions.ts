/** 领域动作：封装 API 调用 + 状态派发。组件只调用这些动作，不含业务逻辑。 */

import type { Dispatch } from "react";
import { ApiFailure, type CommandResult } from "../lib/api";
import {
  createProject as apiCreate,
  exportProject as apiExport,
  getEditLock,
  type ExportQuality,
  getProject,
  listEvents,
  listProjects,
  newCommandId,
  type ProjectListItem,
  renameProject as apiRename,
  sendCommand,
} from "../lib/api";
import { showError, type EditorAction, type EditorState } from "./editor";
import type { ExportResult, Project, Rational } from "../types/api";

export interface ActionResult {
  ok: boolean;
  command?: CommandResult;
  error?: ApiFailure;
}

function isRetryableConflict(err: unknown): boolean {
  return err instanceof ApiFailure && err.code === "REVISION_CONFLICT" && !!err.retryable;
}

/** 拉取工程列表并标记连接状态。返回列表（失败返回 null）。 */
export async function loadProjects(
  dispatch: Dispatch<EditorAction>,
): Promise<ProjectListItem[] | null> {
  try {
    const projects = await listProjects();
    dispatch({ type: "PROJECTS_LOADED", projects });
    dispatch({ type: "SVC_UP", up: true });
    return projects;
  } catch (err) {
    dispatch({ type: "SVC_UP", up: false });
    showError(dispatch, err);
    return null;
  }
}

/** 选择并加载工程。name 可选：用于状态栏/左栏显示人类可读名（缺省回退 id）。 */
export async function selectProject(
  dispatch: Dispatch<EditorAction>,
  projectId: string,
  name?: string,
): Promise<boolean> {
  dispatch({ type: "PROJECT_SELECTED", projectId, name: name ?? projectId });
  return refreshProject(dispatch, projectId);
}

/** 刷新工程（重置到最新 revision）。加载成功后状态栏显示"就绪"（不再长期"启动中"）。 */
export async function refreshProject(
  dispatch: Dispatch<EditorAction>,
  projectId: string,
): Promise<boolean> {
  try {
    const project = await getProject(projectId);
    dispatch({ type: "PROJECT_LOADED", project });
    dispatch({ type: "REVISION_SET", revision: project.revision });
    dispatch({ type: "RESET_SELECTION_FOR", trackId: null });
    dispatch({ type: "SVC_UP", up: true });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "就绪" });
    return true;
  } catch (err) {
    dispatch({ type: "SYNC_STATE_SET", syncState: "error" });
    showError(dispatch, err);
    return false;
  }
}

/** 轮询 Agent 编辑租约；读取失败时保留上一次状态，避免网络抖动误解锁。 */
export async function pollEditLock(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
): Promise<void> {
  if (!state.currentId) {
    dispatch({ type: "EDIT_LOCK_SET", lease: null });
    return;
  }
  try {
    const status = await getEditLock(state.currentId);
    dispatch({ type: "EDIT_LOCK_SET", lease: status.locked ? status.lease : null });
  } catch {
    // 锁状态宁可保守保持，也不因一次断网让人工操作穿透 Agent 编辑窗口。
  }
}

/** 创建工程。projectId 可选（由后端分配）；name 可选（人类可读名，后端持久化）。创建后自动选中（WP-03/A07）。 */
export async function createProject(
  dispatch: Dispatch<EditorAction>,
  opts: { projectId?: string; name?: string; width: number; height: number; fps: number },
): Promise<string | null> {
  dispatch({ type: "SYNC_STATE_SET", syncState: "saving" });
  try {
    const res = await apiCreate(opts);
    const pid = res.projectId;
    if (!pid) throw new ApiFailure({ status: 500, code: "CREATE_FAILED", message: "创建工程未返回 projectId" });
    // 显示名优先级：用户输入名 → 输入的 projectId → 后端分配 id。
    const displayName = opts.name?.trim() || opts.projectId?.trim() || pid;
    dispatch({ type: "PROJECT_SELECTED", projectId: pid, name: displayName });
    await loadProjects(dispatch);
    await refreshProject(dispatch, pid);
    return pid;
  } catch (err) {
    dispatch({ type: "SYNC_STATE_SET", syncState: "error" });
    showError(dispatch, err);
    return null;
  }
}

/**
 * 重命名工程：先乐观更新显示名，再 PATCH 后端；失败回滚并报错。
 * 后端 PATCH /projects/{id} 由并行 worker 提供，未落地时调用会收到 4xx → 如实报错，不静默。
 */
export async function renameProject(
  dispatch: Dispatch<EditorAction>,
  projectId: string,
  name: string,
): Promise<boolean> {
  const trimmed = name.trim();
  if (!trimmed) return false;
  dispatch({ type: "SYNC_STATE_SET", syncState: "saving" });
  try {
    await apiRename(projectId, trimmed);
    dispatch({ type: "PROJECT_NAME_SET", name: trimmed });
    // 列表内就地改名（避免重命名后本地列表仍显示旧名，下次拉取会覆盖）
    const renamed = (await listProjects()).map((p) => (p.id === projectId ? { ...p, name: trimmed } : p));
    dispatch({ type: "PROJECTS_LOADED", projects: renamed });
    dispatch({ type: "SYNC_STATE_SET", syncState: "saved" });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已重命名为 ${trimmed}` });
    return true;
  } catch (err) {
    dispatch({ type: "SYNC_STATE_SET", syncState: "error" });
    showError(dispatch, err);
    return false;
  }
}

// ---------------------------------------------------------------------------
// 通用命令提交（含 REVISION_CONFLICT 自动重试）
// ---------------------------------------------------------------------------

/**
 * 模块级最新状态绑定：由 EditorBridge 在每次状态变化后调用 bindLatestState，
 * 使 runCommand 的冲突重试能读到最新 revision（不受闭包陈旧状态影响）。
 */
let latestState: EditorState | null = null;
export function bindLatestState(s: EditorState) {
  latestState = s;
}
/** 读取最新绑定的状态（拖拽提交等非渲染上下文使用）。 */
export function getLatestState(): EditorState | null {
  return latestState;
}

export async function runCommand(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  type: string,
  payload: Record<string, unknown>,
  opts: { autoShow?: boolean } = {},
): Promise<ActionResult> {
  const autoShow = opts.autoShow ?? true;
  if (!state.currentId) {
    const err = {
      status: 400,
      code: "NO_PROJECT",
      message: "请先创建或选择一个工程",
    };
    if (autoShow) showError(dispatch, err);
    return { ok: false, error: err as ApiFailure };
  }
  const projectId = state.currentId;
  const doPost = async (expected: string) =>
    sendCommand(projectId, type, payload, expected, newCommandId());

  dispatch({ type: "SYNC_STATE_SET", syncState: "saving" });
  try {
    const result = await doPost(state.revision);
    dispatch({ type: "REVISION_SET", revision: result.revision });
    await refreshProject(dispatch, projectId);
    return { ok: true, command: result };
  } catch (err) {
    if (isRetryableConflict(err)) {
      // 版本漂移：重新拉取最新 revision 后重试一次
      const fresh = latestState;
      const currentRev = fresh && fresh.currentId === projectId ? fresh.revision : state.revision;
      const refreshed = await refreshProject(dispatch, projectId);
      if (refreshed) {
        const currentRev2 = latestState ? latestState.revision : currentRev;
        try {
          const result = await doPost(currentRev2);
          dispatch({ type: "REVISION_SET", revision: result.revision });
          await refreshProject(dispatch, projectId);
          return { ok: true, command: result };
        } catch (err2) {
          const f2 = err2 instanceof ApiFailure ? err2 : new ApiFailure({ status: 0, code: "NETWORK", message: String(err2) });
          dispatch({ type: "SYNC_STATE_SET", syncState: "error" });
          if (autoShow) showError(dispatch, f2);
          return { ok: false, error: f2 };
        }
      }
    }
    const f = err instanceof ApiFailure ? err : new ApiFailure({ status: 0, code: "NETWORK", message: String(err) });
    dispatch({ type: "SYNC_STATE_SET", syncState: "error" });
    if (autoShow) showError(dispatch, f);
    return { ok: false, error: f };
  }
}

// ---- track / clip / history 命令 ----

export async function addTrack(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  kind: "video" | "audio",
): Promise<ActionResult> {
  const trackId = `track_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const res = await runCommand(dispatch, state, "track.add", { trackId, kind });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已添加 ${kind === "video" ? "视频" : "音频"}轨道` });
  }
  return res;
}

export async function insertClip(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  input: {
    trackId: string;
    sourcePath: string;
    sourceStart: Rational;
    timelineStart: Rational;
    timelineEnd: Rational;
  },
): Promise<ActionResult> {
  const clipId = `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const payload = {
    clipId,
    trackId: input.trackId,
    sourcePath: input.sourcePath,
    sourceStart: input.sourceStart,
    timelineStart: input.timelineStart,
    timelineEnd: input.timelineEnd,
  };
  const res = await runCommand(dispatch, state, "clip.insert", payload);
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: `已添加片段 ${clipId}` });
  }
  return res;
}

export async function undo(dispatch: Dispatch<EditorAction>, state: EditorState) {
  const res = await runCommand(dispatch, state, "history.undo", {}, { autoShow: false });
  if (res.ok) {
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "REDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已撤销" });
  } else if (res.error?.code === "UNDO_CONFLICT") {
    dispatch({ type: "UNDO_SET", blocked: true });
    dispatch({ type: "HISTORY_HINT", hint: "后端无待撤销的历史（UNDO_CONFLICT，非命令缺失）" });
    dispatch({ type: "STATUS_SET", severity: "warn", text: "撤销：无可撤销内容" });
  } else if (res.error) {
    showError(dispatch, res.error);
  }
  return res;
}

export async function redo(dispatch: Dispatch<EditorAction>, state: EditorState) {
  const res = await runCommand(dispatch, state, "history.redo", {}, { autoShow: false });
  if (res.ok) {
    dispatch({ type: "REDO_SET", blocked: false });
    dispatch({ type: "UNDO_SET", blocked: false });
    dispatch({ type: "STATUS_SET", severity: "ok", text: "已重做" });
  } else if (res.error?.code === "UNDO_CONFLICT") {
    dispatch({ type: "REDO_SET", blocked: true });
    dispatch({ type: "STATUS_SET", severity: "warn", text: "重做：无可重做内容" });
  } else if (res.error) {
    showError(dispatch, res.error);
  }
  return res;
}

/** 导出结果（区分同步完成 / 进行中 / 失败）。202 in_flight 时 ok=true 但 inFlight=true，
    需由调用方轮询 GET /exports 获取最终状态。 */
export interface ExportOutcome {
  ok: boolean;
  result?: ExportResult;
  error?: ApiFailure;
  jobId?: string;
  inFlight?: boolean;
}

export async function doExport(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
  outPath: string,
  quality: ExportQuality = "high",
): Promise<ExportOutcome> {
  if (!state.currentId) {
    const err = new ApiFailure({ status: 400, code: "NO_PROJECT", message: "请先选择工程" });
    showError(dispatch, err);
    return { ok: false, error: err };
  }
  try {
    const res = await apiExport(state.currentId, outPath, quality);
    // 202 in_flight：同一导出键已在运行，仅回 {jobId, status:"running"}（无 output_path）。
    const anyRes = res as ExportResult & { status?: string; jobId?: string };
    if (anyRes.status === "running" && anyRes.jobId) {
      dispatch({ type: "STATUS_SET", severity: "ok", text: "同一导出任务进行中，正在轮询状态…" });
      return { ok: true, inFlight: true, jobId: anyRes.jobId };
    }
    dispatch({ type: "STATUS_SET", severity: "ok", text: "导出完成（见结果面板）" });
    return { ok: true, result: res, jobId: anyRes.jobId };
  } catch (err) {
    const f = showError(dispatch, err);
    return { ok: false, error: f };
  }
}

/** 轮询事件：若远端 revision 大于本地 → 后台刷新（检测 Agent 外部修改）。 */
export async function pollEvents(
  dispatch: Dispatch<EditorAction>,
  state: EditorState,
): Promise<void> {
  if (!state.currentId) return;
  const since = state.revision === "" ? "0" : state.revision;
  try {
    const events = await listEvents(state.currentId, since);
    if (events.length) {
      const maxRev = events.reduce((m, e) => Math.max(m, Number(e.revision || 0)), 0);
      if (maxRev > Number(state.revision || 0)) {
        await refreshProject(dispatch, state.currentId);
        dispatch({
          type: "STATUS_SET",
          severity: "warn",
          text: "检测到其他端已修改该工程，已刷新为最新",
        });
      } else {
        dispatch({ type: "SYNC" });
      }
    } else {
      dispatch({ type: "SYNC" });
    }
  } catch {
    dispatch({ type: "SVC_UP", up: false });
  }
}

export type { ExportResult, Project };
