"""Font selection through the shared editing, persistence and Agent contract."""
import tempfile
import unittest
from pathlib import Path

from cutvoke.core.protocol import Command
from cutvoke.core.service import EditError, EditService
from cutvoke.core.store import ProjectStore


class CaptionFontContractTests(unittest.TestCase):
    def apply(self, service, project_id, kind, payload):
        return service.execute(Command(kind, payload, project_id=project_id,
            expected_revision=service.get_project(project_id).revision))

    def add(self, service, project_id, caption_id="caption", family="Noto Sans SC"):
        self.apply(service, project_id, "caption.add", {
            "captionId": caption_id, "text": "字体可以保存", "fontFamily": family,
            "start": {"num": "0", "den": "1"}, "end": {"num": "2", "den": "1"}})

    def test_font_update_reopens_and_persisted_undo_restores_previous_family(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "projects.sqlite")
            with ProjectStore(path) as store:
                service = EditService(store=store)
                pid = service.create_project("font persistence").project_id
                self.add(service, pid)
                self.apply(service, pid, "caption.update", {
                    "captionId": "caption", "fontFamily": "Noto Serif SC"})
                self.assertEqual(service.get_project(pid).sequence.captions[0].fontFamily, "Noto Serif SC")
            with ProjectStore(path) as store:
                service = EditService(store=store)
                self.assertEqual(service.get_project(pid).sequence.captions[0].fontFamily, "Noto Serif SC")
                self.apply(service, pid, "history.undo", {})
                self.assertEqual(service.get_project(pid).sequence.captions[0].fontFamily, "Noto Sans SC")
                self.apply(service, pid, "history.redo", {})
                self.assertEqual(service.get_project(pid).sequence.captions[0].fontFamily, "Noto Serif SC")

    def test_preview_and_invalid_family_do_not_change_saved_caption_or_revision(self):
        service = EditService()
        pid = service.create_project("font atomicity").project_id
        self.add(service, pid)
        before = service.get_project(pid).to_dict()
        preview = Command("caption.update", {"captionId": "caption", "fontFamily": "Noto Serif SC"},
                          project_id=pid, expected_revision=service.get_project(pid).revision)
        service.preview_command(preview)
        self.assertEqual(service.get_project(pid).to_dict(), before)
        for value in ("Arial", "", None, True, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(EditError):
                    self.apply(service, pid, "caption.update", {
                        "captionId": "caption", "text": "must roll back", "fontFamily": value})
                self.assertEqual(service.get_project(pid).to_dict(), before)

    def test_patch_bulk_add_and_batch_style_share_font_selection(self):
        service = EditService()
        pid = service.create_project("font styles").project_id
        self.add(service, pid, family="Noto Serif SC")
        self.apply(service, pid, "caption.patch", {"updates": [
            {"captionId": "caption", "fontFamily": "Noto Sans SC"}]})
        self.assertEqual(service.get_project(pid).sequence.captions[0].fontFamily, "Noto Sans SC")
        self.apply(service, pid, "caption.bulkAdd", {"segments": [{
            "text": "另一条", "start": {"num": "2", "den": "1"},
            "end": {"num": "3", "den": "1"}}], "style": {"fontFamily": "Noto Serif SC"}})
        self.assertEqual(service.get_project(pid).sequence.captions[1].fontFamily, "Noto Serif SC")
        self.apply(service, pid, "caption.batchStyle", {"style": {"fontFamily": "Noto Serif SC"}})
        self.assertTrue(all(c.fontFamily == "Noto Serif SC" for c in service.get_project(pid).sequence.captions))

    def test_font_is_discoverable_and_project_lookup_returns_stored_value(self):
        service = EditService()
        pid = service.create_project("font Agent discovery").project_id
        self.add(service, pid, family="Noto Serif SC")
        catalog = {c["type"]: c for c in service.command_catalog()}
        for kind in ("caption.add", "caption.update"):
            self.assertIn("fontFamily", {p["name"] for p in catalog[kind]["params"]})
        result = service.project_lookup(pid, entity_type="caption", fields=["fontFamily"])
        self.assertEqual(result["items"][0]["fontFamily"], "Noto Serif SC")
