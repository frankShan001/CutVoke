"""Bounded 3D geometry, transforms and cameras; no learning model involved."""
from __future__ import annotations
import math
from functools import lru_cache
import numpy as np


def animated(value,time):
    result={key:child for key,child in value.items() if key!='poseFrames'}
    keys=value.get('poseFrames') or []
    if not keys:return result
    if time<=keys[0]['at']:return {**result,**{key:child for key,child in keys[0].items() if key!='at'}}
    if time>=keys[-1]['at']:return {**result,**{key:child for key,child in keys[-1].items() if key!='at'}}
    a,b=next((a,b) for a,b in zip(keys,keys[1:]) if a['at']<=time<b['at'])
    q=(time-a['at'])/(b['at']-a['at'])
    for key in (set(a)|set(b))-{'at'}:
        result[key]=a.get(key,result.get(key,0))*(1-q)+b.get(key,result.get(key,0))*q
    return result


def model_matrix(obj):
    x,y,z=(math.radians(obj.get('rotation'+axis,0)) for axis in 'XYZ')
    cx,sx,cy,sy,cz,sz=math.cos(x),math.sin(x),math.cos(y),math.sin(y),math.cos(z),math.sin(z)
    rx=np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])
    ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    rz=np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])
    result=np.eye(4);result[:3,:3]=rz@ry@rx@np.diag([obj.get('scale'+axis,1) for axis in 'XYZ'])
    result[:3,3]=[obj.get(axis,0) for axis in 'xyz']
    return result


def camera_matrix(camera,width,height):
    eye=np.array([camera.get('x',0),camera.get('y',0),camera.get('z',5)],float)
    target=np.array([camera.get('target'+axis,0) for axis in 'XYZ'],float)
    direction=target-eye;norm=np.linalg.norm(direction)
    if norm<1e-6:raise ValueError('camera position and target must differ')
    forward=direction/norm;up=np.array([0,1,0.])
    if abs(np.dot(forward,up))>.999:up=np.array([0,0,1.])
    right=np.cross(forward,up);right/=np.linalg.norm(right);up=np.cross(right,forward)
    view=np.eye(4);view[:3,:3]=np.stack([right,up,-forward]);view[:3,3]=-view[:3,:3]@eye
    near,far=camera.get('near',.05),camera.get('far',100)
    if far<=near:raise ValueError('camera far must exceed near')
    f=1/math.tan(math.radians(camera.get('fov',45))/2)
    projection=np.zeros((4,4));projection[0,0]=f*height/width;projection[1,1]=f
    projection[2,2]=(far+near)/(near-far);projection[2,3]=2*far*near/(near-far);projection[3,2]=-1
    return projection@view,eye


@lru_cache(maxsize=8)
def primitive(shape):
    triangles=[]
    if shape=='plane':
        triangles=[([-1,-1,0],[1,-1,0],[1,1,0]),([-1,-1,0],[1,1,0],[-1,1,0])]
    elif shape=='cube':
        for axis in range(3):
            for sign in (-1,1):
                normal=np.eye(3)[axis]*sign;u=np.eye(3)[(axis+1)%3];v=np.cross(normal,u)
                points=[normal-u-v,normal+u-v,normal+u+v,normal-u+v]
                triangles.extend([(points[0],points[1],points[2]),(points[0],points[2],points[3])])
    elif shape=='sphere':
        def point(a,b):return [math.sin(a)*math.cos(b),math.cos(a),math.sin(a)*math.sin(b)]
        for row in range(12):
            for column in range(24):
                a,b=row*math.pi/12,(row+1)*math.pi/12;c,d=column*math.tau/24,(column+1)*math.tau/24
                triangles.extend([(point(a,c),point(b,c),point(b,d)),(point(a,c),point(b,d),point(a,d))])
    else:raise ValueError('shape must be cube/sphere/plane/mesh')
    positions=np.asarray(triangles,np.float32)
    if shape=='sphere':
        winding=np.sum(np.cross(positions[:,1]-positions[:,0],positions[:,2]-positions[:,0])*positions.mean(axis=1),axis=1)<0
        positions[winding]=positions[winding][:,[0,2,1]]
        normals=positions.copy()
    else:
        normals=np.cross(positions[:,1]-positions[:,0],positions[:,2]-positions[:,0]);normals/=np.maximum(1e-9,np.linalg.norm(normals,axis=1,keepdims=True))
        normals=np.repeat(normals[:,None,:],3,axis=1)
    uv=np.tile(np.array([[[0,0],[1,0],[1,1]],[[0,0],[1,1],[0,1]]],np.float32),(math.ceil(len(positions)/2),1,1))[:len(positions)]
    return np.concatenate((positions,normals,uv),axis=2).reshape(-1,8)


