"""Harness contracts only; native MCPB evidence must come from matching runners."""
import importlib.util
import io
import json
import queue
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
            process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO('{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n'))
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


class NativeResponseTests(unittest.TestCase):
    def client(self, messages):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        client = module.Client.__new__(module.Client)
        client.serial = 0
        client.process = mock.Mock(stdin=io.StringIO())
        client.log = Path(directory.name) / 'transcript.jsonl'
        client.messages = queue.Queue()
        for message in messages:
            client.messages.put(message)
        return client

    def test_notifications_before_matching_response(self):
        client = self.client([
            {'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'},
            {'jsonrpc': '2.0', 'method': 'notifications/resources/list_changed'},
            {'jsonrpc': '2.0', 'id': 1, 'result': {'tools': []}},
        ])
        self.assertEqual(client.request('tools/list'), {'tools': []})
        self.assertEqual(len(client.log.read_text().splitlines()), 3)

    def test_invalid_envelopes_and_ids_fail_closed(self):
        for response in [
            [], {'stdioClosed': True}, {'unexpectedStdout': 'noise'},
            {'id': 1, 'result': {}},
            *[{'jsonrpc': '2.0', 'id': value, 'result': {}}
              for value in (2, None, True, 1.0, '1')],
            {'jsonrpc': '2.0', 'id': 1, 'error': {'code': -1}},
            {'jsonrpc': '2.0', 'id': 1},
            {'jsonrpc': '2.0', 'method': 'notice', 'result': {}},
            {'jsonrpc': '2.0', 'id': 1, 'method': 'request', 'result': {}},
        ]:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                self.client([response]).request('tools/list')

    def test_notifications_do_not_restart_deadline(self):
        client = self.client([{'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'}])
        with mock.patch.object(module.time, 'monotonic', side_effect=[0, 1, 181]):
            with self.assertRaises(TimeoutError):
                client.request('tools/list')

    def test_empty_queue_is_explicit_timeout(self):
        client = self.client([])
        client.messages = mock.Mock()
        client.messages.get.side_effect = queue.Empty
        with mock.patch.object(module.time, 'monotonic', side_effect=[0, 1]):
            with self.assertRaises(TimeoutError):
                client.request('tools/list')
        client.messages.get.assert_called_once_with(timeout=179)


class CliPhaseEvidenceTests(unittest.TestCase):
    def test_real_timeout_retains_bounded_safe_phase_and_original_exception(self):
        import subprocess
        import sys
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def shortened(command, **kwargs):
                self.assertEqual(kwargs['timeout'], 180)
                self.assertEqual(json.loads((root / 'unpack-1.json').read_text())['status'], 'started')
                kwargs['timeout'] = 1.0  # Test only; production still passes 180.
                return real_run(command, **kwargs)
            code = "import sys,time;print('SECRET',flush=True);print('TOKEN',file=sys.stderr,flush=True);time.sleep(5)"
            with mock.patch.object(module.subprocess, 'run', side_effect=shortened):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    module.run([sys.executable, '-c', code], evidence=root, phase='unpack-1')
            self.assertEqual(caught.exception.timeout, 1.0)
            text = (root / 'unpack-1.json').read_text()
            record = json.loads(text)
            self.assertEqual(record['exception'], 'TimeoutExpired')
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['timeoutSeconds'], 180)
            self.assertGreater(record['elapsedSeconds'], 0)
            self.assertGreater(record['stdoutBytes'], 0)
            self.assertGreater(record['stderrBytes'], 0)
            self.assertLess(len(text), 512)
            for secret in ('SECRET', 'TOKEN', sys.executable, code):
                self.assertNotIn(secret, text)

    def test_exit_and_launch_failures_reraise_same_object_without_raw_output(self):
        import subprocess
        errors = [subprocess.TimeoutExpired(['SECRET'], 180, output='PRIVATE', stderr='TOKEN'),
                  subprocess.CalledProcessError(7, ['SECRET'], output=b'PRIVATE', stderr=b'TOKEN'),
                  FileNotFoundError('PRIVATE/path')]
        for error in errors:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with mock.patch.object(module.subprocess, 'run', side_effect=error), mock.patch('builtins.print') as output:
                    with self.assertRaises(type(error)) as caught:
                        module.run(['SECRET'], evidence=root, phase='validate-2')
                self.assertIs(caught.exception, error)
                output.assert_not_called()
                record = json.loads((root / 'validate-2.json').read_text())
                self.assertEqual(record['exception'], type(error).__name__)
                self.assertEqual(record['returncode'], getattr(error, 'returncode', None))
                self.assertNotIn('PRIVATE', json.dumps(record))
                self.assertNotIn('TOKEN', json.dumps(record))

    def test_diagnostic_write_or_replace_failure_does_not_change_outcome(self):
        import subprocess
        for operation in ('write_text', 'replace'):
            for fail in (False, True):
                with self.subTest(operation=operation, fail=fail), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    error = subprocess.TimeoutExpired(['SECRET'], 180)
                    result = subprocess.CompletedProcess(['SECRET'], 0, b'OK', b'')
                    with mock.patch.object(Path, operation, side_effect=PermissionError('private path')):
                        with mock.patch.object(module.subprocess, 'run', side_effect=error if fail else None, return_value=result):
                            if fail:
                                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                                    module.run(['SECRET'], evidence=root, phase='unpack-2')
                                self.assertIs(caught.exception, error)
                            else:
                                self.assertIs(module.run(['SECRET'], evidence=root, phase='unpack-2'), result)

    def test_success_records_counts_and_rejects_arbitrary_phase(self):
        import subprocess
        result = subprocess.CompletedProcess(['SECRET'], 0, b'x' * 1000000, b'token')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(module.subprocess, 'run', return_value=result) as run:
                self.assertIs(module.run(['SECRET'], evidence=root, phase='validate-1'), result)
                run.assert_called_once_with(['SECRET'], check=True, timeout=180, capture_output=True)
                record = json.loads((root / 'validate-1.json').read_text())
                self.assertEqual(record['status'], 'completed')
                self.assertEqual(record['stdoutBytes'], 1000000)
                self.assertEqual(record['stderrBytes'], 5)
                with self.assertRaises(ValueError):
                    module.run(['SECRET'], evidence=root, phase='../secret')
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
