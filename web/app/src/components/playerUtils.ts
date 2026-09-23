/** 播放器工具（纯 TS，无 JSX）：片段查找 / 时间换算 / 画布比例表。 */

import type { EditorState } from "../store/editor";
import type { Track } from "../types/api";

export function findClipById(st: EditorState, id: string) {
  for (const t of st.project?.sequence?.tracks || []) {
    const c = t.clips.find((x) => x.id === id);
    if (c) return c;
  }
  return undefined;
}

export function rationalSeconds(rt: { num: string; den: string }): number {
  return Number(rt.num) / Number(rt.den || "1");
}

/** End of visible, renderable media; unlike the timeline ruler, this is not rounded up. */
export function playbackEndSecs(tracks: Track[]): number {
  let end = 0;
  for (const track of tracks) {
    if (track.visible === false || (track.kind !== "video" && track.kind !== "audio")) continue;
    if (track.kind === "audio" && track.muted) continue;
    for (const clip of track.clips || []) {
      if (clip.hidden) continue;
      const clipEnd = rationalSeconds(clip.timelineEnd);
      if (Number.isFinite(clipEnd)) end = Math.max(end, clipEnd);
    }
  }
  return end;
}

/** Whether the current position is covered by a visible video clip. */
export function hasVideoCoverageAt(tracks: Track[], time: number): boolean {
  if (!Number.isFinite(time)) return false;
  return tracks.some((track) =>
    track.kind === "video" && track.visible !== false && (track.clips || []).some((clip) => {
      if (clip.hidden) return false;
      const start = rationalSeconds(clip.timelineStart);
      const end = rationalSeconds(clip.timelineEnd);
      return Number.isFinite(start) && Number.isFinite(end) && start <= time && time < end;
    }),
  );
}

export function gcd(a: number, b: number): number {
  while (b) {
    const t = b;
    b = a % b;
    a = t;
  }
  return a || 1;
}

export function secsToRational(v: number): { num: string; den: string } {
  const snapped = Math.round(v * 10) / 10;
  const num = Math.round(snapped * 10);
  const den = 10;
  const g = gcd(Math.abs(num), den);
  return { num: String(num / g), den: String(den / g) };
}

export function videoDuration(v: HTMLVideoElement | null): number {
  return v && !Number.isNaN(v.duration) ? v.duration : 0;
}

export function videoDurationSecs(v: HTMLVideoElement | null): number {
  const d = videoDuration(v);
  // 在空工程或 metadata 尚未到达时，HTMLMediaElement.duration 是 NaN。
  // 播放器时间码必须始终可读，不能把 NaN 伪装成 Infinity 再传给 fmtTime。
  return Number.isFinite(d) && d > 0 ? d : 0;
}

export const PLAYER_RATIOS: { label: string; wh: [number, number] }[] = [
  { label: "16:9", wh: [16, 9] },
  { label: "9:16", wh: [9, 16] },
  { label: "1:1", wh: [1, 1] },
  { label: "4:3", wh: [4, 3] },
];
