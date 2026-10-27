"""On-demand creative documents, copied verbatim from the editing Skill at build time."""
from __future__ import annotations

import hashlib
from pathlib import Path


# Relative to skills/cutvoke-editor; this is also the build's explicit allowlist.
GUIDE_FILES = {
    "principles": ("references/principles.md", "Shared creative principles", 1,
                   "New edits and substantial redesigns; apply relevant principles only for local corrections."),
    "music-video": ("references/workflows/music-video.md", "Music video and artistic visual work", 2,
                    "Music-led, performance or abstract visual work, especially strong artistic expression."),
    "documentary": ("references/workflows/documentary.md", "Documentary, travel and observation", 2,
                    "Factual journeys, events and place-led vlogs; preserve evidence and useful location sound."),
    "narrative": ("references/workflows/narrative.md", "Narrative and character-led scenes", 2,
                  "Fiction or a sequence whose main purpose is a developing event or character change."),
    "explainer": ("references/workflows/explainer.md", "Explanation, interview and dialogue", 2,
                  "Tutorials, interviews, talks and speech-led factual explanations."),
    "showcase": ("references/workflows/showcase.md", "Promotion, demonstration and showcase", 2,
                 "Product/brand promotion, feature demonstrations and portfolio showcases."),
    "project-plan": ("references/project-plan.md", "A plan for this project", 3,
                     "Adapt a source-linked plan for a new edit or substantial redesign; reuse adequate existing plans."),
    "review": ("references/review.md", "Review and delivery", 1,
               "Technical and applicable creative acceptance, proportional to the requested change."),
    "efficiency": ("references/efficiency.md", "Efficient iteration and review", 1,
                   "Large media sets, expensive renders and repeated revisions; optional supporting reference."),
}
GUIDE_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "editor_guides"


def descriptor(guide_id: str) -> dict:
    if guide_id not in GUIDE_FILES:
        raise ValueError(f"Unknown creative guide: {guide_id}; use topic=creative for the index")
    _source, title, layer, when = GUIDE_FILES[guide_id]
    return {"id": guide_id, "title": title, "layer": layer, "whenToRead": when,
            "uri": f"cutvoke://creative/{guide_id}",
            "request": {"topic": "creative", "guide": guide_id}}


def catalog() -> dict:
    """Return a small routing index without reading any document bodies."""
    return {"title": "Creative workflow: principles, work type, project plan", "version": 1,
            "layers": ["1: shared principles", "2: relevant purpose/work type", "3: this project's plan"],
            "loading": [
                "For new/substantial creative work read principles, the relevant workflow, then project-plan. Combine a second workflow only when needed.",
                "Local corrections reuse the approved direction: read the needed command and affected review guidance, not all workflows or a new treatment.",
                "Review is proportional; efficiency is optional. User intent determines intensity, not a mandatory gentle or spectacular style.",
                "Bodies are loaded one at a time. Use the returned request or URI; no external Skill, repository or filesystem access is required."],
            "guides": [descriptor(key) for key in GUIDE_FILES]}


def document(guide_id: str) -> dict:
    result = descriptor(guide_id)
    path = GUIDE_DIRECTORY / f"{guide_id}.md"
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ValueError(f"Bundled creative guide unavailable: {guide_id}; repair the CutVoke package") from error
    result.update({"contentFormat": "text/markdown", "content": payload.decode("utf8"),
                   "sha256": hashlib.sha256(payload).hexdigest()})
    # Resolve the Skill's local relative review link for MCP-only readers.
    result["related"] = [descriptor("review")] if guide_id != "review" else []
    return result
