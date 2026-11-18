import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ci_ffmpeg', Path(__file__).resolve().parents[2] / 'tools/verify/install_ci_ffmpeg.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
EC2 = 'http://us-east-2.ec2.archive.ubuntu.com/ubuntu/'
ARCHIVE = 'https://archive.ubuntu.com/ubuntu/'
METADATA = ('Package: ffmpeg\nArchitecture: amd64\nVersion: 7:6.1.1-3ubuntu5\n'
            'Filename: pool/universe/f/ffmpeg/ffmpeg_6.1.1-3ubuntu5_amd64.deb\n'
            'Size: 1900000\nSHA256: ' + 'a' * 64 + '\n')
SOURCE = ('Types: deb\nURIs: ' + guard.MIRROR_URI + '\n'
          'Suites: noble noble-updates noble-backports\nComponents: main universe restricted multiverse\n'
          'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n\n'
          'Types: deb\nURIs: http://security.ubuntu.com/ubuntu/\nSuites: noble-security\n'
          'Components: main universe restricted multiverse\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n')


class MirrorInstall(unittest.TestCase):
    def test_configured_candidates_only(self):
        self.assertEqual(guard.candidates(EC2 + '\tpriority:1\n' + ARCHIVE + '\tpriority:2'), [EC2, ARCHIVE])
        azure = 'http://azure.archive.ubuntu.com/ubuntu/'
        for primary in (EC2, azure):
            self.assertEqual(guard.candidates(primary + '\tpriority:1\n' + ARCHIVE +
                '\tpriority:2\nhttps://security.ubuntu.com/ubuntu/\tpriority:3'), [primary, ARCHIVE])

    def test_reject_urls(self):
        for bad in ('https://archive.ubuntu.com.evil/ubuntu/', 'https://evil@archive.ubuntu.com/ubuntu/',
                    'https://archive.ubuntu.com:443/ubuntu/', 'https://archive.ubuntu.com/ubuntu/?x=1',
                    'https://archive.ubuntu.com/ubuntu/../', 'file:///ubuntu/', ARCHIVE+'#x',
                    ARCHIVE+'\n'+ARCHIVE, 'https://azure.archive.ubuntu.com.evil/ubuntu/',
                    'http://us-west-1.ec2.archive.ubuntu.com/ubuntu/',
                    'http://archive.ubuntu.com/ubuntu/\n'+ARCHIVE, '', 'http://127.0.0.1/ubuntu/'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                guard.candidates(bad)

    def test_package_metadata(self):
        path, size = guard.package_metadata(METADATA)
        self.assertTrue(path.endswith('.deb'))
        self.assertEqual(size, 1900000)
        for bad in (METADATA.replace('pool/universe/', '../pool/universe/'),
                    METADATA.replace('6.1.1', '7.1.5'), METADATA.replace('1900000', '512'),
                    METADATA.replace('SHA256:', 'MD5sum:'), METADATA.replace('_amd64.deb', '_amd64.deb?x')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                guard.package_metadata(bad)

    def test_select_canonical_only_without_ranking_or_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def qualified(url, *_):
                calls.append(url)
                return 3
            self.assertEqual(guard.select([EC2, ARCHIVE], 'file', 1, Path(d), qualified), ARCHIVE)
            self.assertEqual(calls, [ARCHIVE])
            calls.clear()
            def failed(url, *_):
                calls.append(url)
                raise ValueError('failed')
            with self.assertRaises(RuntimeError):
                guard.select([EC2, ARCHIVE], 'file', 1, Path(d), failed)
            self.assertEqual(calls, [ARCHIVE])

    def test_missing_exact_configured_canonical_stops_before_probe(self):
        for urls in ([EC2], ['http://archive.ubuntu.com/ubuntu/'], []):
            with self.subTest(urls=urls), patch.object(guard, 'probe') as unused:
                with self.assertRaises(RuntimeError):
                    guard.select(urls, 'file', 1, Path('/unused'), unused)
                unused.assert_not_called()

    def test_range_probe_limits_and_failures(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def fake(args, **kwargs):
                self.assertIn('--max-time', args)
                self.assertEqual(args[args.index('--max-time') + 1], '15')
                self.assertNotIn('--location', args)
                self.assertEqual(kwargs['timeout'], 17)
                (root/'probe.deb.part').write_bytes(b'x' * guard.PROBE_BYTES)
                (root/'probe.headers').write_text(f'HTTP/1.1 206 Partial Content\nContent-Range: bytes 0-{guard.PROBE_BYTES-1}/1900000\n')
                return SimpleNamespace(returncode=0, stdout='206 1.5')
            self.assertEqual(guard.probe(ARCHIVE, 'file', 1900000, root, fake), 1.5)
            for output in ('302 1.0', '200 1.0', '206 14.0'):
                def bad(args, **kwargs):
                    fake(args, **kwargs)
                    return SimpleNamespace(returncode=0, stdout=output)
                with self.subTest(output=output), self.assertRaises(ValueError):
                    guard.probe(ARCHIVE, 'file', 1900000, root, bad)
            with self.assertRaises(ValueError):
                guard.probe(ARCHIVE, 'file', 1900000, root,
                    lambda *a, **k: SimpleNamespace(returncode=28, stdout=''))

    def test_task_local_sources_preserve_authentication(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); etc = root/'etc'; parts = etc/'sources.list.d'; parts.mkdir(parents=True)
            (parts/'ubuntu.sources').write_text(SOURCE)
            target = root/'task'; target.mkdir()
            args = guard.source_copy(etc, target, ARCHIVE)
            self.assertEqual((parts/'ubuntu.sources').read_text(), SOURCE)
            self.assertEqual((target/'sources.list.d/ubuntu.sources').read_text(), SOURCE.replace(guard.MIRROR_URI, ARCHIVE))
            self.assertTrue(any('Dir::State::lists=' in a for a in args))
            self.assertFalse(any('trusted' in a.lower() or 'unauthenticated' in a.lower() for a in args))

    def test_unknown_authentication_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);etc=root/'etc';parts=etc/'sources.list.d';parts.mkdir(parents=True)
            for source in (SOURCE.replace('Signed-By:', '# Signed-By:'), SOURCE+'Trusted: yes\n',
                           SOURCE.replace('URIs: '+guard.MIRROR_URI, 'URIs: '+guard.MIRROR_URI+' https://evil.invalid/')):
                (parts/'ubuntu.sources').write_text(source)
                with self.subTest(source=source), self.assertRaises(ValueError):
                    guard.source_copy(etc, root/'target', ARCHIVE)

    def test_authentication_update_failure_stops_install(self):
        def runner(args, **kwargs):
            if args[:3] == ['apt-cache', 'policy', 'ffmpeg']:
                return SimpleNamespace(stdout='  Candidate: 7:6.1.1-3ubuntu5\n')
            if args[:2] == ['apt-cache', 'show']:
                return SimpleNamespace(stdout=METADATA)
            if '--simulate' in args:
                return SimpleNamespace(stdout='complete install plan')
            if args[-1] == 'update':
                raise subprocess.CalledProcessError(100, args, stderr='bad signature')
            self.fail('installation must not run after update authentication failure')
        with patch.object(guard.Path, 'read_text', return_value=ARCHIVE), \
                patch.object(guard, 'select', return_value=ARCHIVE), \
                patch.object(guard, 'source_copy', return_value=[]), \
                patch.object(guard.subprocess, 'run', side_effect=runner), \
                self.assertRaises(subprocess.CalledProcessError):
            guard.main()

    def test_install_failure_is_not_masked(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            if args[:3] == ['apt-cache', 'policy', 'ffmpeg']:
                return SimpleNamespace(stdout='  Candidate: 7:6.1.1-3ubuntu5\n')
            if args[:2] == ['apt-cache', 'show']:
                return SimpleNamespace(stdout=METADATA)
            if '--simulate' in args:
                return SimpleNamespace(stdout='complete install plan')
            self.assertTrue(kwargs['check'])
            self.assertNotIn('timeout', kwargs)  # outer group deadline owns APT
            if args[-1] == 'update':
                return SimpleNamespace(returncode=0)
            if args[-3:] == ['install', '-y', 'ffmpeg']:
                raise subprocess.CalledProcessError(100, args, stderr='hash mismatch')
            self.fail('unexpected command')
        with patch.object(guard.Path, 'read_text', return_value=ARCHIVE), \
                patch.object(guard, 'select', return_value=ARCHIVE), \
                patch.object(guard, 'source_copy', return_value=[]), \
                patch.object(guard.subprocess, 'run', side_effect=runner), \
                self.assertRaises(subprocess.CalledProcessError):
            guard.main()
        self.assertEqual(sum(args[-3:] == ['install', '-y', 'ffmpeg'] and '--simulate' not in args for args in calls), 1)

    def test_short_or_wrong_range_probe_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for length, total in ((123, 1900000), (guard.PROBE_BYTES, 1900001)):
                def fake(*args, **kwargs):
                    (root/'probe.deb.part').write_bytes(b'x' * length)
                    (root/'probe.headers').write_text(f'Content-Range: bytes 0-{guard.PROBE_BYTES-1}/{total}\n')
                    return SimpleNamespace(returncode=0, stdout='206 1.0')
                with self.subTest(length=length, total=total), self.assertRaises(ValueError):
                    guard.probe(ARCHIVE, 'file', 1900000, root, fake)

    def test_real_runner_security_pocket_preserved(self):
        source = SOURCE.replace('http://security.ubuntu.com/ubuntu/', guard.MIRROR_URI)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);etc=root/'etc';parts=etc/'sources.list.d';parts.mkdir(parents=True)
            (parts/'ubuntu.sources').write_text(source)
            target=root/'task';target.mkdir()
            guard.source_copy(etc, target, ARCHIVE)
            result=(target/'sources.list.d/ubuntu.sources').read_text()
            self.assertEqual(result, source.replace(guard.MIRROR_URI, ARCHIVE))
            self.assertEqual(result.count('URIs: '+ARCHIVE), 2)
            self.assertIn('Suites: noble-security', result)
            self.assertEqual(result.count('Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg'), 2)
