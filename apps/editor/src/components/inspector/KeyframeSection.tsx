import { tr, useLocale } from "../../i18n";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { addKeyframe, removeKeyframe } from "../../store/clipEdit";
import { rationalToSecs, secsToRational } from "../../lib/rational";
import { evaluateKeyframes } from "../../lib/keyframes";
import type { Clip, Keyframe, KeyframeInterpolation } from "../../types/api";

type Bezier = [number,number,number,number];
const effectSpecs: Record<string,Record<string,[number,number,number]>> = {
  "cutvoke.color": { brightness: [-1,1,0], contrast: [0,2,1], saturation: [0,3,1] },
  "cutvoke.fx.blur": { sigma: [1,20,5] },
};

/** Clip-local parameter curves; each committed gesture is one undoable command. */
export function KeyframeSection({ clip }: { clip: Clip }) {
  useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [param, setParam] = useState("opacity");
  const [kfTime, setKfTime] = useState("0");
  const [kfValue, setKfValue] = useState("1");
  const [kfInterp, setKfInterp] = useState<KeyframeInterpolation>("linear");
  const [bezier,setBezier] = useState<Bezier>([.25,.1,.25,1]);
  const [selected,setSelected] = useState<string[]>([]);
  const [offset,setOffset] = useState("0");
  const [timeScale,setTimeScale] = useState("1");
  const [draft,setDraft] = useState<Keyframe[] | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: string; moved: boolean } | null>(null);
  const duration = Math.max(.001,rationalToSecs(clip.timelineEnd)-rationalToSecs(clip.timelineStart));
  const audio = state.project?.sequence.tracks.some(track=>track.kind==="audio" && track.clips.some(item=>item.id===clip.id));
  const options: {name: string; min: number; max: number; value: number}[] = [
    {name:"opacity",min:0,max:1,value:1}, {name:"x",min:-state.project!.sequence.width,max:state.project!.sequence.width,value:0},
    {name:"y",min:-state.project!.sequence.height,max:state.project!.sequence.height,value:0},
    {name:"scale",min:.05,max:5,value:1}, {name:"rotation",min:-180,max:180,value:0}, {name:"volume",min:0,max:8,value:1},
  ];
  if (audio) options.splice(0,5);
  (clip.effects || []).forEach((effect,index) => {
    if (effect.effectId === "cutvoke.color" && clip.effects!.slice(index+1).some(item=>item.effectId === "cutvoke.color")) return;
    Object.entries(effectSpecs[effect.effectId] || {}).forEach(([name,[min,max,value]]) => options.push({
      name:`effect:${effect.instanceId || `legacy_${index}`}:${name}`,min,max,value:Number(effect.params[name] ?? value),
    }));
  });
  const spec = options.find(option=>option.name===param) || options[0];
  const lane = draft || [...(clip.keyframes?.[param] || [])].sort((a,b)=>rationalToSecs(a.time)-rationalToSecs(b.time));
  const readOnly = Boolean(state.editLock);
  useEffect(()=>{setSelected([]);setDraft(null);setParam(audio?"volume":"opacity");},[clip.id,audio]);
  useEffect(()=>{setDraft(null);},[clip.keyframes]);
  const commit = async (payload: Record<string,unknown>) => {
    const result = await runCommand(dispatch,getLatestState() || state,"clip.keyframe",{clipId:clip.id,...payload});
    if (result.ok) dispatch({type:"UNDO_SET",blocked:false});
    return result;
  };
  const select = (frame: Keyframe, multiple=false) => {
    setSelected(current=>multiple ? current.includes(frame.id) ? current.filter(id=>id!==frame.id) : [...current,frame.id] : [frame.id]);
    setKfTime(String(rationalToSecs(frame.time)));setKfValue(String(frame.value));
    setKfInterp(frame.interpolation);setBezier(frame.bezier || [.25,.1,.25,1]);
  };
  const W=320,H=144,pad=16;
  const x=(time:number)=>pad+time/duration*(W-2*pad);
  const y=(value:number)=>H-pad-(value-spec.min)/(spec.max-spec.min)*(H-2*pad);
  const curve = Array.from({length:121},(_,i)=>{
    const time=i/120*duration;
    return `${i===0?"M":"L"}${x(time).toFixed(3)},${y(evaluateKeyframes(lane,time,spec.value)).toFixed(3)}`;
  }).join(" ");
  const moveNode = (event: PointerEvent<SVGSVGElement>) => {
    if (!drag.current || !svgRef.current || readOnly) return;
    const rect=svgRef.current.getBoundingClientRect();
    const time=Math.max(0,Math.min(duration,((event.clientX-rect.left)/rect.width*W-pad)/(W-2*pad)*duration));
    const value=Math.max(spec.min,Math.min(spec.max,spec.max-((event.clientY-rect.top)/rect.height*H-pad)/(H-2*pad)*(spec.max-spec.min)));
    drag.current.moved=true;
    setDraft(lane.map(frame=>frame.id===drag.current!.id ? {...frame,time:secsToRational(time),value} : frame));
    setKfTime(time.toFixed(3));setKfValue(value.toFixed(3));
  };
  return <>
    <div className="inspector__sep" />
    <div id="clip-opacity-keyframes" tabIndex={-1} className="inspector__subrow"><span className="inspector__key">{param==="opacity"?tr("不透明度关键帧"):tr("参数关键帧")}</span></div>
    <label className="cv-hint">{tr("关键帧参数")}<select className="cv-input" value={param} aria-label={tr("关键帧参数")} disabled={readOnly}
      onChange={event=>{const next=options.find(option=>option.name===event.target.value)!;setParam(next.name);setKfValue(String(next.value));setSelected([]);setDraft(null);}}>
      {options.map(option=><option key={option.name} value={option.name}>{option.name}</option>)}</select></label>
    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tr("参数时间曲线")} style={{width:"100%",background:"var(--surface)",touchAction:"none",border:"1px solid var(--border)",marginTop:8}}
      onPointerMove={moveNode} onPointerUp={event=>{
        if (!drag.current) return;
        const current=drag.current;drag.current=null;
        event.currentTarget.releasePointerCapture(event.pointerId);
        if (current.moved) {const frame=lane.find(item=>item.id===current.id)!;void commit({action:"update",param,keyframeId:frame.id,time:frame.time,value:frame.value}).finally(()=>setDraft(null));}
      }} onPointerCancel={()=>{drag.current=null;setDraft(null);}}>
      <path d={`M${pad},${pad} V${H-pad} H${W-pad}`} stroke="var(--border)" fill="none" />
      <path d={curve} fill="none" stroke="var(--accent)" strokeWidth="2" />
      {lane.map(frame=><circle key={frame.id} cx={x(rationalToSecs(frame.time))} cy={y(Number(frame.value))} r={selected.includes(frame.id)?6:4}
        fill={selected.includes(frame.id)?"var(--accent)":"var(--text)"} tabIndex={0} role="button" aria-label={tr("曲线节点 {0}",frame.id)}
        onKeyDown={event=>{if(event.key==="Enter" || event.key===" ")select(frame,event.ctrlKey);}}
        onPointerDown={event=>{if(readOnly)return;select(frame,event.ctrlKey || event.metaKey);drag.current={id:frame.id,moved:false};svgRef.current!.setPointerCapture(event.pointerId);}} />)}
      <text x={pad} y={12} fill="var(--text-dim)" fontSize="9">{spec.max}</text><text x={W-pad} y={H-2} textAnchor="end" fill="var(--text-dim)" fontSize="9">{duration.toFixed(2)}s</text>
    </svg>
    <div className="inspector__subrow" style={{marginTop:6,gap:4,flexWrap:"wrap"}}>
      <input type="number" step="0.01" min="0" max={duration} value={kfTime} aria-label={tr("关键帧时间（秒）")} className="cv-input" style={{width:72}} onChange={e=>setKfTime(e.target.value)} />
      <input type="number" step="0.01" min={spec.min} max={spec.max} value={kfValue} aria-label={param==="opacity"?tr("关键帧 opacity 值（0-1）"):tr("关键帧值")} className="cv-input" style={{width:72}} onChange={e=>setKfValue(e.target.value)} />
      <select className="cv-input" value={kfInterp} aria-label={tr("关键帧插值")} onChange={e=>setKfInterp(e.target.value as KeyframeInterpolation)}>
        {["linear","ease-in","ease-out","cubic-bezier"].map(value=><option key={value} value={value}>{value}</option>)}</select>
      <button className="cv-btn cv-btn--sm cv-btn--secondary" disabled={readOnly} onClick={()=>{
        const time=Number(kfTime),value=Number(kfValue);if(!Number.isFinite(time)||!Number.isFinite(value))return;
        void addKeyframe(dispatch,getLatestState() || state,{clipId:clip.id,param,time,value,interpolation:kfInterp,...(kfInterp==="cubic-bezier"?{bezier}:{})});
      }}><Plus size={10}/>{tr(" 添加")}</button>
      {selected.length===1?<button className="cv-btn cv-btn--sm cv-btn--secondary" disabled={readOnly} onClick={()=>void commit({action:"update",param,keyframeId:selected[0],time:secsToRational(Number(kfTime)),value:Number(kfValue),interpolation:kfInterp,...(kfInterp==="cubic-bezier"?{bezier}:{})})}>{tr("更新节点")}</button>:null}
    </div>
    {kfInterp==="cubic-bezier"?<>
      <svg viewBox="0 0 160 120" role="img" aria-label={tr("缓动控制曲线")} style={{width:"100%",height:120,touchAction:"none"}}
        onPointerMove={event=>{
          const handle=event.currentTarget.dataset.handle;if(!handle || readOnly)return;
          const rect=event.currentTarget.getBoundingClientRect();
          const px=Math.max(0,Math.min(1,((event.clientX-rect.left)/rect.width*160-20)/120));
          const py=Math.max(0,Math.min(1,1-((event.clientY-rect.top)/rect.height*120-10)/100));
          const index=Number(handle);setBezier(current=>current.map((value,i)=>i===index?px:i===index+1?py:value) as Bezier);
        }} onPointerUp={event=>{
          if(event.currentTarget.dataset.handle===undefined)return;
          delete event.currentTarget.dataset.handle;event.currentTarget.releasePointerCapture(event.pointerId);
          if(selected.length===1 && !readOnly)void commit({action:"update",param,keyframeId:selected[0],interpolation:"cubic-bezier",bezier});
        }} onPointerCancel={event=>{delete event.currentTarget.dataset.handle;}}>
        <path d={`M20,110 L${20+bezier[0]*120},${110-bezier[1]*100} M140,10 L${20+bezier[2]*120},${110-bezier[3]*100}`} fill="none" stroke="var(--text-dim)" />
        <path d={`M20,110 C${20+bezier[0]*120},${110-bezier[1]*100} ${20+bezier[2]*120},${110-bezier[3]*100} 140,10`} fill="none" stroke="var(--accent)" strokeWidth="2" />
        {[0,2].map(index=><circle key={index} cx={20+bezier[index]*120} cy={110-bezier[index+1]*100} r="6" fill="var(--accent)" aria-label={tr("贝塞尔控制点 {0}",index/2+1)}
          onPointerDown={event=>{if(readOnly)return;const svg=event.currentTarget.ownerSVGElement!;svg.dataset.handle=String(index);svg.setPointerCapture(event.pointerId);}} />)}
      </svg>
      <div style={{display:"flex",gap:4,marginTop:6}}>{bezier.map((value,index)=><label className="cv-hint" key={index}>{["x1","y1","x2","y2"][index]}<input type="number" className="cv-input" min="0" max="1" step="0.05" value={value} aria-label={`Bezier ${["x1","y1","x2","y2"][index]}`} onChange={event=>setBezier(current=>current.map((v,i)=>i===index?Number(event.target.value):v) as Bezier)} /></label>)}</div>
    </>:null}
    <p className="cv-hint">{tr("拖动节点调整时间和值；多选后可整体移动或缩放时间。")}</p>
    {lane.length===0?<p className="cv-hint">{tr("暂无关键帧。")}</p>:lane.map(frame=><div key={frame.id} className="inspector__subrow" style={{gap:4}}>
      <input type="checkbox" checked={selected.includes(frame.id)} aria-label={tr("选择关键帧 {0}",frame.id)} onChange={()=>select(frame,true)} />
      <span className="inspector__key">t={rationalToSecs(frame.time).toFixed(2)}s</span><span>{Number(frame.value).toFixed(2)} · {frame.interpolation}</span>
      <button className="cv-btn cv-btn--sm cv-btn--danger" disabled={readOnly} title={tr("删除关键帧")} onClick={()=>void removeKeyframe(dispatch,getLatestState() || state,{clipId:clip.id,param,keyframeId:frame.id})}><Trash2 size={10}/>{tr(" 删除")}</button>
    </div>)}
    {lane.length>0?<div className="inspector__subrow" style={{gap:4,flexWrap:"wrap",marginTop:6}}>
      <button className="cv-btn cv-btn--sm cv-btn--secondary" onClick={()=>setSelected(lane.map(frame=>frame.id))}>{tr("全选节点")}</button>
      <label className="cv-hint">{tr("移动秒数")}<input className="cv-input" type="number" step="0.1" value={offset} aria-label={tr("关键帧移动秒数")} style={{width:70}} onChange={e=>setOffset(e.target.value)}/></label>
      <label className="cv-hint">{tr("时间倍率")}<input className="cv-input" type="number" min="0.01" step="0.1" value={timeScale} aria-label={tr("关键帧时间倍率")} style={{width:70}} onChange={e=>setTimeScale(e.target.value)}/></label>
      <button className="cv-btn cv-btn--sm cv-btn--secondary" disabled={!selected.length || readOnly} onClick={()=>void commit({action:"retime",keyframeIds:selected,offset:Number(offset),timeScale:Number(timeScale),anchor:0})}>{tr("应用时间调整")}</button>
    </div>:null}
  </>;
}
