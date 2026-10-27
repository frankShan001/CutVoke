import { useEffect, useRef, useState } from 'react';
import { tr, useLocale } from '../../i18n';
import { getEffectCatalog, type EffectSpec } from '../../lib/effects';
import { modifiedProperties, type PropertyRow } from '../../lib/modifiedProperties';
import { useEditor } from '../../store/editor';
import type { Clip } from '../../types/api';

export function ModifiedPropertiesSection({clip}: {clip: Clip}) {
  const locale=useLocale();
  const {state}=useEditor({subscribeToClock:false});
  const [specs,setSpecs]=useState<EffectSpec[]>([]),[mode,setMode]=useState<'modified'|'animated'>('modified');
  const details=useRef<HTMLDetailsElement>(null),lastPress=useRef(0);
  useEffect(()=>{let cancelled=false;void getEffectCatalog().then(value=>{if(!cancelled)setSpecs(value);}).catch(()=>{});return()=>{cancelled=true;};},[locale]);
  useEffect(()=>{
    lastPress.current=0;
    const key=(event: KeyboardEvent)=>{
      if(event.key.toLowerCase()!=='u' || event.repeat || event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey ||
        state.view!=='editor' || state.opening || state.exportOpen || state.templateOpen || state.agentPanelOpen ||
        (event.target as HTMLElement | null)?.closest('input,textarea,select,dialog,[contenteditable="true"],[role="dialog"],[role="alertdialog"]'))return;
      event.preventDefault();const now=performance.now();setMode(lastPress.current>0 && now-lastPress.current<600?'modified':'animated');lastPress.current=now;
      if(details.current)details.current.open=true;
      details.current?.scrollIntoView({block:'nearest'});
    };
    window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
  },[clip.id,state.view,state.opening,state.exportOpen,state.templateOpen,state.agentPanelOpen]);
  const rows=modifiedProperties(clip,specs).filter(row=>mode==='modified' || row.animated);
  function jump(row: PropertyRow) {
    const inspector=details.current?.closest('.inspector');
    const target=inspector?.querySelector<HTMLElement>(`[data-property-section="${row.section}"]`);
    if(target){if(target instanceof HTMLDetailsElement)target.open=true;target.querySelectorAll('details').forEach(node=>node.open=true);target.scrollIntoView({block:'nearest'});}
    if(row.effectId && row.instanceId)window.dispatchEvent(new CustomEvent('cutvoke:edit-effect',{detail:{clipId:clip.id,effectId:row.effectId,instanceId:row.instanceId}}));
    if(row.section==='keys')window.dispatchEvent(new CustomEvent('cutvoke:keyframe-channel',{detail:{clipId:clip.id,param:row.label}}));
  }
  const label=(value: string) => ({volume:tr('音量'),speed:tr('速度'),fadeIn:tr('淡入'),fadeOut:tr('淡出'),sourceStart:tr('源起点'),muted:tr('静音'),hidden:tr('隐藏'),
    freezeAt:tr('定格'),speedCurve:tr('速度曲线'),compositing:tr('图层合成'),motionPath:tr('空间运动路径'),x:tr('水平位置 X'),y:tr('垂直位置 Y'),scale:tr('缩放'),rotation:tr('旋转'),opacity:tr('不透明度')} as Record<string,string>)[value] || value;
  return <details ref={details} className="inspector__section" data-testid="modified-properties"><summary>{tr('已修改属性总览')} <kbd>U / UU</kbd></summary>
    <div className="inspector__subrow"><button type="button" className="cv-chip" aria-pressed={mode==='modified'} onClick={()=>setMode('modified')}>{tr('已修改')}</button>
      <button type="button" className="cv-chip" aria-pressed={mode==='animated'} onClick={()=>setMode('animated')}>{tr('动画属性')}</button></div>
    <p className="cv-hint">{tr('当前片段的静态修改、动画、绑定和实例覆盖；点击属性进入对应编辑区。')}</p>
    {rows.length?rows.map(row=><button type="button" key={row.key} className="modified-property" onClick={()=>jump(row)}>
      <span>{label(row.label)}{row.animated?' ◇':''}</span><code title={JSON.stringify(row.value)}>{row.value==='added'?tr('已添加'):typeof row.value==='number'?Number(row.value.toFixed(3)):JSON.stringify(row.value)}</code>
    </button>):<p className="cv-hint">{tr('没有匹配的属性')}</p>}
  </details>;
}
