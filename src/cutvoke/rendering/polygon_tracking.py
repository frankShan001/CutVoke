"""Bounded forward/backward optical-flow assistance for editable polygon roto."""
from __future__ import annotations

import math
import subprocess

import numpy as np


def track_polygon(path, points, *, start=0., seconds=5., sample_fps=8, path_mode='linear'):
    import cv2
    from cutvoke.domain.compositing import number
    from cutvoke.rendering.render import RenderService
    start=number(start,'start',0,86400)
    seconds=number(seconds,'seconds',.1,30)
    sample_fps=number(sample_fps,'sampleFps',1,15)
    if math.ceil(seconds*sample_fps)+1 > 256:
        raise ValueError('at most 256 tracking samples; lower sampleFps or shorten seconds')
    if not isinstance(points,list) or not 3<=len(points)<=128:
        raise ValueError('tracking requires 3..128 normalized source contour anchors')
    if path_mode not in {'linear','smooth','bezier'}:raise ValueError('pathMode must be linear/smooth/bezier')
    if any(not isinstance(point,dict) or set(point)-{'x','y','inHandle','outHandle','handlesLinked'} for point in points):
        raise ValueError('invalid contour anchor fields')
    initial=np.array([[number(point.get('x'),'x',0,1),number(point.get('y'),'y',0,1)] for point in points],np.float32)
    for point in points:
        for key in ('inHandle','outHandle'):
            if key in point:
                handle=point[key]
                if not isinstance(handle,dict) or set(handle)-{'x','y'}:raise ValueError('invalid Bezier handle')
                for axis in ('x','y'):number(handle.get(axis),axis,0,1)
    if path_mode!='linear':
        from cutvoke.rendering.render import _smooth_closed_path,_bezier_closed_path
        anchors=[tuple(point) for point in initial]
        initial=np.asarray(_bezier_closed_path(anchors,points) if path_mode=='bezier' else _smooth_closed_path(anchors),np.float32)
    if len(initial)>64:
        closed=np.concatenate((initial,initial[:1]))
        distance=np.concatenate(([0],np.cumsum(np.linalg.norm(np.diff(closed,axis=0),axis=1))))
        positions=np.linspace(0,distance[-1],64,endpoint=False)
        initial=np.stack([np.interp(positions,distance,closed[:,axis]) for axis in range(2)],axis=1).astype(np.float32)
    renderer=RenderService(gpu_effects=False)
    info=renderer.probe_media(path)
    if not info.get('has_video'): raise ValueError('polygon tracking requires video')
    original_width,original_height=int(info['width']),int(info['height'])
    ratio=min(1.,640/max(original_width,original_height))
    width,height=max(2,round(original_width*ratio)),max(2,round(original_height*ratio))
    command=[renderer.ffmpeg,'-v','error','-nostdin','-ss',str(start),'-i',str(path),'-t',str(seconds),
             '-vf',f'fps={sample_fps},scale={width}:{height}', '-an','-f','rawvideo','-pix_fmt','gray','pipe:1']
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    frames=[];previous=None;vertices=initial*np.array([width-1,height-1]);lost=None;minimum=1.
    try:
        for index in range(min(256,math.ceil(seconds*sample_fps))):
            raw=process.stdout.read(width*height)
            if len(raw)!=width*height:break
            current=np.frombuffer(raw,np.uint8).reshape(height,width)
            confidence=1.
            if previous is not None:
                flow=cv2.calcOpticalFlowFarneback(previous,current,None,.5,4,25,4,7,1.5,0)
                reverse=cv2.calcOpticalFlowFarneback(current,previous,None,.5,4,25,4,7,1.5,0)
                # Sample every polygon point rather than translating one rigid
                # box. Forward/backward disagreement detects a drifting edge.
                x=vertices[:,0].astype(np.float32).reshape(-1,1);y=vertices[:,1].astype(np.float32).reshape(-1,1)
                delta=cv2.remap(flow,x,y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE).reshape(-1,2)
                candidate=vertices+delta
                back=cv2.remap(reverse,candidate[:,0].astype(np.float32).reshape(-1,1),candidate[:,1].astype(np.float32).reshape(-1,1),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE).reshape(-1,2)
                errors=np.linalg.norm(delta+back,axis=1)
                confidence=float(np.exp(-np.quantile(errors,.9)/2))
                texture=float(cv2.Laplacian(previous,cv2.CV_32F).var())
                if texture<.5 or confidence<.35 or np.any(candidate<0) or np.any(candidate[:,0]>=width) or np.any(candidate[:,1]>=height):
                    lost=index/sample_fps;break
                old_area=abs(cv2.contourArea(vertices.astype(np.float32)))
                new_area=abs(cv2.contourArea(candidate.astype(np.float32)))
                if old_area>1 and not .5<new_area/old_area<2:
                    lost=index/sample_fps;break
                vertices=candidate
            minimum=min(minimum,confidence)
            normalized=vertices/np.array([width-1,height-1])
            frames.append({'at':round(index/sample_fps,9),'confidence':round(confidence,6),
                           'points':[{'x':round(float(x),6),'y':round(float(y),6)} for x,y in normalized]})
            previous=current.copy()
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=10)
    if not frames: raise ValueError('no video frames available at the requested source time')
    return {'status':'lost' if lost is not None else 'completed','lostAt':lost,'minimumConfidence':minimum,
            'sourceStart':start,'sampleFps':sample_fps,'sampleCount':len(frames),'pathFrames':frames,
            'method':'forward-backward-dense-optical-flow','trackingWidth':width,'trackingHeight':height}
