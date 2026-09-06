"""Load the integration modules without pulling in Home Assistant."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "esl_zhsunyco"


def _stub_bleak() -> None:
    """Provide the minimal bleak surface protocol.py imports."""
    if "bleak" in sys.modules:
        return
    bleak = types.ModuleType("bleak")

    class BleakClient:  # noqa: D101
        pass

    bleak.BleakClient = BleakClient
    sys.modules["bleak"] = bleak


def _load(name: str, path: Path, package: types.ModuleType):
    spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_modules() -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    """Return (const, protocol, imaging) loaded as a standalone package."""
    _stub_bleak()
    package = types.ModuleType("esl_pkg")
    package.__path__ = [str(COMPONENT)]
    sys.modules["esl_pkg"] = package

    const = _load("const", COMPONENT / "const.py", package)
    protocol = _load("protocol", COMPONENT / "protocol.py", package)
    imaging = _load("imaging", COMPONENT / "imaging.py", package)
    return const, protocol, imaging


def load_rle():
    """Load the RLE module on its own; it has no dependencies."""
    package = sys.modules.get("esl_pkg")
    if package is None:
        package = types.ModuleType("esl_pkg")
        package.__path__ = [str(COMPONENT)]
        sys.modules["esl_pkg"] = package
    return _load("rle", COMPONENT / "rle.py", package)


def load_imaging():
    """Load patterns + imaging together; imaging imports patterns lazily."""
    _stub_bleak()
    package = sys.modules.get("esl_pkg")
    if package is None:
        package = types.ModuleType("esl_pkg")
        package.__path__ = [str(COMPONENT)]
        sys.modules["esl_pkg"] = package
    _load("const", COMPONENT / "const.py", package)
    patterns = _load("patterns", COMPONENT / "patterns.py", package)
    imaging = _load("imaging", COMPONENT / "imaging.py", package)
    return patterns, imaging
