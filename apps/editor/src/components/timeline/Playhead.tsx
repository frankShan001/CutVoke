/** 播放头：时间线竖线 + 顶部可拖游标。拖动更新全局 playhead（秒）。
    定位：相对 .timeline__content；x 偏移 = 左侧轨道标签宽 + 播放头像素。 */

import { useTimelineScrub } from "./useTimelineScrub";
import { useEditor } from "../../store/editor";
import { toPx } from "./util";

export function Playhead({ pxPerSec, maxSecs }: { pxPerSec: number; maxSecs: number }) {
  const { state } = useEditor();
  const scrub = useTimelineScrub(pxPerSec, maxSecs);

  const playheadSecs = Math.max(0, Math.min(maxSecs, state.playhead));
  const left = `calc(var(--timeline-content-inset-x, 12px) + var(--track-label-w) + var(--space-2) + ${toPx(playheadSecs, pxPerSec)}px)`;

  return (
    <div className="timeline-playhead" style={{ left }} aria-hidden="true">
      <div
        className="timeline-playhead__drag"
        {...scrub}
      />
      <span className="timeline-playhead__head" />
    </div>
  );
}
