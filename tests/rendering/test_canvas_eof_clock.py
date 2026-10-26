"""A finite canvas must not retain a source-file EOF in the wrong time base."""
import subprocess

import pytest

from cutvoke.domain.protocol import Command
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.rendering.render import RenderService


@pytest.mark.parametrize("source_fps", [7, 15, 60])
def test_silent_three_shot_transition_preserves_clock_and_final_blue(tmp_path, source_fps):
    renderer = RenderService()
    service = EditService()
    project = service.create_project("finite-canvas-clock", width=160, height=90, fps=Rational.of(15))
    for index, color in enumerate(("red", "green", "blue")):
        source = tmp_path / f"{color}.mp4"
        subprocess.run([renderer.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                        f"color=c={color}:s=160x90:r={source_fps}:d=1", "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, timeout=30)
        current = service.get_project(project.project_id)
        service.execute(Command(type="clip.insert", project_id=current.project_id,
            expected_revision=current.revision, payload={
                "trackId": "video", "clipId": color, **({"createTrackKind": "video"} if index == 0 else {}),
                "sourcePath": str(source), "timelineStart": {"num": str(index), "den": "1"},
                "timelineEnd": {"num": str(index + 1), "den": "1"}}))
    current = service.get_project(project.project_id)
    service.execute(Command(type="effect.setTransition", project_id=current.project_id,
        expected_revision=current.revision, payload={"clipId": "green",
            "effectId": "cutvoke.transition.crossfade", "params": {"duration": .3}}))
    output = tmp_path / "three-shots.mp4"
    result = renderer.render(service.get_project(project.project_id), str(output))
    assert result["duration"] == pytest.approx(3, abs=1e-6)
    assert result["has_audio"] is False
    decoded = subprocess.run([renderer.ffmpeg, "-v", "error", "-nostdin", "-i", str(output),
                              "-vf", "crop=2:2:0:0", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, check=True, timeout=30).stdout
    assert len(decoded) == 45 * 12
    for at, expected in ((.5, (253, 0, 0)), (1.5, (0, 127, 0)), (2.5, (0, 0, 254))):
        sample = decoded[int(at * 15) * 12:int(at * 15) * 12 + 3]
        assert max(abs(actual - wanted) for actual, wanted in zip(sample, expected)) <= 4, (at, list(sample))
