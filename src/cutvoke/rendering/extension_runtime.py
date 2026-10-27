"""Build and verify a private runtime from pinned offline wheel files."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile

from cutvoke.rendering.extension_downloads import InstallCancelled,verified
from cutvoke.storage.extension_paths import atomic_json


def safe_extract(archive,target,maximum_bytes):
    target=Path(target).resolve();total=0
    with zipfile.ZipFile(archive) as stream:
        for entry in stream.infolist():
            destination=(target/entry.filename).resolve();total+=entry.file_size
            if not destination.is_relative_to(target) or stat.S_ISLNK(entry.external_attr>>16) or total>maximum_bytes:
                raise ValueError('invalid runtime archive member or expanded size')
            if entry.is_dir():destination.mkdir(parents=True,exist_ok=True);continue
            destination.parent.mkdir(parents=True,exist_ok=True)
            with stream.open(entry) as source,destination.open('wb') as output:shutil.copyfileobj(source,output)


def checked_process(command,log,*,cancelled,timeout=900,env=None):
    with Path(log).open('ab') as stream:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,env=env,
                                 creationflags=0x08000000 if os.name=='nt' else 0)
        started=time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():raise InstallCancelled('installation cancelled')
                if time.monotonic()-started>timeout:raise RuntimeError('runtime verification timed out')
                time.sleep(.15)
            if process.returncode:
                detail=Path(log).read_text('utf8',errors='replace')[-2500:]
                raise RuntimeError('isolated runtime installation/verification failed: '+detail)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def _remove_stage(path,parent):
    path=Path(path).resolve();parent=Path(parent).resolve()
    if path==parent or not path.is_relative_to(parent):raise ValueError('staging cleanup escaped the installation directory')
    if path.exists():shutil.rmtree(path)


def import_offline(path,artifacts,downloader,cancelled):
    allowed={artifact['filename']:artifact for artifact in artifacts if not artifact.get('bundled')}
    with zipfile.ZipFile(path) as archive:
        names=[entry.filename for entry in archive.infolist()]
        if len(names)!=len(set(names)):raise ValueError('offline bundle contains duplicate filenames')
        for entry in archive.infolist():
            if cancelled():raise InstallCancelled('installation cancelled')
            if entry.filename=='manifest.json':continue
            if entry.filename not in allowed:raise ValueError('offline bundle contains a file outside this pinned installation plan')
            artifact=allowed[entry.filename]
            if entry.file_size!=artifact['sizeBytes']:raise ValueError('offline bundle file size mismatch')
            target=downloader.path(artifact)
            if verified(target,artifact):continue
            temporary=target.with_suffix(target.suffix+'.offline')
            try:
                with archive.open(entry) as source,temporary.open('wb') as output:
                    while data:=source.read(1024**2):
                        if cancelled():raise InstallCancelled('installation cancelled')
                        output.write(data)
                if not verified(temporary,artifact):raise ValueError('offline bundle SHA-256 mismatch')
                os.replace(temporary,target)
            finally:temporary.unlink(missing_ok=True)
    missing=[value['filename'] for value in allowed.values() if not verified(downloader.path(value),value)]
    if missing:raise ValueError('offline bundle is missing '+', '.join(missing))


def install_runtime(plan,profile,catalog,downloader,*,job_id,bundled_root,cancelled,progress):
    root=Path(plan['installDirectory']).resolve();stages=root/'.staging';stages.mkdir(parents=True,exist_ok=True)
    stage=stages/job_id;stage.mkdir(exist_ok=False);artifacts=[catalog['artifacts'][key] for key in profile['artifacts']]
    target=root/(profile['id']+'-'+uuid.uuid4().hex[:12]);paths={}
    try:
        if plan.get('offlinePath'):import_offline(plan['offlinePath'],artifacts,downloader,cancelled)
        completed=0;total=sum(value['sizeBytes'] for value in artifacts)
        progress(downloadedBytes=0,downloadTotal=total,progress=0)
        for artifact in artifacts:
            progress(phase='checking_sources',progress=round(completed/max(1,total)*65),filename=artifact['filename'])
            paths[artifact['id']]=downloader.fetch(artifact,cancelled=cancelled,bundled_root=bundled_root)
            completed+=artifact['sizeBytes'];progress(downloadedBytes=completed,downloadTotal=total,progress=round(completed/max(1,total)*65))
        if cancelled():raise InstallCancelled('installation cancelled')
        progress(phase='installing',progress=68)
        runtime=stage/'runtime';site=runtime/'Lib'/'site-packages';site.mkdir(parents=True)
        python_artifact=next(value for value in artifacts if value.get('kind')=='python-embed')
        safe_extract(paths[python_artifact['id']],runtime,python_artifact['installedBytes']+1024**2)
        (runtime/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../workerlib\nimport site\n',encoding='utf8')
        pip=next(value for value in artifacts if value['filename'].startswith('pip-') and value['filename'].endswith('.whl'))
        safe_extract(paths[pip['id']],site,pip['installedBytes']+1024**2)
        workers=next(value for value in artifacts if value.get('kind')=='worker-code')
        safe_extract(paths[workers['id']],stage/'workerlib',workers['installedBytes']+1024**2)
        wheel_dir=stage/'wheels';wheel_dir.mkdir();wheels=[]
        for artifact in artifacts:
            if artifact['filename'].endswith('.whl') and artifact['id']!=pip['id']:
                destination=wheel_dir/artifact['filename'];shutil.copyfile(paths[artifact['id']],destination);wheels.append(str(destination))
        python=runtime/'python.exe';log=stage/'installation.log'
        checked_process([str(python),'-I','-m','pip','--isolated','install','--no-index','--no-deps','--no-compile',
            '--disable-pip-version-check','--upgrade','--target',str(site),*wheels],log,cancelled=cancelled)
        for dll in site.rglob('*.dll'):
            if dll.name.lower().startswith(('msvcp','vcruntime','concrt','vccorlib','vcomp')) and not (runtime/dll.name).exists():shutil.copyfile(dll,runtime/dll.name)
        models=stage/'models';models.mkdir()
        model_license=Path(bundled_root)/'OPTIONAL-MODELS-LICENSE.txt'
        if model_license.is_file():shutil.copyfile(model_license,stage/'MODEL-LICENSE.txt')
        for key in profile['models'].values():
            artifact=catalog['artifacts'][key];shutil.copyfile(paths[key],models/artifact['filename'])
        progress(phase='verifying',progress=85)
        report_path=stage/'verification.json'
        checked_process([str(python),'-I','-m','cutvoke.rendering.extension_probe','--extension',profile['extensionId'],
            '--root',str(stage),'--backend',profile['backend'],'--output',str(report_path)],log,cancelled=cancelled,timeout=600)
        report=json.loads(report_path.read_text('utf8'));report.update(profileId=profile['id'],catalogVersion=catalog['version'],executable=str(target/'runtime'/'python.exe'))
        if cancelled():raise InstallCancelled('installation cancelled')
        if profile['extensionId']=='object':
            atomic_json(stage/'.cutvoke-sam2-ready.json',{'revision':catalog['provenance']['sam2Commit'],'torch':report['versions']['torch'],'cudaAvailable':report['backend']=='cuda'})
        # Wheel copies are installer scratch, not a second installed cache.
        _remove_stage(wheel_dir,stage)
        atomic_json(report_path,report);os.replace(stage,target)
        return {'root':str(target),'python':str(target/'runtime'/'python.exe'),'models':{name:str(target/'models'/catalog['artifacts'][key]['filename']) for name,key in profile['models'].items()},
                'profileId':profile['id'],'catalogVersion':catalog['version'],'verified':True,'verification':report,'installedAt':time.time()}
    finally:
        if stage.exists():_remove_stage(stage,stages)
