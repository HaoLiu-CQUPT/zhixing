from __future__ import annotations

import importlib.util
import importlib.abc
import importlib.machinery
import marshal
import os
import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
ONBOARDING_ROOT = Path(__file__).resolve().parent
EXTRACT_ROOT = WORKSPACE / "new-kernel-review" / "newqi24.exe_extracted"
PYZ_ROOT = EXTRACT_ROOT / "PYZ-00.pyz_extracted"
MAIN_PYC = EXTRACT_ROOT / "newqi24.pyc"
PATCH_PATH = ONBOARDING_ROOT / "patched_group_member_handler.py"
PATCH_MODULE = "handlers.message_handlers.group_member_handler"
TEXT_MESSAGE_PATCH_PATH = ONBOARDING_ROOT / "patched_text_message.py"
TEXT_MESSAGE_MODULE = "handlers.message_handlers.TextMessage"
_DLL_DIRECTORY_HANDLES = []


class _SplitPackageFinder(importlib.abc.MetaPathFinder):
    """Join pure-Python and native package halves from a PyInstaller extract."""

    def find_spec(self, fullname, path=None, target=None):
        relative = Path(*fullname.split("."))
        pure_directory = PYZ_ROOT / relative
        native_directory = EXTRACT_ROOT / relative
        initializer = pure_directory / "__init__.pyc"
        if not (
            pure_directory.is_dir()
            and native_directory.is_dir()
            and initializer.is_file()
        ):
            return None
        loader = importlib.machinery.SourcelessFileLoader(fullname, str(initializer))
        spec = importlib.util.spec_from_loader(fullname, loader, is_package=True)
        if spec is not None:
            spec.submodule_search_locations = [
                str(pure_directory),
                str(native_directory),
            ]
        return spec


def _require(path: Path) -> Path:
    if not path.exists():
        raise RuntimeError(f"runtime_component_missing:{path.name}")
    return path


def _prepare_runtime() -> None:
    for path in (
        EXTRACT_ROOT,
        PYZ_ROOT,
        MAIN_PYC,
        PATCH_PATH,
        TEXT_MESSAGE_PATCH_PATH,
    ):
        _require(path)

    sys.path[:0] = [
        str(ONBOARDING_ROOT),
        str(PYZ_ROOT),
        str(EXTRACT_ROOT),
        str(EXTRACT_ROOT / "base_library.zip"),
    ]
    dll_directories = [EXTRACT_ROOT]
    dll_directories.extend(
        directory
        for directory in EXTRACT_ROOT.iterdir()
        if directory.is_dir()
        and (
            directory.name.endswith(".libs")
            or any(directory.glob("*.dll"))
        )
    )
    if hasattr(os, "add_dll_directory"):
        for directory in dll_directories:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
    os.environ["PATH"] = os.pathsep.join(
        [*(str(directory) for directory in dll_directories), os.environ.get("PATH", "")]
    )
    sys.meta_path.insert(0, _SplitPackageFinder())

    # Match the small subset of PyInstaller runtime attributes used by the
    # reviewed kernel while keeping the reviewed archive itself unchanged.
    sys.frozen = True  # type: ignore[attr-defined]
    sys._MEIPASS = str(EXTRACT_ROOT)  # type: ignore[attr-defined]

    # pyinstxtractor stores a package initializer beside its package directory
    # (for example utils.pyc + utils/). Teach the normal filesystem importer
    # the package shape that PyInstaller's importer previously supplied.
    for package_name in ("utils",):
        initializer = PYZ_ROOT / f"{package_name}.pyc"
        package_directory = PYZ_ROOT / package_name
        if not initializer.exists() or not package_directory.is_dir():
            continue
        spec = importlib.util.spec_from_file_location(
            package_name,
            initializer,
            submodule_search_locations=[str(package_directory)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"package_loader_unavailable:{package_name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)


def _install_onboarding_patch() -> None:
    spec = importlib.util.spec_from_file_location(PATCH_MODULE, PATCH_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("onboarding_patch_loader_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PATCH_MODULE] = module
    spec.loader.exec_module(module)


def _install_text_message_patch() -> None:
    original = __import__(TEXT_MESSAGE_MODULE, fromlist=["*"])
    spec = importlib.util.spec_from_file_location(
        "onboarding_patched_text_message",
        TEXT_MESSAGE_PATCH_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("text_message_patch_loader_unavailable")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    patch.install(original)


def _run_reviewed_kernel() -> None:
    with MAIN_PYC.open("rb") as handle:
        magic = handle.read(4)
        if magic != importlib.util.MAGIC_NUMBER:
            raise RuntimeError("kernel_python_version_mismatch")
        handle.read(12)
        code = marshal.load(handle)

    namespace = {
        "__name__": "__main__",
        "__file__": str(MAIN_PYC),
        "__package__": None,
        "__cached__": None,
        "__builtins__": __builtins__,
    }
    exec(code, namespace, namespace)


if __name__ == "__main__":
    _prepare_runtime()
    _install_onboarding_patch()
    _install_text_message_patch()
    _run_reviewed_kernel()
