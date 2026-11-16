"""Explicit project format boundary for the Issue 55 migration work.

The directory, snapshot and copy-migration entry points use this boundary.
Detection must precede opening a writable ProjectStore. Conversion is a separate,
user-confirmed action; reading never converts or edits the supplied snapshot.
"""
from __future__ import annotations

from copy import deepcopy
from enum import Enum
from cutvoke.domain.model import LEGACY_PROJECT_SCHEMAS

FORMAT = "cutvoke.project-v2"
VERSION = 2


class ProjectFormatError(Exception):
    """Unsupported/corrupt format. Never catch as a legacy-read fallback."""


class ProjectFormat(Enum):
    LEGACY = "legacy"
    CURRENT = "current"


def inspect_snapshot(value: object) -> ProjectFormat:
    if not isinstance(value, dict):
        raise ProjectFormatError("Project snapshot must be an object")
    # Presence of any envelope field prevents legacy reinterpretation.
    if any(key in value for key in ("format", "formatVersion", "document")):
        if value.get("format") != FORMAT or type(value.get("formatVersion")) is not int or value["formatVersion"] != VERSION:
            raise ProjectFormatError("Unsupported project format")
        if set(value) != {"format", "formatVersion", "document"}:
            raise ProjectFormatError("Ambiguous project envelope")
        _validate_document(value.get("document"), "2")
        return ProjectFormat.CURRENT
    _validate_document(value, LEGACY_PROJECT_SCHEMAS)
    return ProjectFormat.LEGACY


def _validate_document(value: object, schema: str | tuple[str, ...]) -> None:
    accepted = (schema,) if isinstance(schema, str) else schema
    if not isinstance(value, dict) or value.get("schemaVersion") not in accepted:
        raise ProjectFormatError("Unsupported project schema")
    if not isinstance(value.get("projectId"), str) or not value["projectId"]:
        raise ProjectFormatError("Missing project identity")
    if not isinstance(value.get("revision"), str):
        raise ProjectFormatError("Missing project revision")
    if not isinstance(value.get("sequence"), dict) and not (
        isinstance(value.get("sequences"), list) and value["sequences"]
        and all(isinstance(item, dict) for item in value["sequences"])
    ):
        raise ProjectFormatError("Missing sequence data")


def convert_snapshot(value: object) -> dict:
    """Convert a detached COPY only. Caller must obtain the user's choice first.

    This container conversion does not repair already-trimmed animation or
    claim that UI, history, media collection, or automation clocks are migrated.
    """
    if inspect_snapshot(value) is not ProjectFormat.LEGACY:
        raise ProjectFormatError("Conversion requires a legacy snapshot")
    document = deepcopy(value)
    document["schemaVersion"] = "2"
    return {"format": FORMAT, "formatVersion": VERSION, "document": document}


def read_current_snapshot(value: object) -> dict:
    if inspect_snapshot(value) is not ProjectFormat.CURRENT:
        raise ProjectFormatError("Legacy project requires explicit conversion")
    return deepcopy(value["document"])


def encode_current_snapshot(document: dict) -> dict:
    _validate_document(document, "2")
    return {"format": FORMAT, "formatVersion": VERSION, "document": deepcopy(document)}
