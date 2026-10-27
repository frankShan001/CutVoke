"""Installer discovery and user-consent contract shared by MCP and HTTP."""
from cutvoke.editing.service import EditError


def tool_specs():
    string={'type':'string','minLength':1}
    def spec(name,description,properties=None,required=()):
        return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties or {},'required':list(required),'additionalProperties':False}}
    return [
        spec('extension_status','Read optional local cutout extension readiness and shared installation jobs. Discovery/status NEVER downloads or installs. Base editing works without extensions. Model installation does not imply that your AI supports image input.'),
        spec('extension_plan','Preflight a local optional extension: actual device/driver, disk budget, pinned runtime/profile, download and installed sizes. Does not download or install. If state=needs_user_confirmation, briefly explain purpose and cost and ASK the human user in this conversation. Do not infer consent from a general editing request. If declined, respect it. Unknown/unsupported agent vision must not start AI matting; manual UI can still use a local model.',
             {'extensionId':{'type':'string','enum':['person','object']},'sourceMode':{'type':'string','enum':['auto','china','global','offline'],'default':'auto'},'installDirectory':string,'offlinePath':string},('extensionId',)),
        spec('extension_confirm','Record the actual HUMAN accept/decline response to an installation plan. Call only after explicit user consent or an already explicit instruction to install this extension. userResponse must quote/summarize that real instruction, never invent approval. Persisted receipts are shared with the UI. Network mirror switching within this same pinned plan needs no further confirmation.',
             {'planId':string,'decision':{'type':'string','enum':['accept','decline']},'userResponse':{'type':'string','minLength':1,'maxLength':1200},'channel':{'type':'string','enum':['agent','ui'],'default':'agent'}},('planId','decision','userResponse')),
        spec('extension_install','Install a previously confirmed optional extension asynchronously. Requires the exact confirmation receipt returned by extension_confirm. Uses fixed verified downloads and a private runtime; no user Python/Git required. Returns a shared jobId; does not edit any project. Poll extension_job and preserve it for retry/cancellation. A cancelled/failed retry reuses verified cache and an unchanged confirmed plan.',
             {'planId':string,'confirmationId':string},('planId','confirmationId')),
        spec('extension_job','Read/cancel an installation job started by UI or MCP; status and cancellation require no agent vision. Poll at bounded intervals. Completed means runtime smoke verification passed, not that this user clip has acceptable cutout quality or speed. Resume via the unchanged plan and confirmation; cancelled means partial downloads are retained.',
             {'jobId':string,'operation':{'type':'string','enum':['status','cancel'],'default':'status'}},('jobId',)),
        spec('extension_export_offline','Export an offline extension bundle from an already complete verified local cache. Never downloads missing files, never includes user media, and refuses overwrite. The destination is a new absolute ZIP path. Import by extension_plan(sourceMode=offline,offlinePath=...) followed by normal user confirmation and install.',
             {'planId':string,'outPath':string},('planId','outPath')),
    ]


def call(api,name,args):
    installer=api.extension_installer
    try:
        if name=='extension_status':return installer.status()
        if name=='extension_plan':return {'plan':installer.plan(args['extensionId'],source_mode=args.get('sourceMode','auto'),install_directory=args.get('installDirectory'),offline_path=args.get('offlinePath'))}
        if name=='extension_confirm':return {'confirmation':installer.confirm(args['planId'],args['decision'],args['userResponse'],args.get('channel','agent'))}
        if name=='extension_install':return {'job':installer.install(args['planId'],args['confirmationId'])}
        if name=='extension_job':return {'job':installer.job(args['jobId'],args.get('operation','status'))}
        if name=='extension_export_offline':return installer.export_offline(args['planId'],args['outPath'])
    except ValueError as error:
        code=str(error).split(':',1)[0]
        if code in {'USER_CONFIRMATION_REQUIRED','INSTALLATION_BUSY','DISK_INSUFFICIENT','INSTALLATION_PLAN_CHANGED'}:raise EditError(code,str(error),retryable=code!='USER_CONFIRMATION_REQUIRED') from error
        raise
    raise ValueError('unknown extension tool')
