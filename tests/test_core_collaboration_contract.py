"""Core contracts for the human + external-agent editing workflow.

These tests use only the standard library so they run in a fresh checkout.
They protect the small, high-value promises of CutVoke instead of asserting
implementation details: an Agent lease blocks concurrent human writes, audit
events preserve the real editor identity, and caption-only edits keep the
already-rendered base preview reusable.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import quote
from urllib.request import Request, urlopen

from cutvoke.client import CutVokeClient
from cutvoke.core.caption_render import build_ass
from cutvoke.core.httpapi import HttpApi, build_handler
from cutvoke.core.mcp_server import MCPServer
from cutvoke.core.model import AssetReference, Caption, Clip, Sequence, Track
from cutvoke.core.protocol import Actor, Command, ErrorCode
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


def command(
    command_type: str,
    payload: dict,
    *,
    project_id: str,
    revision: str,
    command_id: str,
    actor: Actor,
    lease_id: str = "",
) -> Command:
    """Build an explicit protocol command for a test interaction."""

    return Command(
        type=command_type,
        payload=payload,
        command_id=command_id,
        project_id=project_id,
        expected_revision=revision,
        actor=actor,
        edit_lease_id=lease_id,
    )


class CountingRenderer:
    """A deterministic preview renderer; media bytes themselves are irrelevant here."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def render(self, project, out_path: str, *, quality: str, overwrite: bool) -> dict:
        self.calls.append(project.to_dict())
        with open(out_path, "wb") as output:
            output.write(b"cutvoke-preview")
        return {"duration": 2.0}


