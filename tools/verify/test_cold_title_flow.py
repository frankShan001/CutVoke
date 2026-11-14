"""Small independent guards; no browser or real user data required."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from verify_cold_title_flow import verify_cache, run_bounded, SCENARIO

class ColdTitleGuards(unittest.TestCase):
    def test_cache_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); assets=root/'assets'; cache=root/'cache'; assets.mkdir(); cache.mkdir()
            data=b'synthetic'; sha=hashlib.sha256(data).hexdigest()
            (assets/'sans-700-v1.ttf').write_bytes(data)
            (assets/'manifest.json').write_text(json.dumps({'source_sha256':'source','instances':{'700':{'sha256':sha}}}))
            with self.assertRaises(RuntimeError): verify_cache(cache,assets)
            output=cache/f'source-700-v2-{sha}.ttf';output.write_bytes(data)
            self.assertEqual(verify_cache(cache,assets)['sha256'],sha)
            output.write_bytes(b'corrupt')
            with self.assertRaises(RuntimeError): verify_cache(cache,assets)
            output.write_bytes(data);(cache/'fallback-v1.ttf').write_bytes(data)
            with self.assertRaises(RuntimeError): verify_cache(cache,assets)
    def test_bounded_process(self):
        self.assertEqual(run_bounded([sys.executable,'-c','pass']),0)
        self.assertEqual(run_bounded([sys.executable,'-c','import time; time.sleep(60)'],timeout=.1,grace=.1),124)
    def test_grep_keeps_suffix_not_prefix(self):
        self.assertFalse(SCENARIO.startswith('^'))
        self.assertTrue(SCENARIO.endswith('$'))

if __name__=='__main__': unittest.main()
