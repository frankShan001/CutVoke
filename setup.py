"""Bundle the already-built browser editor into distributable Python wheels."""

from pathlib import Path
from shutil import copytree, rmtree

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithEditor(build_py):
    def run(self) -> None:
        # A repeated local build otherwise keeps obsolete package files in
        # build/lib, including __pycache__ from older namespace discovery.
        # Restrict cleanup to this repository's generated build directory.
        if not getattr(self, "editable_mode", False):
            build_root = Path(self.build_lib).resolve()
            expected_root = Path(__file__).resolve().parent / "build"
            if not build_root.is_relative_to(expected_root.resolve()):
                raise RuntimeError(f"refusing to reset package staging outside {expected_root}")
            staged_package = build_root / "cutvoke"
            if staged_package.exists():
                rmtree(staged_package)
        super().run()
        # Editable installs resolve web/dist from the source checkout at runtime.
        if getattr(self, "editable_mode", False):
            return
        source = Path(__file__).resolve().parent / "web" / "dist"
        if not (source / "index.html").is_file():
            raise RuntimeError(
                "Web editor is missing: run `npm ci` and `npm run build` "
                "in web/app before building a CutVoke wheel"
            )
        target = Path(self.build_lib) / "cutvoke" / "web"
        if target.exists():
            rmtree(target)
        copytree(source, target)


setup(cmdclass={"build_py": BuildWithEditor})
