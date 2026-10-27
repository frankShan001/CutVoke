"""Deterministic, editable light filaments rendered as lossless RGBA cache media.

The cache is disposable. Project authority remains the effect parameters, including
subject-attached anchor samples. Front/back layers can straddle a tracked cutout.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import time
import uuid

from cutvoke.rendering.mask_motion import progress

VERSION=4


def schema():
    from cutvoke.rendering.vfx_fusion import schema as fusion_schema
    def n(default,lo,hi,cn,integer=False):
        return dict(type='integer' if integer else 'number',default=default,
                    minimum=lo,maximum=hi,description={'zh-CN':cn})
    return {
        **fusion_schema(),
        'layer': dict(type='string',enum=['back','front','reflection','all'],default='back',
                      description={'zh-CN':'光流空间层次'},**{'x-cutvoke-enum-labels':{
                          'back':{'zh-CN':'车身后方','en':'Behind subject'},
                          'front':{'zh-CN':'车身前方','en':'In front of subject'},
                          'reflection':{'zh-CN':'地面反射','en':'Ground reflection'},
                          'all':{'zh-CN':'完整光流','en':'Complete light flow'}}}),
        'palette': dict(type='string',enum=['cyan-gold','blue-violet','amber'],default='cyan-gold',
                        description={'zh-CN':'光流色彩'},**{'x-cutvoke-enum-labels':{
                            'cyan-gold':{'zh-CN':'青蓝与琥珀','en':'Cyan and amber'},
                            'blue-violet':{'zh-CN':'蓝紫辉光','en':'Blue and violet'},
                            'amber':{'zh-CN':'暖金光流','en':'Amber'}}}),
        'anchorX':n(.55,0,1,'机车光源位置 X'),
        'anchorY':n(.5,0,1,'机车光源位置 Y'),
        'span':n(1.5,.2,3,'光流延伸范围'),
        'amplitude':n(.18,.01,.5,'光流环绕高度'),
        'tilt':n(-.12,-1,1,'光流倾斜'),
        'phase':n(0,-10,10,'环绕相位'),
        'filaments':n(14,3,28,'光丝数量',True),
        'intensity':n(1,0,3,'光流亮度'),
        'start':n(0,0,3600,'光流启动时间'),
        'duration':n(1.7,.1,20,'光流穿越时间'),
        'groundY':n(.78,0,1,'地面反射位置'),
        'seed':n(31,0,100000,'固定变化种子',True),
        'anchorFrames': dict(type='array',maxItems=256,items=dict(type='object',
            properties={'time':dict(type='number',minimum=0,maximum=3600),
                        'x':dict(type='number',minimum=0,maximum=1),
                        'y':dict(type='number',minimum=0,maximum=1)},
            required=['time','x','y'],additionalProperties=False),
            description={'zh-CN':'逐帧机车附着点；时间为片段内秒数'}),
    }


def anchor(params,time):
    frames=params.get('anchorFrames',[])
    if not frames:return float(params.get('anchorX',.55)),float(params.get('anchorY',.5))
    if time<=frames[0]['time']:return frames[0]['x'],frames[0]['y']
    for a,b in zip(frames,frames[1:]):
        if time<=b['time']:
            q=(time-a['time'])/(b['time']-a['time'])
            return a['x']+(b['x']-a['x'])*q,a['y']+(b['y']-a['y'])*q
    return frames[-1]['x'],frames[-1]['y']


def frame(params,width,height,time):
    from PIL import Image,ImageDraw,ImageFilter
    import numpy as np
    from cutvoke.rendering.vfx_fusion import blur_alpha
    start,duration=float(params.get('start',0)),float(params.get('duration',1.7))
    fade=max(0.,min(1.,(time-start)/.10,(start+duration+.35-time)/.35))
    empty=Image.new('RGBA',(width,height))
    if not fade or not params.get('intensity',1):return empty
    q=progress(time,start,duration,'minimum-jerk')
    head=-.15+1.40*q
    cx,cy=anchor(params,time);cx*=width;cy*=height
    span=float(params.get('span',1.5))*width
    amplitude=float(params.get('amplitude',.18))*height
    phase=float(params.get('phase',0))+.48*q
    tilt=float(params.get('tilt',-.12))
    layer=params.get('layer','back')
    reflected=layer=='reflection'
    rng=random.Random(int(params.get('seed',31)))
    colours={'cyan-gold':[(50,205,255),(120,240,255),(255,185,98)],
             'blue-violet':[(50,180,255),(142,110,255),(255,155,220)],
             'amber':[(255,142,45),(255,210,118),(255,245,206)]}[params.get('palette','cyan-gold')]
    field=Image.new('L',(width,height));core=Image.new('RGBA',(width,height))
    fd=ImageDraw.Draw(field);cd=ImageDraw.Draw(core)
    count=int(params.get('filaments',14))
    samples=220
    for filament in range(count):
        colour=colours[filament%len(colours)]
        offset=(filament-(count-1)/2)*height*.0042
        wiggle=rng.uniform(.003,.013)*height
        shift=rng.uniform(0,math.tau)
        previous=None;previous_s=None
        for i in range(samples):
            s=-.15+i/(samples-1)*1.45
            z=math.sin(math.tau*s+phase)
            selected=(layer=='all' or reflected or (layer=='back' and z<.08)
                      or (layer=='front' and z>=.08))
            age=head-s
            strength=fade*math.exp(-max(0,age)*3.8)*min(1,max(0,age)*25)
            if not selected or not 0<=age<=.75:
                previous=None;continue
            x=cx+span*(s-.5)
            y=cy+amplitude*math.sin(math.tau*s+phase)+tilt*(x-cx)+offset
            y+=wiggle*math.sin(29*s+shift+time*3)
            if reflected:
                floor=float(params.get('groundY',.78))*height
                y=floor+(floor-y)*.33
                strength*=.23
            point=(x,y)
            if previous is not None:
                alpha=round(255*strength)
                cd.line([previous,point],fill=(*colour,alpha),width=max(1,round(width/900)))
                fd.line([previous,point],fill=alpha,width=max(2,round(width/280)))
            previous,previous_s=point,s
    # A narrow white-hot core keeps the fine coloured strands luminous rather
    # than turning the whole ribbon into a uniformly coloured tube.
    previous=None
    for i in range(samples):
        s=-.15+i/(samples-1)*1.45;z=math.sin(math.tau*s+phase);age=head-s
        selected=layer=='all' or reflected or (layer=='back' and z<.08) or (layer=='front' and z>=.08)
        if not selected or not 0<=age<=.67:previous=None;continue
        x=cx+span*(s-.5);y=cy+amplitude*math.sin(math.tau*s+phase)+tilt*(x-cx)
        strength=fade*math.exp(-age*3.0)*min(1,age*30)
        if reflected:
            floor=float(params.get('groundY',.78))*height;y=floor+(floor-y)*.33;strength*=.23
        if previous is not None:
            cd.line([previous,(x,y)],fill=(250,255,255,round(255*strength)),width=max(1,round(width/330)))
        previous=(x,y)
    # Additive-looking luminous halos with straight RGB and a blurred alpha.
    out=Image.new('RGBA',(width,height))
    for radius,opacity in [(width*.021,.32),(width*.007,.65)]:
        a=blur_alpha(field,radius)
        a=a.point(lambda v:min(255,round(v*opacity*3)))
        glow=Image.new('RGBA',(width,height),(*colours[0],0));glow.putalpha(a)
        out=Image.alpha_composite(out,glow)
    hot=field.filter(ImageFilter.GaussianBlur(max(.3,width/1800)))
    hot=hot.point(lambda v:round(v*.62))
    white=Image.new('RGBA',(width,height),(235,252,255,0));white.putalpha(hot)
    out=Image.alpha_composite(out,core)
    out=Image.alpha_composite(out,white)
    # Sparse, tangent-driven sparks: drag slows the fragments; gravity bends them.
    draw=ImageDraw.Draw(out)
    for i in range(18):
        born=start+duration*(.12+(i%12)/14)
        age=time-born
        if not 0<age<.42:continue
        s=-.15+1.4*progress(born,start,duration,'minimum-jerk')
        birth_x,birth_y=anchor(params,born);birth_x*=width;birth_y*=height
        birth_phase=float(params.get('phase',0))+.48*progress(born,start,duration,'minimum-jerk')
        x=birth_x+span*(s-.5);y=birth_y+amplitude*math.sin(math.tau*s+birth_phase)+tilt*(x-birth_x)
        z=math.sin(math.tau*s+birth_phase)
        if (layer=='back' and z>=.08) or (layer=='front' and z<.08) or reflected:continue
        particle_rng=random.Random(int(params.get('seed',31))*100+i)
        v=width*(.09+particle_rng.random()*.14);drag=3.8
        dx=v*(1-math.exp(-drag*age))/drag
        dy=(-height*.14)*(1-math.exp(-drag*age))/drag+height*.28*age*age
        a=round(210*(1-age/.42)*fade)
        draw.line((x+dx,y+dy,x+dx+width*.007,y+dy-height*.002),fill=(255,218,150,a),width=1)
    intensity=float(params.get('intensity',1))
    if intensity!=1:
        a=np.asarray(out).copy();a[:,:,3]=np.clip(a[:,:,3].astype(float)*intensity,0,255).astype('uint8')
        out=Image.fromarray(a)
    if reflected:out=out.filter(ImageFilter.GaussianBlur(width*.003))
    from cutvoke.rendering.vfx_fusion import constrain
    return constrain(out,params,time)


def cached_source(params,width,height,fps,duration,ffmpeg='ffmpeg',cancelled=None,*,renderer=None,kind='light',prefix_duration=None,transient_directory=None,start_time=0.,cache_directory=None,metrics=None):
    """Create an atomic, lossless disposable source; no pixels enter project JSON."""
    # Compositing controls do not change the generated RGBA light movie.
    drawing={key:value for key,value in params.items() if key not in ('blendMode','highlightProtect','highlightThreshold')}
    identity=[VERSION,drawing,width,height,str(fps),round(duration,9)]
    if start_time:
        identity.insert(0,round(start_time,12))
    if kind!='light':identity.insert(0,kind)
    key=hashlib.sha256(json.dumps(identity,
                                sort_keys=True,separators=(',',':')).encode()).hexdigest()
    directory=Path(cache_directory) if cache_directory else Path(tempfile.gettempdir())/'cutvoke-light-cache-v1'
    directory.mkdir(parents=True,exist_ok=True)
    if transient_directory is not None and start_time:
        directory=Path(transient_directory)
    target=directory/(key+'.mkv')
    if target.is_file() and target.stat().st_size>256:
        if metrics is not None: metrics.update(diskHit=True,generatedFrames=0)
        return str(target)
    if prefix_duration is not None and prefix_duration < duration:
        if transient_directory is not None:
            directory=Path(transient_directory)
        duration=max(1/float(fps),math.ceil(prefix_duration*float(fps)-1e-7)/float(fps))
        identity[-1]=round(duration,9)
        key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        target=directory/(key+'.mkv')
        if target.is_file() and target.stat().st_size>256:
            if metrics is not None: metrics.update(diskHit=True,generatedFrames=0)
            return str(target)
    temporary=directory/(key+'-'+uuid.uuid4().hex+'.mkv')
    command=[ffmpeg,'-v','error','-y','-f','rawvideo','-pixel_format','rgba','-video_size',f'{width}x{height}',
             '-framerate',str(fps),'-i','pipe:0','-an','-c:v','ffv1','-level','3','-pix_fmt','bgra','-threads','2',str(temporary)]
    with tempfile.TemporaryFile() as errors:
        started=time.monotonic()
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors)
        try:
            for i in range(max(1,math.ceil(duration*float(fps)-1e-7))):
                if cancelled and cancelled():raise RuntimeError('light render cancelled')
                process.stdin.write((renderer or frame)(params,width,height,start_time+i/float(fps)).tobytes())
            process.stdin.close()
            if process.wait(timeout=120):
                errors.seek(0);raise RuntimeError(errors.read().decode('utf-8','replace')[-1000:])
            os.replace(temporary,target)
            if metrics is not None: metrics.update(diskHit=False,generatedFrames=max(1,math.ceil(duration*float(fps)-1e-7)),generationSeconds=time.monotonic()-started)
        except BaseException:
            if process.poll() is None:process.kill();process.wait(timeout=10)
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if process.stdin and not process.stdin.closed:process.stdin.close()
    return str(target)
