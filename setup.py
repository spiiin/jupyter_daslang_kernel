"""Keep checkout scripts/tests outside the package and tag bundled native DLLs."""
from setuptools import setup
from setuptools.command.build_py import build_py
from wheel.bdist_wheel import bdist_wheel
from pathlib import Path

MODULES = {"__init__", "__main__", "cli", "paths", "kernel", "session",
           "timing_magic", "inspection_magic", "worker", "worker_client", "module_sources", "file_magics", "strudel_magic", "cell_parser", "editor_services",
           "persistent_types", "diagnostics", "native_output", "console"}


class PackageModules(build_py):
    def find_package_modules(self, package, package_dir):
        return [item for item in super().find_package_modules(package, package_dir)
                if item[1] in MODULES]


class WindowsWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def run(self):
        for filename in ("notebook_parser.dll", "notebook_runtime.dll"):
            if not Path("_bin", filename).is_file():
                raise RuntimeError("Missing native DLLs. Run build_package.py --sdk PATH first.")
        super().run()

    def get_tag(self):
        return "py3", "none", "win_amd64"


setup(cmdclass={"build_py": PackageModules, "bdist_wheel": WindowsWheel})
