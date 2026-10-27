"""Local 3D scene rendering: meshes, materials, camera, light and pixel depth."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import threading
import re
import numpy as np
from PIL import Image,ImageColor
from cutvoke.rendering.scene_geometry import animated,camera_matrix,geometry,model_matrix

VERSION=1
_state=threading.local()


def spec():
    from cutvoke.rendering.effects import EffectSpec
    def number(default=0,low=-1000,high=1000):return {'type':'number','minimum':low,'maximum':high,'default':default}
    def obj(properties,required=()):return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
    def array(item,maximum,minimum=0):return {'type':'array','items':item,'maxItems':maximum,'minItems':minimum}
    pose={**{axis:number() for axis in 'xyz'},**{'rotation'+axis:number(0,-36000,36000) for axis in 'XYZ'},**{'scale'+axis:number(1,.01,100) for axis in 'XYZ'}}
    pose_frames=array(obj({'at':number(0,0,86400),**{key:{k:v for k,v in value.items() if k!='default'} for key,value in pose.items()}},('at',)),32)
    camera={**{axis:number(5 if axis=='z' else 0) for axis in 'xyz'},**{'target'+axis:number() for axis in 'XYZ'},'fov':number(45,5,150),'near':number(.05,.001,10),'far':number(100,.01,10000)}
    camera_keys=array(obj({'at':number(0,0,86400),**{key:{k:v for k,v in value.items() if k!='default'} for key,value in camera.items()}},('at',)),32)
    object_schema=obj({'id':{'type':'string','minLength':1,'maxLength':96},'shape':{'type':'string','enum':['cube','sphere','plane','mesh'],'default':'cube'},
      'color':{'type':'string','default':'#55bbff'},'opacity':number(1,0,1),'roughness':number(.5,.02,1),'texturePath':{'type':'string'},**pose,
      'poseFrames':pose_frames,'vertices':array(obj({axis:number(0,-100000,100000) for axis in 'xyz'},tuple('xyz')),4096),
      'triangles':array(obj({axis:{'type':'integer','minimum':0,'maximum':4095} for axis in 'abc'},tuple('abc')),8192),
      'uvs':array(obj({'u':number(0,-100,100),'v':number(0,-100,100)},('u','v')),4096)},('id',))
    defaults=[{'id':'cube','shape':'cube','color':'#40baff','x':-.45,'rotationX':18,'rotationY':30,'scaleX':.7,'scaleY':.7,'scaleZ':.7},
              {'id':'sphere','shape':'sphere','color':'#ffbd55','x':.65,'z':-.6,'scaleX':.65,'scaleY':.65,'scaleZ':.65}]
    parameters={'type':'object','properties':{'objects':{**array(object_schema,32,1),'default':defaults},
        'camera':{**obj({**camera,'poseFrames':camera_keys}),'default':{'z':5,'fov':45}},
        'light':{**obj({'x':number(-3),'y':number(4),'z':number(5),'intensity':number(.9,0,8),'ambient':number(.25,0,1),'color':{'type':'string','default':'#ffffff'}}),'default':{'x':-3,'y':4,'z':5,'ambient':.25,'intensity':.9}},
        'renderer':{'type':'string','enum':['auto','opengl','software'],'default':'auto'},'depthLayers':{'type':'integer','minimum':1,'maximum':8,'default':4}},'additionalProperties':False}
    labels={'objects':('对象','Objects'),'camera':('摄像机','Camera'),'light':('灯光','Light'),'shape':('形状','Shape'),
        'poseFrames':('动画姿态','Animated poses'),'vertices':('顶点','Vertices'),'triangles':('三角面','Triangles'),'uvs':('纹理坐标','Texture coordinates'),
        'fov':('视野角度','Field of view'),'near':('近裁剪面','Near clipping plane'),'far':('远裁剪面','Far clipping plane'),
        'roughness':('粗糙度','Roughness'),'depthLayers':('透明深度层数','Transparent depth layers'),'renderer':('渲染器','Renderer'),
        'targetX':('目标 X','Target X'),'targetY':('目标 Y','Target Y'),'targetZ':('目标 Z','Target Z'),'ambient':('环境光','Ambient light'),
        'at':('时间（秒）','Time (seconds)'),'texturePath':('材质图片路径','Material image path')}
    def label(schema):
        for key,value in schema.get('properties',{}).items():
            if key in labels:value['x-cutvoke-label']=dict(zip(('zh-CN','en'),labels[key]))
            label(value)
        if 'items' in schema:label(schema['items'])
    label(parameters)
    return EffectSpec(id='cutvoke.fx.scene3d',version='1.0.0',name={'zh-CN':'本机三维场景','en':'Local 3D scene'},category='fx',parameters=parameters,
      implementation={'engine':'ffmpeg','filter':'scene3d'},applies_to=('video','image'),keywords=('3D','scene','mesh','depth','OBJ','三维','场景','遮挡'),
      description={'zh-CN':'本机网格与材质、摄像机、灯光和逐像素深度遮挡。可选 OpenGL 加速；无需 AI 模型。',
                   'en':'Local meshes/materials, camera, lighting and per-pixel depth occlusion. Optional OpenGL acceleration; no AI model.'},license='MIT')


def validate(params):
    def color(value):
        if not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value):raise ValueError('3D colors must use opaque #RRGGBB; use opacity for transparency')
    objects=params.get('objects') or []
    if len({value['id'] for value in objects})!=len(objects):raise ValueError('3D object IDs must be unique within the scene')
    count=0
    for value in [*objects,params.get('camera',{})]:
        keys=value.get('poseFrames') or []
        if any(a['at']>=b['at'] for a,b in zip(keys,keys[1:])):raise ValueError('3D pose times must strictly increase')
    for value in objects:
        color(value.get('color','#55bbff'))
        if value.get('shape')=='mesh':
            geometry(value);count+=len(value['triangles'])
            if value.get('uvs') and len(value['uvs'])!=len(value.get('vertices',[])):raise ValueError('mesh UV count must match vertices')
        else:count+=576 if value.get('shape')=='sphere' else 12
        if value.get('texturePath'):
            path=Path(value['texturePath'])
            if not path.is_absolute() or not path.is_file():raise ValueError('texturePath must be an existing absolute local image path')
            texture(str(path))
    if count>16384:raise ValueError('scene accepts at most 16384 triangles in total')
    camera=params.get('camera',{})
    camera_matrix(camera,16,16)
    for key in camera.get('poseFrames',[]):camera_matrix(animated(camera,key['at']),16,16)
    color(params.get('light',{}).get('color','#ffffff'))


@lru_cache(maxsize=32)
def _texture(path,size,mtime):
    with Image.open(path) as image:
        if image.width*image.height>32_000_000:raise ValueError('3D textures accept at most 32 million pixels')
        image=image.convert('RGBA');image.thumbnail((2048,2048));return image.copy()


def texture(path):
    if not path:return Image.new('RGBA',(1,1),(255,255,255,255))
    stat=Path(path).stat();return _texture(str(path),stat.st_size,stat.st_mtime_ns)


def _context():
    if getattr(_state,'context',None) is not None:return _state.context
    import moderngl
    try:context=moderngl.create_standalone_context(require=330)
    except Exception:context=moderngl.create_standalone_context(require=330,backend='egl')
    _state.context=context;return context


def availability():
    try:
        context=_context();return {'ready':True,'defaultRenderer':'opengl','device':context.info.get('GL_RENDERER','OpenGL 3.3'),'softwareFallback':True,'requiresAI':False}
    except Exception as error:return {'ready':True,'defaultRenderer':'software','softwareFallback':True,'requiresAI':False,'openglUnavailable':str(error)[:300]}


VERTEX='''#version 330
in vec3 position;in vec3 normal;in vec2 uv;
uniform mat4 mvp;uniform mat4 model;
out vec3 world;out vec3 n;out vec2 texcoord;
void main(){world=(model*vec4(position,1)).xyz;n=transpose(inverse(mat3(model)))*normal;texcoord=uv;gl_Position=mvp*vec4(position,1);}
'''
FRAGMENT='''#version 330
in vec3 world;in vec3 n;in vec2 texcoord;
uniform sampler2D picture;uniform sampler2D previousDepth;uniform int passIndex;
uniform vec2 resolution;uniform vec4 material;uniform vec3 eye;
uniform vec3 lightPosition;uniform vec3 lightColor;uniform float ambient;uniform float intensity;uniform float roughness;
out vec4 color;
void main(){
if(passIndex>0 && gl_FragCoord.z<=texture(previousDepth,gl_FragCoord.xy/resolution).r+0.000001)discard;
vec4 tex=texture(picture,texcoord);float alpha=tex.a*material.a;if(alpha<0.002)discard;
vec3 normal=normalize(n);if(!gl_FrontFacing)normal=-normal;
vec3 direction=normalize(lightPosition-world);vec3 viewDirection=normalize(eye-world);
float diffuse=max(0.0,dot(normal,direction));vec3 halfway=normalize(direction+viewDirection);
float shine=pow(max(0.0,dot(normal,halfway)),mix(120.0,4.0,roughness))*(1.0-roughness)*0.25;
vec3 rgb=tex.rgb*material.rgb*(ambient+intensity*diffuse*lightColor)+shine*lightColor;
color=vec4(clamp(rgb,0.0,1.0),alpha);
}
'''


def _opengl(params,width,height,time):
    import moderngl
    context=_context();program=context.program(vertex_shader=VERTEX,fragment_shader=FRAGMENT)
    vp,eye=camera_matrix(animated(params.get('camera',{}),time),width,height)
    light=params.get('light',{});light_color=np.asarray(ImageColor.getrgb(light.get('color','#ffffff')))/255
    program['eye'].value=tuple(eye);program['lightPosition'].value=tuple(light.get(axis,default) for axis,default in zip('xyz',(-3,4,5)))
    program['lightColor'].value=tuple(light_color);program['ambient'].value=light.get('ambient',.25);program['intensity'].value=light.get('intensity',.9)
    program['picture'].value=0;program['previousDepth'].value=1;program['resolution'].value=(width,height)
    resources=[];objects=[]
    try:
        for raw in params.get('objects',[]):
            obj=animated(raw,time)
            buffer=context.buffer(geometry(obj).astype('f4').tobytes());vao=context.vertex_array(program,[(buffer,'3f 3f 2f','position','normal','uv')])
            image=texture(obj.get('texturePath'));tex=context.texture(image.size,4,image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
            tex.filter=(moderngl.LINEAR,moderngl.LINEAR);tex.repeat_x=False;tex.repeat_y=False
            resources.extend([vao,buffer,tex]);objects.append((obj,vao,tex))
        targets=[]
        for _ in range(2):
            color=context.texture((width,height),4);depth=context.depth_texture((width,height));depth.compare_func=''
            depth.filter=(moderngl.NEAREST,moderngl.NEAREST);fbo=context.framebuffer([color],depth)
            targets.append((fbo,depth));resources.extend([fbo,color,depth])
        context.enable(moderngl.DEPTH_TEST);context.disable(moderngl.BLEND);context.disable(moderngl.CULL_FACE);context.depth_func='<='
        result=np.zeros((height,width,4),np.float32)
        for index in range(params.get('depthLayers',4)):
            fbo,depth=targets[index%2];fbo.use();fbo.clear(0,0,0,0,depth=1)
            targets[(index+1)%2][1].use(location=1);program['passIndex'].value=index
            for obj,vao,tex in objects:
                model=model_matrix(obj);program['model'].write(model.astype('f4').T.tobytes());program['mvp'].write((vp@model).astype('f4').T.tobytes())
                program['material'].value=(*[value/255 for value in ImageColor.getrgb(obj.get('color','#55bbff'))],obj.get('opacity',1))
                program['roughness'].value=obj.get('roughness',.5);tex.use(location=0);vao.render(moderngl.TRIANGLES)
            layer=np.frombuffer(fbo.read(components=4,alignment=1),np.uint8).reshape(height,width,4)[::-1].astype(np.float32)/255
            weight=layer[...,3:]*(1-result[...,3:]);result[...,:3]+=layer[...,:3]*weight;result[...,3:]+=weight
            if not np.any((layer[...,3]>.002)&(result[...,3]<.998)):break
        result[...,:3]/=np.maximum(result[...,3:],1e-8)
        _state.renderer='opengl:'+context.info.get('GL_RENDERER','OpenGL')
        return Image.fromarray(np.clip(result*255,0,255).round().astype(np.uint8))
    finally:
        for resource in resources:resource.release()
        program.release()


def frame(params,width,height,time):
    renderer=params.get('renderer','auto')
    if renderer!='software':
        try:return _opengl(params,width,height,time)
        except Exception as error:
            if renderer=='opengl':raise RuntimeError(f'local OpenGL renderer unavailable: {error}') from error
    from cutvoke.rendering.scene_software import render
    _state.renderer='software';return render(params,width,height,time)


def last_renderer():return getattr(_state,'renderer','cached')
