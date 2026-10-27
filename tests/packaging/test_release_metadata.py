"""Release metadata must describe one version and the declared dev extras."""
import ast
import json
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


def metadata():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    lock = tomllib.loads((ROOT / 'uv.lock').read_text())
    roots = [p for p in lock['package'] if p['name'] == project['name'] and p['source'] == {'editable': '.'}]
    assert len(roots) == 1
    return project, roots[0]


def test_release_versions_agree_across_runtime_desktop_and_locks():
    project, locked = metadata()
    runtime = ast.parse((ROOT / 'src/cutvoke/__init__.py').read_text())
    versions = [ast.literal_eval(n.value) for n in runtime.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
    assert len(versions) == 1
    desktop = json.loads((ROOT / 'apps/desktop/package.json').read_text())
    desktop_lock = json.loads((ROOT / 'apps/desktop/package-lock.json').read_text())
    actual = {'runtime': versions[0], 'desktop': desktop['version'], 'desktop lock': desktop_lock['version'],
              'desktop lock root': desktop_lock['packages']['']['version'], 'uv project': locked['version']}
    assert actual == dict.fromkeys(actual, project['version'])


def test_uv_optional_dependency_members_match_project_declarations():
    project, locked = metadata()
    expected = {extra: {canonicalize_name(Requirement(text).name) for text in requirements}
                for extra, requirements in project['optional-dependencies'].items()}
    actual = {extra: {canonicalize_name(item['name']) for item in requirements}
              for extra, requirements in locked['optional-dependencies'].items()}
    assert actual == expected