class CollaborationContractTests(unittest.TestCase):
    def test_clip_insert_can_create_track_atomically_for_preview_and_undo(self) -> None:
        service = EditService()
        project = service.create_project("atomic-auto-track")
        payload = {
            "trackId": "music",
            "createTrackKind": "audio",
            "clipId": "wind",
            "sourcePath": "wind.wav",
            "timelineStart": {"num": 0, "den": 1},
            "timelineEnd": {"num": 2, "den": 1},
        }
        request = command(
            "clip.insert", payload,
            project_id=project.project_id,
            revision=project.revision,
            command_id="auto-track-insert",
            actor=Actor("agent", "contract-test"),
        )

        preview = service.preview_command(request)
        self.assertTrue(preview["valid"])
        self.assertEqual(
            [(item["type"], item["change"]) for item in preview["changedEntities"]],
            [("track", "created"), ("clip", "created")],
        )
        self.assertEqual(service.get_project(project.project_id).revision, project.revision)
        self.assertEqual(service.get_project(project.project_id).sequence.tracks, [])

        inserted = service.execute(request)
        self.assertEqual(inserted.revision, "1")
        created = service.get_project(project.project_id).sequence.tracks
        self.assertEqual([(track.id, track.kind) for track in created], [("music", "audio")])
        self.assertEqual([clip.id for clip in created[0].clips], ["wind"])

        undone = service.execute(command(
            "history.undo", {},
            project_id=project.project_id,
            revision=inserted.revision,
            command_id="undo-auto-track-insert",
            actor=Actor("human", "editor"),
        ))
        self.assertEqual(undone.previous_revision, inserted.revision)
        self.assertEqual(service.get_project(project.project_id).sequence.tracks, [])

    def test_agent_dry_run_reports_time_ranges_for_speed_curves_and_attached_moves(self) -> None:
        service = EditService()
        project = service.create_project("dry-run-timeline-ranges")
        actor = Actor("agent", "timeline-range-agent")

        def apply(command_type: str, payload: dict, serial: int) -> None:
            current = service.get_project(project.project_id)
            service.execute(command(
                command_type, payload,
                project_id=project.project_id,
                revision=current.revision,
                command_id=f"timeline-range-{serial}",
                actor=actor,
            ))

        apply("track.add", {"trackId": "video", "kind": "video"}, 1)
        apply("clip.insert", {
            "trackId": "video", "clipId": "parent", "sourcePath": "parent.mp4",
            "timelineStart": {"num": 0, "den": 1},
            "timelineEnd": {"num": 4, "den": 1},
        }, 3)
        apply("clip.insert", {
            "trackId": "overlay", "createTrackKind": "video",
            "createTrackRole": "sticker", "role": "sticker",
            "clipId": "title", "sourcePath": "title.png",
            "timelineStart": {"num": 1, "den": 1},
            "timelineEnd": {"num": 2, "den": 1},
            "attachedToClipId": "parent",
        }, 4)
        before = service.get_project(project.project_id)

        curve_preview = service.preview_command(command(
            "clip.speed", {"clipId": "parent", "curve": {"points": [
                {"at": 0, "speed": 0.5}, {"at": 1, "speed": 0.5},
            ]}},
            project_id=project.project_id,
            revision=before.revision,
            command_id="dry-run-speed-curve",
            actor=actor,
        ))
        curve_impact = next(item for item in curve_preview["changedEntities"]
                            if item["id"] == "parent")
        self.assertEqual(curve_impact["timelineRange"]["before"], {
            "start": {"num": "0", "den": "1"},
            "end": {"num": "4", "den": "1"},
        })
        self.assertGreater(
            int(curve_impact["timelineRange"]["after"]["end"]["num"]) /
            int(curve_impact["timelineRange"]["after"]["end"]["den"]), 4)
        self.assertEqual(service.get_project(project.project_id).revision, before.revision)

        move_preview = service.preview_command(command(
            "clip.move", {"clipId": "parent", "timelineStart": {"num": 6, "den": 1}},
            project_id=project.project_id,
            revision=before.revision,
            command_id="dry-run-attached-move",
            actor=actor,
        ))
        moved = {item["id"]: item for item in move_preview["changedEntities"]}
        self.assertEqual(moved["parent"]["timelineRange"], {
            "before": {"start": {"num": "0", "den": "1"},
                       "end": {"num": "4", "den": "1"}},
            "after": {"start": {"num": "6", "den": "1"},
                      "end": {"num": "10", "den": "1"}},
        })
        self.assertEqual(moved["title"]["timelineRange"], {
            "before": {"start": {"num": "1", "den": "1"},
                       "end": {"num": "2", "den": "1"}},
            "after": {"start": {"num": "7", "den": "1"},
                      "end": {"num": "8", "den": "1"}},
        })
        self.assertEqual(service.get_project(project.project_id).revision, before.revision)
        self.assertEqual(service.get_project(project.project_id).sequence.tracks[0].clips[0].timeline_end,
                         Rational.of(4))

    def test_failed_atomic_clip_insert_does_not_leave_an_empty_track(self) -> None:
        service = EditService()
        project = service.create_project("failed-auto-track")
        for kind, source, end in (
            ("video", "wind.wav", 2),
            ("audio", "wind.wav", 0),
            ("invalid", "wind.wav", 2),
        ):
            with self.subTest(kind=kind, end=end), self.assertRaises(EditError):
                service.execute(command(
                    "clip.insert",
                    {
                        "trackId": "new-track",
                        "createTrackKind": kind,
                        "sourcePath": source,
                        "timelineStart": {"num": 0, "den": 1},
                        "timelineEnd": {"num": end, "den": 1},
                    },
                    project_id=project.project_id,
                    revision=project.revision,
                    command_id=f"failed-auto-{kind}-{end}",
                    actor=Actor("agent", "contract-test"),
                ))
            current = service.get_project(project.project_id)
            self.assertEqual(current.revision, project.revision)
            self.assertEqual(current.sequence.tracks, [])

    def test_agent_http_can_dry_run_then_commit_atomic_auto_track(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            service = EditService()
            project = service.create_project("http-auto-track")
            api = HttpApi(service, CountingRenderer(), media_dir=media_dir)
            self.addCleanup(api.close)
            body = {
                "type": "clip.insert",
                "payload": {
                    "trackId": "music",
                    "createTrackKind": "audio",
                    "clipId": "music-clip",
                    "sourcePath": "wind.wav",
                    "timelineStart": {"num": 0, "den": 1},
                    "timelineEnd": {"num": 2, "den": 1},
                },
                "expectedRevision": project.revision,
                "commandId": "http-auto-track-insert",
                "actor": {"kind": "agent", "id": "music-agent"},
            }
            route = f"/api/v1/projects/{project.project_id}/commands"
            preview_status, preview = api.handle("POST", route, {**body, "dryRun": True})
            self.assertEqual(preview_status, 200)
            self.assertEqual([item["type"] for item in preview["changedEntities"]],
                             ["track", "clip"])
            self.assertEqual(service.get_project(project.project_id).revision, project.revision)

            commit_status, committed = api.handle("POST", route, body)
            self.assertEqual(commit_status, 200)
            self.assertEqual(committed["revision"], "1")
            self.assertEqual([item["type"] for item in committed["changedEntities"]],
                             ["track", "clip"])
            self.assertEqual(len(service.get_project(project.project_id).sequence.tracks), 1)

    def test_audio_with_embedded_cover_is_still_classified_as_audio(self) -> None:
        self.assertEqual(HttpApi._infer_kind("wind.mp3", True, True), "audio")
        self.assertEqual(HttpApi._infer_kind("voice.m4a", True, True), "audio")
        self.assertEqual(HttpApi._infer_kind("clip.mp4", True, True), "video")

    def test_clip_insert_rejects_known_audio_on_a_video_track(self) -> None:
        service = EditService()
        project = service.create_project("typed-track-contract")
        added = service.execute(
            command(
                "track.add",
                {"trackId": "visuals", "kind": "video"},
                project_id=project.project_id,
                revision=project.revision,
                command_id="typed-track-add",
                actor=Actor("agent", "contract-test"),
            )
        )

        with self.assertRaises(EditError) as rejected:
            service.execute(
                command(
                    "clip.insert",
                    {
                        "clipId": "wrong-kind",
                        "trackId": "visuals",
                        "sourcePath": "wind.wav",
                        "timelineStart": {"num": 0, "den": 1},
                        "timelineEnd": {"num": 3, "den": 1},
                    },
                    project_id=project.project_id,
                    revision=added.revision,
                    command_id="typed-track-insert",
                    actor=Actor("agent", "contract-test"),
                )
            )
        self.assertEqual(rejected.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertIn("use an audio track", rejected.exception.message)

    def test_renderer_keeps_audio_but_skips_missing_picture_from_dirty_video_track(self) -> None:
        image = Clip(
            id="picture",
            asset_ref=AssetReference("picture", source_path="picture.png"),
            timeline_start=Rational.of(0, 1),
            timeline_end=Rational.of(4, 1),
            source_start=Rational.of(0, 1),
        )
        misplaced_audio = Clip(
            id="legacy-audio",
            asset_ref=AssetReference("wind", source_path="wind.wav"),
            timeline_start=Rational.of(0, 1),
            timeline_end=Rational.of(3, 1),
            source_start=Rational.of(0, 1),
        )
        sequence = Sequence(
            id="main",
            width=1920,
            height=1080,
            fps=Rational.of(30, 1),
            tracks=[
                Track(id="visuals", kind="video", clips=[image]),
                Track(id="legacy", kind="video", clips=[misplaced_audio]),
            ],
        )
        renderer = RenderService()
        renderer.probe_media = lambda src: {
            "duration": 3.0 if src.endswith(".wav") else 0.0,
            "has_video": src.endswith(".png"),
            "has_audio": src.endswith(".wav"),
        }

        graph, inputs, expected, has_audio, warnings, _caption_dir = renderer._compile(
            sequence, (0, 0)
        )

        self.assertEqual(inputs, [os.path.abspath("picture.png"), os.path.abspath("wind.wav")])
        self.assertIn("[1:a]", graph)
        self.assertNotIn("[1:v]", graph)
        self.assertTrue(has_audio)
        self.assertEqual(expected, 4.0)
        self.assertTrue(any("picture skipped and its audio kept" in item for item in warnings))

    def test_misplaced_audio_clip_can_be_moved_to_an_audio_track(self) -> None:
        service = EditService()
        project = service.create_project("repair-track-contract")
        misplaced = Clip(
            id="legacy-audio",
            asset_ref=AssetReference("wind", source_path="wind.wav"),
            timeline_start=Rational.of(0, 1),
            timeline_end=Rational.of(3, 1),
            source_start=Rational.of(0, 1),
        )
        project.sequence.tracks = [
            Track(id="wrong-video", kind="video", clips=[misplaced]),
            Track(id="correct-audio", kind="audio"),
        ]

        moved = service.execute(
            command(
                "clip.move",
                {"clipId": "legacy-audio", "trackId": "correct-audio"},
                project_id=project.project_id,
                revision=project.revision,
                command_id="repair-track-move",
                actor=Actor("human", "contract-test"),
            )
        )

        repaired = service.get_project(project.project_id)
        self.assertEqual(moved.revision, "1")
        self.assertEqual(repaired.sequence.tracks[0].clips, [])
        self.assertEqual(
            [clip.id for clip in repaired.sequence.tracks[1].clips],
            ["legacy-audio"],
        )

    def test_asset_swap_rejects_audio_on_video_track_without_partial_change(self) -> None:
        service = EditService()
        project = service.create_project("typed-swap-contract")
        added = service.execute(
            command(
                "track.add",
                {"trackId": "visuals", "kind": "video"},
                project_id=project.project_id,
                revision=project.revision,
                command_id="typed-swap-track",
                actor=Actor("agent", "contract-test"),
            )
        )
        inserted = service.execute(
            command(
                "clip.insert",
                {
                    "clipId": "poster",
                    "trackId": "visuals",
                    "sourcePath": "poster.png",
                    "timelineStart": {"num": 0, "den": 1},
                    "timelineEnd": {"num": 3, "den": 1},
                },
                project_id=project.project_id,
                revision=added.revision,
                command_id="typed-swap-insert",
                actor=Actor("agent", "contract-test"),
            )
        )

        swap_request = command(
            "asset.swap",
            {"clipIds": ["poster"], "sourcePath": "voice.mp3"},
            project_id=project.project_id,
            revision=inserted.revision,
            command_id="typed-swap-reject",
            actor=Actor("agent", "contract-test"),
        )
        with self.assertRaises(EditError) as preview_error:
            service.preview_command(swap_request)
        with self.assertRaises(EditError) as rejected:
            service.execute(swap_request)
        self.assertEqual(preview_error.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(rejected.exception.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(preview_error.exception.message, rejected.exception.message)
        unchanged = service.get_project(project.project_id)
        self.assertEqual(unchanged.revision, inserted.revision)
        self.assertEqual(
            unchanged.sequence.tracks[0].clips[0].asset_ref.source_path,
            "poster.png",
        )

    def test_caption_ass_matches_editable_preview_style_contract(self) -> None:
        caption = Caption(
            id="style-contract",
            text="半透明背景，无描边",
            start=Rational.of(0, 1),
            end=Rational.of(2, 1),
            strokeWidth=0,
            background="rgba(0,0,0,0.55)",
        )

        ass = build_ass([caption], 1920, 1080)

        # CSS rgba(0,0,0,0.55) → ASS 反向 alpha 0x73；0 是无描边而非默认 2px。
        self.assertIn("&H73000000", ass)
        style = next(line for line in ass.splitlines() if line.startswith("Style: C0,"))
        style_fields = style.removeprefix("Style: ").split(",")
        self.assertEqual(float(style_fields[17]), 1.0)  # ASS Shadow
        self.assertEqual(style_fields[22], "1")  # ASS Encoding
        # 0.5/0.5 是工程模型的默认画布锚点，不能被导出端静默改成底部 MarginV。
        self.assertIn(r"{\pos(960,540)}半透明背景，无描边", ass)

    def test_caption_shadow_is_written_to_ass_shadow_not_encoding(self) -> None:
        for shadow in (0, 7):
            with self.subTest(shadow=shadow):
                caption = Caption(
                    id=f"shadow-{shadow}",
                    text="阴影字段",
                    start=Rational.of(0, 1),
                    end=Rational.of(1, 1),
                    shadow=shadow,
                )
                ass = build_ass([caption], 1920, 1080)
                style = next(line for line in ass.splitlines() if line.startswith("Style: C0,"))
                style_fields = style.removeprefix("Style: ").split(",")
                self.assertEqual(float(style_fields[17]), float(shadow))
                self.assertEqual(style_fields[22], "1")

    def test_caption_font_size_scales_from_1080p_reference(self) -> None:
        caption = Caption(
            id="font-scale",
            text="字号缩放",
            start=Rational.of(0, 1),
            end=Rational.of(1, 1),
            fontSize=32,
        )
        # Domain CSS em pixels are scaled/rounded first, then converted using
        # the actual bundled Noto Sans SC Win height (1448 / 1000).
        for width, height, expected_size in ((1280, 720, "30.408"), (3840, 2160, "92.672")):
            with self.subTest(height=height):
                ass = build_ass([caption], width, height, css_caption_ids={caption.id})
                style = next(line for line in ass.splitlines() if line.startswith("Style: C0,"))
                style_fields = style.removeprefix("Style: ").split(",")
                self.assertEqual(style_fields[2], expected_size)

    def test_preview_media_streams_a_range_for_an_encoded_project_id(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            service = EditService()
            project_id = "预览 # 100%"
            service.create_project(project_id)
            renderer = CountingRenderer()
            api = HttpApi(service, renderer, media_dir=media_dir)
            base_handler = build_handler(api)

            class QuietHandler(base_handler):
                def log_message(self, _format, *_args) -> None:
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                encoded_id = quote(project_id, safe="")
                url = (
                    f"http://127.0.0.1:{server.server_address[1]}"
                    f"/api/v1/projects/{encoded_id}/preview-media"
                )
                request = Request(url, headers={"Range": "bytes=0-6"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                    self.assertTrue(response.headers["ETag"])
                    self.assertEqual(response.read(), b"cutvoke")
                client = CutVokeClient(f"http://127.0.0.1:{server.server_address[1]}")
                self.assertEqual(client.get_project(project_id)["projectId"], project_id)
                lease = client.edit_lock(project_id, owner="sdk-agent")
                self.assertTrue(lease["locked"])
                self.assertEqual(
                    client.get_edit_lock(project_id)["lease"]["leaseId"],
                    lease["lease"]["leaseId"],
                )
                applied = client.command(
                    project_id,
                    "track.add",
                    {"trackId": "sdk-agent-track", "kind": "video"},
                    expected_revision="0",
                    edit_lease_id=lease["lease"]["leaseId"],
                    actor={"kind": "agent", "id": "sdk-agent"},
                )
                self.assertEqual(applied["revision"], "1")
                added_caption = client.command(
                    project_id,
                    "caption.add",
                    {
                        "captionId": "sdk-caption",
                        "text": "SDK 初稿",
                        "start": {"num": 0, "den": 1},
                        "end": {"num": 2, "den": 1},
                    },
                    expected_revision=applied["revision"],
                    edit_lease_id=lease["lease"]["leaseId"],
                    actor={"kind": "agent", "id": "sdk-agent"},
                )
                patched_caption = client.patch_captions(
                    project_id,
                    [{"captionId": "sdk-caption", "text": "SDK 精修"}],
                    expected_revision=added_caption["revision"],
                    edit_lease_id=lease["lease"]["leaseId"],
                    actor={"kind": "agent", "id": "sdk-agent"},
                )
                self.assertEqual(patched_caption["revision"], "3")
                self.assertEqual(
                    service.get_project(project_id).sequence.captions[0].text,
                    "SDK 精修",
                )
                event = service.events_since(project_id, "0")[0]
                self.assertEqual(event.actor.id, "sdk-agent")
                renewed = client.edit_lock(
                    project_id,
                    action="renew",
                    lease_id=lease["lease"]["leaseId"],
                    ttl_seconds=60,
                )
                self.assertEqual(renewed["lease"]["leaseId"], lease["lease"]["leaseId"])
                released = client.edit_lock(
                    project_id, action="release", lease_id=renewed["lease"]["leaseId"]
                )
                self.assertTrue(released["released"])
                self.assertFalse(client.get_edit_lock(project_id)["locked"])
                self.assertEqual(len(renderer.calls), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                api.close()

    def test_mcp_commands_are_attributed_and_support_edit_leases(self) -> None:
        service = EditService()
        mcp = MCPServer(service)
        created = mcp.call_tool("create_project", {"projectId": "mcp-project"})
        self.assertTrue(created["ok"])
        command_tool = next(tool for tool in mcp.list_tools() if tool["name"] == "command_apply")
        self.assertIn("editLeaseId", command_tool["inputSchema"]["properties"])
        self.assertIn(
            "caption.patch",
            {item["type"] for item in mcp.call_tool("capabilities", {})["commands"]},
        )
        lease = mcp.call_tool(
            "edit_lock",
            {"projectId": "mcp-project", "action": "acquire", "owner": "subtitle-agent"},
        )["lease"]
        applied = mcp.call_tool(
            "command_apply",
            {
                "projectId": "mcp-project",
                "type": "track.add",
                "payload": {"trackId": "agent-track", "kind": "video"},
                "expectedRevision": "0",
                "editLeaseId": lease["leaseId"],
                "actorId": "subtitle-agent",
            },
        )
        self.assertTrue(applied["ok"])
        event = service.events_since("mcp-project", "0")[0]
        self.assertEqual(event.actor.kind, "mcp")
        self.assertEqual(event.actor.id, "subtitle-agent")
        self.assertTrue(
            mcp.call_tool(
                "edit_lock",
                {"projectId": "mcp-project", "action": "release", "leaseId": lease["leaseId"]},
            )["released"]
        )

    def test_encoded_project_id_routes_to_the_original_project(self) -> None:
        service = EditService()
        project_id = "中文项目 / 100%"
        service.create_project(project_id)
        api = HttpApi(service)
        self.addCleanup(api.close)

        encoded_path = f"/api/v1/projects/{quote(project_id, safe='')}"
        status, payload = api.handle("GET", encoded_path, {})

        self.assertEqual(status, 200)
        self.assertEqual(payload["projectId"], project_id)

    def test_project_summary_exposes_a_compact_agent_timeline_index(self) -> None:
        service = EditService()
        project = service.create_project("summary-http", name_hint="summary-http")
        api = HttpApi(service)
        self.addCleanup(api.close)
        service.execute(command(
            "track.add", {"trackId": "v1", "kind": "video"},
            project_id=project.project_id, revision=project.revision,
            command_id="summary-track", actor=Actor("test", "summary"),
        ))
        status, payload = api.handle("GET", "/api/v1/projects/summary-http/summary", {})

        self.assertEqual(status, 200)
        self.assertEqual(payload["name"], "summary-http")
        self.assertEqual(payload["trackCount"], 1)
        self.assertEqual(payload["tracks"][0]["trackId"], "v1")
        self.assertEqual(payload["duration"], {"num": "0", "den": "1"})

    def test_http_rejects_a_non_object_json_body(self) -> None:
        api = HttpApi(EditService())
        self.addCleanup(api.close)

        status, payload = api.handle("POST", "/api/v1/projects", [])  # type: ignore[arg-type]

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ErrorCode.INVALID_ARGUMENT)

    def test_http_lock_endpoint_exposes_agent_changes_without_accepting_human_writes(self) -> None:
        service = EditService()
        service.create_project("http-project")
        api = HttpApi(service)
        self.addCleanup(api.close)

        status, acquired = api.handle(
            "POST",
            "/api/v1/projects/http-project/edit-lock",
            {"owner": "subtitle-agent", "leaseId": "lease-http", "ttlSeconds": 30},
        )
        self.assertEqual(status, 200)
        self.assertTrue(acquired["locked"])
        self.assertEqual(acquired["lease"]["owner"], "subtitle-agent")

        status, blocked = api.handle(
            "POST",
            "/api/v1/projects/http-project/commands",
            {
                "type": "track.add",
                "payload": {"trackId": "human-track", "kind": "video"},
                "expectedRevision": "0",
                "commandId": "http-human-blocked",
                "actor": {"kind": "human", "id": "editor"},
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(blocked["error"]["code"], ErrorCode.EDIT_LOCKED)

        status, blocked_rename = api.handle(
            "PATCH",
            "/api/v1/projects/http-project",
            {"name": "human rename during agent edit"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(blocked_rename["error"]["code"], ErrorCode.EDIT_LOCKED)
        self.assertEqual(service.get_project("http-project").name, "")

        status, agent_write = api.handle(
            "POST",
            "/api/v1/projects/http-project/commands",
            {
                "type": "track.add",
                "payload": {"trackId": "agent-track", "kind": "video"},
                "expectedRevision": "0",
                "commandId": "http-agent-write",
                "editLeaseId": "lease-http",
                "actor": {"kind": "agent", "id": "subtitle-agent"},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(agent_write["revision"], "1")

        status, events = api.handle(
            "GET", "/api/v1/projects/http-project/events", {"since": "0"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(events["events"]), 1)
        self.assertEqual(events["events"][0]["actor"]["kind"], "agent")

        srt = "1\n00:00:00,000 --> 00:00:01,000\nlease-protected subtitle\n"
        status, blocked_import = api.handle(
            "POST",
            "/api/v1/projects/http-project/captions/import",
            {"srt": srt, "actor": {"kind": "human", "id": "editor"}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(blocked_import["error"]["code"], ErrorCode.EDIT_LOCKED)
        self.assertEqual(len(service.get_project("http-project").sequence.captions), 0)

        status, imported = api.handle(
            "POST",
            "/api/v1/projects/http-project/captions/import",
            {
                "srt": srt,
                "editLeaseId": "lease-http",
                "actor": {"kind": "agent", "id": "subtitle-agent"},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(imported["imported"], 1)
        self.assertEqual(service.get_project("http-project").revision, "2")
        self.assertEqual(
            service.events_since("http-project", "0")[-1].actor.kind,
            "agent",
        )

        status, released = api.handle(
            "POST",
            "/api/v1/projects/http-project/edit-lock/release",
            {"leaseId": "lease-http"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(released["released"])
        self.assertFalse(released["locked"])

        status, stale_release = api.handle(
            "POST",
            "/api/v1/projects/http-project/edit-lock/release",
            {"leaseId": "lease-already-gone"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(stale_release["released"])
        self.assertFalse(stale_release["locked"])
        self.assertIsNone(stale_release["lease"])

    def test_srt_import_keeps_the_web_editor_actor_identity(self) -> None:
        service = EditService()
        service.create_project("srt-project")
        api = HttpApi(service)
        self.addCleanup(api.close)

        status, imported = api.handle(
            "POST",
            "/api/v1/projects/srt-project/captions",
            {
                "srt": "1\n00:00:00,000 --> 00:00:01,500\n人工导入的字幕\n",
                "actor": {"kind": "human", "id": "ui"},
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(imported["imported"], 1)
        event = service.events_since("srt-project", "0")[0]
        self.assertEqual(event.actor.kind, "human")
        self.assertEqual(event.actor.id, "ui")

    def test_agent_lease_blocks_human_write_then_allows_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = os.path.join(temp_dir, "projects.sqlite3")
            # 在 Windows 上必须先关闭两个 SQLite 连接，才能删除临时数据库。
            with ProjectStore(database_path) as human_store, ProjectStore(database_path) as agent_store:
                human = EditService(human_store)
                agent = EditService(agent_store)
                human.create_project("shared-project")

                lease = agent.acquire_edit_lease(
                    "shared-project", owner="subtitle-agent", ttl_seconds=30
                )
                with self.assertRaises(EditError) as blocked:
                    human.execute(
                        command(
                            "track.add",
                            {"trackId": "human-track", "kind": "video"},
                            project_id="shared-project",
                            revision="0",
                            command_id="human-blocked",
                            actor=Actor("human", "editor"),
                        )
                    )
                self.assertEqual(blocked.exception.code, ErrorCode.EDIT_LOCKED)

                agent_result = agent.execute(
                    command(
                        "track.add",
                        {"trackId": "agent-track", "kind": "video"},
                        project_id="shared-project",
                        revision="0",
                        command_id="agent-write",
                        actor=Actor("agent", "subtitle-agent"),
                        lease_id=lease["leaseId"],
                    )
                )
                self.assertEqual(human.get_project("shared-project").revision, agent_result.revision)
                self.assertTrue(agent.release_edit_lease("shared-project", lease["leaseId"]))

                human_result = human.execute(
                    command(
                        "track.add",
                        {"trackId": "human-track", "kind": "video"},
                        project_id="shared-project",
                        revision=agent_result.revision,
                        command_id="human-takeover",
                        actor=Actor("human", "editor"),
                    )
                )
                self.assertEqual(human_result.previous_revision, agent_result.revision)
                self.assertEqual(
                    [track.id for track in agent.get_project("shared-project").sequence.tracks],
                    ["agent-track", "human-track"],
                )

    def test_locked_tracks_reject_clip_mutations_and_can_be_unlocked(self) -> None:
        service = EditService()
        project = service.create_project("locked-track-contract")
        track = service.execute(
            command(
                "track.add",
                {"trackId": "video-locked", "kind": "video"},
                project_id=project.project_id,
                revision=project.revision,
                command_id="locked-track-add",
                actor=Actor("human", "editor"),
            )
        )
        inserted = service.execute(
            command(
                "clip.insert",
                {
                    "clipId": "clip-locked",
                    "trackId": "video-locked",
                    "sourcePath": "before.mp4",
                    "timelineStart": {"num": 0, "den": 1},
                    "timelineEnd": {"num": 4, "den": 1},
                },
                project_id=project.project_id,
                revision=track.revision,
                command_id="locked-track-clip-add",
                actor=Actor("human", "editor"),
            )
        )
        locked = service.execute(
            command(
                "track.update",
                {"trackId": "video-locked", "locked": True},
                project_id=project.project_id,
                revision=inserted.revision,
                command_id="locked-track-lock",
                actor=Actor("human", "editor"),
            )
        )

        swap_payload = {"clipIds": ["clip-locked"], "sourcePath": "after.mp4"}
        with self.assertRaises(EditError) as preview_error:
            service.preview_command(
                command(
                    "asset.swap",
                    swap_payload,
                    project_id=project.project_id,
                    revision=locked.revision,
                    command_id="locked-track-swap-preview",
                    actor=Actor("agent", "editor"),
                )
            )
        self.assertIn("video-locked is locked", preview_error.exception.message)

        with self.assertRaises(EditError) as swap_error:
            service.execute(
                command(
                    "asset.swap",
                    swap_payload,
                    project_id=project.project_id,
                    revision=locked.revision,
                    command_id="locked-track-swap",
                    actor=Actor("agent", "editor"),
                )
            )
        self.assertIn("video-locked is locked", swap_error.exception.message)

        with self.assertRaises(EditError) as insert_error:
            service.execute(
                command(
                    "clip.insert",
                    {
                        "clipId": "clip-locked-extra",
                        "trackId": "video-locked",
                        "sourcePath": "extra.mp4",
                        "timelineStart": {"num": 4, "den": 1},
                        "timelineEnd": {"num": 5, "den": 1},
                    },
                    project_id=project.project_id,
                    revision=locked.revision,
                    command_id="locked-track-insert",
                    actor=Actor("agent", "editor"),
                )
            )
        self.assertIn("video-locked is locked", insert_error.exception.message)

        unchanged = service.get_project(project.project_id)
        self.assertEqual(unchanged.revision, locked.revision)
        self.assertEqual(len(unchanged.sequence.tracks[0].clips), 1)
        self.assertEqual(
            unchanged.sequence.tracks[0].clips[0].asset_ref.source_path,
            "before.mp4",
        )

        unlocked = service.execute(
            command(
                "track.update",
                {"trackId": "video-locked", "locked": False},
                project_id=project.project_id,
                revision=locked.revision,
                command_id="locked-track-unlock",
                actor=Actor("human", "editor"),
            )
        )
        swapped = service.execute(
            command(
                "asset.swap",
                swap_payload,
                project_id=project.project_id,
                revision=unlocked.revision,
                command_id="unlocked-track-swap",
                actor=Actor("human", "editor"),
            )
        )
        self.assertEqual(swapped.revision, "5")
        self.assertEqual(
            service.get_project(project.project_id).sequence.tracks[0].clips[0].asset_ref.source_path,
            "after.mp4",
        )

    def test_caption_text_and_timing_update_is_one_atomic_revision(self) -> None:
        service = EditService()
        project = service.create_project("manual-caption-project")
        added = service.execute(
            command(
                "caption.add",
                {
                    "captionId": "caption-manual",
                    "text": "AI 初稿",
                    "start": {"num": 1, "den": 1},
                    "end": {"num": 3, "den": 1},
                },
                project_id=project.project_id,
                revision=project.revision,
                command_id="manual-caption-add",
                actor=Actor("human", "editor"),
            )
        )

        updated = service.execute(
            command(
                "caption.update",
                {
                    "captionId": "caption-manual",
                    "text": "人工精修",
                    "start": {"num": 5, "den": 4},
                    "end": {"num": 7, "den": 2},
                },
                project_id=project.project_id,
                revision=added.revision,
                command_id="manual-caption-update",
                actor=Actor("human", "editor"),
            )
        )
        self.assertEqual(updated.previous_revision, added.revision)
        self.assertEqual(updated.revision, "2")
        caption = service.get_project(project.project_id).sequence.captions[0]
        self.assertEqual((caption.text, caption.start.to_json(), caption.end.to_json()), (
            "人工精修", {"num": "5", "den": "4"}, {"num": "7", "den": "2"},
        ))

        with self.assertRaises(EditError) as invalid_text:
            service.execute(
                command(
                    "caption.update",
                    {"captionId": "caption-manual", "text": "   "},
                    project_id=project.project_id,
                    revision=updated.revision,
                    command_id="manual-caption-empty-text",
                    actor=Actor("agent", "subtitle-agent"),
                )
            )
        self.assertEqual(invalid_text.exception.code, ErrorCode.INVALID_ARGUMENT)

        with self.assertRaises(EditError) as rejected:
            service.execute(
                command(
                    "caption.update",
                    {
                        "captionId": "caption-manual",
                        "start": {"num": 4, "den": 1},
                        "end": {"num": 2, "den": 1},
                    },
                    project_id=project.project_id,
                    revision=updated.revision,
                    command_id="manual-caption-invalid-time",
                    actor=Actor("human", "editor"),
                )
            )
        self.assertEqual(rejected.exception.code, ErrorCode.INVALID_ARGUMENT)
        persisted = service.get_project(project.project_id)
        self.assertEqual(persisted.revision, updated.revision)
        self.assertEqual(persisted.sequence.captions[0].text, "人工精修")

    def test_caption_edit_reuses_preview_base_media(self) -> None:
        with tempfile.TemporaryDirectory() as media_dir:
            service = EditService()
            project = service.create_project("caption-project")
            self.assertIn(
                "caption.patch", {item["type"] for item in service.command_catalog()}
            )
            renderer = CountingRenderer()
            api = HttpApi(service, renderer, media_dir=media_dir)
            self.addCleanup(api.close)

            added_first = service.execute(
                command(
                    "caption.add",
                    {
                        "captionId": "caption-1",
                        "text": "原字幕",
                        "start": {"num": 0, "den": 1},
                        "end": {"num": 2, "den": 1},
                    },
                    project_id=project.project_id,
                    revision=project.revision,
                    command_id="caption-add",
                    actor=Actor("human", "editor"),
                )
            )
            added_second = service.execute(
                command(
                    "caption.add",
                    {
                        "captionId": "caption-2",
                        "text": "第二条字幕",
                        "start": {"num": 2, "den": 1},
                        "end": {"num": 4, "den": 1},
                    },
                    project_id=project.project_id,
                    revision=added_first.revision,
                    command_id="caption-add-second",
                    actor=Actor("human", "editor"),
                )
            )
            first_status, first_preview = api.handle(
                "GET",
                f"/api/v1/projects/{project.project_id}/preview-media",
                {},
                inline_media=False,
            )
            self.assertEqual(first_status, 200)
            first_path = first_preview["__video_path__"]
            first_etag = first_preview["__video_etag__"]
            first_duration = first_preview["duration"]
            self.assertTrue(os.path.isfile(first_path))
            self.assertEqual(first_duration, 2.0)
            self.assertEqual(len(renderer.calls), 1)
            self.assertEqual(renderer.calls[0]["sequence"]["captions"], [])

            patch_status, patched = api.handle(
                "POST",
                f"/api/v1/projects/{project.project_id}/commands",
                {
                    "type": "caption.patch",
                    "payload": {
                        "updates": [
                            {"captionId": "caption-1", "text": "修改后的第一条"},
                            {"captionId": "caption-2", "text": "修改后的第二条"},
                        ],
                    },
                    "expectedRevision": added_second.revision,
                    "commandId": "caption-patch",
                    "actor": {"kind": "agent", "id": "subtitle-agent"},
                },
            )
            self.assertEqual(patch_status, 200)
            self.assertEqual(patched["previousRevision"], added_second.revision)
            self.assertEqual(patched["revision"], "3")
            self.assertEqual(
                [caption.text for caption in service.get_project(project.project_id).sequence.captions],
                ["修改后的第一条", "修改后的第二条"],
            )
            duplicate_status, duplicate = api.handle(
                "POST",
                f"/api/v1/projects/{project.project_id}/commands",
                {
                    "type": "caption.patch",
                    "payload": {
                        "updates": [
                            {"captionId": "caption-1", "text": "不应写入"},
                            {"captionId": "caption-1", "text": "重复 ID"},
                        ],
                    },
                    "expectedRevision": patched["revision"],
                    "commandId": "caption-patch-duplicate",
                    "actor": {"kind": "agent", "id": "subtitle-agent"},
                },
            )
            self.assertEqual(duplicate_status, 400)
            self.assertEqual(duplicate["error"]["code"], ErrorCode.INVALID_ARGUMENT)
            self.assertEqual(service.get_project(project.project_id).revision, patched["revision"])
            self.assertEqual(
                service.get_project(project.project_id).sequence.captions[0].text,
                "修改后的第一条",
            )
            missing_status, missing = api.handle(
                "POST",
                f"/api/v1/projects/{project.project_id}/commands",
                {
                    "type": "caption.patch",
                    "payload": {
                        "updates": [
                            {"captionId": "caption-1", "text": "不能部分提交"},
                            {"captionId": "missing-caption", "text": "不存在"},
                        ],
                    },
                    "expectedRevision": patched["revision"],
                    "commandId": "caption-patch-missing",
                    "actor": {"kind": "agent", "id": "subtitle-agent"},
                },
            )
            self.assertEqual(missing_status, 400)
            self.assertEqual(missing["error"]["code"], ErrorCode.INVALID_ARGUMENT)
            persisted_after_failure = service.get_project(project.project_id)
            self.assertEqual(persisted_after_failure.revision, patched["revision"])
            self.assertEqual(
                [caption.text for caption in persisted_after_failure.sequence.captions],
                ["修改后的第一条", "修改后的第二条"],
            )
            second_status, second_preview = api.handle(
                "GET",
                f"/api/v1/projects/{project.project_id}/preview-media",
                {},
                inline_media=False,
            )
            self.assertEqual(second_status, 200)
            second_path = second_preview["__video_path__"]
            second_etag = second_preview["__video_etag__"]
            second_duration = second_preview["duration"]

            self.assertEqual(second_path, first_path)
            self.assertEqual(second_etag, first_etag)
            self.assertEqual(second_duration, first_duration)
            self.assertEqual(len(renderer.calls), 1)

            undone = service.execute(
                command(
                    "history.undo",
                    {},
                    project_id=project.project_id,
                    revision=patched["revision"],
                    command_id="caption-patch-undo",
                    actor=Actor("human", "editor"),
                )
            )
            self.assertEqual(undone.previous_revision, patched["revision"])
            self.assertEqual(
                [caption.text for caption in service.get_project(project.project_id).sequence.captions],
                ["原字幕", "第二条字幕"],
            )


if __name__ == "__main__":
    unittest.main()
