/** 时间线标尺：秒刻度 + 主/次刻度分级。 */

import { useRef } from "react";
import { useEditor } from "../../store/editor";
import { fmtTime, ticksForRange, toPx, type Tick } from "./util";

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
  const { state, dispatch } = useEditor();
  const pointerId = useRef<number | null>(null);
  const ticks = ticksForRange(lengthSecs, pxPerSec);

  const seekTo = (time: number) => {
    dispatch({ type: "PLAYHEAD_SET", t: Math.max(0, Math.min(maxSeekSecs, time)) });
  };
  const seekAt = (clientX: number, el: HTMLDivElement) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return;
    seekTo((clientX - rect.left) / pxPerSec);
  };
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    pointerId.current = e.pointerId;
    // preventDefault 阻止浏览器自动聚焦；显式聚焦后，方向键才可逐帧调整。
    e.currentTarget.focus();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    seekAt(e.clientX, e.currentTarget);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (pointerId.current === e.pointerId) seekAt(e.clientX, e.currentTarget);
  };
  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (pointerId.current === e.pointerId) pointerId.current = null;
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
      aria-label="时间线播放头"
      aria-valuemin={0}
      aria-valuemax={maxSeekSecs}
      aria-valuenow={currentTime}
      aria-valuetext={`${fmtTime(currentTime)} / ${fmtTime(maxSeekSecs)}`}
      title="单击或拖动定位；方向键逐帧；End 到内容末尾（标尺尾部空白不可播放）"
      aria-keyshortcuts="ArrowLeft ArrowDown ArrowRight ArrowUp PageUp PageDown Home End"
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
    >
      {ticks.map((t) => (
        <TickView key={`${t.secs}-${t.major}`} tick={t} pxPerSec={pxPerSec} />
      ))}
    </div>
  );
}

function TickView({ tick, pxPerSec }: { tick: Tick; pxPerSec: number }) {
  const left = toPx(tick.secs, pxPerSec);
  return (
    <div className="timeline-ruler__tick" style={{ left }}>
      <span className={`timeline-ruler__line ${tick.major ? "timeline-ruler__line--major" : "timeline-ruler__line--minor"}`} />
      {tick.major ? <span className="timeline-ruler__label">{tick.secs}</span> : null}
    </div>
  );
}
