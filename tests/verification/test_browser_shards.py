"""The CI coverage guard must reject omissions, overlaps and empty groups."""
from collections import Counter
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('browser_shards', Path(__file__).resolve().parents[2] / 'tools/verify/check_browser_shards.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class BrowserShardCoverage(unittest.TestCase):
    def test_partition(self):
        guard.verify(Counter(a=1, b=1), [Counter(a=1), Counter(b=1)])

    def test_reject_invalid_partitions(self):
        for shards in ([Counter(a=1), Counter(a=1)], [Counter(a=1), Counter()],
                       [Counter(a=1), Counter(b=1, c=1)]):
            with self.subTest(shards=shards), self.assertRaises(AssertionError):
                guard.verify(Counter(a=1, b=1), shards)

    def test_nested_projects(self):
        report = {'suites': [{'suites': [{'specs': [{'id': 'id', 'tests': [
            {'projectId': 'a'}, {'projectId': 'b'}]}]}]}]}
        self.assertEqual(guard.identities(report), Counter({('id', 'a'): 1, ('id', 'b'): 1}))
