"""Isolated local server for browser regression tests; never touches user projects."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from cutvoke.interfaces.httpapi import HttpApi, serve
from cutvoke.domain.protocol import Actor, Command
from cutvoke.domain.rational import Rational
from cutvoke.rendering.render import RenderService
from cutvoke.editing.service import EditService
from cutvoke.storage.store import ProjectStore
from cutvoke.runtime import web_directory


def seed_fixture(service: EditService, root: str) -> None:
    paths: list[str] = []
    for color in ("red", "green", "blue"):
        path = os.path.join(root, f"{color}.mp4")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=15:d=5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", path,
        ], check=True, capture_output=True)
        paths.append(path)
    project = service.create_project("e2e-fixture", width=160, height=90,
                                     fps=Rational.of(15))

    def apply(kind: str, payload: dict, key: str) -> None:
        current = service.get_project(project.project_id)
        service.execute(Command(
            type=kind, payload=payload, command_id=f"fixture-{key}",
            project_id=project.project_id, expected_revision=current.revision,
            actor=Actor("test", "browser-fixture"),
        ))

    apply("track.add", {"trackId": "v1", "kind": "video"}, "track")
    for index, path in enumerate(paths):
        apply("clip.insert", {
            "trackId": "v1", "clipId": f"color-{index}", "sourcePath": path,
            "timelineStart": {"num": index * 5, "den": 1},
            "timelineEnd": {"num": (index + 1) * 5, "den": 1},
        }, f"clip-{index}")
    apply("caption.add", {
        "captionId": "fixture-caption", "text": "原字幕",
        "start": {"num": 1, "den": 1}, "end": {"num": 3, "den": 1},
    }, "caption")


def main() -> None:
    web_dir = web_directory()
    if web_dir is None:
        raise RuntimeError("build the Web editor before starting e2e_server")
    port = int(os.environ.get("CUTVOKE_E2E_PORT", "8799"))
    with tempfile.TemporaryDirectory(prefix="cutvoke-e2e-") as root:
        # The optional extension journal belongs to this disposable runtime.
        # Status reads must never create files in the user's actual profile.
        os.environ["CUTVOKE_EXTENSION_HOME"] = os.path.join(root, "extensions")
        service = EditService(store=ProjectStore(os.path.join(root, "projects.sqlite")))
        seed_fixture(service, root)
        if os.environ.get("CUTVOKE_E2E_FOLDERS") == "1":
            from cutvoke.storage.project_directory import ProjectLibraryStore
            service.close(); service.store.close()
            service = EditService(store=ProjectLibraryStore(os.path.join(root, "projects.sqlite")))
        api = HttpApi(service, RenderService(), media_dir=os.path.join(root, "media"))
        try:
            serve(api, host="127.0.0.1", port=port, web_dir=str(web_dir))
        finally:
            api.close()


if __name__ == "__main__":
    main()
