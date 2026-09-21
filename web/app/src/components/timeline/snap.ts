/** 磁吸吸附工具：把拖动目标值吸到最近的边缘/播放头。
    阈值：0.3s（或 8px）。返回 {value, snappedTo: number|null}。 */

export const SNAP_THRESHOLD_SECS = 0.3;

/** 在快照点数组中找到最近且距离 < 阈值的点；无则返回原始值。 */
export function snapTo(value: number, snaps: number[], threshold = SNAP_THRESHOLD_SECS): {
  value: number;
  snapped: boolean;
  to: number | null;
} {
  if (snaps.length === 0) return { value, snapped: false, to: null };
  let best = -1;
  let bestDist = Infinity;
  for (let i = 0; i < snaps.length; i++) {
    const d = Math.abs(snaps[i] - value);
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  }
  if (best >= 0 && bestDist < threshold) {
    return { value: snaps[best], snapped: true, to: snaps[best] };
  }
  return { value, snapped: false, to: null };
}

/**
 * 收集拖动片段时其它片段的左/右缘 + 播放头 → 作为吸附点。
 * 可跨轨收集（跨轨移动时吸附也生效）。排除自身 clipId。
 */
export function collectSnapPoints(
  clips: { id: string; start: number; end: number }[],
  excludeId: string,
  playheadSecs: number,
): number[] {
  const pts = new Set<number>();
  for (const c of clips) {
    if (c.id === excludeId) continue;
    pts.add(c.start);
    pts.add(c.end);
  }
  if (playheadSecs >= 0) pts.add(playheadSecs);
  return [...pts];
}

/** 帧边界换算（取整到最近帧）——快捷键逐帧用。 */
export function frameStep(fps: number): number {
  return fps > 0 ? 1 / fps : 1 / 30;
}