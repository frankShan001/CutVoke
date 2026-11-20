"""Immutable compiled-window identity and conservative renderer admission.

The admission gate requires a verified tool/implementation namespace and retains
source-change fences. Dataclasses validate immutable shape; admit_window rejects
unsupported renderer contexts before constructing a selective recovery identity.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
import math


def _strings(value):
    return type(value) is tuple and all(type(item) is str for item in value)


@dataclass(frozen=True)
class CompiledInput:
    path: str
    digest: str
    arguments: tuple[str, ...]

    def __post_init__(self):
        if (type(self.path) is not str or not self.path
                or type(self.digest) is not str or len(self.digest) != 64
                or any(c not in '0123456789abcdef' for c in self.digest)
                or not _strings(self.arguments)):
            raise ValueError('compiled input requires an immutable path, SHA256 and exact input arguments')


@dataclass(frozen=True)
class CompiledWindowIdentity:
    graph: str
    inputs: tuple[CompiledInput, ...]
    origin: float
    window: tuple[float, float]
    frame_rate: tuple[int, int]
    size: tuple[int, int]
    working_filter: str
    working_color: str
    alpha: bool
    filter_threads: int
    output_arguments: tuple[str, ...]
    context: tuple[str, ...]

    def __post_init__(self):
        if (type(self.graph) is not str or not self.graph
                or type(self.inputs) is not tuple
                or any(type(item) is not CompiledInput for item in self.inputs)
                or type(self.window) is not tuple or len(self.window) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (self.origin, *self.window))
                or self.window[0] < 0 or self.window[1] <= self.window[0]
                or self.origin > self.window[0]):
            raise ValueError('invalid immutable compiled graph/window')
        for pair in (self.frame_rate, self.size):
            if type(pair) is not tuple or len(pair) != 2 or any(type(v) is not int or v <= 0 for v in pair):
                raise ValueError('frame rate and dimensions require positive integer tuples')
        if (type(self.working_filter) is not str or not self.working_filter
                or type(self.working_color) is not str or not self.working_color
                or type(self.alpha) is not bool or type(self.filter_threads) is not int
                or self.filter_threads <= 0 or not _strings(self.output_arguments)
                or not _strings(self.context)):
            raise ValueError('invalid immutable compiled output context')

    def key(self, verified_namespace: str) -> str:
        if type(verified_namespace) is not str or not verified_namespace:
            raise ValueError('verified namespace required')
        payload = ['compiled-window-v1', verified_namespace, asdict(self)]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def admit_window(renderer, session, seq, graph, inputs, *, cursor, duration,
                 origin, overlay_position, alpha, quality, chunk_audio, caption_cwd):
    """Return a complete narrow CPU/FFV1 identity, or None for legacy recovery.

    Capture post-compile thread context into immutable values; no pending key is
    stored on the renderer. Prepared/unknown inputs and timing contexts fall back.
    """
    if not session.compiled_enabled:
        return None
    ctx = renderer._render_ctx
    if (alpha or not inputs or chunk_audio or caption_cwd or quality not in ('high', 'medium', 'low', 'preview')
            or getattr(ctx, 'gpu', None) is not None or getattr(ctx, 'pixel_gpu', None) is not None
            or getattr(ctx, 'bounded_origin', None) is not None or getattr(ctx, 'clip_clocks', {})
            or getattr(ctx, 'preview_scale', 1) != 1
            or getattr(ctx, 'alpha', False) != alpha):
        return None
    from cutvoke.domain.rational import Rational
    for track in seq.tracks:
        if track.kind not in ('video', 'audio') or track.role:
            return None
        for clip in track.clips:
            if (clip.speed != Rational.of(1) or clip.speed_curve is not None
                    or clip.freeze_at is not None or clip.role):
                return None
    # simple_snapshot is the outer gate. These checks additionally exclude any
    # renderer-resolved input absent from that original content-hashed snapshot.
    try:
        import os
        import subprocess
        from cutvoke.rendering.render import _is_image_src, _setpts_clears_frame_rate, RenderError, RenderCancelled
        if any(clip.keyframes and not _is_image_src(clip.asset_ref.source_path)
               for track in seq.tracks for clip in track.clips):
            return None  # Non-image animated clips retain full-snapshot recovery.
        rate = seq.fps.to_fraction()
        ordered = []
        seeks = getattr(ctx, 'input_seeks', {})
        if type(seeks) is not dict or any(p not in inputs for p in seeks):
            return None
        for path in inputs:
            digest = session.source_digest(path)
            if digest is None:
                return None
            args = ['-threads', '2']
            if _is_image_src(path):
                args += ['-loop', '1', '-framerate', f'{rate.numerator}/{rate.denominator}']
            else:
                seek = seeks.get(os.path.abspath(path))
                if seek:
                    if type(seek) not in (int, float) or not math.isfinite(seek):
                        return None
                    args += ['-ss', str(seek)]
                decoder_args = renderer._video_input_arguments(path)
                if decoder_args:
                    return None  # Special decoders await production-path acceptance.
                args += decoder_args
            ordered.append(CompiledInput(path, digest, tuple(args)))
        threads = min(8, max(2, (os.cpu_count() or 2) // 2),
                      max(2, 16_777_216 // max(1, seq.width * seq.height)))
        start = cursor - origin
        # The encoder adds this clock conversion after the compiler's graph.
        outputs = ('ffv1', 'lossless', 'no-audio', 'no-concat', quality,
            f'output-start={start!r}', f'output-duration={duration!r}',
            f'trim-start-frame={math.ceil(start * float(rate) - 1e-7)}',
            f'trim-end-frame={math.ceil((start + duration) * float(rate) - 1e-7)}',
            f'setpts-clears-fps={_setpts_clears_frame_rate(renderer.ffmpeg)}')
        return CompiledWindowIdentity(graph, tuple(ordered), origin, (cursor, cursor + duration),
            (rate.numerator, rate.denominator), (seq.width, seq.height),
            renderer._working_video_filter(), getattr(ctx, 'working_color', 'bt709'),
            alpha, threads, outputs,
            ('cpu', f'overlay={tuple(overlay_position)!r}',
             f'sequence-duration={renderer._sequence_duration(seq)!r}'))
    except RenderCancelled:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, subprocess.SubprocessError, RenderError):
        return None
