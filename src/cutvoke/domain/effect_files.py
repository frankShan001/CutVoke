"""Declared local file dependencies shared by preflight and project packages."""


def effect_file_paths(effect):
    params=effect.get('params') or {}
    if effect.get('effectId')=='cutvoke.fx.lut':
        path=params.get('file')
        return [(path,'lut')] if isinstance(path,str) and path else []
    if effect.get('effectId')=='cutvoke.fx.scene3d':
        return [(obj['texturePath'],'texture') for obj in params.get('objects',[])
                if isinstance(obj.get('texturePath'),str) and obj['texturePath']]
    return []
