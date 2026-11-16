"""A stale destination must not silently become a same-track move."""
import tempfile
import unittest
from pathlib import Path

from cutvoke.domain.protocol import Actor, Command, ErrorCode
from cutvoke.editing.service import EditError, EditService
from cutvoke.storage.store import ProjectStore


def rat(value):
    return {'num': str(value), 'den': '1'}


class ClipMoveTargetTests(unittest.TestCase):
    def exercise(self, store):
        service = EditService(store=store)
        service.create_project('move-target')
        counter = 0

        def command(kind, payload):
            nonlocal counter
            counter += 1
            project = service.get_project('move-target')
            return Command(type=kind, payload=payload, command_id=f'move-target-{counter}',
                           project_id=project.project_id, expected_revision=project.revision,
                           actor=Actor('human', 'move-target-test'))

        def apply(kind, payload):
            return service.execute(command(kind, payload))

        apply('clip.insert', {'trackId': 'video', 'createTrackKind': 'video',
                             'clipId': 'parent', 'sourcePath': 'still.png',
                             'timelineStart': rat(0), 'timelineEnd': rat(2)})
        apply('clip.insert', {'trackId': 'title', 'createTrackKind': 'text',
                             'clipId': 'child', 'text': {'content': 'Title'},
                             'attachedToClipId': 'parent',
                             'timelineStart': rat(0), 'timelineEnd': rat(1)})
        apply('track.add', {'trackId': 'destination', 'kind': 'video'})
        invalid = {'clipId': 'parent', 'trackId': 'missing', 'timelineStart': rat(3)}
        for kind, payload in (
                ('clip.move', invalid),
                ('edit.batch', {'commands': [
                    {'type': 'clip.move', 'payload': {'clipId': 'parent', 'timelineStart': rat(1)}},
                    {'type': 'clip.move', 'payload': invalid}]})):
            for preview in (True, False):
                with self.subTest(persisted=store is not None, kind=kind, preview=preview):
                    before = service.get_project('move-target').to_dict()
                    depth = service._undo_depth('move-target')
                    events = len(service._events['move-target'])
                    request = command(kind, payload)
                    with self.assertRaises(EditError) as caught:
                        (service.preview_command if preview else service.execute)(request)
                    self.assertEqual(caught.exception.code, ErrorCode.INVALID_ARGUMENT)
                    self.assertIn('target track not found: missing', caught.exception.message)
                    self.assertEqual(service.get_project('move-target').to_dict(), before)
                    self.assertEqual(service._undo_depth('move-target'), depth)
                    self.assertEqual(len(service._events['move-target']), events)
                    self.assertNotIn(request.command_id, service._idempotency['move-target'])
                    if store:
                        self.assertEqual(store.load('move-target').to_dict(), before)
                        self.assertIsNone(store.get_idempotent('move-target', request.command_id))

        # Both the implicit source track and explicit same-track target stay valid.
        for target in (None, 'video', 'destination'):
            payload = {'clipId': 'parent', 'timelineStart': rat(counter)}
            if target is not None:
                payload['trackId'] = target
            apply('clip.move', payload)
            project = service.get_project('move-target')
            track = next(t for t in project.sequence.tracks if any(c.id == 'parent' for c in t.clips))
            self.assertEqual(track.id, target or 'video')
            parent = next(c for c in track.clips if c.id == 'parent')
            child = next(c for t in project.sequence.tracks for c in t.clips if c.id == 'child')
            self.assertEqual(child.timeline_start, parent.timeline_start)

    def test_memory(self):
        self.exercise(None)

    def test_persisted(self):
        with tempfile.TemporaryDirectory() as tmp, ProjectStore(str(Path(tmp) / 'project.sqlite')) as store:
            self.exercise(store)
