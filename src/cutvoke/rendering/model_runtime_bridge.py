"""Invoke optional workers from frozen apps without a host Python/Git install."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile

from cutvoke.storage.extension_paths import active_extension


def worker_script(name,directory):
    if name not in {'sam2_worker.py'}:raise ValueError('unknown isolated worker')
    target=Path(directory)/name;source=Path(__file__).with_name(name)
    if source.is_file():shutil.copyfile(source,target)
    else:
        archive=Path(__file__).resolve().parents[1]/'assets'/'extension_packages'/'cutvoke-workers.zip'
        with zipfile.ZipFile(archive) as stream:target.write_bytes(stream.read('cutvoke/rendering/'+name))
    return target


def person_python():
    entry=active_extension('person');path=entry.get('python')
    return Path(path) if entry.get('verified') and path and Path(path).is_file() else None


def run_person(source,output,*,model_file,edge_softness,selection_point,selection_points,selection_at_seconds,selection_prompts,tracking_mode,progress,cancelled):
    if tracking_mode not in {'semantic','magic_touch'} or os.environ.get('CUTVOKE_MATTING_ISOLATED_WORKER')=='1':return None
    python=person_python()
    if python is None:return None
    from cutvoke.rendering.person_cutout import PersonCutoutCancelled,PersonCutoutError
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    payload={'source_path':str(Path(source).resolve()),'output_path':str(output),'model_file':str(model_file) if model_file else None,
             'edge_softness':edge_softness,'selection_point':selection_point,'selection_points':selection_points,'selection_at_seconds':selection_at_seconds,
             'selection_prompts':selection_prompts,'tracking_mode':tracking_mode}
    with tempfile.TemporaryDirectory(prefix='.person-worker-',dir=output.parent) as directory:
        directory=Path(directory);request=directory/'request.json';state=directory/'progress.json';result=directory/'result.json'
        request.write_text(json.dumps({'arguments':payload,'progressPath':str(state),'resultPath':str(result)}),encoding='utf8')
        env={**os.environ,'CUTVOKE_MATTING_ISOLATED_WORKER':'1'}
        with (directory/'worker.log').open('w+b') as log:
            process=subprocess.Popen([str(python),'-I','-m','cutvoke.rendering.person_isolated_worker','--request',str(request)],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,creationflags=0x08000000 if os.name=='nt' else 0)
            last=None
            try:
                while process.poll() is None:
                    if cancelled and cancelled():raise PersonCutoutCancelled('local matting cancelled')
                    if state.exists() and progress:
                        try:
                            value=json.loads(state.read_text('utf8'));marker=(value['phase'],value['progress'])
                            if marker!=last:progress(*marker);last=marker
                        except (OSError,ValueError,KeyError):pass
                    time.sleep(.15)
                if process.returncode:
                    log.seek(0);detail=log.read().decode('utf8',errors='replace')[-2000:]
                    raise PersonCutoutError('isolated person cutout failed: '+detail)
                return json.loads(result.read_text('utf8'))
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