def geometry(obj):
    if obj.get('shape','cube')!='mesh':return primitive(obj.get('shape','cube'))
    vertices=np.array([[point[axis] for axis in 'xyz'] for point in obj.get('vertices',[])],np.float32)
    faces=np.array([[face[axis] for axis in 'abc'] for face in obj.get('triangles',[])],int)
    if not len(vertices) or not len(faces) or faces.min()<0 or faces.max()>=len(vertices):raise ValueError('mesh triangles reference unavailable vertices')
    positions=vertices[faces];normals=np.cross(positions[:,1]-positions[:,0],positions[:,2]-positions[:,0])
    normals/=np.maximum(1e-9,np.linalg.norm(normals,axis=1,keepdims=True));normals=np.repeat(normals[:,None,:],3,axis=1)
    coords=obj.get('uvs')
    uv=np.array([[point['u'],point['v']] for point in coords],np.float32)[faces] if coords else np.zeros((*positions.shape[:2],2),np.float32)
    return np.concatenate((positions,normals,uv),axis=2).reshape(-1,8)


def import_obj(text):
    if not isinstance(text,str) or len(text.encode('utf-8'))>2*1024*1024:raise ValueError('OBJ text must be at most 2 MiB')
    vertices=[];coords=[];expanded=[];uvs=[];triangles=[];lookup={};warnings=[]
    for raw in text.splitlines():
        words=raw.split('#',1)[0].split()
        if not words:continue
        if words[0] in {'v','vt'}:
            count=3 if words[0]=='v' else 2
            values=[float(value) for value in words[1:count+1]]
            if len(values)!=count or any(not math.isfinite(value) or abs(value)>100000 for value in values):raise ValueError('invalid OBJ coordinate')
            (vertices if words[0]=='v' else coords).append(values)
            if len(vertices)>4096 or len(coords)>4096:raise ValueError('OBJ accepts at most 4096 input vertices/UVs')
        elif words[0]=='f':
            if not 3<=len(words)-1<=64:raise ValueError('OBJ faces require 3..64 vertices')
            indices=[]
            for word in words[1:]:
                parts=word.split('/');v=int(parts[0]);v=v-1 if v>0 else len(vertices)+v
                t=int(parts[1]) if len(parts)>1 and parts[1] else None
                if t is not None:t=t-1 if t>0 else len(coords)+t
                if not 0<=v<len(vertices) or t is not None and not 0<=t<len(coords):raise ValueError('OBJ face index out of bounds')
                pair=(v,t)
                if pair not in lookup:
                    lookup[pair]=len(expanded);expanded.append(dict(zip('xyz',vertices[v])))
                    uvs.append(dict(zip(('u','v'),coords[t] if t is not None else (0,0))))
                indices.append(lookup[pair])
            triangles.extend(dict(zip('abc',(indices[0],indices[i],indices[i+1]))) for i in range(1,len(indices)-1))
        elif words[0] in {'mtllib','usemtl'} and not warnings:warnings.append('OBJ material references are not loaded. Assign a local material/texture explicitly; no external files or URLs are fetched.')
        if len(expanded)>4096 or len(triangles)>8192:raise ValueError('mesh exceeds 4096 expanded vertices or 8192 triangles')
    if not triangles:raise ValueError('OBJ contains no renderable faces')
    return {'shape':'mesh','vertices':expanded,'triangles':triangles,'uvs':uvs},warnings
