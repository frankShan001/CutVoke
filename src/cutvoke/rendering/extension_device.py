"""Cheap local checks; a hardware inventory is not an inference benchmark."""
from functools import lru_cache
import os
import platform
import shutil
import subprocess
import sys
import time


@lru_cache(maxsize=2)
def _inventory(bucket):
    ram=0
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),*[('value'+str(i),ctypes.c_ulonglong) for i in range(7)]]
        memory=Memory();memory.length=ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):ram=memory.value0
    else:
        try:ram=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')
        except (OSError,ValueError,AttributeError):pass
    gpus=[]
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader,nounits'],
            capture_output=True,text=True,timeout=4,stdin=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name=='nt' else 0)
        for line in result.stdout.splitlines():
            name,memory,driver=[value.strip() for value in line.split(',')]
            gpus.append({'name':name,'memoryMiB':int(memory),'driver':driver})
    except (OSError,ValueError,subprocess.TimeoutExpired):pass
    machine=platform.machine().lower();arch='arm64' if machine in {'arm64','aarch64'} else 'x64' if machine in {'amd64','x86_64'} else machine
    system={'win32':'windows','darwin':'macos'}.get(sys.platform,'linux' if sys.platform.startswith('linux') else sys.platform)
    return {'system':system,'architecture':arch,'ramBytes':ram,'cpuCount':os.cpu_count() or 1,'gpus':gpus,'benchmarkAvailable':False}


def inspect_device(extension_id,profiles):
    device=dict(_inventory(int(time.monotonic()//60)))
    eligible=[value for value in profiles if value['extensionId']==extension_id and value['system']==device['system'] and value['architecture']==device['architecture']]
    cuda=any(gpu['memoryMiB']>=6144 and int(gpu['driver'].split('.')[0])>=570 for gpu in device['gpus'])
    desired='cuda128' if extension_id=='object' and cuda else 'cpu'
    profile=next((value for value in eligible if value['backend']==desired),None)
    if profile is None:return {**device,'suitability':'unsupported','reason':'No verified runtime build for this system/architecture.','profileId':None}
    minimum=8*1024**3 if extension_id=='object' else 2*1024**3
    suitability='not_recommended' if device['ramBytes'] and device['ramBytes']<minimum else 'recommended' if extension_id!='object' or desired=='cuda128' else 'slow'
    return {**device,'suitability':suitability,'profileId':profile['id'],'backend':desired,
        'reason':'Hardware precheck only. Inspect a short real result before committing a long job.','speedEstimate':None}


def disk_budget(directory,download_bytes,installed_bytes):
    from pathlib import Path
    path=Path(directory).expanduser().resolve()
    while not path.exists() and path.parent!=path:path=path.parent
    free=shutil.disk_usage(path).free
    required=download_bytes+installed_bytes+512*1024**2
    return {'freeBytes':free,'requiredBytes':required,'sufficient':free>=required}
