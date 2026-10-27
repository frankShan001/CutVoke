import type { Clip } from '../types/api';

export interface PropertyRow {
  key: string; label: string; value: unknown; animated: boolean; section: string;
  instanceId?: string; effectId?: string;
}
interface Spec { effectId: string; name: string; defaults: Record<string, unknown> }
const number = (value: {num: string;den: string} | undefined, fallback: number) => value ? Number(value.num)/Number(value.den) : fallback;

/** A view of saved authoring values, not a second evaluator or render model. */
export function modifiedProperties(clip: Clip, specs: readonly Spec[]): PropertyRow[] {
  const rows: PropertyRow[]=[];
  const add=(key: string,label: string,value: unknown,section: string,extra: Partial<PropertyRow>={}) =>
    rows.push({key,label,value,animated:false,section,...extra});
  for(const [key,value,base] of [['volume',number(clip.volume,1),1],['speed',number(clip.speed,1),1],
    ['fadeIn',number(clip.fadeIn,0),0],['fadeOut',number(clip.fadeOut,0),0],['sourceStart',number(clip.sourceStart,0),0]] as const) {
    if(value!==base)add(key,key,value,['volume','fadeIn','fadeOut'].includes(key)?'audio':'timing');
  }
  if(clip.hidden)add('hidden','hidden',true,'timing');
  if(clip.freezeAt)add('freezeAt','freezeAt',number(clip.freezeAt,0),'timing');
  if(clip.speedCurve)add('speedCurve','speedCurve',clip.speedCurve,'timing');
  for(const [index,effect] of (clip.effects || []).entries()) {
    const spec=specs.find(item=>item.effectId===effect.effectId),instanceId=effect.instanceId || `legacy_${index}`;
    const extra={effectId:effect.effectId,instanceId};
    const section=effect.effectId==='cutvoke.transform'?'transform':effect.effectId==='cutvoke.text'?'title':'effects';
    const before=rows.length;
    function difference(value: unknown,baseline: unknown,path: string) {
      if(JSON.stringify(value)===JSON.stringify(baseline))return;
      if(value && typeof value==='object' && !Array.isArray(value)) {
        for(const [key,child] of Object.entries(value))if(!key.startsWith('_'))difference(child,(baseline as Record<string,unknown> | undefined)?.[key],path?`${path}.${key}`:key);
      } else add(`effect:${instanceId}:${path}`,`${spec?.name || effect.effectId} · ${path}`,value,section,extra);
    }
    difference(effect.params || {},spec?.defaults || {},'');
    if(effect.enabled===false)add(`bypass:${instanceId}`,`${spec?.name || effect.effectId} · enabled`,false,section,extra);
    if(effect.range)add(`range:${instanceId}`,`${spec?.name || effect.effectId} · range`,effect.range,section,extra);
    if(effect.compositing)add(`mix:${instanceId}`,`${spec?.name || effect.effectId} · compositing`,effect.compositing,'effects',extra);
    if(rows.length===before && !['cutvoke.transform','cutvoke.text','cutvoke.color'].includes(effect.effectId))
      add(`added:${instanceId}`,spec?.name || effect.effectId,'added','effects',extra);
  }
  for(const [param,lane] of Object.entries(clip.keyframes || {}))if(lane.length) {
    const existing=rows.find(row=>row.key===param);
    if(existing)existing.animated=true;
    add(`keys:${param}`,param,lane.length,'keys',{animated:true});
  }
  if(clip.compositing && Object.keys(clip.compositing).length)add('compositing','compositing',clip.compositing,'compositing');
  for(const [param,link] of Object.entries(clip.propertyLinks || {}))add(`link:${param}`,param,link,'links',{animated:true});
  if(clip.motionPath)add('motionPath','motionPath',clip.motionPath.points.length,'motion',{animated:true});
  for(const [control,value] of Object.entries(clip.propertyOverrides || {}))add(`override:${control}`,control,value,'template');
  return rows;
}
