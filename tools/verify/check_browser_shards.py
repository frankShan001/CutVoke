"""Verify Playwright's real two-shard selection partitions the full suite exactly."""
from collections import Counter
import json
from pathlib import Path
import subprocess


def identities(report):
    result = []
    def walk(suite):
        for spec in suite.get('specs', []):
            for test in spec['tests']:
                result.append((spec['id'], test['projectId']))
        for child in suite.get('suites', []):
            walk(child)
    walk(report)
    return Counter(result)


def verify(full, shards):
    assert full, 'full suite must not be empty'
    assert all(shards), 'each shard must contain tests'
    assert all(count == 1 for count in full.values()), 'duplicate full-suite identities'
    combined = sum(shards, Counter())
    assert combined == full, 'shards must cover every full-suite identity exactly once'


def main():
    editor = Path(__file__).resolve().parents[2] / 'apps' / 'editor'
    def collect(shard=None):
        command = ['npx', '--no-install', 'playwright', 'test', '--list', '--reporter=json']
        if shard:
            command.append(f'--shard={shard}/2')
        run = subprocess.run(command, cwd=editor, check=True, capture_output=True, text=True, timeout=90)
        return identities(json.loads(run.stdout))
    full, shards = collect(), [collect(1), collect(2)]
    verify(full, shards)
    print(json.dumps({'full': sum(full.values()), 'shards': [sum(s.values()) for s in shards],
                      'exact_partition': True}))


if __name__ == '__main__':
    main()
