"""Small original media fixtures for composition integration tests."""
import subprocess
import numpy as np
from PIL import Image
from cutvoke.domain.model import AssetReference, Clip, Track
from cutvoke.domain.protocol import Command
from cutvoke.domain.rational import Rational as R
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService
from cutvoke.storage.store import ProjectStore


def setup(tmp_path):
    store = ProjectStore(str(tmp_path / "library.sqlite"))
    service = EditService(store)
    project = service.create_project("composition", width=96, height=64, fps=R.of(10))
    image = tmp_path / "red.png"
    Image.new("RGB", (96, 64), (180, 40, 20)).save(image)
    project.sequence.tracks = [Track("v_base", "video", [Clip("base", AssetReference("red", str(image)), R.of(0), R.of(3), R.of(0))])]
    store.save(project)
    def command(kind, **payload):
        return service.execute(Command(kind, payload, project_id="composition", expected_revision=service.get_project("composition").revision))
    renderer = RenderService(gpu_effects=False)
    renderer.gpu_acceleration = False
    return service, store, renderer, command


def frames(path, width=96, height=64):
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True)
    return np.frombuffer(result.stdout, np.uint8).reshape(-1, height, width, 3).astype(float)
