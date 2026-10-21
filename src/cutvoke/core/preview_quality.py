"""Render-only preview geometry; the editable project and export stay full size."""
import copy


MAX_DIMENSION = 640
SUPPORTED_FX={'gblur','grayscale','chromatic','rgbsplit','invert','sepia','vintage',
              'posterize','mirror','vignette','chromakey','lut3d','curves','crop',
              'trail','hsl','colorbalance','mask','glow','vibrance','colorize','lens',
              'vflip','lightWeave','opticalField','shatter'}


def dimensions(sequence, maximum=MAX_DIMENSION):
    scale=min(1.0,maximum/max(sequence.width,sequence.height)) if maximum else 1.0
    if scale==1:
        return sequence.width,sequence.height
    return max(2,int(sequence.width*scale)//2*2),max(2,int(sequence.height*scale)//2*2)


def snapshot(project,maximum=MAX_DIMENSION):
    width,height=dimensions(project.sequence,maximum)
    if (width,height)==(project.sequence.width,project.sequence.height):
        return project,1.0
    # Compound clips can own a different coordinate space. Preserve their
    # established renderer until that space has its own proxy transform.
    if any(c.nested is not None for t in project.sequence.tracks for c in t.clips):
        return project,1.0
    if any(e.get('effectId','').startswith('cutvoke.fx.') and
           e['effectId'].rsplit('.',1)[-1] not in SUPPORTED_FX
           for t in project.sequence.tracks if t.visible for c in t.clips if not c.hidden
           for e in c.effects if e.get('enabled',True)):
        return project,1.0
    result=copy.deepcopy(project)
    seq=result.sequence
    sx,sy=width/seq.width,height/seq.height
    scale=min(sx,sy)
    seq.width,seq.height=width,height
    for track in seq.tracks:
        for clip in track.clips:
            for name,ratio in [('x',sx),('y',sy)]:
                clip.keyframes[name]=[copy.copy(key) for key in clip.keyframes[name]] if name in clip.keyframes else []
                for key in clip.keyframes[name]:
                    key.value*=ratio
                if not clip.keyframes[name]:
                    clip.keyframes.pop(name,None)
            for effect in clip.effects:
                params=effect.setdefault('params',{})
                key=effect.get('effectId','').rsplit('.',1)[-1]
                if key=='transform':
                    position=params.get('position',{})
                    position['x']=position.get('x',0)*sx
                    position['y']=position.get('y',0)*sy
                    params['position']=position
                for name in ({'sigma'} if key=='gblur' else
                             {'offset'} if key in {'chromatic','rgbsplit'} else
                             {'pixels'} if key=='shake' else
                             {'gap'} if key=='shatter' else set()):
                    defaults={'sigma':5,'offset':4,'gap':3}
                    if name in params or name in defaults:
                        params[name]=params.get(name,defaults.get(name,0))*scale
                # Internal render hints are never written back to project JSON.
                if key in {'glow','shatter'}:
                    params['_previewScale']=scale
    return result,scale
