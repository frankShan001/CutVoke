"""Offline audio-first tutorial. Run from a CutVoke source checkout; see README.md."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('cutvoke_demo_transport', ROOT / 'skills/cutvoke-editor/scripts/demo.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)
DURATION, RATE, PROJECT = 6, 48000, 'audio-first'


def tone(path, seconds, frequencies):
    """Original constant-amplitude tones; fades are applied by CutVoke, not baked in."""
    frames = bytearray()
    for i in range(round(seconds * RATE)):
        value = round(32767 * sum(.08 * math.sin(2 * math.pi * f * i / RATE) for f in frequencies))
        frames.extend(struct.pack('<hh', value, value))
    with wave.open(str(path), 'wb') as out:
        out.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        out.writeframes(frames)


def audio_metrics(path):
    transport.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'])
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-vn', '-ac', '1',
                                   '-ar', str(RATE), '-f', 's16le', '-'], timeout=120)
    values = [v[0] / 32768 for v in struct.iter_unpack('<h', pcm)]
    def rms(start, end):
        part = values[round(start * RATE):round(end * RATE)]
        if not part:
            raise RuntimeError('Missing decoded audio interval')
        return math.sqrt(sum(v*v for v in part) / len(part))
    def tone_amplitude(start, end, frequency):
        part = values[round(start * RATE):round(end * RATE)]
        real = sum(v * math.cos(2 * math.pi * frequency * i / RATE) for i, v in enumerate(part))
        imag = sum(v * math.sin(2 * math.pi * frequency * i / RATE) for i, v in enumerate(part))
        return 2 * math.hypot(real, imag) / len(part)
    cue_inside = tone_amplitude(2.3, 2.8, 880)
    cue_outside = tone_amplitude(1, 1.5, 880)
    if cue_inside < .015 or cue_outside > .002:
        raise RuntimeError('Expected the 880 Hz cue only in its timeline interval')
    peak = max(abs(v) for v in values)
    overall = rms(0, DURATION)
    result = {'peak': peak, 'rms': overall, 'startRms': rms(0, .1),
              'middleRms': rms(1, 1.1), 'endRms': rms(5.9, 6), 'cueAmplitude': cue_inside, 'cueOutsideAmplitude': cue_outside}
    if not .005 < overall < .5 or peak >= .98:
        raise RuntimeError(f'Silent or clipped output: {result}')
    if not (result['startRms'] < result['middleRms'] * .3 and result['endRms'] < result['middleRms'] * .3):
        raise RuntimeError(f'Expected endpoint fades: {result}')
    return result


def semantic_project(project):
    """Package restoration rewrites source paths, not the editable timeline."""
    value = copy.deepcopy(project)
    def normalize(node):
        if isinstance(node, dict):
            if isinstance(node.get('assetRef'), dict):
                node['assetRef']['sourcePath'] = '<relocated>'
            for child in node.values(): normalize(child)
        elif isinstance(node, list):
            for child in node: normalize(child)
    normalize(value)
    return value


def http(web, route, payload=None, binary=False):
    request = urllib.request.Request(web.url + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        method='PATCH' if payload is not None else 'GET',
        headers={'Content-Type': 'application/json'} if payload is not None else {})
    with web.opener.open(request, timeout=30) as response:
        return response.read() if binary else json.load(response)


def export(mcp, project, revision, path, command_id):
    submitted = mcp.call('command_apply', projectId=project, expectedRevision=revision,
        type='export.enqueue', commandId=command_id,
        payload={'outPath': str(path), 'quality': 'high', 'overwrite': False})
    mcp.wait('export_job', project, submitted['changedEntities'][0]['jobId'])
    probe = json.loads(transport.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    if abs(float(probe['format']['duration']) - DURATION) > .1 or (video['width'], video['height'], video['r_frame_rate']) != (640, 360, '30/1'):
        raise RuntimeError('Unexpected export dimensions, duration or frame rate')
    return audio_metrics(path)


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources, frames = output / 'sources', output / 'frames'
    sources.mkdir(); frames.mkdir()
    tone(sources / 'music.wav', 6, [220, 330])
    tone(sources / 'cue.wav', 1, [880])
    from PIL import Image
    Image.new('RGB', (640, 360), '#17334a').save(sources / 'card.png')
    command = [sys.executable, '-m', 'cutvoke']
    mcp = web = restored = None
    lease = None
    try:
        mcp = transport.MCP(command, output / 'data/projects.sqlite', output, None)
        if mcp.call('project_list')['projects']:
            raise RuntimeError('Expected an empty project library')
        created = mcp.call('create_project', projectId=PROJECT, name='Audio first · editable tutorial', width=640, height=360, fps=30)
        revision = created['revision']
        imported = mcp.call('media_import', projectId=PROJECT,
            paths=[str(sources / n) for n in ('card.png', 'music.wav', 'cue.wav')])['assets']
        web = transport.WebUploads(command, output / 'data/projects.sqlite', output, None)
        query = '?' + urllib.parse.urlencode({'projectId': PROJECT})
        for asset, role in zip(imported[1:], ['music', 'sound_effect']):
            route = '/api/v1/assets/' + asset['assetId']
            if http(web, route + query, {'audioRole': role})['audioRole'] != role:
                raise RuntimeError('Audio role was not saved')
            png = http(web, route + '/waveform' + query, binary=True)
            if not png.startswith(b'\x89PNG'):
                raise RuntimeError('Waveform did not return PNG')
            (frames / (role + '-waveform.png')).write_bytes(png)
            if hashlib.sha256(http(web, route + '/media' + query, binary=True)).digest() != hashlib.sha256((sources / ('music.wav' if role == 'music' else 'cue.wav')).read_bytes()).digest():
                raise RuntimeError('Audition stream differs from imported source')
        listing = http(web, '/api/v1/assets' + query)['assets']
        if {a['audioRole'] for a in listing if a['kind'] == 'audio'} != {'music', 'sound_effect'}:
            raise RuntimeError('Project-scoped role listing mismatch')
        lease = mcp.call('edit_lock', projectId=PROJECT, action='acquire', owner='audio-first-tutorial', ttlSeconds=120)['lease']['leaseId']
        rat = lambda n: {'num': str(n), 'den': '1'}
        edits = [
            {'type':'clip.insert','payload':{'trackId':'picture','createTrackKind':'video','clipId':'card','assetId':imported[0]['assetId'],'timelineStart':rat(0),'timelineEnd':rat(6)}},
            {'type':'clip.insert','payload':{'trackId':'music-track','createTrackKind':'audio','clipId':'music','assetId':imported[1]['assetId'],'timelineStart':rat(0),'timelineEnd':rat(6)}},
            {'type':'clip.audio','payload':{'clipId':'music','volume':.6,'fadeIn':.8,'fadeOut':.8}},
            {'type':'clip.insert','payload':{'trackId':'cue-track','createTrackKind':'audio','clipId':'cue','assetId':imported[2]['assetId'],'timelineStart':rat(2),'timelineEnd':rat(3)}},
            {'type':'clip.audio','payload':{'clipId':'cue','volume':.4,'fadeIn':.1,'fadeOut':.2}},
        ]
        for i, text in enumerate(['Start gently', 'A small cue', 'Leave some space']):
            edits.append({'type':'caption.add','payload':{'captionId':f'caption-{i}', 'text':text,'start':rat(2*i),'end':rat(2*i+2),'fontSize':28,'x':.5,'y':.75}})
        payload = {'commands': edits}
        mcp.call('command_preview', projectId=PROJECT, type='edit.batch', payload=payload, expectedRevision=revision)
        result = mcp.call('command_apply', projectId=PROJECT, type='edit.batch', payload=payload,
            expectedRevision=revision, commandId='assemble-audio-first', editLeaseId=lease, actorId='audio-first-tutorial')
        revision = result['revision']
        mcp.call('edit_lock', projectId=PROJECT, action='release', leaseId=lease); lease = None
        before = mcp.call('project_query', projectId=PROJECT)['project']
        mcp.call('preview_frames', projectId=PROJECT, expectedRevision=revision, times=[.5,2.5,5.5], width=640, output_frames=frames)
        audio_content = mcp.rpc('tools/call', {'name':'preview_audio','arguments':{'projectId':PROJECT,'expectedRevision':revision,'time':2.5,'radius':1}})
        if audio_content.get('isError'):
            raise RuntimeError(audio_content)
        audio = next(c for c in audio_content['content'] if c['type'] == 'audio')
        (output / 'mix-audition.wav').write_bytes(base64.b64decode(audio['data']))
        directory = mcp.call('project_directory', projectId=PROJECT)
        video = Path(directory['exportDirectory']) / 'audio-first.mp4'
        package = Path(directory['backupDirectory']) / 'audio-first.cvkpkg'
        measurements = export(mcp, PROJECT, revision, video, 'export-audio-first')
        mcp.call('project_package', projectId=PROJECT, expectedRevision=revision, outPath=str(package))
        roundtrip = output / 'roundtrip'
        transport.run([*command, 'unpack', '--package', str(package), '--dest', str(roundtrip)])
        restored_output = output / 'restored-client'; restored_output.mkdir()
        restored = transport.MCP(command, roundtrip / 'projects.sqlite', restored_output, None)
        after = restored.call('project_query', projectId=PROJECT)['project']
        if semantic_project(before) != semantic_project(after):
            raise RuntimeError('Package round trip changed editable timeline')
        restored_directory = restored.call('project_directory', projectId=PROJECT)
        restored_video = Path(restored_directory['exportDirectory']) / 'roundtrip.mp4'
        export(restored, PROJECT, revision, restored_video, 'export-restored-audio-first')
        # Compare decoded audio, not MP4 metadata or container bytes.
        def pcm(path):
            return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-vn','-f','s16le','-ac','2','-ar',str(RATE),'-'], timeout=120)
        if pcm(video) != pcm(restored_video):
            raise RuntimeError('Round-trip export audio differs')
        evidence = {'projectId':PROJECT,'revision':revision,'videoPath':str(video.relative_to(output)),
            'packagePath':str(package.relative_to(output)), 'duration':DURATION, 'fullDecode':True,
            'audio':measurements,'audioRoles':['music','sound_effect'],'waveforms':True,
            'auditionStreamMatchesSource':True,'editablePackageRoundTrip':True,'roundTripAudioMatches':True,
            'guiReview':'Not automated by this script. Open the project, play/listen and edit a caption using the README steps.',
            'listening':'Measurements only; no subjective listening claim.',
            'sources':'Original synthesized tones and a flat color card; MIT, no external media or services.'}
        (output / 'result.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print(json.dumps(evidence, indent=2))
        return evidence
    finally:
        try:
            if restored: restored.close(output / 'restored-client')
        finally:
            try:
                if mcp:
                    try:
                        if lease: mcp.call('edit_lock', projectId=PROJECT, action='release', leaseId=lease)
                    finally:
                        mcp.close(output)
            finally:
                if web: web.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New output directory; existing paths are never overwritten')
    run(parser.parse_args().output)
