"""P1 效果扩充与 Agent 试算接口的契约测试。

这些测试不把“资源库里有名字”当作完成：每个新效果必须同时存在于注册表、
带可校验的默认参数，并可被渲染编译器转换为真实的 ffmpeg 滤镜图节点。
"""

from __future__ import annotations

import unittest
import shutil
import subprocess

from cutvoke.core.effects import default_registry
from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.protocol import Actor, Command
from cutvoke.core.render import RenderService, _AUDIO_FX_STEPS
from cutvoke.core.service import EditService


NEW_EFFECT_IDS = {
    "cutvoke.fx.vibrance": "vibrance=",
    "cutvoke.fx.colorize": "colorize=",
    "cutvoke.fx.deband": "deband=",
    "cutvoke.fx.lens": "lenscorrection=",
    "cutvoke.fx.cas": "cas=",
    "cutvoke.fx.vflip": "vflip",
    "cutvoke.fx.filmgrain": "noise=all_seed=314159",
    "cutvoke.fx.grid": "drawgrid=",
}


class BuiltinEffectExpansionTests(unittest.TestCase):
    def test_builtin_effect_parameters_have_explanations(self) -> None:
        for spec in default_registry(refresh=True).all():
            for name, schema in spec.parameters.get("properties", {}).items():
                with self.subTest(effect_id=spec.id, parameter=name):
                    self.assertTrue(schema.get("description"))
                for child_name, child_schema in schema.get("properties", {}).items():
                    with self.subTest(effect_id=spec.id, parameter=f"{name}.{child_name}"):
                        self.assertTrue(child_schema.get("description"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for effect render smoke test")
    def test_new_effects_render_one_real_frame(self) -> None:
        """A valid filter string alone is not enough: FFmpeg must accept the graph."""
        registry = default_registry(refresh=True)
        renderer = RenderService(registry=registry)
        for effect_id in NEW_EFFECT_IDS:
            with self.subTest(effect_id=effect_id):
                spec = registry.get(effect_id)
                parts: list[str] = []
                output = renderer._fx_one(
                    str(spec.implementation["filter"]), spec.default_params(),
                    "0:v", parts, [0], 0,
                )
                result = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=0.1",
                        "-filter_complex", ";".join(parts), "-map", f"[{output}]",
                        "-frames:v", "1", "-f", "null", "-",
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for effect render smoke test")
    def test_builtin_visual_effect_defaults_render_a_frame(self) -> None:
        registry = default_registry(refresh=True)
        renderer = RenderService(registry=registry)
        for spec in registry.all():
            if spec.category != "fx" or not set(spec.applies_to).intersection({"image", "video"}):
                continue
            if str(spec.implementation.get("filter", "")) in _AUDIO_FX_STEPS:
                continue
            with self.subTest(effect_id=spec.id):
                parts: list[str] = []
                output = renderer._fx_one(
                    str(spec.implementation["filter"]), spec.default_params(),
                    "0:v", parts, [0], 0,
                )
                # File-backed effects such as LUT do nothing until given a resource.
                if not parts:
                    continue
                result = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=0.1",
                        "-filter_complex", ";".join(parts), "-map", f"[{output}]",
                        "-frames:v", "1", "-f", "null", "-",
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for effect render smoke test")
    def test_builtin_audio_effect_defaults_render_a_segment(self) -> None:
        registry = default_registry(refresh=True)
        for spec in registry.all():
            key = str(spec.implementation.get("filter", ""))
            if key not in _AUDIO_FX_STEPS:
                continue
            with self.subTest(effect_id=spec.id):
                expression = _AUDIO_FX_STEPS[key](spec.default_params())
                result = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1",
                        "-filter_complex", f"[0:a]{expression}[out]",
                        "-map", "[out]", "-f", "null", "-",
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_effects_are_discoverable_validated_and_render_compilable(self) -> None:
        registry = default_registry(refresh=True)
        renderer = RenderService(registry=registry)

        for effect_id, expected_filter in NEW_EFFECT_IDS.items():
            with self.subTest(effect_id=effect_id):
                spec = registry.get(effect_id)
                params = registry.validate_params(effect_id, {})
                self.assertEqual(params, spec.default_params())

                parts: list[str] = []
                out = renderer._fx_one(  # noqa: SLF001 - rendering bridge contract
                    str(spec.implementation["filter"]), params, "input", parts, [0], 0
                )
                self.assertTrue(out.startswith("vfx"))
                self.assertEqual(len(parts), 1)
                self.assertIn(expected_filter, parts[0])

        capabilities = registry.capabilities()
        self.assertGreaterEqual(capabilities["count"], 80)
        self.assertGreaterEqual(capabilities["categories"]["fx"], 37)

    def test_effects_are_constrained_to_their_renderable_target(self) -> None:
        registry = default_registry(refresh=True)
        for effect_id in (
            "cutvoke.fx.loudnorm",
            "cutvoke.fx.equalizer",
            "cutvoke.fx.compressor",
            "cutvoke.fx.pan",
        ):
            self.assertEqual(registry.get(effect_id).applies_to, ("audio", "video"))
        self.assertEqual(registry.get("cutvoke.fx.vibrance").applies_to, ("image", "video"))
        self.assertEqual(registry.get("cutvoke.text").applies_to, ("text",))

    def test_mcp_command_preview_has_no_project_side_effect(self) -> None:
        service = EditService()
        mcp = MCPServer(service)
        self.assertTrue(mcp.call_tool("create_project", {"projectId": "preview-contract"})["ok"])

        preview_tool = next(tool for tool in mcp.list_tools() if tool["name"] == "command_preview")
        self.assertIn("expectedRevision", preview_tool["inputSchema"]["required"])

        preview = mcp.call_tool(
            "command_preview",
            {
                "projectId": "preview-contract",
                "type": "track.add",
                "payload": {"trackId": "would-be-track", "kind": "video"},
                "expectedRevision": "0",
                "actorId": "planning-agent",
            },
        )

        self.assertTrue(preview["ok"])
        self.assertTrue(preview["valid"])
        self.assertEqual(preview["changedEntities"][0]["id"], "would-be-track")
        project = service.get_project("preview-contract")
        self.assertEqual(project.revision, "0")
        self.assertEqual(project.sequence.tracks, [])

    def test_mcp_project_summary_is_compact_and_includes_edit_context(self) -> None:
        service = EditService()
        mcp = MCPServer(service)
        service.create_project("summary-contract", name_hint="summary")
        service.execute(Command(
            type="track.add", payload={"trackId": "v1", "kind": "video"},
            command_id="summary-track", project_id="summary-contract", expected_revision="0",
            actor=Actor("test", "summary"),
        ))
        summary = mcp.call_tool("project_summary", {"projectId": "summary-contract"})

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["revision"], "1")
        self.assertEqual(summary["trackCount"], 1)
        self.assertEqual(summary["tracks"][0]["trackId"], "v1")
        self.assertNotIn("project", summary)

    def test_mcp_effects_list_filters_by_render_target(self) -> None:
        mcp = MCPServer(EditService())
        audio = mcp.call_tool("effects_list", {"appliesTo": "audio"})
        image = mcp.call_tool("effects_list", {"appliesTo": "image"})

        self.assertTrue(audio["ok"])
        self.assertEqual(audio["appliesTo"], "audio")
        self.assertEqual(
            {item["effectId"] for item in audio["effects"]},
            {
                "cutvoke.fx.compressor",
                "cutvoke.fx.equalizer",
                "cutvoke.fx.loudnorm",
                "cutvoke.fx.pan",
            },
        )
        self.assertTrue(image["ok"])
        self.assertIn("cutvoke.fx.vibrance", {item["effectId"] for item in image["effects"]})
        self.assertNotIn("cutvoke.fx.loudnorm", {item["effectId"] for item in image["effects"]})
