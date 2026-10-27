"""A pinhole camera projecting an editable 3D image plane, with no script eval.

This deliberately keeps the editor's ordered layer compositing: planes do not
intersect through a depth buffer. Coordinates use canvas-height world units.
"""
from __future__ import annotations

import math

DEFAULTS = {"rotationX": 0., "rotationY": 0., "rotationZ": 0., "positionX": 0.,
            "positionY": 0., "positionZ": 0., "cameraX": 0., "cameraY": 0.,
            "cameraDistance": 2., "focalLength": 50.}


def spec():
    from cutvoke.rendering.effects import EffectSpec
    labels = {"rotationX": ("三维旋转 X", "3D rotation X"), "rotationY": ("三维旋转 Y", "3D rotation Y"),
              "rotationZ": ("三维旋转 Z", "3D rotation Z"), "positionX": ("平面位置 X", "Plane X"),
              "positionY": ("平面位置 Y", "Plane Y"), "positionZ": ("平面深度", "Plane depth"),
              "cameraX": ("摄像机 X", "Camera X"), "cameraY": ("摄像机 Y", "Camera Y"),
              "cameraDistance": ("摄像机距离", "Camera distance"), "focalLength": ("焦距", "Focal length")}
    def field(key, value):
        bounds = (-85,85) if key in {"rotationX","rotationY"} else (-180,180) if key=="rotationZ" else (1.1,20) if key=="cameraDistance" else (10,150) if key=="focalLength" else (-4,4)
        return {"type":"number","default":value,"minimum":bounds[0],"maximum":bounds[1],
                "unit":"deg" if key.startswith("rotation") else "mm" if key=="focalLength" else "H",
                "x-cutvoke-label":{"zh-CN":labels[key][0],"en":labels[key][1]}, "x-cutvoke-step":.1}
    properties={key:field(key,value) for key,value in DEFAULTS.items()}
    properties["poseFrames"]={"type":"array","maxItems":32,"items":{"type":"object","properties":{
        "at":{"type":"number","minimum":0,"maximum":86400},
        **{key:{k:v for k,v in schema.items() if k!='default'} for key,schema in properties.items()}},
        "required":["at"],"additionalProperties":False}}
    return EffectSpec(id="cutvoke.fx.cameraPlane", version="1.0.0", name={"zh-CN":"三维平面与摄像机","en":"3D plane and camera"},
        category="fx", parameters={"type":"object","properties":properties,"additionalProperties":False},
        implementation={"engine":"ffmpeg","filter":"cameraPlane"}, applies_to=("video","image"),
        keywords=("3D","camera","perspective","三维","摄像机","透视"),
        description={"zh-CN":"针孔摄像机投影：三轴旋转、深度、焦距和有界姿态关键点。按轨道顺序合成；不提供网格、景深或三维遮挡。",
                     "en":"Pinhole projection with rotation, depth, focal length and bounded pose keys. Composites in layer order; no meshes, depth of field or 3D occlusion."},
        license="AGPL-3.0-or-later")


def _value(params, key, time):
    base=float(params.get(key,DEFAULTS[key]))
    keys=params.get("poseFrames") or []
    if not keys:return f'{base:.12g}'
    values=[float(p.get(key,base)) for p in keys]
    result=f'{values[-1]:.12g}'
    for a,b,left,right in reversed(list(zip(keys,keys[1:],values,values[1:]))):
        change=(right-left)/(b['at']-a['at'])
        segment=f'({left:.12g}+({time}-{a["at"]:.12g})*{change:.12g})'
        result=f'if(lt({time},{b["at"]:.12g}),{segment},{result})'
    return f'if(lt({time},{keys[0]["at"]:.12g}),{values[0]:.12g},{result})'


