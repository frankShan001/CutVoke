import type { Dispatch } from "react";
import type { EditorAction, EditorState } from "./editor";
import { rationalToSecs } from "../lib/rational";

export function selectedClipIds(state: EditorState): string[] {
  return state.selectedClipIds?.length ? state.selectedClipIds
    : state.selection?.clipId && !state.selection.effectId ? [state.selection.clipId] : [];
}

export function selectTimelineClip(dispatch: Dispatch<EditorAction>, state: EditorState, clipId: string,
  modifiers: { ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean } = {}) {
  const ordered = state.project?.sequence.tracks.flatMap(track => [...track.clips]
    .sort((a,b) => rationalToSecs(a.timelineStart)-rationalToSecs(b.timelineStart)).map(clip => clip.id)) || [];
  const anchor = state.selectionAnchorId || state.selection?.clipId || clipId;
  let ids = selectedClipIds(state);
  if (modifiers.shiftKey && ordered.includes(anchor)) {
    const a = ordered.indexOf(anchor), b = ordered.indexOf(clipId);
    const range = ordered.slice(Math.min(a,b),Math.max(a,b)+1);
    ids = modifiers.ctrlKey || modifiers.metaKey ? [...new Set([...ids,...range])] : range;
  } else if (modifiers.ctrlKey || modifiers.metaKey) {
    ids = ids.includes(clipId) ? ids.filter(id => id !== clipId) : [...ids,clipId];
  } else ids = [clipId];
  dispatch({ type: "CLIP_SELECTION_SET", clipIds: ids, primaryId: clipId,
    anchorId: modifiers.shiftKey || modifiers.ctrlKey || modifiers.metaKey ? anchor : clipId });
}

export function withAttachedClips(state: EditorState, ids: string[]): string[] {
  const result = new Set(ids);
  const clips = state.project?.sequence.tracks.flatMap(track => track.clips) || [];
  let count = -1;
  while (count !== result.size) {
    count = result.size;
    for (const clip of clips) if (clip.attachedToClipId && result.has(clip.attachedToClipId)) result.add(clip.id);
  }
  return [...result];
}
