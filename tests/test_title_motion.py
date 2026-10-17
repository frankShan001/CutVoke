"""New title choreography must survive commands and change actual video pixels."""

import shutil
import subprocess

import numpy as np
from PIL import Image
import pytest

from cutvoke.core.caption_render import _ass_escape, _ass_color_hex_to_bgr
from cutvoke.core.model import Caption
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore
from cutvoke.core.title_motion import ENTRANCES, EXITS, title_motion_events


def events(cap, params, **extra):
    return title_motion_events(cap, {'params': params, 'fps': Rational.of(24), **extra},
                               'C0', 320, 180, font_path=None,
                               escape=_ass_escape, color=_ass_color_hex_to_bgr)


@pytest.mark.parametrize('style', ENTRANCES)
def test_new_entrances_have_distinct_early_and_settled_geometry(style):
    cap = Caption('t', 'AB', Rational.of(0), Rational.of(2),
                  animIn=500, animInStyle=style)
    lines = events(cap, {})
    assert len(lines) > 5
    assert lines[0].split(',,')[-1] != lines[-1].split(',,')[-1]


@pytest.mark.parametrize('style', EXITS)
def test_new_exits_change_geometry(style):
    cap = Caption('t', 'AB', Rational.of(0), Rational.of(2),
                  animOut=500, animOutStyle=style)
    lines = events(cap, {})
    assert len(lines) > 8
    assert lines[0].split(',,')[-1] != lines[-1].split(',,')[-1]


def test_frame_event_boundaries_have_no_gaps_and_window_uses_absolute_clock():
    cap = Caption('t', r'A{B}\C', Rational.of(1, 3), Rational.of(2),
                  animLoopStyle='wave', animLoopMs=700)
    whole = events(cap, {})
    later = events(cap, {}, window=(1., 2.))
    assert later == [line for line in whole if line.split(',')[1] >= '0:00:01.00']
    primary = [line.split(',') for line in whole if line.startswith('Dialogue: 3,')]
    # All letters share each exact frame interval, then the next frame starts.
    intervals = sorted(set((parts[1], parts[2]) for parts in primary))
    assert all(left[1] == right[0] for left, right in zip(intervals, intervals[1:]))
    assert intervals[0][0] == '0:00:00.33'
    assert intervals[-1][1] == '0:00:02.00'
    escaped = '\n'.join(whole)
    assert r'\{' in escaped and r'\}' in escaped


def read_frames(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'rawvideo',
                          '-pix_fmt', 'rgb24', 'pipe:1'],
                         capture_output=True, check=True, timeout=40).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 180, 320, 3).astype(float)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_text_keyframes_export_reload_undo_and_later_preview(tmp_path):
    Image.new('RGB', (320, 180)).save(tmp_path / 'black.png')
    serial = 0
    with ProjectStore(str(tmp_path / 'projects.sqlite')) as store:
        service = EditService(store)
        service.create_project('motion', width=320, height=180, fps=Rational.of(24))
        def command(kind, payload):
            nonlocal serial
            serial += 1
            return service.execute(Command(
                type=kind, payload=payload, command_id=f'motion-{serial}',
                project_id='motion', expected_revision=service.get_project('motion').revision,
                actor=Actor('agent', 'motion-test')))
        command('clip.insert', {
            'trackId': 'v', 'createTrackKind': 'video', 'sourcePath': str(tmp_path / 'black.png'),
            'timelineStart': Rational.of(0).to_json(), 'timelineEnd': Rational.of(2).to_json()})
        command('clip.insert', {
            'trackId': 't', 'createTrackKind': 'text', 'clipId': 'headline',
            'text': {'content': 'MOVE', 'fontSize': 140, 'bold': True,
                     'animInStyle': 'none', 'animOutStyle': 'none',
                     'strokeWidth': 0, 'shadow': 0},
            'timelineStart': Rational.of(0).to_json(), 'timelineEnd': Rational.of(2).to_json()})
        for parameter, first, last in [('x', 85, 230), ('scale', .8, 1.2),
                                       ('rotation', -10, 10), ('opacity', .45, 1)]:
            for at, value in [(0, first), (2, last)]:
                command('clip.keyframe', {'clipId': 'headline', 'param': parameter,
                                         'time': Rational.of(at).to_json(), 'value': value})
        command('effect.update', {'clipId': 'headline', 'effectId': 'cutvoke.text',
                                 'params': {'animLoopStyle': 'glitch', 'animGlow': 8,
                                            'animColor': '#ff39d4'}})
        command('history.undo', {})
        command('history.redo', {})
    with ProjectStore(str(tmp_path / 'projects.sqlite')) as store:
        project = EditService(store).get_project('motion')
        title = project.sequence.tracks[1].clips[0]
        assert len(title.keyframes['x']) == 2
        assert title.effects[0]['params']['animLoopStyle'] == 'glitch'
        renderer = RenderService()
        renderer.render(project, str(tmp_path / 'whole.mp4'), quality='high')
        renderer.render_preview_window(project, str(tmp_path / 'later.mp4'), 1., .75)
        renderer.extract_frame(project, .6, str(tmp_path / 'seek.png'))
    whole, later = read_frames(tmp_path / 'whole.mp4'), read_frames(tmp_path / 'later.mp4')
    assert len(whole) == 48 and len(later) == 18
    assert np.abs(whole[24:42] - later).mean() < 2
    seek = np.asarray(Image.open(tmp_path / 'seek.png').convert('RGB')).astype(float)
    assert np.abs(whole[14] - seek).mean() < 2, 'fractional window must retain its first title frame'
    energy = whole.sum(axis=(1, 2, 3))
    assert np.all(energy > 3000), 'ASS rounding must not hide alternating frames'
    def center(frame):
        weights = frame.mean(axis=2)
        return (weights * np.arange(320)[None, :]).sum() / weights.sum()
    assert center(whole[40]) - center(whole[5]) > 90
    red = whole[:, :, :, 0]
    green = whole[:, :, :, 1]
    assert np.count_nonzero((red > 50) & (red > green * 1.5)) > 100, 'colored glow must render'


def test_stagger_has_one_animated_background_panel():
    cap = Caption('t', 'ABCD', Rational.of(0), Rational.of(1),
                  animInStyle='stagger', animIn=300, background='#112233',
                  panelWidth=.8, panelHeight=.3)
    lines = events(cap, {})
    panel = [line for line in lines if line.startswith('Dialogue: 0,')]
    assert panel and all(r'\p1' in line for line in panel)
    intervals = [tuple(line.split(',')[1:3]) for line in panel]
    assert len(intervals) == len(set(intervals)), 'one panel per interval, not one per character'
