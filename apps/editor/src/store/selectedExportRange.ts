import { rationalToSecs } from "../lib/rational";
import type { EditorState } from "./editor";
import { selectedClipIds } from "./timelineSelection";

export interface SelectedExportRange {
  projectId: string;
  revision: string;
  clipId: string;
  start: number;
  end: number;
}

/** Use only committed bounds in the current sequence, never source trim offsets. */
export function selectedExportRange(state: EditorState): SelectedExportRange | null {
  const { currentId, project, revision } = state;
  if (!currentId || project?.projectId !== currentId || !revision || project.revision !== revision ||
      state.opening || state.syncState === "saving" || state.selection?.effectId || state.selectedCaptionId) return null;
  const ids = selectedClipIds(state);
  if (ids.length !== 1) return null;
  const clips = project.sequence.tracks.flatMap(track => track.clips).filter(clip => clip.id === ids[0]);
  if (clips.length !== 1) return null;
  const [clip] = clips;
  const start = rationalToSecs(clip.timelineStart);
  const end = rationalToSecs(clip.timelineEnd);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) return null;
  return { projectId: currentId, revision, clipId: clip.id, start, end };
}