def project_corners(params, width, height, time=0.):
    """Inspectable reference geometry; the renderer uses the same pinhole model."""
    values={key:float(_value_at(params,key,time)) for key in DEFAULTS}
    ax,ay,az=(math.radians(values[key]) for key in ('rotationX','rotationY','rotationZ'))
    result=[]
    for x,y in ((-width/height/2,-.5),(width/height/2,-.5),(-width/height/2,.5),(width/height/2,.5)):
        xr=math.cos(ay)*x+math.sin(ay)*math.sin(ax)*y
        yr=math.cos(ax)*y
        z=-math.sin(ay)*x+math.cos(ay)*math.sin(ax)*y+values['positionZ']+values['cameraDistance']
        if z<=.05:return None
        x,y=math.cos(az)*xr-math.sin(az)*yr,math.sin(az)*xr+math.cos(az)*yr
        focal=height*values['focalLength']/25
        result.append((width/2+focal*(x+values['positionX']-values['cameraX'])/z,
                       height/2+focal*(y+values['positionY']-values['cameraY'])/z))
    return result


def _value_at(params,key,time):
    base=params.get(key,DEFAULTS[key]); keys=params.get('poseFrames') or []
    if not keys:return base
    if time<=keys[0]['at']:return keys[0].get(key,base)
    if time>=keys[-1]['at']:return keys[-1].get(key,base)
    a,b=next((a,b) for a,b in zip(keys,keys[1:]) if a['at']<=time<b['at'])
    q=(time-a['at'])/(b['at']-a['at'])
    return a.get(key,base)*(1-q)+b.get(key,base)*q


def build(renderer, params, source, parts, counter):
    stem=f'camera{counter[0]}';counter[0]+=1
    fps=getattr(renderer._render_ctx,'sequence_fps',30)
    offset=getattr(renderer._render_ctx,'fx_offset',0.)
    clock=f'(on/{fps}+{offset:.12g})'
    angles=[_value(params,key,clock) for key in ('rotationX','rotationY','rotationZ')]
    depth=_value(params,'cameraDistance',clock)+'+'+_value(params,'positionZ',clock)
    # Each corner expression has its own FFmpeg expression register bank.
    prefix=';'.join(f'st({i},({angle})*PI/180)' for i,angle in enumerate(angles))
    corners=((-1,-1),(1,-1),(-1,1),(1,1))
    z=[f'({depth}-sin(ld(1))*({sx}*W/H/2)+cos(ld(1))*sin(ld(0))*({sy}*.5))' for sx,sy in corners]
    near=f'min(min({z[0]},{z[1]}),min({z[2]},{z[3]}))'
    coords=[]
    for i,(sx,sy) in enumerate(corners):
        xr=f'(cos(ld(1))*({sx}*W/H/2)+sin(ld(1))*sin(ld(0))*({sy}*.5))'
        yr=f'(cos(ld(0))*({sy}*.5))'
        rotated=(f'(cos(ld(2))*{xr}-sin(ld(2))*{yr})',f'(sin(ld(2))*{xr}+cos(ld(2))*{yr})')
        for axis,term in zip(('X','Y'),rotated):
            size='W' if axis=='X' else 'H'
            point=f'{size}/2+H*({_value(params,"focalLength",clock)})/25*({term}+({_value(params,"position"+axis,clock)})-({_value(params,"camera"+axis,clock)}))/max(.05,{z[i]})'
            identity=(0 if (sx if axis=='X' else sy)<0 else size)
            coords.append(f'{axis.lower()}{i}=\'{prefix};if(lte({near},.05),{identity},clip({point},-4*{size},5*{size}))\'')
    parts.append(f'[{source}]fps={fps},format=gbrap,pad=iw+4:ih+4:2:2:color=black@0,perspective='+
                 ':'.join(coords)+f':sense=destination:eval=frame:interpolation=linear,crop=iw-4:ih-4:2:2[{stem}projected]')
    # A plane crossing the near plane is hidden as a whole. This prevents
    # singular homographies and inverted polygons; the limit is documented.
    near_alpha=(prefix+';gt('+near+',.05)').replace('W/H','(W+4)/(H+4)').replace(clock,f'(N/{fps}+{offset:.12g})')
    parts.append(f"[{stem}projected]geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({near_alpha})'[{stem}out]")
    return stem+'out'
