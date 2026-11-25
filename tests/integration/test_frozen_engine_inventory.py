import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

from tools.verify.verify_unix_frozen_engine_inventory import PROGRAMS, verify


@unittest.skipUnless(sys.platform == "linux", "Linux package inventory")
class FrozenEngineInventoryTests(unittest.TestCase):
    def setUp(self):
        scratch_dir = tempfile.TemporaryDirectory()
        self.addCleanup(scratch_dir.cleanup)
        scratch = scratch_dir.name
        self.builder = Path(scratch) / "builder"
        self.extracted = Path(scratch) / "中文 extracted"
        for program in PROGRAMS:
            path = self.builder / program
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(program.encode())
            path.chmod(0o700)
        payload = self.builder / "_internal/model.data"
        payload.write_bytes(b"original bytes")
        (self.builder / "empty build dir").mkdir()
        (self.builder / "_internal/alias").symlink_to("model.data")
        (self.builder / "media").symlink_to("_internal/bin")
        (self.builder / "link-chain").symlink_to("media/../alias")
        shutil.copytree(self.builder, self.extracted, symlinks=True)
        (self.extracted / "empty build dir").rmdir()
        for program in PROGRAMS:
            (self.extracted / program).chmod(0o755)

    def test_accepts_relocated_tar_preserving_symlinks_and_owner_execution(self):
        archive = self.extracted.parent / "test.tar.gz"
        with tarfile.open(archive, "w:gz") as target:
            target.add(self.extracted, arcname="app/resources/engine")
        extracted = self.extracted.parent / "verified-download"
        extracted.mkdir()
        subprocess.run(["/usr/bin/tar", "-xzf", str(archive), "-C", str(extracted)], check=True)
        self.assertEqual(verify(self.builder, extracted / "app/resources/engine"),
                         {"files": 4, "relativeLinks": 3})

    def test_rejects_modified_or_unexpected_bytes(self):
        path = self.extracted / "_internal/model.data"
        path.write_bytes(b"modified bytes")
        with self.assertRaisesRegex(ValueError, "changed extracted entry"):
            verify(self.builder, self.extracted)
        path.write_bytes(b"original bytes")
        (self.extracted / "_internal/unknown").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "unexpected extracted entry"):
            verify(self.builder, self.extracted)

    def test_rejects_missing_file_and_removed_execute(self):
        (self.extracted / "link-chain").unlink()
        with self.assertRaisesRegex(ValueError, "missing extracted entry"):
            verify(self.builder, self.extracted)
        (self.extracted / "link-chain").symlink_to("media/../alias")
        (self.extracted / PROGRAMS[1]).chmod(0o644)
        with self.assertRaisesRegex(ValueError, "missing regular owner-executable program"):
            verify(self.builder, self.extracted)

    def test_rejects_unsafe_or_changed_relative_links(self):
        alias = self.extracted / "_internal/alias"
        for target, reason in [
            ("../../builder/_internal/model.data", "escaping link"),
            ("missing", "unresolved link"),
            (str(self.builder / "_internal/model.data"), "absolute or cyclic link"),
            ("alias", "absolute or cyclic link"),
            ("model.data/..", "unresolved link"),
            ("./model.data", "changed extracted entry"),
        ]:
            with self.subTest(target=target):
                alias.unlink()
                alias.symlink_to(target)
                with self.assertRaisesRegex(ValueError, reason):
                    verify(self.builder, self.extracted)

    def test_rejects_special_file_and_same_tree(self):
        with self.assertRaisesRegex(ValueError, "independent roots"):
            verify(self.builder, self.builder)
        os.mkfifo(self.extracted / "fifo")
        with self.assertRaisesRegex(ValueError, "unsupported entry"):
            verify(self.builder, self.extracted)


if __name__ == "__main__":
    unittest.main()
