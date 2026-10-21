"""GPU pixel kernels shared by accurate playback, stills and export.

OpenCL is provided by FFmpeg and the display driver; no Python GPU runtime or
download is required. Unsupported devices retain the existing CPU renderer.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import ast
import hashlib
import math
from pathlib import Path
import random
import subprocess
import tempfile
import threading


class GpuEffectError(RuntimeError):
    pass


def number(value) -> str:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('GPU parameter must be finite')
    text = f'{value:.15g}'
    return text + ('f' if '.' in text or 'e' in text else '.0f')


def expression(source):
    """Translate our renderer's arithmetic expressions, without executing code.

    Sharing the existing formula avoids a second set of matte shapes/curves.
    Only the explicitly supported expression nodes and functions are accepted.
    """
    source = source.replace('\\,', ',').replace('if(', 'choose(')
    def emit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value,(int,float)):
            return number(node.value)
        if isinstance(node, ast.Name) and node.id in {'X','Y','W','H','T','PI'}:
            return node.id
        if isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
            return f'({"-" if isinstance(node.op,ast.USub) else "+"}{emit(node.operand)})'
        if isinstance(node, ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            operator={ast.Add:'+',ast.Sub:'-',ast.Mult:'*',ast.Div:'/'}[type(node.op)]
            return f'({emit(node.left)}{operator}{emit(node.right)})'
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
            name=node.func.id
            if name in {'st','ld'}:
                index=node.args[0]
                if not isinstance(index,ast.Constant) or index.value not in range(10):
                    raise ValueError('invalid expression register')
                register=f'r{index.value}'
                return f'({register}={emit(node.args[1])})' if name=='st' else register
            args=[emit(arg) for arg in node.args]
            if name=='choose' and len(args)==3:
                return f'(({args[0]}!=0.0f)?{args[1]}:{args[2]})'
            if name in {'lt','lte','gt','gte','eq'} and len(args)==2:
                op={'lt':'<','lte':'<=','gt':'>','gte':'>=','eq':'=='}[name]
                return f'((float)({args[0]}{op}{args[1]}))'
            functions={'clip':'clamp','min':'fmin','max':'fmax','mod':'fmod',
                       'pow':'pow','sqrt':'sqrt','sin':'sin','cos':'cos','abs':'fabs'}
            if name in functions:
                return functions[name]+'('+','.join(args)+')'
        raise ValueError('unsupported GPU expression: '+ast.dump(node))
    statements=source.split(';')
    return ';'.join(emit(ast.parse(part,mode='eval').body) for part in statements if part)


COMMON = r'''
const sampler_t nearest = CLK_NORMALIZED_COORDS_FALSE | CLK_ADDRESS_CLAMP | CLK_FILTER_NEAREST;
float4 pixel(read_only image2d_t image, int2 p) {
    int2 size = get_image_dim(image);
    if (p.x < 0 || p.y < 0 || p.x >= size.x || p.y >= size.y) return (float4)(0.0f);
    return read_imagef(image, nearest, p);
}
float4 over(float4 dst, float4 src) {
    float a = src.w+dst.w*(1.0f-src.w);
    float3 rgb = a>0.0f ? (src.xyz*src.w+dst.xyz*dst.w*(1.0f-src.w))/a : dst.xyz;
    return floor(clamp((float4)(rgb,a),0.0f,1.0f)*255.0f+0.5f)/255.0f;
}
'''


_probe_lock = threading.Lock()
_warmup_lock = threading.Lock()
_warmups = set()


def device(ffmpeg: str):
    # functools' cache alone permits duplicate concurrent misses. A poster and
    # background playback must share one real driver probe, including failure.
    with _probe_lock:
        return _device(ffmpeg)


def warmup(ffmpeg: str):
    """Initialize the driver while the home screen opens, without rendering media."""
    with _warmup_lock:
        if ffmpeg in _warmups:
            return
        if len(_warmups) >= 8:
            _warmups.clear()
        _warmups.add(ffmpeg)
    threading.Thread(target=device, args=(ffmpeg,), name='cutvoke-gpu-probe', daemon=True).start()


@lru_cache(maxsize=8)
def _device(ffmpeg: str):
    """Probe a real shader dispatch on GPU, not just a listed encoder/filter."""
    source = COMMON + '''
kernel void cv_effect(write_only image2d_t dst, uint index, read_only image2d_t src) {
    int2 p=(int2)(get_global_id(0),get_global_id(1));
    write_imagef(dst,p,pixel(src,p));
}'''
    with tempfile.TemporaryDirectory(prefix='cutvoke-opencl-probe-') as directory:
        path = Path(directory)/'probe.cl'
        path.write_text(source,encoding='utf-8')
        escaped = str(path).replace('\\','/').replace(':','\\:').replace("'","\\'")
        for vendor in ('NVIDIA','AMD','Intel'):
            specification = f'opencl=cutvoke:,device_type=gpu,device_vendor={vendor}'
            try:
                result = subprocess.run([
                    ffmpeg,'-v','error','-nostdin','-init_hw_device',specification,
                    '-filter_hw_device','cutvoke','-f','lavfi','-i','color=red:s=32x32:d=0.04:r=25',
                    '-vf',f"format=rgba,hwupload,program_opencl=source='{escaped}':kernel=cv_effect,hwdownload,format=rgba",
                    '-frames:v','1','-f','rawvideo','pipe:1'],capture_output=True,timeout=8)
                if result.returncode == 0 and len(result.stdout) == 32*32*4:
                    return dict(specification=specification,vendor=vendor)
            except (OSError,subprocess.SubprocessError):
                pass
    return None


class Compiler:
    def __init__(self, directory, selected, fps):
        self.directory = Path(directory)
        self.device = selected
        self.fps = fps
        self.kernels = []
        self.time_offset = 0.
        self._write_lock=threading.Lock()

    def program(self, body, kind, size=None):
        source = COMMON + '\nkernel void cv_effect(write_only image2d_t dst, uint index, read_only image2d_t src) {\n'
        source += 'int2 p=(int2)(get_global_id(0),get_global_id(1));\n'
        source += f'float t=(float)index*{number(self.fps.denominator)}/{number(self.fps.numerator)}+{number(self.time_offset)};\n'
        source += body + '\n}\n'
        key = hashlib.sha256(source.encode()).hexdigest()
        path = self.directory/(key+'.cl')
        with self._write_lock:
            if not path.exists():
                path.write_text(source,encoding='utf-8')
        escaped = str(path).replace('\\','/').replace(':','\\:').replace("'","\\'")
        self.kernels.append(kind)
        dimensions = f':size={size[0]}x{size[1]}' if size else ''
        return (f"format=rgba,hwupload,program_opencl=source='{escaped}':kernel=cv_effect{dimensions},"
                'hwdownload,format=rgba,format=gbrap')

    def composite(self, labels, parts, output):
        """One GPU dispatch composites all full-canvas lanes in track order.

        Upload each lane once, retain all inputs on the GPU for the dispatch,
        and download only the final composite. No per-layer readback chain.
        """
        inputs=','.join(f'read_only image2d_t layer{i}' for i in range(len(labels)))
        source=COMMON+f'\nkernel void cv_scene(write_only image2d_t dst,uint index,{inputs}) {{\n'
        source+='int2 p=(int2)(get_global_id(0),get_global_id(1));\n'
        source+='float4 color=pixel(layer0,p);\n'
        for i in range(1,len(labels)):
            source+=f'color=over(color,pixel(layer{i},p));\n'
        source+='write_imagef(dst,p,color);\n}\n'
        path=self.directory/(hashlib.sha256(source.encode()).hexdigest()+'.cl')
        path.write_text(source,encoding='utf-8')
        escaped=str(path).replace('\\','/').replace(':','\\:').replace("'","\\'")
        hardware=[]
        for i,label in enumerate(labels):
            uploaded=f'{output}_gpu{i}'
            parts.append(f'[{label}]format=rgba,hwupload[{uploaded}]')
            hardware.append(f'[{uploaded}]')
        parts.append(''.join(hardware)+f"program_opencl=source='{escaped}':kernel=cv_scene:inputs={len(labels)},"
                     f'hwdownload,format=rgba,format=gbrap[{output}]')
        self.kernels.append('multi-track-composite')
        return output

    def matte(self, alpha):
        expressions=expression(alpha).split(';')
        body='float W=(float)get_image_width(src), H=(float)get_image_height(src);\n'
        body+='float X=(float)p.x,Y=(float)p.y,T=t,PI=3.14159265358979323846f;\n'
        body+='float '+','.join(f'r{i}=0.0f' for i in range(10))+';\n'
        body+='float4 color=pixel(src,p);\n'
        body+=';\n'.join(expressions[:-1])+';\n' if len(expressions)>1 else ''
        body+=f'color.w=floor(color.w*clamp({expressions[-1]},0.0f,1.0f)*255.0f)/255.0f;\n'
        body+='write_imagef(dst,p,color);'
        return self.program(body,'mask')

    def warp_matte(self, initial, x, y, background):
        body='float W=(float)get_image_width(src),H=(float)get_image_height(src);\n'
        body+='float X=(float)p.x,Y=(float)p.y,T=t,PI=3.14159265358979323846f;\n'
        body+='float '+','.join(f'r{i}=0.0f' for i in range(10))+';\n'
        body+=expression(initial)+';\n'
        body+=f'float2 at=(float2)({expression(x)},{expression(y)});\n'
        body+=f'float value={number(float(background)/255)};\n'
        body+='''if (at.x>=0.0f && at.y>=0.0f && at.x<W && at.y<H) {
  // Match geq's bounded bilinear sampler, including its last-row clamp.
  at=clamp(at,(float2)(0.0f),(float2)(W-2.0f,H-2.0f));
  int2 base=convert_int2(floor(at)); float2 fraction=at-convert_float2(base);
  float a=mix(pixel(src,base).x,pixel(src,base+(int2)(1,0)).x,fraction.x);
  float b=mix(pixel(src,base+(int2)(0,1)).x,pixel(src,base+(int2)(1,1)).x,fraction.x);
  value=floor(mix(a,b,fraction.y)*255.0f)/255.0f;
}
write_imagef(dst,p,(float4)(value,value,value,1.0f));'''
        return self.program(body,'text-mask-motion')+',format=gray'

    def fragments(self, params, width, height):
        rows,cols = int(params.get('rows',12)),int(params.get('columns',3))
        slope = math.tan(math.radians(float(params.get('angle',-24))))
        rmin,rmax = min(0.,-slope*width),max(float(height),height-slope*width)
        step = (rmax-rmin)/rows
        cells=[]
        for row in range(rows):
            a,b=rmin+row*step,rmin+(row+1)*step
            for col in range(cols):
                x0,x1=round(col*width/cols),round((col+1)*width/cols)
                y0=max(0,math.floor(min(a+slope*x0,a+slope*x1)))
                y1=min(height,math.ceil(max(b+slope*x0,b+slope*x1)))
                if y1-y0>2:
                    cells.append((x0,y0,x1-x0,y1-y0,a,b))
        rng=random.Random(int(params.get('seed',17)))
        start,duration=float(params.get('start',0)),float(params.get('duration',1))
        outbound=params.get('mode','assemble')=='disperse'
        heading=math.radians(float(params.get('heading',-165)))
        spread,gap=float(params.get('spread',.65)),float(params.get('gap',3))
        stagger=float(params.get('stagger',.22))
        body='float4 color=pixel(src,p); color.w=0.0f;\n'
        for x0,y0,w,h,a,b in cells:
            delay=rng.random()*stagger*duration
            magnitude=spread*(.35+rng.random()*.75)
            dx=math.cos(heading)*magnitude*width
            dy=math.sin(heading)*magnitude*height+(rng.random()-.5)*height*.35
            if params.get('scatterMode')=='radial':
                ox,oy=float(params.get('originX',.5))*width,float(params.get('originY',.5))*height
                dx=(x0+w/2-ox)*spread*(1.3+rng.random())+dx*.12
                dy=(y0+h/2-oy)*spread*(1.2+rng.random())+dy*.12
            body+=f'''{{
float u=clamp((t-{number(start+delay)})/{number(duration-delay)},0.0f,1.0f);
float q=u*u*u*(10.0f-15.0f*u+6.0f*u*u);
q={'1.0f-q' if outbound else 'q'};
float f=1.0f-q;
int2 origin=(int2)((int)({number(x0)}+{number(dx)}*f),(int)({number(y0)}+{number(dy)}*f-{number(35*params.get('_previewScale',1))}*f*f));
int2 relative=p-origin;
if (relative.x>=0 && relative.y>=0 && relative.x<{w} && relative.y<{h}) {{
  int2 at=relative+(int2)({x0},{y0});
  float distance=(float)at.y-({number(slope)})*(float)at.x-({number(a)});
  float coverage=clamp(distance-{number(gap)}*f,0.0f,1.0f)*clamp({number(b-a)}-distance-{number(gap)}*f,0.0f,1.0f);
  float4 tile=pixel(src,at);
  tile.w=floor(tile.w*coverage*{'fmin(1.0f,q*3.0f)' if params.get('fadeFragments',True) else '1.0f'}*255.0f)/255.0f;
  color=over(color,tile);
}}
}}\n'''
        enabled=f't<{number(start)}' if outbound else f't>={number(start+duration)}'
        body+=f'if ({enabled}) color=over(color,pixel(src,p));\nwrite_imagef(dst,p,color);'
        return self.program(body,'shatter',size=(width,height))


@contextmanager
def compiler(ffmpeg, fps, enabled=True):
    selected = device(ffmpeg) if enabled else None
    if selected is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix='cutvoke-gpu-effects-') as directory:
        yield Compiler(directory,selected,fps)
