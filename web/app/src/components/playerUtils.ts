/** 播放器工具（纯 TS，无 JSX）：片段查找 / 时间换算 / 画布比例表。 */

import type { EditorState } from "../store/editor";

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
  return d > 0 ? d : Number.POSITIVE_INFINITY;
}

export const PLAYER_RATIOS: { label: string; wh: [number, number] }[] = [
  { label: "16:9", wh: [16, 9] },
  { label: "9:16", wh: [9, 16] },
  { label: "1:1", wh: [1, 1] },
  { label: "4:3", wh: [4, 3] },
];
