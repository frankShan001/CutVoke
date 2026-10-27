import { useEffect,useState } from 'react';
import { Bot,Copy } from 'lucide-react';
import { tr } from '../../i18n';
import { useEditor } from '../../store/editor';
import { getLatestState } from '../../store/actions';
import { flushPendingEdits } from '../../lib/pendingEdits';
import { Button } from '../ui';

interface Request {requestId:string;clipId:string;revision:string;kind:string;status:string;result?:{summary?:string;reviewedRevision?:string}}
interface Inbox {requests:Request[];connection:{connectedVisionAgents:number;waitingVisionAgents:number;automaticWake:boolean}}
async function request<T>(path:string,body?:object):Promise<T>{
  const response=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  const data=await response.json();if(!response.ok)throw new Error(data.error?.message || response.statusText);return data;
}

export function AgentHandoffSection({active,projectId,clipId,jobId,readOnly}:{active:boolean;projectId:string|null;clipId:string|null;jobId?:string;readOnly:boolean}){
  const {state}=useEditor({subscribeToClock:false});
  const [inbox,setInbox]=useState<Inbox|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState(false);
  const base=`/api/v1/projects/${encodeURIComponent(projectId || '')}/agent-requests`;
  useEffect(()=>{
    setInbox(null);setError('');setCopied(false);if(!active || !projectId)return;
    let stopped=false,running=false;const abort=new AbortController();
    const load=async()=>{if(running)return;running=true;try{
      const response=await fetch(base,{signal:abort.signal});const value=await response.json();
      if(!response.ok)throw new Error(value.error?.message || response.statusText);
      if(!stopped)setInbox(value);
    }catch(cause){if(!stopped)setError(String(cause));}finally{running=false;}};
    void load();const timer=window.setInterval(()=>void load(),1500);
    return()=>{stopped=true;abort.abort();window.clearInterval(timer);};
  },[active,projectId,base]);
  const requests=inbox?.requests.filter(value=>value.clipId===clipId).slice(0,3) || [];
  async function create(kind:'matting.select'|'matting.review'){
    if(!projectId || !clipId || !await flushPendingEdits())return;
    const context=getLatestState() || state;if(context.currentId!==projectId || context.editLock)return;
    setBusy(true);setError('');setCopied(false);
    try{
      await request(base,{kind,clipId,jobId:kind==='matting.review'?jobId:undefined,expectedRevision:context.revision});
      const next=await request<Inbox>(base);if(getLatestState()?.currentId===projectId)setInbox(next);
    }catch(cause){setError(String(cause));}finally{setBusy(false);}
  }
  async function copy(value:Request){
    const text=`CutVoke: ${value.kind==='matting.review'?'请检查所选片段的本地抠像结果':'请协助选择所选片段的抠像目标'}。\n先自行确认当前模型能看图、当前客户端能传递工具返回的图片。不支持或不确定时，不要使用视觉抠像接口，告诉我改为手动操作。\n先调用 editor_help({"topic":"local-models"}) 和 editor_help({"topic":"agent-bridge"})。再通过 agent_requests 读取以下请求，注册并确认视觉能力后认领；只处理该请求范围，实际看图后再报告结果。\n${JSON.stringify({projectId,operation:'get',requestId:value.requestId})}`;
    try{await navigator.clipboard.writeText(text);setCopied(true);}catch(cause){setError(String(cause));}
  }
  const status=(value:string)=>({pending:tr('待 AI 接手'),claimed:tr('AI 正在处理'),completed:tr('AI 已返回结果'),declined:tr('AI 未接手'),cancelled:tr('已取消'),stale:tr('工程已变化，请重新发起')} as Record<string,string>)[value] || value;
  return <details className="agent-handoff" data-testid="agent-handoff"><summary><Bot size={13}/>{tr('外部 AI 协助')}</summary>
    <p className="cv-hint">{tr('直接抠像由本机模型执行，无需外部 AI。请求协助不会启动聊天客户端。')}</p>
    <p className="cv-hint" role="status">{inbox?.connection.waitingVisionAgents?tr('有支持看图的 AI 正在等待接单'):inbox?.connection.connectedVisionAgents?tr('AI 已登记，等待主动读取请求'):tr('没有等待接单的 AI；请求将保留，可复制交接说明。')}</p>
    <div className="agent-handoff__actions"><Button size="sm" variant="secondary" disabled={!clipId || readOnly || busy} onClick={()=>void create('matting.select')}>{tr('请求 AI 协助选取')}</Button>
      <Button size="sm" variant="secondary" disabled={!jobId || !clipId || readOnly || busy} onClick={()=>void create('matting.review')}>{tr('请求 AI 检查结果')}</Button></div>
    {requests.map(value=><div key={value.requestId} className="agent-handoff__request"><strong>{status(value.status)}</strong>
      {(value.result?.reviewedRevision || value.revision)!==state.revision?<p className="cv-hint">{tr('工程已变化，请重新发起')}</p>:null}
      {value.result?.summary?<p dir="auto">{value.result.summary}</p>:null}
      <div className="agent-handoff__actions"><Button size="sm" variant="ghost" disabled={busy} onClick={()=>void copy(value)}><Copy size={12}/>{tr('复制交接说明')}</Button>
        {['pending','claimed'].includes(value.status)?<Button size="sm" variant="ghost" disabled={busy} onClick={()=>{
          setBusy(true);void request(`${base}/${encodeURIComponent(value.requestId)}/cancel`,{}).then(()=>request<Inbox>(base)).then(next=>{if(getLatestState()?.currentId===projectId)setInbox(next);}).catch(cause=>setError(String(cause))).finally(()=>setBusy(false));
        }}>{tr('取消请求')}</Button>:null}</div>
    </div>)}
    {copied?<p role="status" className="cv-hint">{tr('交接说明已复制')}</p>:null}
    {error?<p role="alert" className="person-cutout__message--error">{error}</p>:null}
  </details>;
}
