"""Original, deterministic noise, warp, displacement and editable vector tools."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageColor, ImageFilter

VERSION = 1
PROCEDURAL = {"cutvoke.fx.fractalNoise", "cutvoke.fx.turbulentDisplace", "cutvoke.fx.vectorPath", "cutvoke.fx.trackedMask", "cutvoke.fx.scene3d"}


def specs():
    from cutvoke.rendering.effects import EffectSpec
    from cutvoke.rendering.operator_descriptions import describe
    def num(default, low, high):
        return {"type": "number", "default": default, "minimum": low, "maximum": high}
    def integer(default, low, high):
        return {"type": "integer", "default": default, "minimum": low, "maximum": high}
    noise = {"seed": integer(7, 0, 1000000), "scale": num(.12, .005, 2), "octaves": integer(4, 1, 6), "evolution": num(.25, -10, 10)}
    point = {"type": "object", "properties": {key: {"type": "number", "minimum": -4, "maximum": 4}
             for key in ("x", "y", "inX", "inY", "outX", "outY")}, "required": ["x", "y"], "additionalProperties": False}
    values = [
        ("fractalNoise", "分形噪声", "Fractal noise", {**noise, "contrast": num(1, .1, 8), "mix": num(.5, 0, 1)}),
        ("turbulentDisplace", "湍流置换", "Turbulent displacement", {**noise, "amount": num(24, 0, 128)}),
        ("waveWarp", "波浪扭曲", "Wave warp", {"amplitude": num(12, 0, 128), "wavelength": num(120, 4, 4096),
            "frequency": num(.5, -10, 10), "phase": num(0, -360, 360), "axis": {"type": "string", "enum": ["x", "y"], "default": "x"}}),
        ("trackedMask", "光流路径蒙版", "Optical-flow path matte", {
            "points": {"type": "array", "minItems": 3, "maxItems": 64, "items": {"type": "object", "properties": {"x": num(.5,0,1), "y": num(.5,0,1)}, "required": ["x","y"], "additionalProperties": False},
                       "default": [{"x":.2,"y":.2},{"x":.8,"y":.2},{"x":.8,"y":.8},{"x":.2,"y":.8}]},
            "pathFrames": {"type": "array", "maxItems": 256, "items": {"type": "object", "properties": {
                "at": num(0,0,86400), "confidence": num(1,0,1), "points": {"type": "array", "minItems": 3, "maxItems": 64,
                    "items": {"type": "object", "properties": {"x": num(.5,0,1), "y": num(.5,0,1)}, "required": ["x","y"], "additionalProperties": False}}},
                "required": ["at","points"], "additionalProperties": False}},
            "feather": num(.01,0,.5), "invert": {"type":"boolean","default":False}}),
        ("vectorPath", "矢量路径", "Vector path", {
            "points": {"type": "array", "minItems": 2, "maxItems": 64, "items": point,
                       "default": [{"x": .15, "y": .75}, {"x": .5, "y": .15}, {"x": .85, "y": .75}]},
            "closed": {"type": "boolean", "default": True}, "strokeColor": {"type": "string", "default": "#ffffff"},
            "fillColor": {"type": "string", "default": "#2277ee"}, "strokeWidth": num(.008, 0, .2), "fillOpacity": num(.3, 0, 1),
            "trimStart": num(0, 0, 1), "trimEnd": num(1, 0, 1),
            "trimFrames": {"type": "array", "maxItems": 256, "items": {"type": "object", "properties": {
                "at": num(0, 0, 86400), "start": num(0, 0, 1), "end": num(1, 0, 1)}, "required": ["at", "end"], "additionalProperties": False}},
            "repeaterCount": integer(1, 1, 64), "offsetX": num(.03, -2, 2), "offsetY": num(0, -2, 2),
            "repeaterRotation": num(0, -360, 360), "repeaterScale": num(1, .5, 1.5)}),
    ]
    from cutvoke.rendering.camera_plane import spec as camera_spec
    from cutvoke.rendering.scene3d import spec as scene_spec
    return [camera_spec(),scene_spec()] + [EffectSpec(id="cutvoke.fx." + key, version="1.0.0", name={"zh-CN": cn, "en": en}, category="fx",
             parameters={"type": "object", "properties": describe(props), "additionalProperties": False},
             implementation={"engine": "ffmpeg", "filter": key}, applies_to=("video", "image"),
             keywords=(key, en, cn), description={"zh-CN": "可组合基础算子；固定种子、归一化几何，与预览和导出共用实现。", "en": "Composable deterministic operator with bounded parameters and shared preview/export rendering."},
             license="AGPL-3.0-or-later") for key, cn, en, props in values]


def _hash(x, y, seed):
    value = np.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453123
    return value - np.floor(value)


def _noise(width, height, params, time, seed_offset=0):
    yy, xx = np.mgrid[:height, :width].astype(np.float32)
    scale = max(1., min(width, height) * params.get("scale", .12))
    evolution = time * params.get("evolution", .25)
    seed = params.get("seed", 7) + seed_offset
    result = np.zeros((height, width), np.float32)
    total = 0.
    for octave in range(int(params.get("octaves", 4))):
        frequency = 2 ** octave
        x, y = xx / scale * frequency, yy / scale * frequency
        ix, iy = np.floor(x), np.floor(y)
        fx, fy = x - ix, y - iy
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        t = evolution * .73 ** octave
        phase, fractional = math.floor(t), t - math.floor(t)
        fractional = fractional * fractional * (3 - 2 * fractional)
        def sample(z):
            a = _hash(ix, iy, seed + octave * 131 + z * 17)
            b = _hash(ix + 1, iy, seed + octave * 131 + z * 17)
            c = _hash(ix, iy + 1, seed + octave * 131 + z * 17)
            d = _hash(ix + 1, iy + 1, seed + octave * 131 + z * 17)
            return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
        weight = .5 ** octave
        result += (sample(phase) * (1 - fractional) + sample(phase + 1) * fractional) * weight
        total += weight
    return result / total


def frame(params, width, height, time):
    kind = params.get("_operator", "fractalNoise")
    if kind == "vectorPath":
        return vector_frame(params, width, height, time)
    if kind == 'trackedMask':
        return tracked_mask_frame(params, width, height, time)
    if kind == 'scene3d':
        from cutvoke.rendering.scene3d import frame as render_scene
        return render_scene(params,width,height,time)
    noise = _noise(width, height, params, time)
    result = np.full((height, width, 4), 255, np.uint8)
    if kind == "turbulentDisplace":
        amount = float(params.get("amount", 24))
        result[..., 0] = np.clip(128 + (noise - .5) * 2 * amount, 0, 255).round().astype(np.uint8)
        result[..., 1] = np.clip(128 + (_noise(width, height, params, time, 913) - .5) * 2 * amount, 0, 255).round().astype(np.uint8)
        result[..., 2] = 128
    else:
        grayscale = np.clip((noise - .5) * float(params.get("contrast", 1)) + .5, 0, 1)
        result[..., :3] = (grayscale[..., None] * 255).round().astype(np.uint8)
    return Image.fromarray(result)


def tracked_mask_frame(params,width,height,time):
    points = params.get('points') or [{'x':.2,'y':.2},{'x':.8,'y':.2},{'x':.8,'y':.8},{'x':.2,'y':.8}]
    keys = params.get('pathFrames') or []
    if keys:
        if any(keys[i]['at'] >= keys[i+1]['at'] for i in range(len(keys)-1)):
            raise ValueError('pathFrames times must strictly increase')
        if any(len(key['points']) != len(keys[0]['points']) for key in keys):
            raise ValueError('tracked mask vertex count must remain constant')
        if time <= keys[0]['at']: points=keys[0]['points']
        elif time >= keys[-1]['at']: points=keys[-1]['points']
        else:
            a,b=next((a,b) for a,b in zip(keys,keys[1:]) if a['at'] <= time < b['at'])
            q=(time-a['at'])/(b['at']-a['at'])
            points=[{axis:left[axis]*(1-q)+right[axis]*q for axis in ('x','y')} for left,right in zip(a['points'],b['points'])]
    mask=Image.new('L',(width,height));ImageDraw.Draw(mask).polygon([(round(point['x']*width),round(point['y']*height)) for point in points],fill=255)
    feather=float(params.get('feather',.01))*min(width,height)
    if feather: mask=mask.filter(ImageFilter.GaussianBlur(feather))
    if params.get('invert'): mask=Image.fromarray(255-np.asarray(mask))
    result=Image.new('RGBA',(width,height),(255,255,255,0));result.putalpha(mask)
    return result


def vector_frame(params, width, height, time):
    colour = ImageColor.getrgb
    source = params.get("points") or [{"x": .15, "y": .75}, {"x": .5, "y": .15}, {"x": .85, "y": .75}]
    source = [dict(point) for point in source]
    if params.get("closed", True): source.append(source[0])
    points = []
    for a, b in zip(source, source[1:]):
        p0 = np.array([a["x"], a["y"]]); p3 = np.array([b["x"], b["y"]])
        p1 = p0 + np.array([a.get("outX", 0), a.get("outY", 0)])
        p2 = p3 + np.array([b.get("inX", 0), b.get("inY", 0)])
        for t in np.linspace(0, 1, 49, endpoint=False):
            p = (1-t)**3*p0 + 3*(1-t)**2*t*p1 + 3*(1-t)*t*t*p2 + t**3*p3
            points.append(p * [width, height])
    points.append(np.array([source[-1]["x"] * width, source[-1]["y"] * height]))
    points = np.asarray(points)
    distance = np.concatenate(([0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))))
    length = max(1e-9, distance[-1])
    start, end = float(params.get("trimStart", 0)), float(params.get("trimEnd", 1))
    keys = sorted(params.get("trimFrames") or [], key=lambda key: key["at"])
    if keys:
        start = float(np.interp(time, [key["at"] for key in keys], [key.get("start", start) for key in keys]))
        end = float(np.interp(time, [key["at"] for key in keys], [key["end"] for key in keys]))
    selected = points[(distance >= start * length) & (distance <= end * length)]
    if end > start:
        endpoints = np.array([[np.interp(at * length, distance, points[:, axis]) for axis in range(2)] for at in (start, end)])
        selected = np.concatenate((endpoints[:1], selected, endpoints[1:]))
    result = Image.new("RGBA", (width, height))
    if end <= start or len(selected) < 2: return result
    for index in range(int(params.get("repeaterCount", 1)) - 1, -1, -1):
        angle = math.radians(params.get("repeaterRotation", 0) * index)
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        coords = ((selected - [width/2, height/2]) @ rotation.T * params.get("repeaterScale", 1)**index +
                  [width/2 + width * params.get("offsetX", .03) * index, height/2 + height * params.get("offsetY", 0) * index])
        coords = np.clip(coords, -100000, 100000)
        drawing = Image.new("RGBA", result.size); draw = ImageDraw.Draw(drawing)
        if start == 0 and end == 1 and params.get("closed", True):
            draw.polygon([tuple(point) for point in coords], fill=(*colour(params.get("fillColor", "#2277ee")), round(params.get("fillOpacity", .3) * 255)))
        stroke = max(0, round(params.get("strokeWidth", .008) * min(width, height)))
        if stroke: draw.line([tuple(point) for point in coords], fill=(*colour(params.get("strokeColor", "#ffffff")), 255), width=stroke, joint="curve")
        result = Image.alpha_composite(result, drawing)
    return result


def build(renderer, key, params, source, parts, counter):
    stem = f"operator{counter[0]}"; counter[0] += 1
    fps = getattr(renderer._render_ctx, 'sequence_fps', 30)
    parts.append(f"[{source}]fps={fps}[{stem}clock]")
    source = stem + "clock"
    if key == "waveWarp":
        amplitude, wavelength = float(params.get("amplitude", 12)), float(params.get("wavelength", 120))
        coordinate = "Y" if params.get("axis", "x") == "x" else "X"
        cw, ch = getattr(renderer._render_ctx,'canvas_width',1920), getattr(renderer._render_ctx,'canvas_height',1080)
        amplitude_unit = f'W/{cw}' if coordinate == 'Y' else f'H/{ch}'
        wavelength_unit = f'{ch}/H' if coordinate == 'Y' else f'{cw}/W'
        wave = f"{amplitude:.9f}*({amplitude_unit})*sin(2*PI*({coordinate}*({wavelength_unit})/{wavelength:.9f}+T*{float(params.get('frequency', .5)):.9f})+{math.radians(params.get('phase', 0)):.9f})"
        x, y = (f"clip(X+{wave},0,W-1)", "Y") if coordinate == "Y" else ("X", f"clip(Y+{wave},0,H-1)")
        parts.append(f"[{source}]format=gbrap,geq=r='r({x},{y})':g='g({x},{y})':b='b({x},{y})':a='alpha({x},{y})'[{stem}out]")
        return stem + "out"
    path = params.get("_renderedLightPath")
    if not path:
        raise ValueError(f"{key} requires the prepared procedural source")
    escaped = str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    offset = getattr(renderer._render_ctx, "fx_offset", 0.)
    parts.append(f"movie=filename='{escaped}':dec_threads=2,setpts=PTS-STARTPTS+{offset:.12f}/TB,fps={fps}[{stem}map]")
    parts.append(f'[{source}]format=gbrap,split=2[{stem}picture][{stem}reference]')
    parts.append(f'[{stem}map]format=gbrap[{stem}mapformat]')
    parts.append(f'[{stem}mapformat][{stem}reference]scale2ref=w=iw:h=ih[{stem}resized][{stem}unused]')
    parts.append(f'[{stem}unused]nullsink')
    source = stem + 'picture'
    map_label = stem + 'resized'
    if key == "turbulentDisplace":
        cw, ch = getattr(renderer._render_ctx,'canvas_width',1920), getattr(renderer._render_ctx,'canvas_height',1080)
        parts.append(f"[{map_label}]format=gbrp,lutrgb=r='clip(128+(val-128)*w/{cw},0,255)':g='clip(128+(val-128)*h/{ch},0,255)',split=2[{stem}mx][{stem}my]")
        # Displace evaluates every plane, including alpha. A grey map promoted
        # by swscale would get opaque alpha=255 and shift all alpha samples
        # outside a small source. Give alpha the same displacement as RGB.
        for label,plane in (('x','r'),('y','g')):
            input_label=stem+('mx' if label=='x' else 'my')
            parts.append(f"[{input_label}]extractplanes={plane},format=gbrap,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='r(X,Y)'[{stem}{label}]")
        parts.append(f"[{source}]format=gbrap[{stem}src]")
        parts.append(f"[{stem}src][{stem}x][{stem}y]displace=edge=smear[{stem}out]")
    elif key == "fractalNoise":
        opacity = float(params.get("mix", .5))
        parts.append(f"[{source}]format=gbrap[{stem}src]")
        parts.append(f"[{map_label}]format=gbrap[{stem}noise]")
        formula = f"A*(1-{opacity:.9f})+B*{opacity:.9f}"
        parts.append(f"[{stem}src][{stem}noise]blend=c0_expr='{formula}':c1_expr='{formula}':c2_expr='{formula}':c3_expr='A':shortest=0:eof_action=repeat[{stem}out]")
    elif key == 'trackedMask':
        parts.append(f'[{source}]format=gbrap,split=2[{stem}rgb][{stem}alpha]')
        parts.append(f'[{stem}alpha]alphaextract[{stem}original]')
        parts.append(f'[{map_label}]format=gbrap,alphaextract[{stem}coverage]')
        parts.append(f"[{stem}original][{stem}coverage]blend=all_expr='A*B/255':shortest=0:eof_action=repeat[{stem}masked]")
        parts.append(f'[{stem}rgb][{stem}masked]alphamerge[{stem}out]')
    else:
        parts.append(f"[{source}][{map_label}]overlay=shortest=0:eof_action=repeat:format=auto[{stem}out]")
    return stem + "out"
