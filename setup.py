"""Bundle the already-built browser editor into distributable Python wheels."""

from pathlib import Path
from shutil import copytree, rmtree

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithEditor(build_py):
    def run(self) -> None:
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
