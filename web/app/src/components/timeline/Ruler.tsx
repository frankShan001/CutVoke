/** 时间线标尺：秒刻度 + 主/次刻度分级。 */

import { ticksForRange, toPx, type Tick } from "./util";

export function Ruler({ lengthSecs, pxPerSec }: { lengthSecs: number; pxPerSec: number }) {
  const ticks = ticksForRange(lengthSecs, pxPerSec);
  return (
    <div className="timeline-ruler">
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