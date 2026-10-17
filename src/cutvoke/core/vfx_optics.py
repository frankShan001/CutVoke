"""Editable source-attached diffraction, directional light shafts and hot debris."""
from __future__ import annotations

import math
import random

from .vfx_light import anchor, schema as light_schema

VERSION = 2


def schema():
    base = light_schema()
    keys = ('palette', 'anchorX', 'anchorY', 'anchorFrames', 'start', 'duration', 'intensity', 'seed')
    result = {k: base[k] for k in keys}
    def n(default, lo, hi, cn, integer=False):
        return dict(type='integer' if integer else 'number', default=default,
                    minimum=lo, maximum=hi, description={'zh-CN': cn})
    result.update({
        'layer': dict(type='string', enum=['flare','rays','sparks','all'], default='all',
            description={'zh-CN':'光学空间层'}, **{'x-cutvoke-enum-labels': {
                'flare':{'zh-CN':'光谱衍射','en':'Spectral diffraction'},
                'rays':{'zh-CN':'定向光束','en':'Directional shafts'},
                'sparks':{'zh-CN':'惯性火花','en':'Inertial sparks'},
                'all':{'zh-CN':'完整光学场','en':'Complete optical field'}}}),
        'angle': n(-12,-180,180,'光束与火花方向'),
        'spread': n(.9,.1,2,'光学场延伸距离'),
        'rays': n(20,3,48,'定向光束数量',True),
        'streaks': n(6,2,16,'衍射光谱条数',True),
        'particles': n(70,0,160,'惯性火花数量',True),
        'pulseHz': n(2.2,0,12,'光学亮度脉冲'),
    })
    return result


def frame(params, width, height, time):
    from PIL import Image, ImageDraw, ImageFilter
    from .mask_motion import progress
    start = float(params.get('start',0));duration = float(params.get('duration',1.7))
    q = (time-start)/duration
    out = Image.new('RGBA',(width,height))
    if not 0 < q < 1:
        return out
    phase = progress(time,start,duration,'minimum-jerk')
    fade = math.sin(math.pi*q)**.65
    fade *= .72 + .28 * math.cos(time*math.tau*float(params.get('pulseHz',2.2)))**2
    fade *= float(params.get('intensity',1))
    rng = random.Random(int(params.get('seed',31)))
    palette = {'cyan-gold':[(40,190,255),(154,234,255),(255,180,74)],
               'blue-violet':[(56,177,255),(173,108,255),(255,119,186)],
               'amber':[(255,130,37),(255,203,106),(255,238,197)]}[params.get('palette','cyan-gold')]
    x,y = anchor(params,time);cx,cy=x*width,y*height
    angle=math.radians(float(params.get('angle',-12)))
    span=width*float(params.get('spread',.9));layer=params.get('layer','all')
    core=Image.new('RGBA',out.size);draw=ImageDraw.Draw(core)
    def stroke(points, colour, alpha, weight=1):
        draw.line(points, fill=(*colour,max(0,min(255,round(alpha*fade)))), width=max(1,round(weight*width/1024)))
    if layer in ('all','rays'):
        for i in range(int(params.get('rays',20))):
            a=angle+rng.uniform(-.23,.23)+.018*math.sin(time*.8+i)
            length=span*rng.uniform(.45,1.1);jitter=rng.uniform(-4,4)*height/576
            for s in range(8):
                r0=s/8;r1=(s+1)/8
                stroke([(cx+math.cos(a)*length*r0,cy+math.sin(a)*length*r0+jitter),
                        (cx+math.cos(a)*length*r1,cy+math.sin(a)*length*r1+jitter)],
                       palette[i%3],18*(1-r0)**1.6,1+rng.random()*2)
    if layer in ('all','flare'):
        for i in range(int(params.get('streaks',6))):
            offset=(i-(int(params.get('streaks',6))-1)/2)*height*.004
            length=span*(.25+.75*math.sin(math.pi*phase))*rng.uniform(.7,1)
            a=angle*.14
            for s in range(-8,8):
                r0=s/8;r1=(s+1)/8;alpha=110*(1-abs((r0+r1)/2))**2
                stroke([(cx+math.cos(a)*length*r0,cy+math.sin(a)*length*r0+offset),
                        (cx+math.cos(a)*length*r1,cy+math.sin(a)*length*r1+offset)],
                       palette[i%3],alpha,1 if i%2 else 2)
        # Soft source bloom has no visible circular boundary or geometric frame.
        r=width*.007
        source=Image.new('L',out.size)
        ImageDraw.Draw(source).ellipse((cx-r,cy-r,cx+r,cy+r),fill=min(255,round(140*fade)))
        source=source.filter(ImageFilter.GaussianBlur(max(1,width*.011)))
        bloom=Image.new('RGBA',out.size,(230,249,255,0));bloom.putalpha(source)
        out=Image.alpha_composite(out,bloom)
    if layer in ('all','sparks'):
        for i in range(int(params.get('particles',70))):
            born=start+rng.random()*duration*.8;age=time-born
            a=angle+rng.uniform(-.35,.35);velocity=width*rng.uniform(.15,.9)
            life=rng.uniform(.18,.65);depth=rng.uniform(.4,1)
            if not 0<age<life:
                continue
            bx,by=anchor(params,born)
            distance=velocity*(1-math.exp(-3.2*age))/3.2
            px=bx*width+math.cos(a)*distance
            py=by*height+math.sin(a)*distance+height*.28*age*age
            trail=width*.009*depth*(1-age/life)
            stroke([(px,py),(px-math.cos(a)*trail,py-math.sin(a)*trail)],
                   palette[i%3],220*(1-age/life)*depth,depth*1.7)
    # Blur alpha only, retaining luminous straight RGB in the transparent halo.
    halo=core.getchannel('A').filter(ImageFilter.GaussianBlur(max(.5,width*.006)))
    glow=Image.new('RGBA',out.size,(*palette[0],0));glow.putalpha(halo)
    out=Image.alpha_composite(out,glow)
    return Image.alpha_composite(out,core)
