"""Native, alpha-preserving slanted fragments with a deterministic motion clock."""
from __future__ import annotations

import math
import random


def schema():
    def n(default, lo, hi, cn, integer=False):
        return dict(type='integer' if integer else 'number', default=default,
                    minimum=lo, maximum=hi, description={'zh-CN': cn})
    return {
        'mode': dict(type='string', enum=['assemble', 'disperse'], default='assemble',
                     description={'zh-CN': '碎片运动方向'}, **{'x-cutvoke-enum-labels':{
                         'assemble':{'zh-CN':'聚合成形','en':'Assemble'},
                         'disperse':{'zh-CN':'飞散解构','en':'Disperse'}}}),
        'start': n(0, 0, 3600, '运动起始时间'),
        'duration': n(1, .1, 20, '运动持续时间'),
        'rows': n(12, 3, 16, '斜切条带数量', True),
        'columns': n(3, 1, 6, '条带分段数量', True),
        'angle': n(-24, -50, 50, '斜切角度'),
        'spread': n(.65, 0, 2, '碎片飞散距离'),
        'heading': n(-165, -180, 180, '飞散方向'),
        'stagger': n(.22, 0, .6, '分片启动错时比例'),
        'gap': n(3, 0, 12, '飞散时碎片间隙'),
        'scatterMode': dict(type='string',enum=['directional','radial'],default='directional',description={'zh-CN':'碎片空间分布'},
                            **{'x-cutvoke-enum-labels':{'directional':{'zh-CN':'顺向拖曳','en':'Directional'},'radial':{'zh-CN':'围绕主体','en':'Radial'}}}),
        'originX': n(.5,0,1,'主体重构中心 X'),
        'originY': n(.5,0,1,'主体重构中心 Y'),
        'fadeFragments': dict(type='boolean',default=True,description={'zh-CN':'飞散时渐隐碎片'}),
        'seed': n(17, 0, 100000, '固定变化种子', True),
    }


def build(params, cur, parts, counter, width, height):
    """Crop tight sprite bounds, retain original alpha, then move each sprite.

    The slanted cells partition a sheared coordinate system. At the end of an
    assembly the original stream replaces the sprites exactly (no residual seams).
    All phase expressions use presentation PTS, so seek and export share motion.
    """
    rows, cols = int(params.get('rows', 12)), int(params.get('columns', 3))
    slope = math.tan(math.radians(float(params.get('angle', -24))))
    rmin, rmax = min(0., -slope*width), max(float(height), height-slope*width)
    step = (rmax-rmin)/rows
    cells=[]
    for row in range(rows):
        a,b=rmin+row*step,rmin+(row+1)*step
        for col in range(cols):
            x0,x1=round(col*width/cols),round((col+1)*width/cols)
            y0=max(0, math.floor(min(a+slope*x0,a+slope*x1)))
            y1=min(height, math.ceil(max(b+slope*x0,b+slope*x1)))
            if y1-y0>2:
                cells.append((x0,y0,x1-x0,y1-y0,a,b))
    stem=f'shard{counter[0]}';counter[0]+=1
    labels=[f'{stem}_p{i}' for i in range(len(cells))]
    parts.append(f'[{cur}]scale={width}:{height},format=rgba,split={len(cells)+2}'
                 f'[{stem}_blank][{stem}_original]'+''.join(f'[{s}]' for s in labels))
    canvas=stem+'_canvas'
    parts.append(f'[{stem}_blank]colorchannelmixer=aa=0[{canvas}]')
    rng=random.Random(int(params.get('seed',17)))
    start,duration=float(params.get('start',0)),float(params.get('duration',1))
    outbound=params.get('mode','assemble')=='disperse'
    heading=math.radians(float(params.get('heading',-165)))
    spread=float(params.get('spread',.65));gap=float(params.get('gap',3))
    stagger=float(params.get('stagger',.22))
    for i,(x0,y0,w,h,a,b) in enumerate(cells):
        delay=rng.random()*stagger*duration
        def phase(clock):
            u=f'clip(({clock}-{start+delay:.9f})/{duration-delay:.9f},0,1)'
            # Short expression for once-per-frame overlay evaluation.
            q=f'(({u})*({u})*({u})*(10-15*({u})+6*({u})*({u})))'
            return f'(1-({q}))' if outbound else q
        u=f'clip((T-{start+delay:.9f})/{duration-delay:.9f},0,1)'
        q='(1-ld(1))' if outbound else 'ld(1)'
        distance=f'(Y+{y0}-({slope:.9f})*(X+{x0})-({a:.9f}))'
        visibility=f'min(1,{q}*3)' if params.get('fadeFragments',True) else '1'
        alpha=(f'st(0,{u});st(1,ld(0)*ld(0)*ld(0)*(10-15*ld(0)+6*ld(0)*ld(0)));'
               f'alpha(X,Y)*clip(({distance}-{gap:.6f}*(1-{q})),0,1)*'
               f'clip(({b-a:.9f}-{distance}-{gap:.6f}*(1-{q})),0,1)*{visibility}')
        alpha=alpha.replace(',',r'\,')
        tile=f'{stem}_tile{i}'
        parts.append(f'[{labels[i]}]crop={w}:{h}:{x0}:{y0}:exact=1,'
                     f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}'[{tile}]")
        magnitude=spread*(.35+rng.random()*.75)
        dx=math.cos(heading)*magnitude*width
        dy=math.sin(heading)*magnitude*height+(rng.random()-.5)*height*.35
        if params.get('scatterMode')=='radial':
            ox,oy=float(params.get('originX',.5))*width,float(params.get('originY',.5))*height
            dx=(x0+w/2-ox)*spread*(1.3+rng.random())+dx*.12
            dy=(y0+h/2-oy)*spread*(1.2+rng.random())+dy*.12
        f=f'(1-({phase("t")}))'
        out=f'{stem}_mix{i}'
        parts.append(f"[{canvas}][{tile}]overlay=x='{x0}+({dx:.6f})*{f}':"
                     f"y='{y0}+({dy:.6f})*{f}-35*{f}*{f}':"
                     f'eval=frame:shortest=1:eof_action=pass:format=auto[{out}]')
        canvas=out
    output=f'{stem}_out'
    enabled=f'lt(t,{start:.9f})' if outbound else f'gte(t,{start+duration:.9f})'
    parts.append(f"[{canvas}][{stem}_original]overlay=shortest=1:eof_action=pass:"
                 f"format=auto:enable='{enabled}'[{output}]")
    return output
