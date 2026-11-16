"""Explicit legacy-package conversion; original archives are never rewritten."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from cutvoke.storage.project_format import inspect_snapshot, ProjectFormat, ProjectFormatError
from cutvoke.storage.projectpack import ProjectPackError, check_compatibility, unpack_project


def _digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            result.update(chunk)
    return result.hexdigest()


def inspect_package_format(path):
    from cutvoke.storage.generated_sources import _unique
    source=Path(path).expanduser().resolve()
    if not source.is_file():
        raise ProjectPackError('Package does not exist')
    token=_digest(source)
    with zipfile.ZipFile(source) as archive:
        if len(archive.namelist())!=len(set(archive.namelist())):
            raise ProjectPackError('Duplicate package members')
        data={}
        for name in ('manifest.json','project.json'):
            if archive.getinfo(name).file_size>16*1024*1024:
                raise ProjectPackError('Package metadata exceeds 16 MiB')
            data[name]=json.loads(archive.read(name),object_pairs_hook=_unique)
    check_compatibility(data['manifest.json'])
    try:
        kind=inspect_snapshot(data['project.json'])
    except ProjectFormatError as error:
        raise ProjectPackError(str(error)) from error
    current=kind is ProjectFormat.CURRENT
    document=data['project.json']['document'] if current else data['project.json']
    if current and data['manifest.json'].get('packageFormat')!=5:
        raise ProjectPackError('New project requires package format 5')
    if not current and data['manifest.json'].get('packageFormat',1)>4:
        raise ProjectPackError('Legacy project/package version mismatch')
    if _digest(source)!=token:
        raise ProjectPackError('Package changed during inspection')
    return {'status':'current' if current else 'legacy','sourceVersion':2 if current else 1,
            'targetVersion':2,'token':token,'projectId':document['projectId'],
            'name':document.get('name') or document['projectId']}


def convert_package_copy(path,token,destination):
    """Caller obtains explicit Convert choice; never registered on failed checks."""
    source=Path(path).expanduser().resolve()
    plan=inspect_package_format(source)
    if plan['status']!='legacy' or not isinstance(token,str) or plan['token']!=token:
        raise ProjectPackError('Package changed; inspect and choose conversion again')
    # Hold an immutable temporary snapshot while ZIP preflight/extraction runs.
    with tempfile.TemporaryDirectory(prefix='cutvoke-package-conversion-') as temporary:
        snapshot=Path(temporary)/'source.zip'
        shutil.copyfile(source,snapshot)
        if _digest(snapshot)!=token:
            raise ProjectPackError('Package changed while copying')
        with zipfile.ZipFile(snapshot) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            destinations={}
            for item in [*manifest.get('assets',[]),*manifest.get('luts',[]),*manifest.get('effectFiles',[])]:
                name=item.get('packagePath','')
                if not isinstance(name,str) or not name.startswith('media/') or "\\" in name:
                    raise ProjectPackError('Invalid media package path')
                target=Path(name).name.casefold()
                previous=destinations.setdefault(target,name)
                if previous!=name:
                    raise ProjectPackError('Media names collide at extraction destination')
        project,mapping,warnings=unpack_project(str(snapshot),str(destination),_convert_legacy=True)
        # A normal package may allow recovery warnings; conversion cannot mark
        # an incomplete or checksum-mismatched copy successful.
        if warnings:
            raise ProjectPackError('Conversion did not produce a complete verified copy: '+'; '.join(warnings))
        from cutvoke.domain.invariants import validate_project
        result=validate_project(project)
        if not result.structurally_valid or not result.renderable:
            raise ProjectPackError('Converted project failed validation: '+'; '.join(result.errors+result.warnings))
        if _digest(source)!=token:
            raise ProjectPackError('Original package changed during conversion; copy not registered')
        return project,mapping,warnings
