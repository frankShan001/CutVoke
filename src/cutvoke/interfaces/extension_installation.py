"""Shared UI/MCP optional-model installation service; no startup downloads."""
import hashlib
import json
import os
from pathlib import Path
import threading
import zipfile

from cutvoke.rendering.extension_device import inspect_device,disk_budget
from cutvoke.rendering.extension_downloads import Downloader,DownloadUnavailable,InstallCancelled,verified
from cutvoke.rendering.extension_runtime import install_runtime
from cutvoke.storage.extension_jobs import ExtensionJobs
from cutvoke.storage.extension_paths import activate,extension_home


class ExtensionInstaller:
    def __init__(self,home=None,*,catalog=None,device_provider=None,downloader_factory=None,allow_legacy=True):
        self.home=Path(home or extension_home()).resolve();self.home.mkdir(parents=True,exist_ok=True)
        package=Path(__file__).resolve().parents[1]
        self.catalog=catalog or json.loads((package/'catalogs'/'optional_extensions.json').read_text('utf8'))
        self.bundled_root=package/'assets'/'extension_packages'
        self.journal=ExtensionJobs(self.home/'installations.sqlite')
        self.device_provider=device_provider or inspect_device;self.downloader_factory=downloader_factory or Downloader
        self.allow_legacy=allow_legacy;self._threads={};self._closed=False;self._lock=threading.RLock()

    def _profile(self,identity):
        return next(value for value in self.catalog['profiles'] if value['id']==identity)

    def _plan_identity(self,extension_id,profile_id,directory):
        profile=self._profile(profile_id) if profile_id else None
        # Consent covers the actual components and cost. Adding another trusted
        # origin for identical bytes must not ask the user again.
        artifacts=[{key:value[key] for key in ('id','filename','kind','sizeBytes','installedBytes','sha256','bundled') if key in value}
                   for value in (self.catalog['artifacts'][key] for key in profile['artifacts'])] if profile else []
        return hashlib.sha256(json.dumps({'extension':extension_id,'profile':profile,'artifacts':artifacts,
            'directory':str(directory)},sort_keys=True).encode()).hexdigest()

    def _checked_plan(self,plan_id):
        plan=self.journal.get_plan(plan_id)
        try:matches=plan['planHash']==self._plan_identity(plan['extensionId'],plan['profileId'],plan['installDirectory'])
        except (KeyError,StopIteration):matches=False
        if not matches:raise ValueError('INSTALLATION_PLAN_CHANGED: components changed since this plan; request a current extension_plan before confirming or installing')
        return plan

    def _installed(self,identity):
        try:entry=json.loads((self.home/'active.json').read_text('utf8')).get(identity,{})
        except (OSError,ValueError):entry={}
        if entry.get('verified') and Path(entry.get('python','')).is_file():
            try:
                profile=self._profile(entry['profileId'])
                if all(verified(Path(entry['models'][name]),self.catalog['artifacts'][key]) for name,key in profile['models'].items()):return entry
            except (KeyError,StopIteration):pass
        if self.allow_legacy:
            from cutvoke.rendering.person_cutout import availability
            status=availability()
            ready = status['sam2Ready'] if identity=='object' else status['mediapipeReady'] and status['interactiveModelReady']
            if ready and status.get('ffmpegReady',True):
                return {'verified':True,'legacy':True,'profileId':'existing-local-runtime'}
        return None

    def status(self):
        extensions=[]
        for info in self.catalog['extensions']:
            entry=self._installed(info['id']);jobs=self.journal.list(info['id'])
            extensions.append({**info,'installed':bool(entry),'state':'ready' if entry else 'not_installed','installation':entry,'job':jobs[0] if jobs else None})
        return {'extensions':extensions,'automaticModelDownload':False,'modelsIncludedInBasePackage':False,
                'confirmationRequiredForInstall':True,'catalogVersion':self.catalog['version'],
                'sourceModes':['auto','china','global','offline'],'agentVisionStillRequiredForMatting':True}

    def plan(self,extension_id,*,source_mode='auto',install_directory=None,offline_path=None):
        if extension_id not in {value['id'] for value in self.catalog['extensions']}:raise ValueError('unknown optional extension')
        if source_mode not in {'auto','china','global','offline'}:raise ValueError('invalid source mode')
        if offline_path and not Path(offline_path).expanduser().is_file():raise ValueError('offline package not found')
        if source_mode=='offline' and not offline_path:raise ValueError('select an offline package first')
        device=self.device_provider(extension_id,self.catalog['profiles']);profile_id=device.get('profileId')
        directory=Path(install_directory or self.home/'runtimes').expanduser()
        if not directory.is_absolute():raise ValueError('installation directory must be an absolute local directory')
        directory=directory.resolve();cache=directory/'.downloads'
        artifacts=[self.catalog['artifacts'][key] for key in self._profile(profile_id)['artifacts']] if profile_id else []
        downloader=Downloader(cache) if artifacts else None
        download=sum(value['sizeBytes'] for value in artifacts if not value.get('bundled') and not verified(downloader.path(value),value))
        installed=sum(value['installedBytes'] for value in artifacts)
        temporary=sum(value['sizeBytes'] for value in artifacts if value['filename'].endswith('.whl'))
        disk=disk_budget(directory,download+temporary,installed)
        entry=self._installed(extension_id)
        eligible=bool(profile_id and device['suitability'] not in {'unsupported','not_recommended'})
        value={'extensionId':extension_id,'profileId':profile_id,'device':device,'disk':disk,'installDirectory':str(directory),'cacheDirectory':str(cache),
               'sourceMode':source_mode,'offlinePath':str(Path(offline_path).resolve()) if offline_path else None,
               'downloadBytes':0 if offline_path else download,'installedBytes':installed,'temporaryBytes':temporary,
               'alreadyInstalled':bool(entry),'installable':eligible and disk['sufficient'],
               'state':'ready' if entry else 'device_not_recommended' if not eligible else 'disk_insufficient' if not disk['sufficient'] else 'needs_user_confirmation',
               'userMessage':'Optional local cutout extension. First use downloads additional components; review the disk and device estimates before installing.',
               'nextAction':'Use the installed extension.' if entry else 'Ask the user to accept or decline this plan. Never treat a general editing request as installation consent.',
               'speedEstimate':None,'automaticModelDownload':False}
        identity=self._plan_identity(extension_id,profile_id,directory)
        value=self.journal.plan(value,identity);consent=self.journal.confirmation(value['planId'])
        value['confirmation']=consent
        if consent and consent['decision']=='accept' and value['installable'] and not entry:value['state']='confirmed'
        if consent and consent['decision']=='decline' and not entry:
            value['state']='declined';value['nextAction']='The user declined. Continue other work; do not ask again unless the user explicitly requests enabling this extension.'
        return value

    def confirm(self,plan_id,decision,user_response,channel='agent'):
        self._checked_plan(plan_id)
        return self.journal.confirm(plan_id,decision,user_response,channel)

    def install(self,plan_id,confirmation_id):
        with self._lock:
            if self._closed:raise ValueError('installation service closed')
            plan=self._checked_plan(plan_id)
            # Recheck current free space; an old plan is not a reservation.
            if not disk_budget(plan['installDirectory'],plan['downloadBytes']+plan['temporaryBytes'],plan['installedBytes'])['sufficient']:
                raise ValueError('DISK_INSUFFICIENT: free disk space changed; choose a directory with enough space')
            job,created=self.journal.create_job(plan_id,confirmation_id,os.getpid(),already_installed=bool(self._installed(plan['extensionId'])))
            if created:
                thread=threading.Thread(target=self._run,args=(job,plan),daemon=True,name='cutvoke-extension-'+job['jobId'])
                self._threads[job['jobId']]=thread;thread.start()
            return job

    def _run(self,job,plan):
        identity=job['jobId']
        def cancelled():return self._closed or self.journal.get(identity)['status'] in {'cancelling','cancelled'}
        def progress(**values):self.journal.update(identity,**values)
        def event(**values):
            if values.get('phase')=='downloading':
                current=self.journal.get(identity);base=current.get('downloadedBytes',0);total=current.get('downloadTotal',1)
                values['progress']=min(65,round((base+values.get('fileBytes',0))/max(1,total)*65))
            progress(**values)
        try:
            self.journal.update(identity,status='running',phase='checking_sources')
            downloader=self.downloader_factory(plan['cacheDirectory'],mode=plan['sourceMode'],event=event)
            entry=install_runtime(plan,self._profile(plan['profileId']),self.catalog,downloader,job_id=identity,bundled_root=self.bundled_root,cancelled=cancelled,progress=progress)
            # Serialize the commit boundary with peer cancellation. A late
            # cancel must not leave an activated runtime stuck as cancelling.
            with self.journal.transaction():
                current=self.journal.get(identity)
                if self._closed or current['status']=='cancelling':raise InstallCancelled('installation cancelled')
                activate(self.home,plan['extensionId'],entry)
                self.journal._write({**current,'status':'completed','phase':'complete','progress':100,'retryable':False,'result':entry})
        except InstallCancelled:self.journal.update(identity,status='cancelled',phase='cancelled',error='',retryable=True)
        except Exception as error:
            self.journal.update(identity,status='failed',phase='download_unavailable' if isinstance(error,DownloadUnavailable) else 'failed',
                                error=str(error),retryable=True,errorCode='DOWNLOAD_UNAVAILABLE' if isinstance(error,DownloadUnavailable) else 'INSTALLATION_FAILED')
        finally:
            with self._lock:
                self._threads.pop(identity,None)
                if self._closed and not self._threads:self.journal.close()

    def job(self,identity,operation='status'):
        if operation=='cancel':
            current=self.journal.get(identity)
            if current['status'] in {'queued','running'}:return self.journal.update(identity,status='cancelling',phase='cancelling')
            return current
        if operation!='status':raise ValueError('operation must be status/cancel')
        return self.journal.get(identity)

    def export_offline(self,plan_id,out_path):
        plan=self.journal.get_plan(plan_id);profile=self._profile(plan['profileId']);downloader=Downloader(plan['cacheDirectory'])
        values=[self.catalog['artifacts'][key] for key in profile['artifacts'] if not self.catalog['artifacts'][key].get('bundled')]
        if any(not verified(downloader.path(value),value) for value in values):raise ValueError('offline export requires a complete verified local download cache; it never starts downloads')
        target=Path(out_path).expanduser()
        if not target.is_absolute():raise ValueError('offline bundle output must be an absolute local path')
        target=target.resolve()
        if target.exists():raise ValueError('offline bundle output already exists')
        target.parent.mkdir(parents=True,exist_ok=True)
        try:
            with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
                license_text=(self.bundled_root/'OPTIONAL-MODELS-LICENSE.txt').read_text('utf8')
                provenance=[{key:value[key] for key in ('filename','sha256','sources')} for value in values if value.get('kind')=='model']
                archive.writestr('manifest.json',json.dumps({'catalogVersion':self.catalog['version'],'profileId':profile['id'],
                    'modelLicense':{'spdx':'Apache-2.0','text':license_text},'modelProvenance':provenance}))
                for value in values:archive.write(downloader.path(value),value['filename'])
        except BaseException:target.unlink(missing_ok=True);raise
        return {'path':str(target),'profileId':profile['id']}

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True;threads=list(self._threads.values())
            if not threads:self.journal.close();return
        for thread in threads:thread.join(timeout=1)
