"""Discoverable local-model tools and explicit human/agent handoff contracts."""
from __future__ import annotations

from cutvoke.editing.service import EditError
from cutvoke.storage.agent_inbox import require_vision

STRING={'type':'string','minLength':1}
VISION={'type':'object','properties':{key:{'type':'boolean'} for key in ('selfAssessed','imageInput','toolImageInput','videoInput')},
        'required':['selfAssessed','imageInput','toolImageInput'],'additionalProperties':False}
POINT={'type':'object','properties':{'x':{'type':'number','minimum':0,'maximum':1},'y':{'type':'number','minimum':0,'maximum':1},'label':{'type':'integer','enum':[0,1]}},'required':['x','y','label'],'additionalProperties':False}
PROMPTS={'type':'array','minItems':1,'maxItems':16,'items':{'type':'object','properties':{'atSeconds':{'type':'number','minimum':0},
    'points':{'type':'array','minItems':1,'maxItems':8,'items':POINT}},'required':['atSeconds','points'],'additionalProperties':False}}


def tool_specs():
    def tool(name,description,properties,required=()):return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}
    project={'projectId':STRING}; revision={**project,'expectedRevision':STRING};session={'agentId':STRING,'sessionToken':STRING}
    from cutvoke.interfaces.extension_tools import tool_specs as installer_tools
    return [
      tool('local_model_status','Discover optional LOCAL matting models, execution limits and AI usability requirements before use. No model download, external AI call or media upload. External-agent image-input support is NOT inferred from tool availability; read editor_help(topic=local-models).',{}),
      tool('local_matting_start','Run optional LOCAL neural matting asynchronously. FIRST self-assess whether your current model AND host can actually inspect returned tool images. If unsupported/unknown DO NOT CALL; use the manual UI. Inspect source PNGs with preview_frames, choose normalized prompts from those actual frames. SAM2 requires selected mode and 1..16 source-time prompts (1..8 labeled points each). Never guess visual coordinates. Returns jobId, not a committed edit.',
           {**revision,'clipId':STRING,'agentCapabilities':VISION,'trackingMode':{'type':'string','enum':['semantic','magic_touch','sam2'],'default':'sam2'},'selectionMode':{'type':'string','enum':['all','selected'],'default':'selected'},'selectionPrompts':PROMPTS,'edgeSoftness':{'type':'number','minimum':0,'maximum':8}},
           ('projectId','expectedRevision','clipId','agentCapabilities')),
      tool('local_matting_job','Read or cancel a LOCAL matting job created by either the UI or MCP. Poll with a bounded timeout (about once per second). A completed job is not visually accepted: inspect result.outputPath with preview_frames, then explicitly apply. Status/cancel do not require vision and never make a quality claim.',
           {**project,'jobId':STRING,'operation':{'type':'string','enum':['status','cancel'],'default':'status'}},('projectId','jobId')),
      tool('local_matting_apply','Apply an already completed local matting result to its exact clip, with current revision/lease and one undo. Requires self-assessed tool-image vision; inspect real source/result frames first, do not claim quality from success alone. Replaces source only; original media and undo are retained.',
           {**revision,'jobId':STRING,'agentCapabilities':VISION,'editLeaseId':STRING,'commandId':STRING},('projectId','expectedRevision','jobId','agentCapabilities','editLeaseId','commandId')),
      tool('agent_session','Register/heartbeat/close an external agent with SELF-ASSESSED capabilities. Unknown image or tool-image support must be false. Session lasts 120 seconds. Registration does NOT wake an idle AI. While actively handling user-authorized work use agent_requests(operation=wait) to receive UI requests; otherwise UI offers a copyable handoff.',
           {**session,'operation':{'type':'string','enum':['register','heartbeat','close'],'default':'register'},'name':{'type':'string','maxLength':128},'capabilities':VISION}),
      tool('agent_requests','Read/wait for, claim, finish or decline explicit human UI requests. wait<=25s returns a queued request to an ALREADY active tool call; never launches an AI host. Claim/wait/finish require a valid session with self-assessed image+tool-image support. Requests are project/revision scoped; claiming/finishing does not edit the timeline. Do not accept work outside the request scope.',
           {**project,**session,'operation':{'type':'string','enum':['list','get','wait','claim','complete','decline'],'default':'list'},'requestId':STRING,'status':{'type':'string','enum':['pending','claimed','completed','declined','cancelled','stale']},'waitSeconds':{'type':'number','minimum':0,'maximum':25},'result':{'type':'object'}},('projectId',)),
    ]+installer_tools()


