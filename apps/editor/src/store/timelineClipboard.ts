import type { Dispatch } from "react";
import type { Clip } from "../types/api";
import type { EditorAction, EditorState } from "./editor";
import { getLatestState, runCommand, type ActionResult } from "./actions";
import { selectedClipIds, withAttachedClips } from "./timelineSelection";
import { flushPendingEdits } from "../lib/pendingEdits";
import { secsToFrameRatString } from "../components/timeline/util";
import { tr } from "../i18n";

interface ClipboardData {
  version: 1;
  projectId: string;
  items: { trackId: string; trackIndex: number; trackKind: string; trackRole?: string; clip: Clip }[];
}
const KEY = "cutvoke.timeline.clipboard.v1";
const PREFIX = "CUTVOKE_TIMELINE_V1\n";
let clipboard: ClipboardData | null = null;
let busy = false;

function selectCreated(dispatch: Dispatch<EditorAction>, result: ActionResult) {
  const ids = result.command?.changedEntities.filter(item => item.type === "clip" && item.sourceClipId).map(item => item.id) || [];
  if (ids.length) dispatch({ type: "CLIP_SELECTION_SET", clipIds: ids, primaryId: ids[0] });
}

export async function copySelectedClips(dispatch: Dispatch<EditorAction>, state: EditorState): Promise<boolean> {
  if (!await flushPendingEdits()) return false;
  const current = getLatestState() || state;
  const ids = new Set(withAttachedClips(current, selectedClipIds(current)));
  if (!ids.size || !current.currentId) return false;
  const items = current.project?.sequence.tracks.flatMap((track, trackIndex) => track.clips
    .filter(clip => ids.has(clip.id)).map(clip => ({ trackId: track.id, trackIndex, trackKind: track.kind, trackRole: track.role, clip }))) || [];
  clipboard = JSON.parse(JSON.stringify({ version: 1, projectId: current.currentId, items })) as ClipboardData;
  const serialized = JSON.stringify(clipboard);
  try { sessionStorage.setItem(KEY, serialized); } catch { /* In-memory copy remains available. */ }
  void navigator.clipboard?.writeText(PREFIX + serialized).catch(() => undefined);
  dispatch({ type: "STATUS_SET", severity: "ok", text: tr("已复制 {0} 个片段，可在播放头粘贴", items.length) });
  return true;
}

async function readClipboard(): Promise<ClipboardData | null> {
  // Read the application clipboard without opening browser permission prompts.
  // Native copy above also allows another CutVoke window to paste on explicit user action.
  if (!clipboard) {
    try { clipboard = JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch { /* Empty clipboard. */ }
  }
  if (!clipboard) {
    try {
      const text = await navigator.clipboard?.readText();
      if (text?.startsWith(PREFIX)) clipboard = JSON.parse(text.slice(PREFIX.length));
    } catch { /* Native clipboard permission is optional. */ }
  }
  return clipboard?.version === 1 && Array.isArray(clipboard.items) ? clipboard : null;
}

export async function pasteClipsAtPlayhead(dispatch: Dispatch<EditorAction>, state: EditorState) {
  if (busy) return;
  busy = true;
  try {
    if (!await flushPendingEdits()) return;
    const data = await readClipboard();
    if (!data?.items.length) {
      dispatch({ type: "STATUS_SET", severity: "warn", text: tr("请先复制时间线片段") });
      return;
    }
    const current = getLatestState() || state;
    const result = await runCommand(dispatch, current, "clip.paste", {
      items: data.items, timelineStart: secsToFrameRatString(current.playhead, current.project?.sequence.fps),
      ...(!current.selection?.clipId && current.selection?.trackId ? { targetTrackId: current.selection.trackId } : {}),
    });
    if (result.ok) {
      selectCreated(dispatch, result);
      dispatch({ type: "STATUS_SET", severity: "ok", text: tr("已在播放头插入粘贴，后续内容同步顺延") });
    }
  } finally { busy = false; }
}

export async function duplicateSelectedClips(dispatch: Dispatch<EditorAction>, state: EditorState) {
  if (busy) return;
  busy = true;
  try {
    if (!await flushPendingEdits()) return;
    const current = getLatestState() || state;
    const ids = selectedClipIds(current);
    if (!ids.length) return;
    const result = await runCommand(dispatch, current, "clip.duplicateGroup", { clipIds: ids, includeAttached: true });
    if (result.ok) {
      selectCreated(dispatch, result);
      dispatch({ type: "STATUS_SET", severity: "ok", text: tr("已插入副本及绑定图层，后续内容同步顺延") });
    }
  } finally { busy = false; }
}

export async function batchClips(dispatch: Dispatch<EditorAction>, state: EditorState, action: "move" | "remove" | "audio",
  values: Record<string, unknown> = {}): Promise<ActionResult> {
  if (!await flushPendingEdits()) return { ok: false };
  const current = getLatestState() || state;
  const result = await runCommand(dispatch, current, "clip.batch", { clipIds: selectedClipIds(current), action, ...values });
  if (result.ok) {
    if (action === "remove") dispatch({ type: "CLIP_SELECTION_SET", clipIds: [] });
    dispatch({ type: "STATUS_SET", severity: "ok", text: tr("已批量编辑，可一次撤销") });
  }
  return result;
}
