"""Repository hygiene must catch errors even before new files are staged."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.verify.check_asset_layout import check


class AssetLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.image = self.write("docs/images/readme/demo.png", b"original image bytes")
        self.write("README.md", b"![Editor](docs/images/readme/demo.png)\n")
        self.write("resources/source/provenance.json", json.dumps({
            "entries": [{"path": "docs/images/readme/demo.png",
                         "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest()}],
        }).encode())

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_valid_unstaged_source(self):
        self.assertEqual(check(self.root), [])

    def test_detects_changed_source_bytes(self):
        self.image.write_bytes(b"edited image")
        self.assertTrue(any("provenance bytes changed" in error for error in check(self.root)))

    def test_rejects_private_history_even_when_force_added(self):
        self.write(".gitignore", b"docs/history/\n")
        self.write("docs/history/session.md", b"local development notes")
        subprocess.run(["git", "add", "-f", "docs/history/session.md"], cwd=self.root, check=True)
        self.assertTrue(any("Private development record" in error for error in check(self.root)))

    def test_rejects_documentation_link_to_missing_guide(self):
        self.write("README.md", b"[Guide](docs/user/removed.md)\n")
        self.assertTrue(any("Broken documentation link" in error for error in check(self.root)))

    def test_rejects_ignored_local_image_dependency(self):
        self.write(".gitignore", b".artifacts/\n")
        self.write(".artifacts/test.png", b"local")
        self.write("README.md", b"![Temporary](.artifacts/test.png)\n")
        self.assertTrue(any("unversioned file" in error for error in check(self.root)))

    def test_detects_force_added_generated_database(self):
        self.write(".gitignore", b".artifacts/\n")
        self.write(".artifacts/projects.sqlite", b"not user data")
        subprocess.run(["git", "add", "-f", ".artifacts/projects.sqlite"], cwd=self.root, check=True)
        self.assertTrue(any("Local/generated file" in error for error in check(self.root)))

    def test_rejects_review_output_in_source_tree(self):
        self.write("resources/source/materials/new-contact-sheet.png", b"intermediate")
        self.assertTrue(any("mixed with source artwork" in error for error in check(self.root)))

    def test_rejects_retired_directory_and_missing_script_input(self):
        self.write("docs/assets/unused.png", b"old")
        self.write("tools/assets/extract.py", b'ATLAS = "resources/source/materials/missing.png"\n')
        errors = check(self.root)
        self.assertTrue(any("Use source or documentation" in error for error in errors))
        self.assertTrue(any("Missing source input" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
