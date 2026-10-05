"""Imported audio must be auditable in the browser without exposing local paths."""

from __future__ import annotations

import io
import math
import sqlite3
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.model import AssetReference, Clip, Track
from cutvoke.core.rational import Rational
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore


def _tone_wav(seconds: float = 1.0) -> bytes:
    samples = [int(12000 * math.sin(2 * math.pi * 440 * index / 8000))
               for index in range(round(8000 * seconds))]
    output = io.BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(8000)
        sound.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


class MediaAuditionTests(unittest.TestCase):
    def test_asset_schema_migrates_audio_role_for_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "projects.db"
            connection = sqlite3.connect(database)
            connection.execute("""
                CREATE TABLE assets (
                    asset_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'unknown', duration REAL,
                    has_video INTEGER NOT NULL DEFAULT 0,
                    has_audio INTEGER NOT NULL DEFAULT 0,
                    width INTEGER, height INTEGER, created_at REAL NOT NULL
                )
            """)
            connection.execute(
                "INSERT INTO assets (asset_id, name, path, size, kind, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-audio", "legacy.wav", "legacy.wav", 64, "audio", 1.0),
            )
            connection.commit()
            connection.close()

            with ProjectStore(str(database)) as store:
                self.assertEqual(store.get_asset("legacy-audio")["audioRole"], "unclassified")

    def test_audio_role_is_explicit_persistent_and_survives_relink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "projects.db"
            with ProjectStore(str(database)) as store:
                api = HttpApi(EditService(store), RenderService(),
                              media_dir=str(root / "media"))
                asset = api.import_asset("sound.wav", _tone_wav())
                asset_id = asset["assetId"]
                status, listing = api.handle("GET", "/api/v1/assets", {})
                self.assertEqual(status, 200)
                self.assertEqual(listing["assets"][0]["audioRole"], "unclassified")

                status, response = api.handle(
                    "PATCH", f"/api/v1/assets/{asset_id}", {"audioRole": "music"})
                self.assertEqual(status, 200, response)
                self.assertEqual(response["audioRole"], "music")
                status, invalid = api.handle(
                    "PATCH", f"/api/v1/assets/{asset_id}", {"audioRole": "ambience"})
                self.assertEqual(status, 400)
                self.assertEqual(invalid["error"]["code"], "INVALID_ARGUMENT")

                store.add_asset(asset_id="builtin-audio", name="内置·片头",
                                path="builtin.wav", size=64, kind="audio",
                                builtin=True, audio_role="music")
                status, immutable = api.handle(
                    "PATCH", "/api/v1/assets/builtin-audio", {"audioRole": "sound_effect"})
                self.assertEqual(status, 409)
                self.assertEqual(immutable["error"]["code"], "BUILTIN_ASSET_IMMUTABLE")

                Path(asset["path"]).unlink()
                status, restored = api.relink_asset(asset_id, "replacement.wav", _tone_wav())
                self.assertEqual(status, 200)
                self.assertEqual(restored["audioRole"], "music")
                api.close()

            with ProjectStore(str(database)) as store:
                api = HttpApi(EditService(store), RenderService(),
                              media_dir=str(root / "media"))
                status, listing = api.handle("GET", "/api/v1/assets", {})
                self.assertEqual(status, 200)
                saved = next(item for item in listing["assets"] if item["assetId"] == asset_id)
                self.assertEqual(saved["audioRole"], "music")
                api.close()

    def test_imported_audio_has_stream_and_real_waveform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with ProjectStore(str(root / "projects.db")) as store:
                api = HttpApi(EditService(store), RenderService(),
                              media_dir=str(root / "media"))
                asset = api.import_asset("tone.wav", _tone_wav())
                asset_id = asset["assetId"]
                self.assertEqual(asset["kind"], "audio")
                status, media = api.handle("GET", f"/api/v1/assets/{asset_id}/media", {})
                self.assertEqual(status, 200)
                self.assertEqual(media["__media_type__"], "audio/wav")
                self.assertEqual(Path(media["__media_path__"]).read_bytes(), _tone_wav())
                status, waveform = api.handle("GET", f"/api/v1/assets/{asset_id}/waveform", {"w": "480"})
                self.assertEqual(status, 200)
                png = waveform["__png__"]
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(struct.unpack(">II", png[16:24]), (480, 48))
                status, cached = api.handle("GET", f"/api/v1/assets/{asset_id}/waveform", {"w": "480"})
                self.assertEqual(status, 200)
                self.assertEqual(cached["__png__"], png)
                api.close()

    def test_asset_ledger_cannot_stream_unrelated_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "private.wav"
            outside.write_bytes(_tone_wav())
            with ProjectStore(str(root / "projects.db")) as store:
                store.add_asset(asset_id="outside", name="private.wav",
                                path=str(outside), size=outside.stat().st_size,
                                kind="audio", has_audio=True)
                api = HttpApi(EditService(store), RenderService(),
                              media_dir=str(root / "media"))
                for suffix in ("media", "waveform"):
                    status, _ = api.handle("GET", f"/api/v1/assets/outside/{suffix}", {})
                    self.assertEqual(status, 404)
                api.close()

    def test_relink_restores_missing_file_at_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with ProjectStore(str(root / "projects.db")) as store:
                api = HttpApi(EditService(store), RenderService(),
                              media_dir=str(root / "media"))
                asset = api.import_asset("music.wav", _tone_wav())
                source = Path(asset["path"])
                source.unlink()
                status, listing = api.handle("GET", "/api/v1/assets", {})
                self.assertEqual(status, 200)
                self.assertFalse(listing["assets"][0]["available"])
                status, _ = api.relink_asset(asset["assetId"], "short.wav", _tone_wav(0.4))
                self.assertEqual(status, 400)
                self.assertFalse(source.exists())
                status, _ = api.relink_asset(asset["assetId"], "wrong.mp3", _tone_wav())
                self.assertEqual(status, 400)
                status, restored = api.relink_asset(asset["assetId"], "replacement.wav", _tone_wav())
                self.assertEqual(status, 200)
                self.assertEqual(restored["path"], asset["path"])
                self.assertEqual(restored["assetId"], asset["assetId"])
                self.assertEqual(source.read_bytes(), _tone_wav())
                status, listing = api.handle("GET", "/api/v1/assets", {})
                self.assertEqual(status, 200)
                self.assertTrue(listing["assets"][0]["available"])
                status, _ = api.relink_asset(asset["assetId"], "again.wav", _tone_wav())
                self.assertEqual(status, 409)
                api.close()

    def test_audio_only_timeline_has_synchronized_preview_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tone.wav"
            source.write_bytes(_tone_wav())
            service = EditService()
            project = service.create_project("audio-only", width=320, height=180)
            project.sequence.tracks = [Track("audio", "audio", [
                Clip("tone", AssetReference("tone", str(source)),
                     Rational.of(0), Rational.of(1), Rational.of(0))
            ])]
            api = HttpApi(service, RenderService(), media_dir=str(root / "media"))
            status, response = api.handle(
                "GET", "/api/v1/projects/audio-only/preview-window",
                {"index": "0"}, inline_media=False)
            self.assertEqual(status, 200, response)
            preview = Path(response["__video_path__"])
            info = api.render.probe_media(str(preview))
            self.assertTrue(info["has_video"])
            self.assertTrue(info["has_audio"])
            self.assertAlmostEqual(info["duration"], 1.0, delta=0.15)
            api.close()


if __name__ == "__main__":
    unittest.main()
