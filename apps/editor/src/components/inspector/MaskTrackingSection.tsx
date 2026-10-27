import { useState } from "react";
import { tr } from "../../i18n";
import { useEditor } from "../../store/editor";
import { getLatestState, runCommand } from "../../store/actions";
import { sendCommand } from "../../lib/api";
import { rationalToSecs } from "../../lib/rational";
import type { Clip } from "../../types/api";

interface Tracking { status: string; lostAt?: number; sampleCount: number; pathFrames: { at: number; points: {x:number;y:number}[]; confidence?: number }[] }
export function MaskTrackingSection({ clip }: { clip: Clip }) {
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [result, setResult] = useState<{ owner: string; clipId: string; revision: string; tracking: Tracking } | null>(null);
  const original=clip.effects?.find(effect=>effect.effectId==='cutvoke.fx.mask' && effect.params.shape==='freehand');
  const tracked=clip.effects?.find(effect=>effect.effectId==='cutvoke.fx.trackedMask');
  const pointFrames=tracked?.params.pathFrames as {points:{x:number;y:number}[]}[] | undefined;
  const points=(pointFrames?.[0]?.points || tracked?.params.points || original?.params.points) as {x:number;y:number;inHandle?:{x:number;y:number};outHandle?:{x:number;y:number};handlesLinked?:boolean}[] | undefined;
  const duration=rationalToSecs(clip.timelineEnd)-rationalToSecs(clip.timelineStart);
  const compatible=new Set(['cutvoke.transform','cutvoke.color','cutvoke.fx.blur','cutvoke.fx.grayscale','cutvoke.fx.invert','cutvoke.fx.sepia','cutvoke.fx.vibrance','cutvoke.fx.hsl','cutvoke.fx.colorbalance','cutvoke.fx.curves','cutvoke.fx.lut','cutvoke.fx.glow','cutvoke.fx.sharpen']);
  const preceding=(clip.effects || []).slice(0,(clip.effects || []).indexOf(tracked || original!));
  const available=!!clip.assetRef.sourcePath && !clip.nested && !clip.sequenceRef && !!points && points.length>=3 && points.length<=128 &&
    (!clip.speed || rationalToSecs(clip.speed)===1) && !clip.freezeAt && !clip.speedCurve && (!original?.params.motion || original.params.motion==='none') &&
    preceding.every(effect=>effect.enabled===false || compatible.has(effect.effectId));
  const current=result?.owner===state.currentId && result.clipId===clip.id ? result : null;
  async function analyze() {
    const context=getLatestState() || state;
    if(!context.currentId || context.editLock || !available)return;
    setBusy(true);setError('');setResult(null);
    try {
      const response=await sendCommand(context.currentId,'media.trackMask',{sourcePath:clip.assetRef.sourcePath,points:points!,pathMode:tracked?'linear':original?.params.pathMode || 'linear',
        start:rationalToSecs(clip.sourceStart),seconds:Math.min(30,duration),sampleFps:8},context.revision,crypto.randomUUID());
      setResult({owner:context.currentId,clipId:clip.id,revision:context.revision,tracking:response.changedEntities[0] as unknown as Tracking});
    } catch(cause) {setError(String(cause));} finally {setBusy(false);}
  }
  async function apply() {
    const context=getLatestState() || state;
    if(!current || current.revision!==context.revision || context.editLock)return;
    const params={points:current.tracking.pathFrames[0].points,pathFrames:current.tracking.pathFrames,feather:original?.params.feather ?? tracked?.params.feather ?? .01,invert:original?.params.invert ?? tracked?.params.invert ?? false};
    const commands=tracked?[{type:'effect.update',payload:{clipId:clip.id,instanceId:tracked.instanceId,params}}]:
      [{type:'effect.replace',payload:{clipId:clip.id,instanceId:original!.instanceId,effectId:'cutvoke.fx.trackedMask',params}}];
    setBusy(true);try {await runCommand(dispatch,context,'edit.batch',{commands});setResult(null);} finally {setBusy(false);}
  }
  if(!original && !tracked)return null;
  return <details className="inspector__section"><summary>{tr("蒙版光流跟踪")}</summary>
    <p className="cv-hint">{tr("先绘制钢笔蒙版，再分析跟踪；逐点结果可继续编辑，失去跟踪时会停止。")}</p>
    {!available?<p className="cv-hint">{tr('跟踪使用源画面坐标；请先移除前置几何形变、蒙版动画或变速。')}</p>:null}
    <button type="button" className="cv-btn cv-btn--sm" disabled={busy || !available || !!state.editLock} onClick={()=>void analyze()}>{tr("分析蒙版跟踪")}</button>
    {current ? <><p role="status">{current.tracking.status==='lost'?tr("跟踪在 {0} 秒失效，请检查轮廓。",current.tracking.lostAt?.toFixed(2)):tr("跟踪完成")} · {current.tracking.sampleCount} {tr("帧")}</p>
      <button type="button" className="cv-btn cv-btn--sm" disabled={busy || current.revision!==state.revision || !!state.editLock} onClick={()=>void apply()}>{tr("应用跟踪结果")}</button></> : null}
    {error?<p role="alert">{error}</p>:null}
  </details>;
}
