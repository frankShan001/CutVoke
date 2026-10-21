/** 播放器小图标（避免额外依赖）。 */

/** 画布比例图标：按比例宽高绘制矩形。 */
export function RatioIcon({ r }: { r: [number, number] }) {
  const w = r[0] > r[1] ? 14 : 10;
  const h = r[0] > r[1] ? 10 : 14;
  return (
    <svg width={w} height={h} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <rect x="1.5" y="3" width="13" height="10" rx="1.5" />
    </svg>
  );
}
