"""Freeze CutVoke worker code for downloaded Python; never bundle NN weights."""
import hashlib
import json
from pathlib import Path
import zipfile
import runpy


def prepare(root=None):
    root=Path(root or Path(__file__).resolve().parents[2]);package=root/'src'/'cutvoke'
    runpy.run_path(str(root/'tools'/'build'/'prepare_editor_guides.py'))['prepare'](root)
    directory=package/'assets'/'extension_packages';directory.mkdir(parents=True,exist_ok=True)
    archive_path=directory/'cutvoke-workers.zip';expanded=0
    with zipfile.ZipFile(archive_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        sources = [*package.rglob('*.py'), *(package/'assets'/'editor_guides').glob('*.md')]
        for source in sorted(sources):
            if '__pycache__' in source.parts:continue
            name='cutvoke/'+source.relative_to(package).as_posix();payload=source.read_bytes();expanded+=len(payload)
            entry=zipfile.ZipInfo(name,(2024,1,1,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(entry,payload)
        license_path=root/'LICENSE'
        if license_path.is_file():
            payload=license_path.read_bytes();expanded+=len(payload);entry=zipfile.ZipInfo('cutvoke/WORKER-LICENSE.txt',(2024,1,1,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(entry,payload)
    path=package/'catalogs'/'optional_extensions.json';catalog=json.loads(path.read_text('utf8'))
    previous={key for key,value in catalog['artifacts'].items() if value.get('kind')=='worker-code'}
    for key in previous:del catalog['artifacts'][key]
    digest=hashlib.sha256(archive_path.read_bytes()).hexdigest();identity=digest[:16]
    catalog['artifacts'][identity]={'id':identity,'filename':archive_path.name,'kind':'worker-code','bundled':archive_path.name,'sizeBytes':archive_path.stat().st_size,'installedBytes':expanded,'sha256':digest,'sources':[]}
    for profile in catalog['profiles']:profile['artifacts']=[key for key in profile['artifacts'] if key not in previous]+[identity]
    path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return {'path':str(archive_path),'sizeBytes':archive_path.stat().st_size,'sha256':digest}


if __name__=='__main__':print(json.dumps(prepare()))
