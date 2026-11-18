import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ffmpeg61', Path(__file__).resolve().parents[2] / 'tools/verify/check_ffmpeg61.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class FFmpeg61(unittest.TestCase):
    def test_expected_versions(self):
        for tool in ('ffmpeg', 'ffprobe'):
            for version in ('6.1.1-3ubuntu5', '6.1', '6.1-ubuntu'):
                guard.verify(tool, f'{tool} version {version} Copyright\nconfiguration: x')

    def test_wrong_series_or_tool(self):
        for text in ('', 'ffprobe version 6.1.1', 'ffmpeg version 6.10',
                     'ffmpeg version 7.1.5', 'ffmpeg version n6.1', 'ffmpeg version 5.1'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                guard.verify('ffmpeg', text)
