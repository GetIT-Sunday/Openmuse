"""Bundle portable capabilities without moving their canonical source trees."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithCapabilities(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "smearglepaper" / "_bundled"
        if target.exists():
            shutil.rmtree(target)
        root = Path(__file__).parent
        for directory in ("skills", "packs"):
            for source in sorted((root / directory).rglob("*")):
                relative = source.relative_to(root)
                if source.is_symlink():
                    raise ValueError(f"Bundled resources cannot be symlinks: {relative}")
                if source.is_file() and source.suffix in {".md", ".yaml", ".py"} and "__pycache__" not in source.parts:
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)


setup(cmdclass={"build_py": BuildWithCapabilities})
