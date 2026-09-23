/** 时间线布局工具：标尺刻度、缩放、范围计算。 */

import type { Clip, Rational, Track } from "../../types/api";
import { rationalToSecs } from "../../lib/rational";

/** px 每秒的缩放档。 */
export const ZOOM_LEVELS = [5, 10, 20, 40, 80, 160, 320];

export function niceMajorStep(pxPerSec: number): number {
  // 选择使主刻度间距 >= 60px 的"好看"步长：1,2,5,10,20,30,60,120,300…
  const ideal = 60 / pxPerSec;
  const candidates = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 1200];
  for (const c of candidates) {
    if (c >= ideal) return c;
  }
  return 3600;
}

export interface Tick {
  secs: number;
  major: boolean;
}

/** 生成 [0, endSecs] 范围内的刻度（主 + 次）。 */
export function ticksForRange(endSecs: number, pxPerSec: number): Tick[] {
  const majorStep = niceMajorStep(pxPerSec);
  const minorCount = majorStep >= 10 ? 5 : 4;
  const minorStep = majorStep / minorCount;
  const ticks: Tick[] = [];
  const end = Math.ceil(endSecs);
  for (let s = 0; s <= end + 0.0001; s += minorStep) {
    const isMajor = Math.abs(s % majorStep) < 1e-6;
    ticks.push({ secs: Math.round(s * 1e6) / 1e6, major: isMajor });
  }
  // 确保包含末尾主刻度
  return ticks;
}

/** 轨道末段时间（秒），用于计算时间线总长。 */
export function trackEndSecs(track?: Track): number {
  if (!track || !track.clips) return 0;
  let end = 0;
  for (const c of track.clips) {
    const e = rationalToSecs(c.timelineEnd);
    if (!isNaN(e) && e > end) end = e;
  }
  return end;
}

/** 时间线总长：取所有轨道末尾 + 最小 10s，向上取整到 10s 的倍数。 */
export function timelineLengthSecs(tracks: Track[]): number {
  let maxEnd = 0;
  for (const t of tracks) {
    const e = trackEndSecs(t);
    if (e > maxEnd) maxEnd = e;
  }
  const floor = Math.max(10, maxEnd);
  return Math.ceil(floor / 10) * 10;
}

export function clipStartSecs(c: Clip): number {
  return rationalToSecs(c.timelineStart);
}
export function clipEndSecs(c: Clip): number {
  return rationalToSecs(c.timelineEnd);
}

/** 秒 → px。 */
export function toPx(secs: number, pxPerSec: number): number {
  return secs * pxPerSec;
}

/** px → 秒。 */
export function toSecs(px: number, pxPerSec: number): number {
  return px / pxPerSec;
}

/** 秒值对齐到最近一帧；缺少有效帧率时沿用 0.1s 的安全步长。 */
export function snapSecsToFrame(secs: number, frameRate?: Rational): number {
  const fpsNum = Number(frameRate?.num);
  const fpsDen = Number(frameRate?.den);
  if (!Number.isFinite(secs)) return 0;
  if (!Number.isFinite(fpsNum) || !Number.isFinite(fpsDen) || fpsNum <= 0 || fpsDen <= 0) {
    return Math.round(secs * 10) / 10;
  }
  const frame = Math.round((secs * fpsNum) / fpsDen);
  return (frame * fpsDen) / fpsNum;
}

/** 最近一帧的时长（秒）；旧/不完整工程回退到 0.1s。 */
export function frameDurationSecs(frameRate?: Rational): number {
  const fpsNum = Number(frameRate?.num);
  const fpsDen = Number(frameRate?.den);
  return Number.isFinite(fpsNum) && Number.isFinite(fpsDen) && fpsNum > 0 && fpsDen > 0
    ? fpsDen / fpsNum
    : 0.1;
}

/** 秒值 → 精确帧时长有理数；不把 1/30s 粗略舍入到 0.1s。 */
export function secsToFrameRatString(secs: number, frameRate?: Rational): { num: string; den: string } {
  const fpsNum = Number(frameRate?.num);
  const fpsDen = Number(frameRate?.den);
  if (!Number.isFinite(secs) || !Number.isFinite(fpsNum) || !Number.isFinite(fpsDen) || fpsNum <= 0 || fpsDen <= 0) {
    return secsToRatString(secs);
  }
  const frame = Math.round((secs * fpsNum) / fpsDen);
  const numerator = frame * fpsDen;
  const divisor = gcd(numerator, fpsNum);
  return { num: String(numerator / divisor), den: String(fpsNum / divisor) };
}

/** px 位移 → 时间位移，并吸附到当前工程帧边界。 */
export function pxToSecSnapped(dx: number, pxPerSec: number, frameRate?: Rational): number {
  const raw = dx / pxPerSec;
  return snapSecsToFrame(raw, frameRate);
}

/** 从首帧位移前的原始值计算新值（move：start+offset；trim：边界+offset）。 */
export function clampSecs(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

/** 秒 → 有理数字符串 num/den（保留 1 位小数的精确约分）。 */
export function secsToRatString(v: number): { num: string; den: string } {
  const snapped = Math.round(v * 10) / 10;
  const den = 10;
  let num = snapped * 10;
  // 整数化
  if (Math.abs(num - Math.round(num)) < 1e-9) num = Math.round(num);
  const g = gcd(Math.round(num), den);
  return { num: String(Math.round(num) / g), den: String(den / g) };
}

function gcd(a: number, b: number): number {
  a = Math.abs(a);
  b = Math.abs(b);
  while (b) {
    const t = b;
    b = a % b;
    a = t;
  }
  return a || 1;
}

export const zoomLabel = (pxPerSec: number) => `${pxPerSec}px/s`;

/** 播放头/总时长格式：m:ss.d。 */
export function fmtTime(secs: number): string {
  if (isNaN(secs) || secs < 0) return "0:00.0";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  const ms = Math.floor((secs - Math.floor(secs)) * 10);
  return `${m}:${String(s).padStart(2, "0")}.${ms}`;
}

/** 播放头毫秒显示：保留帧级微调的可见反馈，片段总时长仍使用紧凑格式。 */
export function fmtTimePrecise(secs: number): string {
  if (!Number.isFinite(secs) || secs < 0) return "0:00.000";
  const totalMs = Math.round(secs * 1000);
  const totalSecs = Math.floor(totalMs / 1000);
  const m = Math.floor(totalSecs / 60);
  const s = totalSecs % 60;
  const ms = totalMs % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}
