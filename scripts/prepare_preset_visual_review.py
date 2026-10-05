"""Prepare a hash-pinned visual review draft without approving any content.

The reviewer must watch the rendered motion, compare siblings, and fill a
decision plus a specific observation for each row before applying it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cutvoke.core.preset_catalog import PresetCatalog  # noqa: E402


def prepare(family: str, output: Path, subcategory: str | None = None) -> int:
    decisions = []
    for spec in PresetCatalog.builtin().all():
        if spec.family != family or spec.status != "candidate":
            continue
        if subcategory is not None and spec.subcategory != subcategory:
            continue
        path = Path(spec.motion_preview)
        if not path.is_absolute():
            path = spec.asset_root / path
        decisions.append({
            "presetId": spec.id,
            "decision": "pending",
            "motionPreviewSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "observation": "",
        })
    if not decisions:
        raise ValueError("no matching candidates")
    payload = {
        "schemaVersion": 1,
        "reviewer": "",
        "reviewedAt": "",
        "method": "Manual comparison of full dynamic previews and aligned motion sheets",
        "decisions": decisions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return len(decisions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("transition", "animation", "fx", "filter", "text"),
                        required=True)
    parser.add_argument("--subcategory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"prepared {prepare(args.family, args.output, args.subcategory)} pending decisions")