def status():
    from cutvoke.rendering.person_cutout import availability
    from cutvoke.rendering.scene3d import availability as scene_status
    return {'matting':availability(),'scene3d':scene_status(),'execution':'local-only','automaticModelDownload':False,'externalAIRequiredForManualUI':False,
      'agentRequirements':{'selfAssessmentRequired':True,'imageInput':True,'toolImageInput':True,'videoInputRequired':False,
        'unsupportedOrUnknown':'Do not start AI matting or perform visual review. Let the user choose points and inspect the result in the manual UI.',
        'distinction':'Local segmentation inference itself does not use your LLM. Image capability is required for the agent to choose/verify targets from actual tool-returned PNG frames.'},
      'models':[{'id':'semantic','scope':'all visible people','readyField':'mediapipeReady','installExtension':'person'},
                {'id':'magic_touch','scope':'one point-selected target; single prompt frame','readyField':'interactiveModelReady','installExtension':'person'},
                {'id':'sam2','scope':'point-selected video object; multiple correction frames','readyField':'sam2Ready','installExtension':'object'}],
      'installation':{'statusTool':'extension_status','planTool':'extension_plan','confirmationTool':'extension_confirm','installTool':'extension_install','jobTool':'extension_job','requiresExplicitUserConsent':True,'shellCommandsRequired':False},
      'limits':{'promptFrames':16,'pointsPerFrame':8,'pendingJobs':2,'coordinates':'normalized source pixels, x/y in 0..1; label 1 includes, 0 excludes','times':'absolute source seconds inside the selected clip range'},
      'workflow':['Assess your current model and host tool-image support. Stop if false/unknown.','Inspect source frames; use actual source-time coordinates.','Submit and retain jobId.','Poll with bounded timeout; inspect real output PNGs.','Acquire/renew lease and apply at current revision with stable commandId; release lease in finally.'],
      'handoff':{'automaticWake':False,'supported':'Persistent UI requests plus active-agent bounded wait/poll. An idle external AI needs its host integration; connection alone is not such an integration.'}}


def call(adapter,name,a):
    api=adapter.api
    if name.startswith('extension_'):
        from cutvoke.interfaces.extension_tools import call as installer_call
        return installer_call(api,name,a)
    if name=='local_model_status':return status()
    if name=='agent_session':return api.agent_inbox.session(a.get('operation','register'),a)
    if name=='local_matting_apply':
        require_vision(a.get('agentCapabilities'))
        job=api._person_cutout_jobs.get(a.get('jobId'))
        if job and job['projectId']==a.get('projectId') and job.get('applyCommandId')==a.get('commandId') and job['status']=='applied' and job.get('applyReceipt'):
            return {'job':job,'command':job['applyReceipt'],'idempotent':True}
    project=adapter._project(a)
    if name=='agent_requests':return api.agent_inbox.act(project,a)
    if name in {'local_matting_start','local_matting_apply'}:require_vision(a.get('agentCapabilities'))
    if name=='local_matting_start':
        body={key:a[key] for key in ('clipId','trackingMode','selectionMode','selectionPrompts','edgeSoftness') if key in a}
        body.setdefault('trackingMode','sam2');body.setdefault('selectionMode','selected')
        from cutvoke.rendering.person_cutout import availability
        ready=availability();field={'sam2':'sam2Ready','magic_touch':'interactiveModelReady','semantic':'mediapipeReady'}[body['trackingMode']]
        if not ready.get(field):
            extension='object' if body['trackingMode']=='sam2' else 'person'
            raise EditError('EXTENSION_NOT_INSTALLED','This optional cutout component is not ready. Call extension_plan(extensionId='+extension+'), obtain explicit user confirmation, then extension_confirm and extension_install. Ordinary editing remains available.')
        result_code,result=api.handle('POST',f'/api/v1/projects/{project.project_id}/person-cutout-jobs',body)
    elif name=='local_matting_job':
        job=api._person_cutout_jobs.get(a['jobId'])
        if not job or job['projectId']!=project.project_id:raise ValueError('matting job not found in this project')
        return {'job':api._person_cutout_jobs.cancel(a['jobId']) if a.get('operation')=='cancel' else job}
    elif name=='local_matting_apply':
        result_code,result=api.handle('POST',f'/api/v1/projects/{project.project_id}/person-cutout-jobs/{a["jobId"]}/apply',
            {key:a[key] for key in ('expectedRevision','editLeaseId','commandId')} | {'actor':{'kind':'agent','id':'local-matting'}})
    else:raise ValueError('unknown local model tool')
    if result_code>=400:
        error=result.get('error',{});raise EditError(error.get('code','LOCAL_MODEL_FAILED'),error.get('message','local model operation failed'))
    return {'job':result} if name=='local_matting_start' else result
