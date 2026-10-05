"""Read real local projects without writing them; exercise recovery on a copy."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cutvoke.core.model import Project, Track
from cutvoke.core.protocol import Command
from cutvoke.core.rational import Rational
from cutvoke.core.service import EditService
from cutvoke.core.store import ProjectStore

OUT = ROOT / "output/acceptance/product-completion-20261003/readiness"
DB = Path.home() / ".cutvoke/data/projects.sqlite"


def snapshot():
    with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT project_id,name,revision,project_json,sequence_json,schema_version "
                          "FROM projects ORDER BY project_id").fetchall()
    return {pid: {"name": name, "revision": revision,
                 "raw": raw or json.dumps({"projectId": pid, "name": name,
                     "revision": revision, "schemaVersion": schema, "sequence": json.loads(seq)})}
            for pid, name, revision, raw, seq, schema in rows}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    before = snapshot()
    corpus = []
    for pid, data in before.items():
        project = Project.from_dict(json.loads(data["raw"]))
        service = EditService()
        service._projects[pid] = project
        started = time.perf_counter()
        report = service.execute(Command("project.preflight", {}, project_id=pid)).changed_entities[0]["report"]
        corpus.append({"name": data["name"], **report,
                       "elapsedSeconds": round(time.perf_counter() - started, 6),
                       "snapshotSha256": hashlib.sha256(data["raw"].encode("utf-8")).hexdigest()})
    master = Project.from_dict(json.loads(before["tide-gallery-master-20260927"]["raw"]))
    original = copy.deepcopy(next(clip for track in master.sequence.tracks if track.kind == "video"
                                  for clip in track.clips if clip.nested is None))
    source = Path(original.asset_ref.source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    recovery_db = OUT / "recovery.sqlite"
    if recovery_db.exists():
        raise FileExistsError("use a fresh evidence directory; refusing to overwrite recovery project")
    with ProjectStore(str(recovery_db)) as store:
        service = EditService(store=store)
        recovered = service.create_project("素材丢失恢复 | 潮汐素材副本", width=320, height=180,
                                           fps=Rational.of(25))
        original.id = "recovery-clip"
        original.timeline_start, original.timeline_end = Rational.of(0), Rational.of(1)
        original.effects, original.keyframes = [], {}
        original.asset_ref.source_path = str(OUT / "deliberately-missing.mp4")
        recovered.sequence.tracks = [Track("recovery-video", "video", [original])]
        store.save(recovered)
        pid = recovered.project_id
        def apply(kind, payload):
            return service.execute(Command(kind, payload, project_id=pid,
                expected_revision=service.get_project(pid).revision))
        missing = apply("project.preflight", {}).changed_entities[0]["report"]
        assert not missing["readyToRender"] and missing["errors"][0]["clipIds"] == ["recovery-clip"]
        store.add_asset(asset_id="registered-replacement", name=source.name, path=str(source),
                        size=source.stat().st_size, kind="video", has_video=True)
        swap = apply("asset.swap", {"clipIds": ["recovery-clip"], "assetId": "registered-replacement"})
        restored = apply("project.preflight", {}).changed_entities[0]["report"]
        assert restored["readyToRender"]
        apply("history.undo", {})
        assert not apply("project.preflight", {}).changed_entities[0]["report"]["readyToRender"]
        apply("history.redo", {})
        output = OUT / "recovered.mp4"
        exported = apply("export.video", {"outPath": str(output), "quality": "low"}).changed_entities
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "NUL"],
                       capture_output=True, check=True, timeout=90)
    with ProjectStore(str(recovery_db)) as store:
        reopened = EditService(store=store)
        assert reopened.execute(Command("project.preflight", {}, project_id=pid)).changed_entities[0]["report"]["readyToRender"]
        assert reopened.get_project(pid).sequence.tracks[0].clips[0].asset_ref.source_path == str(source)
    after = snapshot()
    assert before == after, "original projects changed during this read-only audit"
    result = {"schemaVersion": 1, "status": "passed", "readOnlyDatabase": str(DB),
              "originalProjectCount": len(before), "originalProjectsUnchanged": True,
              "corpus": corpus, "recovery": {"projectId": pid, "missing": missing,
                  "restored": restored, "swapRevision": swap.revision, "export": exported,
                  "undoRedo": True, "saveReopen": True, "fullDecode": True,
                  "outputSha256": hashlib.sha256(output.read_bytes()).hexdigest()}}
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "originalProjects": len(before),
                     "readyProjects": sum(item["readyToRender"] for item in corpus),
                     "originalProjectsUnchanged": True, "recoveryExport": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
