"""Retired copy-and-switch API: v1 projects require explicit format conversion.

Do not retain a callable route that rewrites old identity/history into a v2
folder without the new inspection token and copy-only conversion contract.
"""
from cutvoke.storage.project_format import ProjectFormatError


def plan_migration(library, pid):
    raise ProjectFormatError('Use explicit format inspection and conversion; the original project is retained')


def migrate_project(library, pid, *, token, directory=None):
    raise ProjectFormatError('Use explicit format conversion; in-place legacy migration is disabled')


def rollback_migration(library, pid):
    raise ProjectFormatError('Conversion creates an independent copy; open the retained original in the old version')
