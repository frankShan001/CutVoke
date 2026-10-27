"""Clone editor identities and explicit references without rewriting user text."""
from cutvoke.domain.model import new_id


def fresh_data(value):
    mapping = {}
    def collect(item, context=""):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {"params", "assetRef"}:
                    continue
                if key == "instanceId" or (key == "id" and context != "exposedProperties"):
                    if isinstance(child, str): mapping.setdefault(child, new_id("copy"))
                collect(child, key)
        elif isinstance(item, list):
            for child in item: collect(child, context)
    collect(value)
    references = {"id", "instanceId", "clipId", "attachedToClipId", "activeTrackId", "trackId", "nestedTrackId", "busId", "sequenceRef"}
    def parameter(value):
        pieces = value.split(":", 2)
        return f"effect:{mapping.get(pieces[1], pieces[1])}:{pieces[2]}" if len(pieces) == 3 and pieces[0] == "effect" else value
    def rewrite(item, context=""):
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if key in {"params", "assetRef"}:
                    import copy
                    result[key] = copy.deepcopy(child)
                    continue
                new_key = parameter(key)
                if key == "id" and context == "exposedProperties": result[new_key] = child
                elif key == "param" and isinstance(child, str): result[new_key] = parameter(child)
                elif key in references and isinstance(child, str): result[new_key] = mapping.get(child, child)
                elif key in {"trackIds", "clipIds"} and isinstance(child, list): result[new_key] = [mapping.get(value, value) for value in child]
                else: result[new_key] = rewrite(child, key)
            return result
        if isinstance(item, list): return [rewrite(child, context) for child in item]
        return item
    return rewrite(value)
