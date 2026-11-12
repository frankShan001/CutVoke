"""Subtitle handoff preserves content and intentional duplicate multiplicity."""
from collections import Counter
import shutil

from cutvoke.domain.captions import parse_srt
from cutvoke.domain.rational import Rational
from cutvoke.editing.service import EditService
from cutvoke.storage.project_directory import DirectoryProjectStore, ProjectLibraryStore
from cutvoke.storage.projectpack import pack_project, unpack_project


def timestamp(milliseconds):
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def caption_values(captions):
    # SRT assigns fresh IDs and sorts equal-time cues by ID; compare multiplicity,
    # not random IDs or incidental order among simultaneous cues.
    return Counter((c.text, c.start.num, c.start.den, c.end.num, c.end.den)
                   for c in captions)


def test_long_multilingual_captions_survive_directory_package_and_srt_handoff(tmp_path):
    blocks = []
    expected = Counter()
    for i in range(1200):
        start = Rational.of(i * 2501, 1000)
        end = Rational.of(i * 2501 + 1733, 1000)
        text = (f"镜头 {i} · café العربية 🙂\nsecond line {i}" if i % 7 == 0
                else "Repeated on purpose" if i % 11 == 0 else f"Unique caption {i}")
        expected[(text, start.num, start.den, end.num, end.den)] += 1
        blocks.append(f"{i + 1}\n{timestamp(i * 2501)} --> {timestamp(i * 2501 + 1733)}\n{text}")
    # Two real entities may intentionally share both timing and text.
    for i in range(2):
        blocks.append(f"{1201 + i}\n00:59:59,999 --> 01:00:00,999\nIntentional duplicate")
        expected[("Intentional duplicate", 3599999, 1000, 3600999, 1000)] += 1
    srt = "\ufeff" + "\r\n\r\n".join(b.replace("\n", "\r\n") for b in blocks) + "\r\n"
    count = sum(expected.values())
    library = ProjectLibraryStore(str(tmp_path / "sender.sqlite"))
    service = EditService(library)
    try:
        service.create_project("sender")
        assert service.import_srt("sender", srt) == count
        project = service.get_project("sender")
        assert caption_values(project.sequence.captions) == expected
        exact = [c.to_dict() for c in project.sequence.captions]
        for _ in range(5):
            assert caption_values(parse_srt(service.export_srt("sender"))) == expected
            assert [c.to_dict() for c in service.get_project("sender").sequence.captions] == exact
        package = tmp_path / "handoff.cvkpkg"
        pack_project(project, str(package))
        directory = library.directory("sender")
    finally:
        service.close()
        library.close()

    moved = tmp_path / "移动 工程"
    shutil.move(str(directory), moved)
    store = DirectoryProjectStore(moved)
    service = EditService(store)
    try:
        reopened = service.load_project("sender")
        assert [c.to_dict() for c in reopened.sequence.captions] == exact
        assert caption_values(parse_srt(service.export_srt("sender"))) == expected
    finally:
        service.close()
        store.close()

    restored, mapping, warnings = unpack_project(str(package), str(tmp_path / "receiver"))
    assert not warnings
    assert not mapping  # Captions only: no external media is needed for this test.
    assert [c.to_dict() for c in restored.sequence.captions] == exact
    library = ProjectLibraryStore(str(tmp_path / "receiver.sqlite"))
    service = EditService(library)
    try:
        service.import_project(restored, project_id="received")
        exported = service.export_srt("received")
        assert caption_values(parse_srt(exported)) == expected
        service.create_project("srt-only")
        assert service.import_srt("srt-only", exported) == count
        assert caption_values(service.get_project("srt-only").sequence.captions) == expected
    finally:
        service.close()
        library.close()
