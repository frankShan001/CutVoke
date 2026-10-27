import { useRef,useState } from 'react';
import { tr } from '../../i18n';
import { useEditor } from '../../store/editor';
import { getLatestState,runCommand } from '../../store/actions';
import { flushPendingEdits } from '../../lib/pendingEdits';
import { uploadOneFile } from '../../lib/importMedia';
import { Button } from '../ui';

type Object3D={id:string;shape:string;color?:string;texturePath?:string;[key:string]:unknown};
export function Scene3DControls({clipId,instanceId,params,onChange,onCommit}:{clipId:string;instanceId:string;params:Record<string,unknown>;onChange:(params:Record<string,unknown>)=>void;onCommit:(params:Record<string,unknown>)=>void}){
  const {state,dispatch}=useEditor({subscribeToClock:false});
  const [selected,setSelected]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const meshInput=useRef<HTMLInputElement>(null),textureInput=useRef<HTMLInputElement>(null);
  const objects=(params.objects || []) as Object3D[];const target=objects.find(value=>value.id===selected) || objects[0];
  function update(values:Object3D[]){const next={...params,objects:values};onChange(next);onCommit(next);}
  function add(shape:string){if(objects.length>=32)return;const id=shape+'_'+crypto.randomUUID().slice(0,8);update([...objects,{id,shape,color:'#55bbff',scaleX:.7,scaleY:.7,scaleZ:.7}]);setSelected(id);}
  async function importMesh(file:File){
    setBusy(true);setError('');const owner=state.currentId;
    try{
      if(file.size>2*1024*1024)throw new Error(tr('OBJ 文件最多 2 MiB'));
      const objText=await file.text();if(!await flushPendingEdits())return;
      const context=getLatestState() || state;if(context.currentId!==owner || context.editLock)return;
      const result=await runCommand(dispatch,context,'scene3d.import',{clipId,instanceId,objText});
      if(!result.ok)setError(tr('模型导入未完成'));
    }catch(cause){setError(String(cause));}finally{setBusy(false);}
  }
  async function importTexture(file:File){
    if(!target)return;const owner=state.currentId,id=target.id;setBusy(true);setError('');
    try{
      const result=await uploadOneFile(file,{projectId:owner});if(!result.ok)throw new Error(result.message);
      const context=getLatestState();if(context?.currentId!==owner || context.editLock)return;
      const effect=context.project?.sequence.tracks.flatMap(track=>track.clips).find(clip=>clip.id===clipId)?.effects?.find(effect=>effect.instanceId===instanceId);
      if(!effect)return;const values=effect.params.objects as Object3D[];
      const next={...effect.params,objects:values.map(value=>value.id===id?{...value,texturePath:result.media.path}:value)};
      onChange(next);onCommit(next);
    }catch(cause){setError(String(cause));}finally{setBusy(false);}
  }
  return <div className="scene3d-controls" data-testid="scene3d-controls"><p className="cv-hint">{tr('本机渲染，不使用 AI；物体按逐像素深度相互遮挡。')}</p>
    <div className="agent-handoff__actions">{(['cube','sphere','plane'] as const).map(shape=><Button key={shape} size="sm" variant="secondary" disabled={busy || objects.length>=32} onClick={()=>add(shape)}>{shape==='cube'?tr('添加立方体'):shape==='sphere'?tr('添加球体'):tr('添加平面')}</Button>)}
      <Button size="sm" variant="secondary" disabled={busy || objects.length>=32} onClick={()=>meshInput.current?.click()}>{tr('导入 OBJ 模型')}</Button></div>
    <input ref={meshInput} type="file" accept=".obj,text/plain" hidden aria-label={tr('OBJ 模型文件')} onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file)void importMesh(file);}}/>
    <input ref={textureInput} type="file" accept="image/png,image/jpeg,image/webp" hidden aria-label={tr('三维材质图片')} onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file)void importTexture(file);}}/>
    <label className="inspector__subrow"><span>{tr('三维物体')}</span><select className="cv-input" value={target?.id || ''} onChange={event=>setSelected(event.target.value)}>{objects.map(value=><option value={value.id} key={value.id}>{value.id}</option>)}</select></label>
    <div className="agent-handoff__actions"><Button size="sm" variant="ghost" disabled={busy || !target} onClick={()=>textureInput.current?.click()}>{tr('设置材质图片')}</Button>
      <Button size="sm" variant="ghost" disabled={busy || !target?.texturePath} onClick={()=>update(objects.map(value=>{const next={...value};if(value.id===target.id)delete next.texturePath;return next;}))}>{tr('移除材质图片')}</Button>
      <Button size="sm" variant="ghost" disabled={busy || objects.length<=1} onClick={()=>update(objects.filter(value=>value.id!==target?.id))}>{tr('删除物体')}</Button></div>
    <p className="cv-hint">{tr('在下方对象、摄像机和灯光表单中调整位置、材质与动画。')}</p>
    {error?<p role="alert" className="person-cutout__message--error">{error}</p>:null}
  </div>;
}
