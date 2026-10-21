import { tr, useLocale } from "../../i18n";
/** 时间线标尺：秒刻度 + 主/次刻度分级。 */

import { useTimelineScrub } from "./useTimelineScrub";
import { useEditor } from "../../store/editor";
import { fmtTime, ticksForRange, toPx, type Tick } from "./util";
import { rationalToSecs } from "../../lib/rational";

export function Ruler({
  lengthSecs,
  maxSeekSecs,
  pxPerSec,
}: {
  /** 标尺显示长度，可包含便于继续编辑的尾部留白。 */
  lengthSecs: number;
  /** 播放头可到达的实际内容末尾。 */
  maxSeekSecs: number;
  pxPerSec: number;
}) {
  useLocale();
  const { state, dispatch } = useEditor();
  const scrub = useTimelineScrub(pxPerSec, maxSeekSecs);
  const ticks = ticksForRange(lengthSecs, pxPerSec);

  const seekTo = (time: number) => {
    dispatch({ type: "PLAYHEAD_SET", t: Math.max(0, Math.min(maxSeekSecs, time)) });
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const fps = state.project?.sequence.fps;
    const fpsNum = Number(fps?.num || 30);
    const fpsDen = Number(fps?.den || 1);
    const frame = fpsNum > 0 && fpsDen > 0 ? fpsDen / fpsNum : 1 / 30;
    const current = state.playhead;
    const steps: Record<string, number> = {
      ArrowLeft: -frame,
      ArrowDown: -frame,
      ArrowRight: frame,
      ArrowUp: frame,
      PageDown: -frame * 10,
      PageUp: frame * 10,
      Home: -Infinity,
      End: Infinity,
    };
    if (!(e.key in steps)) return;
    e.preventDefault();
    const delta = steps[e.key];
    seekTo(Number.isFinite(delta) ? current + delta : delta < 0 ? 0 : maxSeekSecs);
  };

  const currentTime = Math.max(0, Math.min(maxSeekSecs, state.playhead));

  return (
    <div
      className="timeline-ruler"
      role="slider"
      aria-label={tr("时间线播放头")}
      aria-valuemin={0}
      aria-valuemax={maxSeekSecs}
      aria-valuenow={currentTime}
      aria-valuetext={`${fmtTime(currentTime)} / ${fmtTime(maxSeekSecs)}`}
      title={tr("单击或拖动定位；方向键逐帧；End 到内容末尾（标尺尾部空白不可播放）")}
      aria-keyshortcuts="ArrowLeft ArrowDown ArrowRight ArrowUp PageUp PageDown Home End"
      tabIndex={0}
      {...scrub}
      onKeyDown={onKeyDown}
    >
      {ticks.map((t) => (
        <TickView key={`${t.secs}-${t.major}`} tick={t} pxPerSec={pxPerSec} />
      ))}
      {(state.project?.sequence.markers || []).map((marker) => (
        <span
          key={marker.id}
          className="timeline-ruler__marker"
          style={{ left: toPx(rationalToSecs(marker.time), pxPerSec) }}
          title={tr("{0} · {1}", marker.name || "标记", fmtTime(rationalToSecs(marker.time)))}
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

function TickView({ tick, pxPerSec }: { tick: Tick; pxPerSec: number }) {
  useLocale();
  const left = toPx(tick.secs, pxPerSec);
  return (
    <div className="timeline-ruler__tick" style={{ left }}>
      <span className={`timeline-ruler__line ${tick.major ? "timeline-ruler__line--major" : "timeline-ruler__line--minor"}`} />
      {tick.major ? <span className="timeline-ruler__label">{tick.secs}</span> : null}
    </div>
  );
}
