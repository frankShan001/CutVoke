"""Observable safety and failure boundaries of the copyable demonstration helper."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[2] / "skills/cutvoke-editor/scripts/demo.py"
spec = importlib.util.spec_from_file_location("cutvoke_skill_demo", SCRIPT)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DemoBoundaries(unittest.TestCase):
    def test_imports_belong_to_created_project_in_both_modes(self):
        class StopBeforeEdit(RuntimeError):
            pass

        for mode in ("mcp", "upload"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "new"
                calls = []
                assets = [{"assetId": f"asset-{i}", "available": True} for i in range(4)]
                client = MagicMock()
                def call(tool_name, **arguments):
                    calls.append((tool_name, arguments))
                    if tool_name == "project_list":
                        return {"projects": []}
                    if tool_name == "create_project":
                        return {"revision": 0}
                    if tool_name == "project_directory":
                        return {"exportDirectory": str(output / "project/exports"),
                                "backupDirectory": str(output / "project/backups")}
                    if tool_name == "media_import":
                        self.assertTrue(any(n == "create_project" for n, _ in calls))
                        self.assertEqual(arguments["projectId"], "trail-notes")
                        return {"assets": assets}
                    if tool_name == "edit_lock":
                        return {"lease": {"leaseId": "test-lease"}}
                    if tool_name == "command_preview":
                        raise StopBeforeEdit()
                    return {}
                client.call.side_effect = call
                client.rpc.return_value = {"tools": []}
                upload = MagicMock()
                upload.request.return_value = {"assets": assets}
                def import_files(paths, project_id):
                    self.assertTrue(any(n == "create_project" for n, _ in calls))
                    self.assertEqual(project_id, "trail-notes")
                    return assets
                upload.import_files.side_effect = import_files
                with patch.object(demo.sys, "argv", ["demo", "--output", str(output), "--import-mode", mode]), \
                     patch.object(demo.shutil, "which", return_value="ffmpeg"), \
                     patch.object(demo, "png"), patch.object(demo, "score"), \
                     patch.object(demo, "MCP", return_value=client), \
                     patch.object(demo, "WebUploads", return_value=upload):
                    with self.assertRaises(StopBeforeEdit):
                        demo.main()
                upload.request.assert_called_once_with("/api/v1/assets?projectId=trail-notes")
                self.assertEqual(sum(n == "create_project" for n, _ in calls), 1)
                self.assertEqual(sum(n == "media_import" for n, _ in calls), mode == "mcp")
                client.close.assert_called_once_with(output)
                upload.close.assert_called_once()

    def test_project_outputs_use_independent_roots_and_legacy_fallback(self):
        output = Path("fresh-demo")
        client = MagicMock()
        client.call.return_value = {"exportDirectory": str(output / "project/exports"),
                                    "backupDirectory": str(output / "project/backups")}
        video, package = demo.project_outputs(client, "trail-notes", output)
        self.assertEqual(video.relative_to(output).as_posix(), "project/exports/trail-notes.mp4")
        self.assertEqual(package.relative_to(output).as_posix(), "project/backups/trail-notes.cvkpkg")
        client.call.assert_called_once_with("project_directory", projectId="trail-notes")
        client.call.return_value = {"storageKind": "legacy", "directory": None}
        self.assertEqual(demo.project_outputs(client, "legacy", output),
                         (output / "trail-notes.mp4", output / "trail-notes.cvkpkg"))

    def test_uploads_include_project_for_write_and_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.png"
            path.write_bytes(b"synthetic")
            upload = demo.WebUploads.__new__(demo.WebUploads)
            asset = {"assetId": "one", "available": True}
            upload.request = MagicMock(side_effect=[{"assetId": "one"}, {"assets": [asset]}])
            self.assertEqual(upload.import_files([path], "project with space"), [asset])
            self.assertEqual(upload.request.call_args_list[0].args,
                ("/api/v1/assets?name=original.png&projectId=project+with+space", b"synthetic"))
            self.assertEqual(upload.request.call_args_list[1].args,
                ("/api/v1/assets?projectId=project+with+space",))

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            marker = output / "keep.txt"
            marker.write_bytes(b"existing work")
            with patch.object(demo.sys, "argv", ["demo", "--output", str(output)]), \
                 patch.object(demo.shutil, "which", return_value="ffmpeg"), \
                 patch.object(demo.subprocess, "Popen") as launch, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    demo.main()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(marker.read_bytes(), b"existing work")
            launch.assert_not_called()

    def test_invalid_inputs_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for flag in ("--engine", "--effects-dir"):
                with self.subTest(flag=flag):
                    output = base / "not-created"
                    with patch.object(demo.sys, "argv", ["demo", "--output", str(output), flag, str(base / "absent")]), \
                         patch.object(demo.subprocess, "Popen") as launch, contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as error:
                            demo.main()
                    self.assertEqual(error.exception.code, 2)
                    self.assertFalse(output.exists())
                    launch.assert_not_called()

    def test_upload_requests_cannot_change_origin(self):
        upload = demo.WebUploads.__new__(demo.WebUploads)
        upload.url = "http://127.0.0.1:12345"
        upload.opener = MagicMock()
        for route in ("//remote.example/upload", "https://remote.example/upload", "/outside-api"):
            with self.assertRaises(ValueError):
                upload.request(route, b"original media")
        upload.opener.open.assert_not_called()

    def test_wrong_runtime_only_cleans_up_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            process = MagicMock()
            process.pid = 43210
            process.poll.return_value = None
            reservation = MagicMock()
            reservation.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 54321)
            with patch.object(demo.socket, "socket", return_value=reservation), \
                 patch.object(demo.subprocess, "Popen", return_value=process), \
                 patch.object(demo.subprocess, "run") as stop_tree, \
                 patch.object(demo.os, "killpg", create=True) as stop_group, \
                 patch.object(demo.os, "kill") as stop_http_pid, \
                 patch.object(demo.WebUploads, "request", return_value={"dataPath": str(output / "other.sqlite"), "processId": 99999}):
                with self.assertRaises(RuntimeError):
                    demo.WebUploads(["trusted-engine"], output / "ours.sqlite", output, None)
            stop_http_pid.assert_not_called()
            if demo.os.name == "nt":
                self.assertEqual(stop_tree.call_args.args[0], ["taskkill", "/PID", "43210", "/T", "/F"])
            else:
                self.assertEqual(stop_group.call_args.args[0], 43210)

    def test_failed_mcp_initialization_closes_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            process = MagicMock()
            process.stdin, process.stdout = io.StringIO(), io.StringIO()
            with patch.object(demo.subprocess, "Popen", return_value=process):
                with self.assertRaises(RuntimeError):
                    demo.MCP(["trusted-engine"], output / "ours.sqlite", output, None)
            self.assertTrue(process.stdin.closed)
            self.assertTrue((output / "calls.json").is_file())
            process.wait.assert_called()


if __name__ == "__main__":
    unittest.main()
