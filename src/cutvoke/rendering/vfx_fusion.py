"""Scene controls for procedural light; parameters contain no media or paths."""
from __future__ import annotations

from functools import lru_cache
import json


def blur_alpha(image, radius):
    """Keep narrow cores native; sample broad soft halos at a bounded resolution."""
    from PIL import Image, ImageFilter
    factor=min(4,max(1,int(radius/3))) if max(image.size)>=640 else 1
    if factor==1:
        return image.filter(ImageFilter.GaussianBlur(radius))
    small=image.resize((max(1,image.width//factor),max(1,image.height//factor)),Image.Resampling.BOX)
    return small.filter(ImageFilter.GaussianBlur(radius/factor)).resize(image.size,Image.Resampling.BILINEAR)


def schema():
    point = {'type':'object','properties':{
        'x':{'type':'number','minimum':0,'maximum':1},
        'y':{'type':'number','minimum':0,'maximum':1}},
        'required':['x','y'],'additionalProperties':False}
    unit = lambda default: {'type':'number','minimum':0,'maximum':1,'default':default}
    return {
        'blendMode':{'type':'string','enum':['normal','screen','add'],'default':'normal',
                     'description':{'zh-CN':'光效混合：普通、滤色或线性减淡'}},
        'occlusionPolygon':{'type':'array','minItems':3,'maxItems':128,'items':point,
                            'description':{'zh-CN':'遮挡光效的主体轮廓；归一化坐标，不包含图片'}},
        'occlusionFeather':{**unit(0),'maximum':.2,'description':{'zh-CN':'遮挡轮廓羽化，占画面短边比例'}},
        'occlusionFollowAnchor':{'type':'boolean','default':False,'description':{'zh-CN':'遮挡轮廓随光源跟踪点平移'}},
        'reflectionRegion':{'type':'object','properties':{
            'x':{**unit(0),'description':{'en':'Horizontal region origin as a fraction of canvas width'}},'y':{**unit(0),'description':{'en':'Vertical region origin as a fraction of canvas height'}},'width':{**unit(1),'description':{'en':'Region width as a fraction of canvas width'}},'height':{**unit(1),'description':{'en':'Region height as a fraction of canvas height'}},
            'feather':{'description':{'en':'Feather width as a fraction of the shorter canvas edge'},**unit(0),'maximum':.2}},'additionalProperties':False,
            'description':{'zh-CN':'反射区域边界；留空使用完整画面'}},
        'highlightProtect':{**unit(0),'description':{'zh-CN':'保留原画面高光，减少亮部叠加过曝'}},
        'highlightThreshold':{**unit(.8),'maximum':.99,'description':{'zh-CN':'开始保护的原画面亮度'}},
    }


@lru_cache(maxsize=32)
def _geometry(parameters, width, height):
    from PIL import Image, ImageDraw, ImageFilter, ImageChops
    params = json.loads(parameters)
    mask = Image.new('L',(width,height),255)
    points = params.get('occlusionPolygon')
    if points:
        ImageDraw.Draw(mask).polygon([(p['x']*width,p['y']*height) for p in points],fill=0)
        radius = params.get('occlusionFeather',0)*min(width,height)
        if radius: mask = mask.filter(ImageFilter.GaussianBlur(radius))
    region = params.get('reflectionRegion')
    if region is not None:
        rect = Image.new('L',(width,height))
        x,y = region.get('x',0)*width,region.get('y',0)*height
        w,h = region.get('width',1)*width,region.get('height',1)*height
        if w>0 and h>0: ImageDraw.Draw(rect).rectangle((x,y,x+w-1,y+h-1),fill=255)
        radius = region.get('feather',0)*min(width,height)
        if radius: rect=rect.filter(ImageFilter.GaussianBlur(radius))
        mask=ImageChops.multiply(mask,rect)
    return mask


def constrain(frame, params, time):
    from PIL import Image, ImageChops
    if not params.get('occlusionPolygon') and params.get('reflectionRegion') is None:
        return frame
    keys = ('occlusionPolygon','occlusionFeather','reflectionRegion')
    geometry = {key:params[key] for key in keys if key in params}
    width,height=frame.size
    mask = _geometry(json.dumps(geometry,sort_keys=True),width,height)
    if params.get('occlusionFollowAnchor') and params.get('occlusionPolygon'):
        from cutvoke.rendering.vfx_light import anchor
        now,initial=anchor(params,time),anchor(params,0)
        # Shift the occlusion separately so a water boundary stays in scene space.
        occlusion={key:value for key,value in geometry.items() if key!='reflectionRegion'}
        mask=_geometry(json.dumps(occlusion,sort_keys=True),width,height).transform((width,height),Image.Transform.AFFINE,
            (1,0,-(now[0]-initial[0])*width,0,1,-(now[1]-initial[1])*height),
            resample=Image.Resampling.BILINEAR,fillcolor=255)
        if 'reflectionRegion' in geometry:
            mask=ImageChops.multiply(mask,_geometry(json.dumps({'reflectionRegion':geometry['reflectionRegion']},sort_keys=True),width,height))
    frame=frame.copy()
    frame.putalpha(ImageChops.multiply(frame.getchannel('A'),mask))
    return frame


def composite(base, light, output, params, parts):
    strength=float(params.get('highlightProtect',0)); mode=params.get('blendMode','normal')
    if strength:
        threshold=float(params.get('highlightThreshold',.8))
        parts.extend([
            f'[{base}]split=2[{output}_base][{output}_luma]',
            f"[{output}_luma]format=gray,lut=y='255*(1-{strength:.9f}*clip((val/255-{threshold:.9f})/{1-threshold:.9f},0,1))'[{output}_protect]",
            f'[{light}]format=rgba,split=2[{output}_rgb][{output}_alpha]',
            f'[{output}_alpha]alphaextract[{output}_a]',
            f'[{output}_a][{output}_protect]blend=all_mode=multiply[{output}_mask]',
            f'[{output}_rgb][{output}_mask]alphamerge[{output}_light]',
        ])
        base,light=f'{output}_base',f'{output}_light'
    if mode in {'screen','add'}:
        parts.extend([
            f"[{light}]format=gbrap,geq=r='r(X,Y)*alpha(X,Y)/255':g='g(X,Y)*alpha(X,Y)/255':b='b(X,Y)*alpha(X,Y)/255':a='alpha(X,Y)',setparams=range=full:colorspace=gbr[{output}_premult]",
            f'[{base}]format=gbrap,setparams=range=full:colorspace=gbr[{output}_scene]',
            f'[{output}_scene][{output}_premult]blend=all_mode={"addition" if mode=="add" else "screen"}:shortest=0:repeatlast=0,setparams=range=full:colorspace=gbr[{output}]'])
    else:
        parts.append(f'[{base}][{light}]overlay=shortest=0:eof_action=pass:repeatlast=0:format=auto[{output}]')
    return output
