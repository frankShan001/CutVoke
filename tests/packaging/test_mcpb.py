"""Offline packaging contract tests, NOT native engine/client acceptance."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location("build_mcpb", Path(__file__).resolve().parents[2] / "tools/build/build_mcpb.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MCPBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.staging = self.root / "中文 portable staging"
        self.staging.mkdir()
        self.output = self.root / "candidate.mcpb"
        self.fixture()

    def fixture(self, platform="linux-x64"):
        prefix = "CutVoke.app/Contents/Resources/engine" if platform.startswith("macos-") else "resources/engine"
        suffix = ".exe" if platform.startswith("windows-") else ""
        self.engine = self.staging / prefix
        files = [f"{prefix}/cutvoke-engine{suffix}", f"{prefix}/_internal/bin/ffmpeg{suffix}",
                 f"{prefix}/_internal/bin/ffprobe{suffix}", f"{prefix}/_internal/library.so",
                 "LICENSE.txt", "licenses/runtime-components/provenance.json",
                 "licenses/ffmpeg/Corresponding-Source.zip", "licenses/ffmpeg-sources.json",
                 "licenses/python/example/LICENSE", "licenses/fonts/OFL.txt", "licenses/Python-LICENSE.txt"]
        for name in files:
            path = self.staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic fixture, not executable or qualified runtime", "utf-8")
            if name.startswith(prefix):
                path.chmod(0o755)
        (self.staging / "build-info.json").write_text(json.dumps({"platform": platform, "version": "0.3.5", "commit": "a" * 40}))
        self.rehash()

    def rehash(self):
        hashes = {p.relative_to(self.staging).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.staging.rglob("*") if p.is_file() and p.name != "file-manifest.json"}
        (self.staging / "file-manifest.json").write_text(json.dumps(hashes))

    def test_manifest_payload_modes_and_reproducible_bytes(self):
        result = module.build(self.staging, self.output)
        second = self.root / "second.mcpb"
        self.assertEqual(result["sha256"], module.build(self.staging, second)["sha256"])
        self.assertFalse(result["runtime_verified"])
        with zipfile.ZipFile(self.output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["manifest_version"], "0.3")
            self.assertFalse(any(item.is_dir() for item in archive.infolist()))
            self.assertEqual(manifest["server"]["mcp_config"]["args"], ["mcp", "--data", "${user_config.data_directory}"])
            self.assertEqual(manifest["compatibility"]["platforms"], ["linux"])
            self.assertEqual(archive.getinfo("engine/cutvoke-engine").external_attr >> 16 & 0o777, 0o755)
            self.assertIn("engine/_internal/library.so", archive.namelist())
            self.assertIn("licenses/ffmpeg/Corresponding-Source.zip", archive.namelist())
            self.assertNotIn("server.json", archive.namelist())
            self.assertNotIn("file-manifest.json", archive.namelist())  # source paths change; do not ship misleading hashes

    def test_all_native_layouts(self):
        for platform in module.PLATFORMS:
            with self.subTest(platform=platform):
                self.fixture(platform)
                target = self.root / (platform + ".mcpb")
                module.build(self.staging, target)
                with zipfile.ZipFile(target) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    suffix = ".exe" if platform == "windows-x64" else ""
                    self.assertEqual(manifest["server"]["entry_point"], "engine/cutvoke-engine" + suffix)
                    self.assertEqual(manifest["compatibility"]["platforms"], [module.PLATFORMS[platform]])

    def test_missing_corrupt_and_untracked_payload_rejected(self):
        binary = self.engine / "_internal/bin/ffmpeg"
        original = binary.read_bytes()
        binary.unlink()
        with self.assertRaisesRegex(ValueError, "Missing native"):
            module.build(self.staging, self.output)
        binary.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "Unverified"):
            module.build(self.staging, self.output)
        binary.write_bytes(original)
        (self.engine / "extra-file").write_text("untracked")
        with self.assertRaisesRegex(ValueError, "Unverified"):
            module.build(self.staging, self.output)
        self.assertFalse(self.output.exists())

    def test_deleted_manifest_dependency_rejected(self):
        (self.engine / "_internal/library.so").unlink()
        with self.assertRaisesRegex(ValueError, "Missing manifested payload"):
            module.build(self.staging, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_bytes_during_archive_write_rejected(self):
        original = zipfile.ZipFile.open
        changed = False
        victim = self.engine / "_internal/library.so"
        def mutate(archive, name, mode="r", *args, **kwargs):
            nonlocal changed
            if mode == "w" and not changed:
                victim.write_text("changed after validation")
                changed = True
            return original(archive, name, mode, *args, **kwargs)
        with mock.patch.object(zipfile.ZipFile, "open", mutate):
            with self.assertRaisesRegex(ValueError, "Input changed during packaging"):
                module.build(self.staging, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".mcpb-*")), [])

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_symlink_change_cannot_publish_unsafe_target(self):
        link = self.engine / "_internal/library-link.so"
        link.symlink_to("library.so")
        self.rehash()
        original = zipfile.ZipFile.open
        changed = False
        def mutate(archive, name, mode="r", *args, **kwargs):
            nonlocal changed
            if mode == "w" and not changed:
                link.unlink()
                link.symlink_to("../../../../outside")
                changed = True
            return original(archive, name, mode, *args, **kwargs)
        with mock.patch.object(zipfile.ZipFile, "open", mutate):
            module.build(self.staging, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read("engine/_internal/library-link.so"), (self.engine / "_internal/library.so").read_bytes())

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_framework_directory_aliases_materialize_verified_files(self):
        self.fixture("macos-arm64")
        framework = self.engine / "_internal/Python.framework"
        resources = framework / "Versions/3.13/Resources"
        resources.mkdir(parents=True)
        (resources / "Info.plist").write_text("fixture metadata")
        (framework / "Versions/Current").symlink_to("3.13", target_is_directory=True)
        (framework / "Resources").symlink_to("Versions/Current/Resources", target_is_directory=True)
        self.rehash()
        module.build(self.staging, self.output)
        with zipfile.ZipFile(self.output) as archive:
            for alias in ("Resources", "Versions/Current/Resources", "Versions/3.13/Resources"):
                entry = archive.getinfo("engine/_internal/Python.framework/" + alias + "/Info.plist")
                self.assertTrue(stat.S_ISREG(entry.external_attr >> 16))
                self.assertEqual(archive.read(entry), b"fixture metadata")
            self.assertFalse(any(item.is_dir() for item in archive.infolist()))
        second = self.root / "repeat.mcpb"
        self.assertEqual(module.digest(self.output), module.build(self.staging, second)["sha256"])

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_directory_alias_missing_or_untracked_content_rejected(self):
        (self.engine / "alias").symlink_to("_internal", target_is_directory=True)
        victim = self.engine / "_internal/library.so"
        victim.unlink()
        with self.assertRaisesRegex(ValueError, "Missing manifested"):
            module.build(self.staging, self.output)
        victim.write_text("untracked")
        self.rehash()
        (self.engine / "_internal/extra").write_text("not in manifest")
        with self.assertRaisesRegex(ValueError, "Unverified"):
            module.build(self.staging, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_directory_alias_cycle_escape_and_dangling_rejected(self):
        link = self.engine / "_internal/alias"
        for target, error in (("..", "Cyclic"), ("../../..", "Escaping"),
                              ("missing", "Broken"), ("alias", "cyclic")):
            with self.subTest(target=target):
                link.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, error):
                    module.build(self.staging, self.output)
                self.assertFalse(self.output.exists())
                link.unlink()

    @unittest.skipIf(os.name == "nt", "POSIX special fixture")
    def test_directory_alias_special_file_rejected(self):
        (self.engine / "alias").symlink_to("_internal", target_is_directory=True)
        os.mkfifo(self.engine / "_internal/fifo")
        with self.assertRaisesRegex(ValueError, "Special file"):
            module.build(self.staging, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_link_target_corruption_rejected(self):
        link = self.engine / "_internal/library-link.so"
        link.symlink_to("library.so")
        self.rehash()
        (self.engine / "_internal/library.so").write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "Unverified file"):
            module.build(self.staging, self.output)
        self.assertFalse(self.output.exists())

    def test_unsafe_manifest_names(self):
        for name in ["../outside", "/absolute", "C:/file", "a\\b", "a//b", "a/./b"]:
            with self.subTest(name=name):
                (self.staging / "file-manifest.json").write_text(json.dumps({name: "a" * 64}))
                with self.assertRaisesRegex(ValueError, "Unsafe package path"):
                    module.build(self.staging, self.output)

    @unittest.skipIf(os.name == "nt", "POSIX symlink/mode fixtures")
    def test_internal_symlink_materialized_and_escape_rejected(self):
        link = self.engine / "_internal/library-link.so"
        link.symlink_to("library.so")
        self.rehash()
        module.build(self.staging, self.output)
        with zipfile.ZipFile(self.output) as archive:
            entry = archive.getinfo("engine/_internal/library-link.so")
            self.assertTrue(stat.S_ISREG(entry.external_attr >> 16))
            self.assertEqual(entry.external_attr >> 16 & 0o777, 0o755)
            self.assertEqual(archive.read(entry), (self.engine / "_internal/library.so").read_bytes())
        link.unlink()
        (self.root / "outside").write_text("outside")
        link.symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "Unsafe symlink"):
            module.build(self.staging, self.root / "escape.mcpb")

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_symlinked_ancestor_rejected(self):
        resources = self.staging / "resources"
        moved = self.root / "external-resources"
        resources.rename(moved)
        resources.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlinked payload root"):
            module.build(self.staging, self.output)

    def test_output_no_clobber_or_in_tree(self):
        self.output.write_text("keep")
        with self.assertRaises(FileExistsError):
            module.build(self.staging, self.output)
        self.assertEqual(self.output.read_text(), "keep")
        with self.assertRaisesRegex(ValueError, "outside"):
            module.build(self.staging, self.staging / "candidate.mcpb")

    @unittest.skipIf(os.name == "nt", "POSIX execute mode")
    def test_nonexecutable_rejected(self):
        (self.engine / "cutvoke-engine").chmod(0o644)
        with self.assertRaisesRegex(ValueError, "execute mode"):
            module.build(self.staging, self.output)


if __name__ == "__main__":
    unittest.main()
