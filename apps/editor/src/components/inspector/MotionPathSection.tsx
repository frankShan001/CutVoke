import { useEffect, useRef, useState } from "react";
import { tr } from "../../i18n";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { rationalToSecs } from "../../lib/rational";
import type { Clip, SpatialPoint } from "../../types/api";

export function MotionPathSection({ clip }: { clip: Clip }) {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [points, setPoints] = useState<SpatialPoint[]>(clip.motionPath?.points || []), [selected, setSelected] = useState(0), [busy, setBusy] = useState(false);
  const drag = useRef<{ index: number; handle?: "in" | "out" } | null>(null);
  const owner = state.currentId;
  const width = state.project?.sequence.width || 1920, height = state.project?.sequence.height || 1080;
  const duration = rationalToSecs(clip.timelineEnd) - rationalToSecs(clip.timelineStart);
  useEffect(() => { setPoints(clip.motionPath?.points || []); setSelected(0); }, [clip.id, clip.motionPath]);
  async function commit(value: SpatialPoint[] | null) {
    const current = getLatestState() || state;
    if (current.currentId !== owner || current.editLock) return;
    setBusy(true);
    try { await runCommand(dispatch, current, "clip.motionPath", { clipId: clip.id, points: value }); }
    finally { setBusy(false); }
  }
  const path = points.map((point, i) => i === 0 ? `M${point.x},${point.y}` : `C${points[i-1].x + (points[i-1].outX || 0)},${points[i-1].y + (points[i-1].outY || 0)} ${point.x + (point.inX || 0)},${point.y + (point.inY || 0)} ${point.x},${point.y}`).join(" ");
  const point = points[selected];
  return <details className="inspector__section"><summary>{tr("空间运动路径")}</summary>
    <fieldset disabled={busy || !!state.editLock} style={{ border: 0, padding: 0, minWidth: 0 }}>
      {!points.length ? <button type="button" className="cv-btn cv-btn--sm" disabled={!!clip.speedCurve || !!clip.freezeAt} onClick={() => {
        const value = [{ at: 0, x: 0, y: 0, outX: width*.12, outY: -height*.2 }, { at: duration, x: width*.2, y: 0, inX: -width*.12, inY: -height*.2 }];
        setPoints(value); void commit(value);
      }}>{tr("创建路径")}</button> : <>
        <svg viewBox={`${-width/2} ${-height/2} ${width} ${height}`} role="img" aria-label={tr("空间路径编辑器")} style={{ width: "100%", height: 180, background: "var(--bg)", touchAction: "none" }}
          onPointerMove={event => { if (!drag.current || busy || state.editLock) return;
            const bounds = event.currentTarget.getBoundingClientRect(), dx = (event.clientX-bounds.left)/bounds.width*width-width/2, dy = (event.clientY-bounds.top)/bounds.height*height-height/2;
            const active = drag.current;
            setPoints(current => current.map((point,i) => i !== active.index ? point : active.handle ? { ...point, [active.handle+"X"]: dx-point.x, [active.handle+"Y"]: dy-point.y } : { ...point, x: dx, y: dy }));
          }} onPointerUp={event => { if (!drag.current) return; drag.current=null; event.currentTarget.releasePointerCapture(event.pointerId); void commit(points); }}
          onPointerCancel={() => { drag.current=null; setPoints(clip.motionPath?.points || []); }}>
          <path d={path} fill="none" stroke="var(--accent)" strokeWidth={width/160} />
          {points.map((point,index) => <g key={index}>
            {selected === index ? (["in","out"] as const).map(handle => <g key={handle}>
              <line x1={point.x} y1={point.y} x2={point.x+(point[`${handle}X`] || 0)} y2={point.y+(point[`${handle}Y`] || 0)} stroke="var(--text-dim)" strokeWidth={width/320} />
              <circle cx={point.x+(point[`${handle}X`] || 0)} cy={point.y+(point[`${handle}Y`] || 0)} r={width/45} fill="var(--text-dim)" role="button" tabIndex={0} aria-label={`${handle} ${index+1}`}
                onPointerDown={event => { if (busy || state.editLock) return; drag.current={index,handle}; event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId); }} />
            </g>) : null}
            <circle cx={point.x} cy={point.y} r={width/45} fill={selected === index ? "var(--accent)" : "var(--text)"} role="button" tabIndex={0} aria-label={tr("路径节点 {0}",index+1)}
              onClick={() => setSelected(index)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(index); } }}
              onPointerDown={event => { if (busy || state.editLock) return; setSelected(index); drag.current={index}; event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId); }} />
          </g>)}
        </svg>
        {point ? <>
          <select className="cv-input" aria-label={tr("路径节点")} value={selected} onChange={event => setSelected(Number(event.target.value))}>{points.map((_,i) => <option key={i} value={i}>{i+1}</option>)}</select>
          {(["at","x","y","inX","inY","outX","outY"] as const).map(key => <label className="inspector__subrow" key={key}>{key}<input type="number" className="cv-input" aria-label={`Path ${key}`} value={point[key] ?? 0} step={key === "at" ? .1 : 1}
            disabled={key === "at" && point.roving} onChange={event => setPoints(current => current.map((p,i) => i === selected ? { ...p, [key]: Number(event.target.value) } : p))} onBlur={() => void commit(points)} /></label>)}
          <label className="inspector__subrow"><input type="checkbox" disabled={selected === 0 || selected === points.length-1} checked={!!point.roving}
            onChange={event => { const value = points.map((p,i) => i === selected ? { ...p, roving: event.target.checked } : p); setPoints(value); void commit(value); }} />{tr("按路径距离分配时间")}</label>
          {selected < points.length-1 ? <><span className="cv-hint">{tr("时间缓动")}</span><div style={{ display: "flex", gap: 4 }}>
            {[0,1,2,3].map(index => <label className="cv-hint" key={index}>{["x1","y1","x2","y2"][index]}<input className="cv-input" type="number" min={0} max={1} step={.05}
              value={(point.timeEase || [0,0,1,1])[index]} aria-label={`Path Bezier ${index}`} onChange={event => setPoints(current => current.map((p,i) => i === selected ? { ...p, timeEase: (p.timeEase || [0,0,1,1]).map((value,j) => j === index ? Number(event.target.value) : value) as [number,number,number,number] } : p))} onBlur={() => void commit(points)} /></label>)}
          </div></> : null}
          <button type="button" className="cv-btn cv-btn--sm" disabled={points.length >= 32} onClick={() => {
            const next = points[selected+1]; if (!next) return;
            const value = [...points.slice(0,selected+1), { x: (point.x+next.x)/2, y: (point.y+next.y)/2, roving: true }, ...points.slice(selected+1)]; setPoints(value); void commit(value);
          }}>{tr("插入节点")}</button>
        </> : null}
        <button type="button" className="cv-btn cv-btn--sm" onClick={() => { setPoints([]); void commit(null); }}>{tr("移除路径")}</button>
      </>}
    </fieldset>
  </details>;
}
