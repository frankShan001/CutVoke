import type { Track } from '../types/api';
export type TimelineFilterMode = 'all' | 'selected' | 'animated';
export function filterTimelineClips(tracks: Track[], query: string, mode: TimelineFilterMode, selected: string[], effectNames: Map<string,string>, mediaName: (reference: Track['clips'][number]['assetRef']) => string = reference => reference.sourcePath.split(/[\\/]/).pop() || '') {
  const tokens=query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean), selection=new Set(selected);
  return new Set(tracks.flatMap(track=>track.clips.filter(clip=>{
    if(mode==='selected' && !selection.has(clip.id)) return false;
    if(mode==='animated' && !Object.values(clip.keyframes || {}).some(lane=>lane.length>0)) return false;
    const words=[track.id,track.kind,clip.id,mediaName(clip.assetRef),clip.assetRef.sourcePath.split(/[\\/]/).pop() || '',
      ...(clip.effects || []).flatMap(effect=>[effect.effectId,effectNames.get(effect.effectId) || '',String(effect.params?.content || '')])].join(' ').toLocaleLowerCase();
    return tokens.every(token=>words.includes(token));
  }).map(clip=>clip.id)));
}

// Selection commands use the visible view without changing project/render data.
let visible: {projectId:string;ids:string[];owner:symbol} | null = null;
export function publishTimelineSelection(projectId:string,ids:string[],owner:symbol) { visible={projectId,ids,owner}; }
export function clearTimelineSelection(owner:symbol) { if(visible?.owner===owner) visible=null; }
export function visibleTimelineSelection(projectId:string | null) { return visible?.projectId===projectId ? visible.ids : undefined; }
