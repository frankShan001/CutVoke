"""Run actual old source against v2 artifacts; requires a separate old src tree.

PYTHONPATH=src python tools/verify/verify_legacy_v2_boundary.py /path/old/src
This does not download code, weaken assertions or modify the old checkout.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from cutvoke.domain.model import Project, Sequence
from cutvoke.domain.rational import Rational as R
from cutvoke.storage.project_directory import DirectoryProjectStore
from cutvoke.storage.projectpack import pack_project

old_source = Path(sys.argv[1]).resolve()
if not (old_source/'cutvoke'/'storage'/'store.py').is_file():
    raise SystemExit('supply an actual old source checkout src directory')
with tempfile.TemporaryDirectory(prefix='cutvoke-legacy-boundary-') as temporary:
    root=Path(temporary)/'project'
    with DirectoryProjectStore(root,create_id='p') as current:
        current.save(Project('2','p','0',Sequence('s',160,90,R.of(24))))
        expected=current.load('p').to_dict()
        package=Path(temporary)/'new.cvkpkg'
        pack_project(current.load('p'),str(package))
    code='''
from pathlib import Path
import sys
from cutvoke.storage.project_directory import DirectoryProjectStore
from cutvoke.storage.store import ProjectStore
from cutvoke.storage.projectpack import unpack_project, ProjectPackError
from cutvoke.domain.model import Project,Sequence
from cutvoke.domain.rational import Rational as R
root=Path(sys.argv[1]);package=sys.argv[2]
try:
    DirectoryProjectStore(root)
except ValueError:
    pass
else:
    raise AssertionError('old directory reader accepted v2')
with ProjectStore(str(root/'project-v2.sqlite')) as old:
    assert old.load('p') is None
    old.save(Project('1','p','old-write',Sequence('old',160,90,R.of(24))))
    assert old.load('p').revision=='old-write'
destination=root.parent/'unpacked'
try:
    unpack_project(package,str(destination))
except (ProjectPackError,KeyError):
    pass
else:
    raise AssertionError('old package reader accepted v2')
assert not destination.exists() or not list(destination.iterdir())
'''
    environment=dict(os.environ,PYTHONPATH=str(old_source),PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([sys.executable,'-c',code,str(root),str(package)],env=environment,cwd=temporary,check=True)
    with DirectoryProjectStore(root) as current:
        assert current.load('p').to_dict()==expected, 'old store changed v2 authority'
    print('PASS: actual old directory/package readers refuse v2; raw old-store writes cannot alter v2 authority')
