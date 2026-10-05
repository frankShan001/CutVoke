"""Use real shots in a 160-second scratch timeline; never edit the source DB."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from cutvoke.core.httpapi import HttpApi
from cutvoke.core.rational import Rational as R
from cutvoke.core.render import RenderService
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "output/playwright/desktop-cache-20261004"
SOURCE = Path.home() / ".cutvoke/data/projects.sqlite"

def fingerprint():
    with sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT * FROM projects ORDER BY project_id").fetchall()
    return {"projects": len(rows), "sha256": hashlib.sha256(repr(rows).encode()).hexdigest()}

def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    before = fingerprint()
    database = EVIDENCE / "projects.sqlite"
    with sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True) as source, sqlite3.connect(database) as target:
        source.backup(target)
    store = ProjectStore(str(database))
    service = EditService(store=store)
    original = service.load_project("tide-gallery-20260927")
    master = service.load_project("tide-gallery-master-20260927")
    test = service.create_project("cache-real-160", name_hint="缓存验证｜160秒真实素材工程", width=640, height=360)
    shot = next(track for track in master.sequence.tracks if track.kind == "video").clips
    track = copy.deepcopy(next(track for track in master.sequence.tracks if track.kind == "video"))
    track.clips = []
    for repeat in range(4):
        for index, clip in enumerate(shot):
            item = copy.deepcopy(clip)
            item.id = f"shot-{repeat}-{index}"
            item.timeline_start += R.of(repeat * 40)
            item.timeline_end += R.of(repeat * 40)
            track.clips.append(item)
    test.sequence.tracks = [track]
    service._persist(test)
    class MeasuredRenderer(RenderService):
        calls = 0
        def render(self, *args, **kwargs):
            self.calls += 1
            return super().render(*args, **kwargs)
        def render_preview_window(self, *args, **kwargs):
            self.calls += 1
            return super().render_preview_window(*args, **kwargs)
    render = MeasuredRenderer()
    api = HttpApi(service, render, media_dir=str(EVIDENCE / "media"))
    result = {"sourceBefore": before, "projectId": test.project_id}
    try:
        started = time.monotonic()
        result["cold"] = []
        for index in range(20):
            tick = time.monotonic()
            path, identity, _ = api._preview_window_file(test, index)
            result["cold"].append({"index": index, "seconds": time.monotonic()-tick, "path": path, "identity": identity})
        result["coldSeconds"] = time.monotonic() - started
        result["renderCallsAfterCold"] = render.calls
        count = render.calls
        result["returns"] = []
        for index in (0, 19, 1, 15, 4, 0, 18, 2):
            tick = time.monotonic(); api._preview_window_file(test, index)
            result["returns"].append({"index": index, "milliseconds": (time.monotonic()-tick)*1000})
        assert render.calls == count
        # Changing the last shot preserves unrelated early window identities.
        unchanged = api._preview_window_file(test, 0)[1]
        test.sequence.tracks[0].clips[-1].opacity = .5
        assert api._preview_window_file(test, 0)[1] == unchanged
        assert render.calls == count
        result["localEditReusedEarlyWindow"] = True
        # Restore before comparing all windows after a fresh service instance.
        test.sequence.tracks[0].clips[-1].opacity = 1
        api.close()
        api = HttpApi(service, render, media_dir=str(EVIDENCE / "media"))
        started = time.monotonic()
        for index in range(20): api._preview_window_file(test, index)
        result["restartAll20Milliseconds"] = (time.monotonic()-started)*1000
        result["restartAdditionalRenders"] = render.calls - count
        assert render.calls == count
        # Real original project still has all effects/keyframes; retain a copy
        # of its existing cache for the browser/desktop visual acceptance run.
        result["cache"] = api.preview_cache.status()
        result["sourceAfter"] = fingerprint()
        assert result["sourceAfter"] == before
        (EVIDENCE / "persistent-cache-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({key: value for key, value in result.items() if key not in ("cold", "returns", "cache")}, ensure_ascii=False))
    finally:
        api.close(); store.close()

if __name__ == "__main__": main()
