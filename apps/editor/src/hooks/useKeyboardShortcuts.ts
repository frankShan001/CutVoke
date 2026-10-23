/** 播放器/时间线辅助工具。全局快捷键由 AppShell 按编辑器视图、焦点与锁定状态管理。 */

import { frameStep } from "../components/timeline/snap";
import { useEffect, useRef, type Dispatch } from "react";
import type { EditorState, EditorAction } from "../store/editor";
import { getLatestState } from "../store/actions";
import { selectedClipIds } from "../store/timelineSelection";
import { batchClips, copySelectedClips, duplicateSelectedClips, pasteClipsAtPlayhead } from "../store/timelineClipboard";

export { frameStep };

export function useTimelineClipboardShortcuts(state: EditorState, dispatch: Dispatch<EditorAction>) {
  const latest = useRef(state);
  latest.current = state;
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      const current = getLatestState() || latest.current;
      const target = event.target as HTMLElement | null;
      if (event.defaultPrevented || event.repeat || event.isComposing || event.altKey
        || current.view !== "editor" || current.opening || current.editLock || current.error
        || current.exportOpen || current.templateOpen || current.agentPanelOpen
        || target?.closest("input,textarea,select,[contenteditable='true'],[role='dialog'],[role='alertdialog']")) return;
      const key = event.key.toLowerCase(), command = event.ctrlKey || event.metaKey;
      if (command && !event.shiftKey && ["c","v","d","a","x"].includes(key)) {
        event.preventDefault();
        if (key === "c") void copySelectedClips(dispatch, current);
        if (key === "v") void pasteClipsAtPlayhead(dispatch, current);
        if (key === "d") void duplicateSelectedClips(dispatch, current);
        if (key === "x") void copySelectedClips(dispatch, current).then(ok => { if (ok) void batchClips(dispatch, getLatestState() || current, "remove"); });
        if (key === "a") dispatch({ type: "CLIP_SELECTION_SET", clipIds: current.project?.sequence.tracks
          .filter(track => track.visible !== false).flatMap(track => track.clips.filter(clip => !clip.hidden).map(clip => clip.id)) || [] });
      } else if (!command && !event.shiftKey && ["delete","backspace"].includes(key)
        && !current.selection?.effectId && selectedClipIds(current).length) {
        event.preventDefault();
        void batchClips(dispatch, current, "remove");
      } else if (key === "escape" && !command) dispatch({ type: "CLIP_SELECTION_SET", clipIds: [] });
    };
    window.addEventListener("keydown", handle, true);
    return () => window.removeEventListener("keydown", handle, true);
  }, [dispatch]);
}

/** 触发播放/暂停（供外部/按钮调用；Player 在 window 上监听该事件）。 */
export function notifyTogglePlay() {
  window.dispatchEvent(new CustomEvent("cutvoke:toggle-play"));
}

/** 从工程 fps 得到单帧时长（秒）。 */
export function fpsToStep(fps: { num: string; den: string } | undefined): number {
  if (!fps) return 1 / 30;
  const n = Number(fps.num);
  const de = Number(fps.den);
  const rate = n / de;
  return Number.isFinite(rate) && rate > 0 ? 1 / rate : 1 / 30;
}
