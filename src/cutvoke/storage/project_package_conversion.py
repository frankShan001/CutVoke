"""Explicit legacy-package conversion; original archives are never rewritten."""
from __future__ import annotations
import hashlib
import json
import os
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



def _publish_new_copy(stage, destination):
    """Exclusive files; failed publication removes only objects we created.

    Never replace a caller/concurrent writer's directory, file or symlink.
    Publication stays unregistered until the caller receives a complete copy.
    """
    target = Path(destination).expanduser().absolute()
    for path in (target, *target.parents):
        if path.is_symlink():
            raise ProjectPackError('Conversion destination cannot contain symlinks')
    if target.exists():
        raise ProjectPackError('Conversion requires a new destination directory')
    directories, files = [], []
    try:
        absent = []
        parent = target.parent
        while not parent.exists():
            absent.append(parent)
            parent = parent.parent
        for directory in [*reversed(absent), target]:
            directory.mkdir()  # exclusive; never accept a concurrent creation
            directories.append((directory, directory.stat()))
        for source in sorted(Path(stage).rglob('*')):
            output = target / source.relative_to(stage)
            # Reject changed parent identities before creating directories or
            # opening leaves; a replaced parent must not redirect extraction.
            for directory, identity in directories:
                now = directory.lstat()
                if directory.is_symlink() or (now.st_dev,now.st_ino)!=(identity.st_dev,identity.st_ino):
                    raise ProjectPackError('Conversion destination changed during publication')
            if source.is_dir():
                output.mkdir()
                directories.append((output, output.stat()))
            else:
                with output.open('xb') as outgoing:
                    identity = os.fstat(outgoing.fileno())
                    files.append((output, identity))
                    with source.open('rb') as incoming:
                        shutil.copyfileobj(incoming,outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
    except BaseException:
        for output, identity in reversed(files):
            try:
                now=output.lstat()
                if (now.st_dev,now.st_ino)==(identity.st_dev,identity.st_ino):output.unlink()
            except OSError:pass
        for directory, identity in reversed(directories):
            try:
                now=directory.lstat()
                if (now.st_dev,now.st_ino)==(identity.st_dev,identity.st_ino):directory.rmdir()
            except OSError:pass  # retain any concurrent writer's files
        raise
    return target

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
            from cutvoke.storage.generated_sources import _portable_name
            destinations={}
            original_paths={}
            for name in archive.namelist():
                try:_portable_name(name)
                except ValueError as error:raise ProjectPackError(str(error)) from error
            for item in [*manifest.get('assets',[]),*manifest.get('luts',[]),*manifest.get('effectFiles',[])]:
                if not isinstance(item,dict):
                    raise ProjectPackError('Invalid media manifest entry')
                name=item.get('packagePath','')
                original=item.get('originalPath')
                if not isinstance(original,str) or not original:
                    raise ProjectPackError('Invalid original media path')
                identity=(name,item.get('sha256'))
                if original in original_paths and original_paths[original]!=identity:
                    raise ProjectPackError('Conflicting original media mappings')
                original_paths[original]=identity
                if not isinstance(name,str) or not name.startswith('media/') or "\\" in name:
                    raise ProjectPackError('Invalid media package path')
                target=Path(name).name.casefold()
                previous=destinations.setdefault(target,name)
                if previous!=name:
                    raise ProjectPackError('Media names collide at extraction destination')
        stage=Path(temporary)/'verified'
        project,mapping,warnings=unpack_project(str(snapshot),str(stage),_convert_legacy=True)
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
        target=Path(destination).expanduser().absolute()
        from cutvoke.storage.project_directory import _map_paths
        from cutvoke.domain.model import Project
        def relocate(value):
            path=Path(value)
            return str(target/path.relative_to(stage)) if path.is_absolute() and path.is_relative_to(stage) else value
        project=Project.from_dict(_map_paths(project.to_dict(),relocate))
        mapping={original:relocate(value) for original,value in mapping.items()}
        _publish_new_copy(stage,target)
        return project,mapping,warnings
