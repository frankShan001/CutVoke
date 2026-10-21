"""Native production evidence inside a normal, reopenable CutVoke package."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cutvoke.domain.model import Project
from cutvoke.storage.projectpack import pack_project


def write_archive(record: dict, destination: str, renderer, *, frame_times=(),
                  overwrite=False, runtime=None) -> dict:
    project = Project.from_dict(record['project'])
    output = Path(destination).expanduser().resolve()
    if output.exists() and not overwrite:
        raise ValueError('output exists; choose another outPath or explicitly set overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cutvoke-archive-', dir=output.parent) as folder:
        root = Path(folder)
        package = root / 'archive.cvkpkg'
        pack_project(project, str(package))
        evidence = {}

        def add(archive, name, data):
            archive.writestr(name, data)
            evidence[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'sizeBytes': len(data)}

        def encoded(value):
            return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

        frames = []
        with zipfile.ZipFile(package, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
            add(archive, 'production/events.jsonl', b''.join(
                (json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8')
                for event in record['events']))
            add(archive, 'production/exports.json', encoded(record['exports']))
            for index, snapshot in enumerate(record['history']):
                add(archive, f'production/history/{index:04d}.json', encoded(snapshot))
            for index, at in enumerate(frame_times):
                picture = root / f'frame-{index}.png'
                # Dense layered shots can exceed the interactive 30s budget.
                # Bound archival evidence separately; failure still leaves no
                # partial archive and never replaces an earlier delivery.
                path = renderer.extract_frame(project, at, str(picture), size=(960, 540), timeout=120)
                if path is None:
                    frames.append({'time': at, 'empty': True})
                else:
                    name = f'production/frames/{index:02d}.png'
                    add(archive, name, picture.read_bytes())
                    frames.append({'time': at, 'path': name, 'empty': False})
            manifest = {
                'format': 'cutvoke-production-archive', 'formatVersion': 1,
                'projectId': project.project_id, 'revision': project.revision,
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'eventCount': len(record['events']), 'retainedHistoryCount': len(record['history']),
                'runtime': runtime, 'frames': frames, 'files': evidence,
                'limitations': [
                    'History contains only snapshots still retained by the editor, not every past revision.',
                    'Historical snapshots and export metadata may refer to original local paths. Only the current project media is packaged.',
                    'Extracted frames are composition evidence, not a recording of the editing session.',
                ],
            }
            archive.writestr('production/manifest.json', encoded(manifest))
            archive.writestr('production/README.md', (
                '# CutVoke production archive\n\n'
                f'Project: {project.project_id}\nRevision: {project.revision}\n\n'
                'Open this .cvkpkg in CutVoke to continue editing the current snapshot.\n'
                'production/ contains actor events, retained undo/redo snapshots, export metadata, '
                'optional native preview frames and SHA-256 evidence hashes.\n\n'
                + '\n'.join('- ' + item for item in manifest['limitations']) + '\n').encode('utf-8'))
        with zipfile.ZipFile(package) as archive:
            bad = archive.testzip()
            if bad:
                raise OSError('archive integrity check failed: ' + bad)
        if overwrite:
            os.replace(package, output)
        else:
            # Same-volume hard-link publication is atomic and cannot replace a
            # file created by another archive request after the initial check.
            try:
                os.link(package, output)
            except FileExistsError as error:
                raise ValueError('output was created while archiving; choose another outPath') from error
    return {'projectId': project.project_id, 'revision': project.revision, 'path': str(output),
            'sizeBytes': output.stat().st_size, 'eventCount': len(record['events']),
            'retainedHistoryCount': len(record['history']), 'frames': frames}
