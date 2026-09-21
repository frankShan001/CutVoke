"""Build CutVoke and include the compiled Web editor in the wheel.

Run `npm run build` in `web/app` before building a distributable wheel.
Backend-only development still works when `web/dist` is absent.
"""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "web" / "dist"


class BuildPyWithWeb(build_py):
    def run(self):
        super().run()
        if not DIST.is_dir():
            print("[build_py] web/dist is missing; building without the Web UI")
            return
        import shutil
        pkg_root = Path(self.build_lib) / "cutvoke"
        target = pkg_root / "web"
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(DIST, target)
        print(f"[build_py] copied Web UI: {DIST} -> {target}")


setup(cmdclass={"build_py": BuildPyWithWeb})
