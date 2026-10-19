"""Content-free effect recipes. This module never receives a project or media store.

Sharing is reconstruction from a closed schema, never directory/project packaging.
All strings in a recipe belong to engine-owned vocabularies (or hex colors).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import zipfile

from .effects import EffectRegistry, animation_slot


MAX_BYTES = 65536
FORMAT = "cutvoke-effect-module"
PRIVACY_ZH = (
    "准备分享的特效包仅包含效果配方、参数和程序生成的通用说明，"
    "不包含您的视频、图片、音频、文字或字幕正文，也不包含工程、个人信息、文件路径和编辑历史。"
    "当前仅在本地准备，尚未上传任何内容；公开提交需要您明确同意。"
)
PRIVACY_EN = (
    "This effect package contains only effect settings and app-generated generic documentation. "
    "It contains no personal information, project videos, images, audio, text or captions, "
    "project files, file paths or editing history. Preparation is local; nothing has been uploaded. "
    "Public submission requires your explicit consent."
)
PRIVACY = {"zh-CN": PRIVACY_ZH, "en": PRIVACY_EN}
TEXT_STYLE_KEYS = ("fontSize", "color", "strokeColor", "strokeWidth", "bold",
                   "align", "x", "y", "rotation", "scale")
MOTION_BOUNDS = {"opacity": (0, 1), "scale": (0.05, 5), "rotation": (-180, 180),
                 "x": (-2, 2), "y": (-2, 2)}
MOTION_SCHEMA = {"type": "object", "properties": {
    "param": {"type": "string", "enum": list(MOTION_BOUNDS)},
    "at": {"type": "number", "minimum": 0, "maximum": 1},
    "value": {"type": "number"},
    "interpolation": {"type": "string", "enum": ["linear", "ease-in", "ease-out"]}},
    "required": ["param", "at", "value"], "additionalProperties": False}
RECIPE_SCHEMA = {"type": "object", "properties": {
    "effects": {"type": "array", "maxItems": 8, "items": {
        "type": "object", "properties": {"effectId": {"type": "string"},
            "params": {"type": "object", "description": "Only safe built-in parameter schemas from effect_module_list(includeSchemas=true)."}},
        "required": ["effectId"], "additionalProperties": False}},
    "motion": {"type": "array", "maxItems": 64, "items": MOTION_SCHEMA},
    "textStyle": {"type": "object", "description": "Style only; content, font paths and user text are forbidden."}},
    "additionalProperties": False}


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        # Never reflect rejected user content into an export or an error message.
        raise ValueError("Effect module has forbidden or missing fields; user content is not allowed")


def _number(value, low=-10000, high=10000):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError("Effect module numbers must be finite and within their allowed range")
    if abs(value - round(value, 6)) > 1e-10:
        raise ValueError("Effect module numbers support at most six decimal places")
    return value


def _safe_schema(schema, key=""):
    """Restrict schemas, not suspicious substrings in user values."""
    kind = schema.get("type")
    if kind in ("number", "integer", "boolean"):
        return True
    if kind == "string":
        return bool(schema.get("enum")) or key.lower().endswith("color")
    if kind == "object":
        return (schema.get("additionalProperties") is False
                and all(_safe_schema(child, name) for name, child in schema.get("properties", {}).items()))
    # Arbitrary arrays, expressions, paths, text and resource references are not shareable.
    return False


def _safe_value(value, schema, key=""):
    kind = schema.get("type")
    if kind in ("number", "integer"):
        _number(value, max(-10000, schema.get("minimum", -10000)),
                min(10000, schema.get("maximum", 10000)))
        if kind == "integer" and type(value) is not int:
            raise ValueError("Effect module requires an integer parameter")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError("Effect module parameter is below its allowed range")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ValueError("Effect module parameter is above its allowed range")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("Effect module requires a boolean parameter")
    elif kind == "string":
        if not isinstance(value, str):
            raise ValueError("Invalid effect module option")
        if schema.get("enum"):
            if value not in schema["enum"]:
                raise ValueError("Effect module option is outside the built-in vocabulary")
        elif not key.lower().endswith("color") or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Effect modules do not accept free text or paths; colors must be #RRGGBB")
    elif kind == "object":
        props = schema.get("properties", {})
        _object(value, props, schema.get("required", ()))
        return {name: _safe_value(item, props[name], name) for name, item in value.items()}
    else:
        raise ValueError("Unsupported effect module parameter type")
    return value


class EffectModuleLibrary:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        # Deliberately exclude discovered/user-supplied effect manifests.
        self.builtins = EffectRegistry()

    def supported(self):
        return {spec.id: spec for spec in self.builtins.all()
                if spec.id != "cutvoke.text" and not spec.dependencies and _safe_schema(spec.parameters)}

    def schemas(self):
        from .command_schema import normalize_schema
        return {"recipe": copy.deepcopy(RECIPE_SCHEMA),
                "effects": [{"effectId": spec.id, "name": spec.label(),
                             "parameters": normalize_schema(spec.parameters),
                             "appliesTo": list(spec.applies_to)} for spec in self.supported().values()],
                "textStyle": {"type": "object", "additionalProperties": False,
                    "properties": {key: normalize_schema(self.builtins.get("cutvoke.text").parameters["properties"][key])
                                   for key in TEXT_STYLE_KEYS}},
                "motion": copy.deepcopy(MOTION_SCHEMA)}

    def validate_recipe(self, recipe):
        _object(recipe, ("effects", "motion", "textStyle"))
        result = {"effects": [], "motion": [], "textStyle": {}}
        effects = recipe.get("effects", [])
        if not isinstance(effects, list) or len(effects) > 8:
            raise ValueError("Use at most eight effects in one module")
        seen_ids, seen_slots = set(), set()
        supported = self.supported()
        for effect in effects:
            _object(effect, ("effectId", "params"), ("effectId",))
            eid = effect["effectId"]
            if not isinstance(eid, str) or eid not in supported:
                raise ValueError("Effect is not in the content-free built-in module catalog")
            spec = supported[eid]
            slot = animation_slot(eid) or ("transition" if spec.category == "transition" else None)
            if eid in seen_ids or (slot and slot in seen_slots):
                raise ValueError("Duplicate effect or animation/transition slot in module")
            seen_ids.add(eid)
            if slot:
                seen_slots.add(slot)
            params = _safe_value(effect.get("params", {}), spec.parameters)
            params = self.builtins.validate_params(eid, params)
            # Validate again after defaults: defaults cannot introduce content either.
            params = _safe_value(params, spec.parameters)
            result["effects"].append({"effectId": eid, "params": params})
        motion = recipe.get("motion", [])
        if not isinstance(motion, list) or len(motion) > 64:
            raise ValueError("Use at most 64 normalized keyframes")
        seen_frames = set()
        for frame in motion:
            _object(frame, MOTION_SCHEMA["properties"], ("param", "at", "value"))
            param = frame["param"]
            if not isinstance(param, str) or param not in MOTION_BOUNDS:
                raise ValueError("Unsupported motion parameter")
            at = _number(frame["at"], 0, 1)
            value = _number(frame["value"], *MOTION_BOUNDS[param])
            interpolation = frame.get("interpolation", "linear")
            if interpolation not in ("linear", "ease-in", "ease-out"):
                raise ValueError("Unsupported interpolation")
            if (param, at) in seen_frames:
                raise ValueError("Duplicate normalized keyframe")
            seen_frames.add((param, at))
            result["motion"].append({"param": param, "at": at, "value": value, "interpolation": interpolation})
        result["motion"].sort(key=lambda frame: (frame["param"], frame["at"]))
        style = recipe.get("textStyle", {})
        _object(style, TEXT_STYLE_KEYS)
        props = self.builtins.get("cutvoke.text").parameters["properties"]
        result["textStyle"] = {key: _safe_value(value, props[key], key) for key, value in style.items()}
        if not any(result.values()):
            raise ValueError("Effect module must contain an effect, motion or text style")
        # A video target also exposes audio operators. Reject recipes for which
        # no supported clip kind could ever satisfy all parts (e.g. a media
        # filter combined with a title-only style), rather than saving dead modules.
        targets = ({"image"}, {"video", "audio"}, {"audio"}, {"text"})
        if not any(
                (not result["textStyle"] or "text" in target)
                and (not result["motion"] or bool(target & {"image", "video", "text"}))
                and all(target.intersection(supported[item["effectId"]].applies_to)
                        for item in result["effects"])
                for target in targets):
            raise ValueError("Module parts have no compatible clip kind; save separate modules")
        if len(encoded(result)) > MAX_BYTES:
            raise ValueError("Effect module is too large")
        return result

    def document(self, recipe):
        return {"format": FORMAT, "formatVersion": 1, "recipe": self.validate_recipe(recipe)}

    @staticmethod
    def identity(document):
        return "cutvoke.module." + hashlib.sha256(encoded(document)).hexdigest()

    def validate_document(self, document):
        _object(document, ("format", "formatVersion", "recipe"), ("format", "formatVersion", "recipe"))
        if document["format"] != FORMAT or type(document["formatVersion"]) is not int or document["formatVersion"] != 1:
            raise ValueError("Unsupported effect module format")
        return self.document(document["recipe"])

    def _path(self, module_id):
        if not isinstance(module_id, str) or not re.fullmatch(r"cutvoke\.module\.[0-9a-f]{64}", module_id):
            raise ValueError("Invalid effect module ID")
        path = self.root / (module_id + ".json")
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("Effect module files must stay inside the local library")
        return path

    def save(self, recipe):
        document = self.document(recipe)
        module_id = self.identity(document)
        path = self._path(module_id)
        self.root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if self.load(module_id) != document:
                raise ValueError("Existing module does not match its content ID")
        else:
            try:
                self._publish(path, encoded(document))
            except FileExistsError:
                # Another process can save the same content while this one publishes.
                if self.load(module_id) != document:
                    raise ValueError("Concurrent module save has different content")
        return {"moduleId": module_id, "module": document, "privacyNotice": dict(PRIVACY),
                "containsUserContent": False, "uploaded": False}

    @staticmethod
    def _publish(path, data):
        # A same-volume hard link publishes complete bytes without overwriting a concurrent file.
        fd, temporary = tempfile.mkstemp(prefix=".effect-module-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.link(temporary, path)
        finally:
            os.unlink(temporary)

    def load(self, module_id):
        path = self._path(module_id)
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        except OSError as error:
            raise ValueError("Local effect module is unavailable") from error
        document = self.validate_document(_decode(raw))
        if self.identity(document) != module_id:
            raise ValueError("Effect module content ID mismatch; refusing changed local content")
        return document

    def list(self, offset=0, limit=20):
        paths = sorted(self.root.glob("cutvoke.module.*.json")) if self.root.exists() else []
        items, rejected = [], 0
        for path in paths:
            try:
                document = self.load(path.stem)
            except ValueError:
                rejected += 1
                continue
            items.append({"moduleId": path.stem, "module": document})
        return {"modules": items[offset:offset + limit], "total": len(items), "rejectedCount": rejected,
                "nextOffset": offset + limit if offset + limit < len(items) else None,
                "privacyNotice": dict(PRIVACY), "uploaded": False}

    def package_files(self, document):
        document = self.validate_document(document)
        module_id = self.identity(document)
        prefix = "effects/recipes/" + module_id + "/"
        return {prefix + "module.json": encoded(document),
                prefix + "README.md": README.encode("utf-8"),
                prefix + "LICENSE": LICENSE.encode("utf-8"),
                prefix + "PRIVACY.txt": (PRIVACY_ZH + "\n\n" + PRIVACY_EN + "\n").encode("utf-8")}

    def export(self, module_id, out_path=None):
        document = self.load(module_id)  # Revalidate on every export; never copy a folder.
        files = self.package_files(document)
        output = Path(out_path).expanduser() if out_path else self.root / "shares" / (module_id + ".zip")
        if not output.is_absolute() or output.suffix.lower() != ".zip":
            raise ValueError("outPath must be an absolute .zip path")
        if output.exists() or output.is_symlink():
            raise ValueError("Share package already exists; choose another outPath")
        output.parent.mkdir(parents=True, exist_ok=True)
        # ZIP member names, timestamps, permissions and contents are app-generated.
        import io
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        data = buffer.getvalue()
        self._publish(output, data)
        return {"moduleId": module_id, "path": str(output), "sizeBytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "files": list(files),
                "privacyNotice": dict(PRIVACY), "containsUserContent": False, "uploaded": False,
                "requiresExplicitConsentToPublish": True}

    def import_package(self, path):
        source = Path(path).expanduser()
        if not source.is_absolute():
            raise ValueError("Use a small absolute effect module ZIP path")
        try:
            if source.stat().st_size > MAX_BYTES * 2:
                raise ValueError("Effect module ZIP exceeds size limit")
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist()
                if len(entries) != 4 or any(entry.file_size > MAX_BYTES for entry in entries):
                    raise ValueError("Share package must contain only four generated module files")
                names = [entry.filename for entry in entries]
                module_files = [name for name in names if name.endswith("/module.json")]
                if len(module_files) != 1:
                    raise ValueError("Share package has no unique module definition")
                document = self.validate_document(_decode(archive.read(module_files[0])))
                expected = self.package_files(document)
                if len(set(names)) != 4 or set(names) != set(expected):
                    raise ValueError("Share package has forbidden files or paths")
                if any(archive.read(name) != data for name, data in expected.items()):
                    raise ValueError("Share package contains unapproved text or changed settings")
        except (zipfile.BadZipFile, OSError, RuntimeError) as error:
            raise ValueError("Invalid effect module ZIP") from error
        return self.save(document["recipe"])

    def validate_contribution(self, folder):
        """Verify the exact exported files, including absence of nested attachments."""
        folder = Path(folder)
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Recipe contribution must be a regular directory")
        entries = list(folder.iterdir())
        if {entry.name for entry in entries} != {"module.json", "README.md", "LICENSE", "PRIVACY.txt"} or any(
                entry.is_symlink() or not entry.is_file() or entry.stat().st_size > MAX_BYTES for entry in entries):
            raise ValueError("Recipe contributions accept only the four generated files; no user assets")
        with (folder / "module.json").open("rb") as stream:
            document = self.validate_document(_decode(stream.read(MAX_BYTES + 1)))
        if folder.name != self.identity(document) or folder.parent.name != "recipes":
            raise ValueError("Recipe contribution directory must match its content ID under recipes/")
        expected = {Path(name).name: data for name, data in self.package_files(document).items()}
        for entry in entries:
            if entry.stat().st_size > MAX_BYTES or entry.read_bytes() != expected[entry.name]:
                raise ValueError("Recipe contribution has unapproved content")
        return document


def _decode(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("Effect module exceeds size limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate fields in effect module")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Non-finite numbers are not allowed")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Invalid effect module JSON") from error


# These generated documents are part of the v1 wire format. Keep them stable;
# changing templates requires a versioned importer compatibility policy.
README = """# CutVoke reusable effect module

This is a content-free declarative recipe, licensed under MIT. It uses existing
CutVoke built-in effects; it does not contain executable plugin code or assets.

Import this ZIP with effect_module_import(path=<absolute ZIP path>), or save the
recipe in module.json using effect_module_save(recipe=...). Obtain the returned
moduleId and apply it with command_preview / command_apply, type=effectModule.apply,
payload={clipId, moduleId}. Use the current revision, stable commandId and edit lease.
Effects and affected motion channels are replaced; unrelated effects remain.
Motion at=0..1 spans the target clip; x/y values are fractions of canvas dimensions.
textStyle changes the existing editable title style and preserves its text.
Engine effect durations use seconds; inspect transition handles and short clips.

Before submitting a PR, test on synthetic media and fixed generic sample text.
Check preview, export, save/reopen and undo/redo. This package contains no claims
of visual acceptance and intentionally excludes all project-derived evidence.
Never add project archives, screenshots, source media, captions, logs or personal
information to this package. Submission and maintainer review are separate steps.
"""

LICENSE = """MIT License

Copyright (c) CutVoke effect module contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
