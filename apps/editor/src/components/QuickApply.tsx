import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Search, X, Loader2, Sparkles, Bookmark, Command } from 'lucide-react';
import { tr, useLocale } from '../i18n';
import { useEditor, showError } from '../store/editor';
import { getLatestState, undo, redo } from '../store/actions';
import { applyResourceToClip } from '../store/resourceEdit';
import { getEffectCatalog, listBuiltinPresets, type EffectSpec, type BuiltinPresetSummary } from '../lib/effects';
import { clipAcceptsEffect } from '../lib/effectDrag';
import { rankQuickApply, type SearchEntry } from '../lib/quickApply';
import '../styles/quick-apply.css';

interface Item extends SearchEntry { effect?: EffectSpec; preset?: BuiltinPresetSummary; run?: () => void; disabled?: string; }
export function QuickApply({ onClose }: { onClose: () => void }) {
  const locale = useLocale();
  const { state, dispatch } = useEditor({ subscribeToClock: false });
  const [projectId] = useState(state.currentId);
  const dialog = useRef<HTMLDialogElement>(null), input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [query,setQuery] = useState(''), [index,setIndex] = useState(0), [busy,setBusy] = useState(false);
  const applying = useRef(false);
  const [catalog,setCatalog] = useState<EffectSpec[]>([]), [presets,setPresets] = useState<BuiltinPresetSummary[]>([]);
  const [loading,setLoading] = useState(true), [error,setError] = useState('');
  const target = state.project?.sequence.tracks.flatMap(track => track.clips.map(clip => ({track,clip})))
    .find(item => item.clip.id === state.selection?.clipId);
  const blocked = state.editLock ? tr('Agent 正在编辑，暂不可应用效果') : target?.track.locked ? tr('所选片段所在轨道已锁定，解锁后可应用效果') :
    !target ? tr('请先在时间线选中一个片段') : undefined;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal(); input.current?.focus();
    return () => { dialog.current?.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  useEffect(() => { if (state.currentId !== projectId || state.editLock || state.opening || state.error) onClose(); },
    [state.currentId,projectId,state.editLock,state.opening,state.error,onClose]);
  useEffect(() => {
    let cancelled = false; setLoading(true);
    Promise.allSettled([
      getEffectCatalog().then(effects=>{if(!cancelled)setCatalog(effects);}),
      listBuiltinPresets().then(builtin=>{if(!cancelled)setPresets(builtin.presets);}),
    ]).then(results=>{const failed=results.find(result=>result.status==='rejected');
      if(!cancelled && failed?.status==='rejected')setError(String(failed.reason instanceof Error?failed.reason.message:failed.reason));})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [locale]);
  const items = useMemo<Item[]>(() => {
    const effects: Item[] = catalog.map(effect => ({ id:effect.effectId,name:effect.name,kind:'effect',keywords:effect.keywords,
      effect,disabled:blocked || (target && !clipAcceptsEffect(effect,target.track,target.clip) ? tr('不适用于当前片段') : undefined) }));
    const builtin: Item[] = presets.flatMap(preset => {
      const effect = catalog.find(effect => effect.effectId === preset.effectId);
      return effect ? [{id:preset.presetId,name:preset.name,kind:'preset' as const,keywords:[...effect.keywords,preset.family],effect,preset,
        disabled:blocked || (target && !clipAcceptsEffect(preset,target.track,target.clip) ? tr('不适用于当前片段') : undefined)}] : [];
    });
    return [...effects,...builtin,
      {id:'menu.undo',name:tr('撤销'),kind:'command',keywords:['undo'],disabled:state.undoBlocked ? tr('暂无可撤销的操作') : undefined,
        run:()=>{void undo(dispatch,getLatestState() || state);}},
      {id:'menu.redo',name:tr('重做'),kind:'command',keywords:['redo'],disabled:state.redoBlocked ? tr('暂无可重做的操作') : undefined,
        run:()=>{void redo(dispatch,getLatestState() || state);}},
      {id:'menu.fit',name:tr('适应工程'),kind:'command',keywords:['fit','timeline'],run:()=>{window.dispatchEvent(new CustomEvent('cutvoke:fit-timeline'));}},
      {id:'menu.export',name:tr('导出'),kind:'command',keywords:['export','render'],run:()=>dispatch({type:'EXPORT_OPEN_SET',open:true})},
    ];
  },[catalog,presets,blocked,target?.clip.id,target?.track.locked,locale,state.undoBlocked,state.redoBlocked,dispatch]);
  const matches = rankQuickApply(items,query,[],state.project?.favorites || []).slice(0,60);
  const active = Math.min(index,Math.max(0,matches.length-1));
  useEffect(() => { setIndex(0); },[query]);
  useEffect(() => { document.getElementById(`${id}-${active}`)?.scrollIntoView({block:'nearest'}); },[active,id]);
  const choose = async (item: Item) => {
    if (applying.current || item.disabled) return;
    const current = getLatestState() || state;
    if (current.currentId !== projectId || current.editLock) { onClose(); return; }
    if (item.run) { onClose(); item.run(); return; }
    if (!item.effect || !target) return;
    applying.current = true; setBusy(true);
    try {
      const result = await applyResourceToClip(dispatch,current,{ effectId:item.effect.effectId,category:item.effect.category,
        appliesTo:item.preset?.appliesTo || item.effect.appliesTo, ...(item.preset ? {presetId:item.preset.presetId,
          duration:item.preset.defaultDuration ?? undefined} : {}) },item.effect,target.clip.id);
      if (result.ok) onClose();
    } catch(error) { showError(dispatch,error); onClose(); }
    finally { applying.current = false; setBusy(false); }
  };
  return <dialog className="quick-apply" ref={dialog} aria-labelledby={`${id}-title`} onKeyDown={event=>{
    if(event.key!=='Tab')return;
    const fields=[...event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled])')];
    const first=fields[0],last=fields.at(-1);
    if(!first || !last){event.preventDefault();return;}
    if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
  }} onCancel={event=>{
    event.preventDefault(); if (!busy) onClose();
  }} onClick={event=>{if(event.target===event.currentTarget && !busy) onClose();}}>
    <div className="quick-apply__heading"><strong id={`${id}-title`}>{tr('快速应用')}</strong>
      <button className="cv-chip" aria-label={tr('关闭')} disabled={busy} onClick={onClose}><X size={16}/></button></div>
    <div className="quick-apply__search"><Search size={18}/><input ref={input} role="combobox" aria-label={tr('搜索特效、预设和命令')}
      aria-expanded="true" aria-controls={`${id}-results`} aria-autocomplete="list" aria-activedescendant={matches.length ? `${id}-${active}` : undefined}
      disabled={busy} value={query} placeholder={tr('搜索特效、预设和命令')} onChange={event=>setQuery(event.target.value)}
      onKeyDown={event=>{
        if(event.nativeEvent.isComposing) return;
        if(event.key==='ArrowDown' || event.key==='ArrowUp') {event.preventDefault();setIndex((active+(event.key==='ArrowDown'?1:-1)+matches.length)%Math.max(1,matches.length));}
        if(event.key==='Enter') {event.preventDefault();if(matches[active]) void choose(matches[active]);}
      }}/>{busy ? <Loader2 size={16} className="cv-spin"/> : <kbd>Enter</kbd>}</div>
    <div className="quick-apply__scopes">{(['','e: ','p: ','m: '] as const).map((scope,i)=><button key={scope} className="cv-chip"
      disabled={busy} onClick={()=>{setQuery(scope);input.current?.focus();}}>{[tr('全部'),tr('特效'),tr('预设'),tr('命令')][i]}{scope ? <kbd>{scope.trim()}</kbd> : null}</button>)}</div>
    <div className="quick-apply__results" id={`${id}-results`} role="listbox" aria-label={tr('搜索结果')}>
      {matches.map((item,i)=>{const Icon=item.kind==='command'?Command:item.kind==='preset'?Bookmark:Sparkles;return <div key={item.id}
        role="option" id={`${id}-${i}`} aria-selected={active===i} aria-disabled={!!item.disabled || busy}
        className={`quick-apply__item${active===i?' is-active':''}${item.disabled?' is-disabled':''}`}
        onMouseEnter={()=>setIndex(i)} onMouseDown={event=>event.preventDefault()} onClick={()=>void choose(item)}>
        <Icon size={16}/><span><strong>{item.name}</strong>{item.disabled ? <small>{item.disabled}</small> : null}</span>
        <small>{item.kind==='command'?tr('命令'):item.kind==='preset'?tr('预设'):tr('特效')}</small></div>;})}
      {!matches.length ? <p className="cv-empty" role="status">{loading?tr('正在加载…'):tr('没有匹配结果')}</p> : null}
    </div>
    {error ? <p role="alert" className="cv-hint">{error}</p> : null}
    <footer className="quick-apply__footer"><span>↑ ↓ · Enter · Esc</span><span>{tr('应用到选中片段；操作可撤销')}</span></footer>
  </dialog>;
}
