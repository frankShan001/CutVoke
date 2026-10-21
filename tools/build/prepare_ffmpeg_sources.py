"""Fetch exact upstream sources, verify SHA256, and retain them for recipients."""
from pathlib import Path
import hashlib
import json
import shutil
import tarfile
import urllib.request

root = Path(__file__).resolve().parents[2]
work = root / '.artifacts' / 'ffmpeg-build'
sources = work / 'sources'
sources.mkdir(parents=True, exist_ok=True)
manifest = root / 'apps' / 'desktop' / 'ffmpeg-sources.json'
for item in json.loads(manifest.read_text('utf-8')):
    archive = sources / item['archive']
    if not archive.is_file():
        with urllib.request.urlopen(item['url'], timeout=120) as response, archive.open('wb') as target:
            shutil.copyfileobj(response, target)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != item['sha256']:
        raise RuntimeError(f"SHA256 mismatch: {item['name']}")
    target = work / item['name']
    if not target.exists():
        with tarfile.open(archive) as source:
            top = source.getmembers()[0].name.split('/')[0]
            source.extractall(work / 'unpack', filter='data')
        (work / 'unpack' / top).rename(target)
    print(f"Verified {item['name']} {item['version']} {item['sha256']}", flush=True)
shutil.copyfile(manifest, sources / manifest.name)
