"""Declared local file dependencies shared by preflight and project packages."""
from copy import deepcopy


def _file_slots(effect):
    params = effect.get('params') or {}
    if effect.get('effectId') == 'cutvoke.fx.lut':
        yield params, 'file', 'lut'
    if effect.get('effectId') == 'cutvoke.fx.scene3d':
        for obj in params.get('objects', []):
            yield obj, 'texturePath', 'texture'


def effect_file_paths(effect):
    return [(container[key], kind) for container, key, kind in _file_slots(effect)
            if isinstance(container.get(key), str) and container[key]]


def map_effect_files(effect, replace):
    """Copy only declared file slots; parameter names can also mean geometry."""
    result = deepcopy(effect)
    for container, key, _kind in _file_slots(result):
        path = container.get(key)
        if isinstance(path, str) and path:
            container[key] = replace(path)
    return result
