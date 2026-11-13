"""Harness contracts only; native MCPB evidence must come from matching runners."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('native_mcpb', Path(__file__).resolve().parents[2] / 'tools/verify/verify_native_mcpb.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativeVerifierTests(unittest.TestCase):
    def test_manifest_command_external_data_and_empty_runtime_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / 'install'
            install.mkdir()
            data = root / '外部 数据'
            (install / 'manifest.json').write_text(json.dumps({'server': {'mcp_config': {
                'command': '${__dirname}/engine/cutvoke-engine',
                'args': ['mcp', '--data', '${user_config.data_directory}']}}}), 'utf-8')
            process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO('{"id":1,"result":{"tools":[]}}\n'))
            process.wait.return_value = 0
            process.poll.return_value = 0
            with mock.patch.object(module.subprocess, 'Popen', return_value=process) as popen:
                with mock.patch.dict(module.os.environ, {'PATH': 'system-tools', 'PYTHONPATH': 'checkout', 'CUTVOKE_FFMPEG_DIR': 'system-ffmpeg'}):
                    client = module.Client(install, data, root)
                args, kwargs = popen.call_args
                self.assertEqual(args[0], [str(install) + '/engine/cutvoke-engine', 'mcp', '--data', str(data)])
                self.assertEqual(kwargs['env']['PATH'], '')
                self.assertNotIn('PYTHONPATH', kwargs['env'])
                self.assertNotIn('CUTVOKE_FFMPEG_DIR', kwargs['env'])
                self.assertEqual(client.request('tools/list'), {'tools': []})
                client.close()
                process.kill.assert_not_called()
                self.assertTrue(process.stdout.closed)
                self.assertIsNone(client.process)
                self.assertFalse(client.reader.is_alive())
                self.assertTrue((root / 'transcript.jsonl').is_file())

    def test_tool_argument_names_do_not_collide_with_dispatch(self):
        client = module.Client.__new__(module.Client)
        payload = {'ok': True, 'revision': '0'}
        raw = {'content': [{'type': 'text', 'text': json.dumps(payload)}]}
        client.request = mock.Mock(return_value=raw)
        result, wire = client.call('create_project', name='MCPB 验收', tool_name='user-field', projectId='smoke')
        self.assertEqual(result, payload)
        self.assertEqual(wire, raw)
        client.request.assert_called_once_with('tools/call', {
            'name': 'create_project', 'arguments': {'name': 'MCPB 验收', 'tool_name': 'user-field', 'projectId': 'smoke'}})

    def test_nonzero_shutdown_rejected(self):
        client = module.Client.__new__(module.Client)
        client.process = mock.Mock()
        client.process.wait.return_value = 1
        client.process.poll.return_value = 1
        client.stderr = io.StringIO()
        client.reader = mock.Mock()
        client.reader.is_alive.return_value = False
        with self.assertRaises(AssertionError):
            client.close()
        self.assertTrue(client.stderr.closed)

    def test_live_reader_rejects_normal_shutdown(self):
        client = module.Client.__new__(module.Client)
        client.process = mock.Mock()
        client.process.wait.return_value = 0
        client.process.poll.return_value = 0
        client.stderr = io.StringIO()
        client.reader = mock.Mock()
        client.reader.is_alive.return_value = True
        with self.assertRaisesRegex(AssertionError, 'stdout remains open'):
            client.close()
        self.assertTrue(client.stderr.closed)
        self.assertIsNone(client.process)

    def test_windows_retirement_retries_only_transient_permission_failure(self):
        install = mock.Mock()
        install.rename.side_effect = [PermissionError('busy'), None]
        install.exists.return_value = False
        with mock.patch.object(module.os, 'name', 'nt'), mock.patch.object(module.time, 'sleep') as sleep:
            module.retire_install(install, 'retired')
        self.assertEqual(install.rename.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_retirement_fails_closed_for_persistent_lock(self):
        install = mock.Mock()
        install.rename.side_effect = PermissionError('busy')
        with mock.patch.object(module.os, 'name', 'nt'), mock.patch.object(module.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                module.retire_install(install, 'retired')
        self.assertEqual(install.rename.call_count, 21)
        self.assertEqual(sleep.call_count, 20)

    def test_retirement_does_not_hide_non_permission_error(self):
        install = mock.Mock()
        install.rename.side_effect = FileExistsError('occupied')
        with self.assertRaises(FileExistsError):
            module.retire_install(install, 'retired')
        install.rename.assert_called_once()

    def test_retirement_requires_original_path_gone(self):
        install = mock.Mock()
        install.exists.return_value = True
        with self.assertRaises(AssertionError):
            module.retire_install(install, 'retired')


if __name__ == '__main__':
    unittest.main()
