"""Separate v2 authoritative tables from tables understood by legacy clients.

Only fixed SQL identifiers are rewritten; values always use SQL parameters.
This adapter is private to a v2 directory store, not a generic SQL interface.
"""
import re

TABLES = ('projects', 'events', 'idempotency', 'edit_leases', 'edit_lease_tokens',
          'history', 'export_jobs', 'assets', 'asr_jobs', 'asr_journal_meta')
_PATTERN = re.compile(r'\b(' + '|'.join(TABLES) + r')\b')


def sql_v2(sql):
    return _PATTERN.sub(lambda match: 'cv2_' + match[0], sql)


class V2Connection:
    def __init__(self, connection):
        self.raw = connection

    def execute(self, sql, parameters=()):
        return self.raw.execute(sql_v2(sql), parameters)

    def executemany(self, sql, parameters):
        return self.raw.executemany(sql_v2(sql), parameters)

    def backup(self, target, **kwargs):
        return self.raw.backup(getattr(target, 'raw', target), **kwargs)

    def __getattr__(self, name):
        return getattr(self.raw, name)
