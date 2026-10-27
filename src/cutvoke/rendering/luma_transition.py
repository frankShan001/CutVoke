"""Optional spatial/temporal luma key preparation, retaining original pictures."""
from __future__ import annotations


def controls():
    unit = lambda default: {'type':'number','minimum':0,'maximum':1,'default':default}
    result = {
        'spatialBlur':{'type':'number','minimum':0,'maximum':32,'default':0,
                       'description':{'zh-CN':'亮度键空间模糊半径，像素；不模糊原画面'}},
        'temporalFrames':{'type':'integer','minimum':1,'maximum':9,'default':1,
                          'description':{'zh-CN':'亮度键的时间平滑帧数；不改变画面时间'}},
        'dissolveMix':{**unit(0),'description':{'zh-CN':'普通叠化占比，减少亮度轮廓感'}},
        'thresholdCurve':{'type':'array','minItems':2,'maxItems':16,'items':{
            'type':'object','properties':{'at':unit(0),'value':unit(0)},
            'required':['at','value'],'additionalProperties':False},
            'description':{'zh-CN':'阈值曲线；at 是转场进度，value 通常从 1 下降到 0'}},
        'localRegion':{'type':'object','properties':{
            'x':unit(0),'y':unit(0),'width':unit(1),'height':unit(1),
            'feather':{**unit(.05),'maximum':.5}},'additionalProperties':False,
            'description':{'zh-CN':'局部亮度揭示区域；区域外使用普通叠化'}},
    }
    locales = 'zh-CN zh-TW en ja ko es fr de pt-BR ru ar'.split()
    labels = {
        'spatialBlur': ['空间模糊','空間模糊','Spatial blur','空間ぼかし','공간 흐림','Desenfoque espacial','Flou spatial','Räumliche Unschärfe','Desfoque espacial','Пространственное размытие','تمويه مكاني'],
        'temporalFrames': ['时间平滑帧数','時間平滑影格數','Temporal frames','時間平滑フレーム数','시간 평활 프레임','Fotogramas temporales','Images temporelles','Zeitliche Glättung','Quadros temporais','Временное сглаживание','إطارات التنعيم الزمني'],
        'dissolveMix': ['叠化占比','疊化占比','Dissolve mix','ディゾルブの割合','디졸브 비율','Mezcla de fundido','Mélange de fondu','Überblendungsanteil','Mistura de dissolução','Доля растворения','نسبة التلاشي'],
        'thresholdCurve': ['阈值曲线','閾值曲線','Threshold curve','しきい値カーブ','임계값 곡선','Curva de umbral','Courbe de seuil','Schwellenwertkurve','Curva de limiar','Кривая порога','منحنى العتبة'],
        'localRegion': ['局部区域','局部區域','Local region','局所領域','로컬 영역','Región local','Zone locale','Lokaler Bereich','Região local','Локальная область','منطقة محلية'],
    }
    for key, values in labels.items():
        result[key]['x-cutvoke-label'] = dict(zip(locales, values))
    return result


def enabled(params):
    return (params.get('spatialBlur',0) > 0 or params.get('temporalFrames',1) > 1 or
            params.get('dissolveMix',0) > 0 or params.get('thresholdCurve') or params.get('localRegion'))


def _curve(points, clock):
    result=f"{points[-1]['value']:.9f}"
    for a,b in reversed(list(zip(points,points[1:]))):
        segment=f"({a['value']:.9f}+({clock}-{a['at']:.9f})*{(b['value']-a['value'])/(b['at']-a['at']):.9f})"
        result=f"if(lt({clock},{b['at']:.9f}),{segment},{result})"
    return f"if(lt({clock},{points[0]['at']:.9f}),{points[0]['value']:.9f},{result})"


def build(a,b,out,params,duration,offset,fps,parts,has_alpha=False):
    prefix=f'{out}_luma'; first=max(0,round(offset*float(fps))); count=max(1,round(duration*float(fps)))
    parts.extend([
        f'[{a}]split=2[{prefix}_prefixsrc][{prefix}_akeysrc]',
        f'[{prefix}_akeysrc]trim=start_frame={first}:end_frame={first+count},setpts=PTS-STARTPTS,split=2[{prefix}_a][{prefix}_akey]',
        f'[{b}]split=2[{prefix}_tail_src][{prefix}_bkeysrc]',
        f'[{prefix}_bkeysrc]trim=end_frame={count},setpts=PTS-STARTPTS,split=2[{prefix}_b][{prefix}_bkey]',
        f'[{prefix}_tail_src]trim=start_frame={count},setpts=PTS-STARTPTS[{prefix}_tail]',
    ])
    selected='akey' if params.get('keySource','outgoing')=='outgoing' else 'bkey'
    unused='bkey' if selected=='akey' else 'akey'
    parts.append(f'[{prefix}_{unused}]nullsink')
    filters=[]
    blur=float(params.get('spatialBlur',0))
    if blur: filters.append(f'gblur=sigma={blur:.6f}')
    frames=int(params.get('temporalFrames',1))
    if frames>1: filters.append(f'tmix=frames={frames}')
    clock=f'clip(T/{duration:.9f},0,1)'
    smooth=f'({clock}*{clock}*(3-2*{clock}))' if params.get('curve','smooth')=='smooth' else clock
    softness=float(params.get('softness',.08))
    if params.get('thresholdCurve'):
        threshold=f'({_curve(params["thresholdCurve"],clock)}*{1+2*softness:.9f}-{softness:.9f})'
    else:
        threshold=f'(1+{softness:.9f}-{1+2*softness:.9f}*{smooth})'
    luminance='clip((lum(X,Y)-16)/219,0,1)'
    if params.get('reverse'): luminance=f'(1-({luminance}))'
    mask=f'clip((({luminance})-{threshold}+{softness:.9f})/{2*softness:.9f},0,1)'
    region=params.get('localRegion')
    region_weight='1'
    if region:
        x,y=region.get('x',0),region.get('y',0);w,h=region.get('width',1),region.get('height',1)
        feather=max(1e-6,region.get('feather',.05))
        region_weight=(f'clip(min(min(X/W-{x:.9f},{x+w:.9f}-X/W),'
                       f'min(Y/H-{y:.9f},{y+h:.9f}-Y/H))/{feather:.9f},0,1)')
    weight=f'({smooth}+(({mask})-{smooth})*{1-float(params.get("dissolveMix",0)):.9f}*({region_weight}))'
    expression=f'255*if(lte(T,0),0,if(gte(T,{duration:.9f}),1,{weight}))'
    planes=':'.join(f"{plane}='{expression}'" for plane in (('lum','cb','cr')+(('a',) if has_alpha else ())))
    filters.append('geq='+planes)
    parts.append(f'[{prefix}_{selected}]'+','.join(filters)+f'[{prefix}_mask]')
    parts.append(f'[{prefix}_a][{prefix}_b][{prefix}_mask]maskedmerge[{prefix}_core]')
    if first:
        parts.append(f'[{prefix}_prefixsrc]trim=end_frame={first},setpts=PTS-STARTPTS[{prefix}_prefix]')
        parts.append(f'[{prefix}_prefix][{prefix}_core][{prefix}_tail]concat=n=3:v=1:a=0[{out}]')
    else:
        parts.append(f'[{prefix}_prefixsrc]nullsink')
        parts.append(f'[{prefix}_core][{prefix}_tail]concat=n=2:v=1:a=0[{out}]')
    return out
