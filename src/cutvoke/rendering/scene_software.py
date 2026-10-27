"""Reference local triangle rasterizer for machines without OpenGL 3.3.

Frustum clipping, perspective-correct attributes and per-pixel depth peeling
share the scene model with the accelerated renderer. No model weights needed.
"""
import numpy as np
from PIL import Image,ImageColor
from cutvoke.rendering.scene_geometry import animated,camera_matrix,geometry,model_matrix


def _clip(vertices):
    polygon=list(vertices)
    for axis,sign in ((0,1),(0,-1),(1,1),(1,-1),(2,1),(2,-1)):
        output=[]
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            da=a[3]+sign*a[axis];db=b[3]+sign*b[axis]
            if da>=0:output.append(a)
            if (da>=0)!=(db>=0):output.append(a+(b-a)*(da/(da-db)))
        polygon=output
        if len(polygon)<3:return []
    return [np.array([polygon[0],polygon[i],polygon[i+1]]) for i in range(1,len(polygon)-1)]


def _sample(image,u,v):
    h,w=image.shape[:2];x=np.clip(u*w-.5,0,w-1);y=np.clip((1-v)*h-.5,0,h-1)
    x0=x.astype(int);y0=y.astype(int);x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1)
    fx=(x-x0)[...,None];fy=(y-y0)[...,None]
    return (image[y0,x0]*(1-fx)+image[y0,x1]*fx)*(1-fy)+(image[y1,x0]*(1-fx)+image[y1,x1]*fx)*fy


def render(params,width,height,time):
    from cutvoke.rendering.scene3d import texture
    vp,eye=camera_matrix(animated(params.get('camera',{}),time),width,height)
    light=params.get('light',{});light_pos=np.array([light.get(axis,value) for axis,value in zip('xyz',(-3,4,5))])
    light_color=np.array(ImageColor.getrgb(light.get('color','#ffffff')))/255
    objects=[]
    for raw in params.get('objects',[]):
        obj=animated(raw,time);data=geometry(obj);model=model_matrix(obj)
        world=(model@np.concatenate((data[:,:3],np.ones((len(data),1))),axis=1).T).T
        normals=(np.linalg.inv(model[:3,:3]).T@data[:,3:6].T).T
        clip=(vp@world.T).T;combined=np.concatenate((clip,world[:,:3],normals,data[:,6:8]),axis=1)
        triangles=[]
        for tri in combined.reshape(-1,3,12):triangles.extend(_clip(tri))
        objects.append((obj,triangles,np.array(texture(obj.get('texturePath')),np.float32)/255))
    result=np.zeros((height,width,4),np.float32);previous=np.full((height,width),-1.,np.float32)
    for layer_index in range(params.get('depthLayers',4)):
        depth=np.ones((height,width),np.float32);layer=np.zeros((height,width,4),np.float32)
        for obj,triangles,image in objects:
            color=np.array(ImageColor.getrgb(obj.get('color','#55bbff')))/255;roughness=obj.get('roughness',.5)
            for tri in triangles:
                inv_w=1/tri[:,3];ndc=tri[:,:3]*inv_w[:,None]
                screen=np.stack(((ndc[:,0]*.5+.5)*width,(.5-ndc[:,1]*.5)*height),axis=1)
                x0=max(0,int(np.floor(screen[:,0].min())));x1=min(width,int(np.ceil(screen[:,0].max())))
                y0=max(0,int(np.floor(screen[:,1].min())));y1=min(height,int(np.ceil(screen[:,1].max())))
                a,b,c=screen;den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
                if abs(den)<1e-10 or x1<=x0 or y1<=y0:continue
                for row in range(y0,y1,64):
                    stop=min(y1,row+64);yy,xx=np.mgrid[row:stop,x0:x1];xx=xx+.5;yy=yy+.5
                    wa=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
                    wb=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den;wc=1-wa-wb
                    bary=np.stack((wa,wb,wc),axis=-1)
                    z=(bary@ndc[:,2])*.5+.5
                    valid=(wa>=-1e-7)&(wb>=-1e-7)&(wc>=-1e-7)&(z<=depth[row:stop,x0:x1])&(z>previous[row:stop,x0:x1]+(1e-6 if layer_index else 0))
                    if not valid.any():continue
                    weights=bary[valid]*inv_w;weights/=weights.sum(axis=1,keepdims=True)
                    attrs=weights@tri[:,4:];world=attrs[:,:3];normal=attrs[:,3:6];uv=attrs[:,6:8]
                    normal/=np.maximum(1e-9,np.linalg.norm(normal,axis=1,keepdims=True))
                    view=eye-world;view/=np.maximum(1e-9,np.linalg.norm(view,axis=1,keepdims=True))
                    normal*=np.where(np.sum(normal*view,axis=1,keepdims=True)<0,-1,1)
                    direction=light_pos-world;direction/=np.maximum(1e-9,np.linalg.norm(direction,axis=1,keepdims=True))
                    diffuse=np.maximum(0,np.sum(normal*direction,axis=1,keepdims=True))
                    half=direction+view;half/=np.maximum(1e-9,np.linalg.norm(half,axis=1,keepdims=True))
                    shine=np.maximum(0,np.sum(normal*half,axis=1,keepdims=True))**(120*(1-roughness)+4*roughness)*(1-roughness)*.25
                    sampled=_sample(image,uv[:,0],uv[:,1]);alpha=sampled[:,3]*obj.get('opacity',1)
                    pixels=np.concatenate((np.clip(sampled[:,:3]*color*(light.get('ambient',.25)+light.get('intensity',.9)*diffuse*light_color)+shine*light_color,0,1),alpha[:,None]),axis=1)
                    rows,columns=np.nonzero(valid);keep=alpha>=.002;rows=rows[keep]+row;columns=columns[keep]+x0
                    depth[rows,columns]=z[valid][keep];layer[rows,columns]=pixels[keep]
        weight=layer[...,3:]*(1-result[...,3:]);result[...,:3]+=layer[...,:3]*weight;result[...,3:]+=weight
        if not np.any((layer[...,3]>.002)&(result[...,3]<.998)):break
        previous=depth
    result[...,:3]/=np.maximum(result[...,3:],1e-8)
    return Image.fromarray(np.clip(result*255,0,255).round().astype(np.uint8))
