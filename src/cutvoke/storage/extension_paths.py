"""User-scoped optional runtimes, independent of project media and app binaries."""
import json
import os
from pathlib import Path
import uuid
import time


def extension_home():
    return Path(os.environ.get('CUTVOKE_EXTENSION_HOME') or Path.home()/'.cutvoke'/'extensions').expanduser().resolve()


def active_extension(extension_id):
    try:
        value=json.loads((extension_home()/'active.json').read_text('utf-8'))
        entry=value.get(extension_id,{})
        if not isinstance(entry,dict):return {}
        return entry
    except (OSError,ValueError,TypeError):return {}


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        for attempt in range(12):
            try:os.replace(temporary,path);break
            except PermissionError:
                if attempt==11:raise
                time.sleep(.01*(attempt+1))
    finally:temporary.unlink(missing_ok=True)


def activate(home,extension_id,entry):
    path=Path(home)/'active.json'
    try:current=json.loads(path.read_text('utf-8'))
    except (OSError,ValueError):current={}
    current[extension_id]=entry;atomic_json(path,current)
