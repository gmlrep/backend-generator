"""Locate packaged generator-core data and resolve template paths."""

from __future__ import annotations

import os
from pathlib import Path

from generator.errors import GenerationError


def generator_data_root() -> Path:
    """Locate the directory that contains ``generator-core/``.

    Supports both the editable ``src/`` layout (three parents up from this file)
    and an installed wheel where ``generator-core`` sits next to the ``generator``
    package in ``site-packages`` (two parents up).
    """
    here = Path(__file__).resolve().parent  # .../generator
    candidates = (
        here.parent,  # site-packages (installed wheel)
        here.parent.parent,  # repo root (editable: .../src/generator → repo)
    )
    for candidate in candidates:
        if (candidate / "generator-core" / "core_template").is_dir():
            return candidate
    raise GenerationError(
        "Cannot locate generator-core data. Set BSG_CORE_TEMPLATE / BSG_MODULES_DIR "
        "or reinstall the package so generator-core is packaged next to generator."
    )


def load_core_version(data_root: Path) -> str:
    version_file = data_root / "generator-core" / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    return "1.0.0"


def resolve_paths(core_template: str | None, modules_dir: str | None) -> tuple[Path, Path, Path]:
    """Return ``(core_template, modules_dir, data_root)``."""
    root = generator_data_root()
    core = core_template or os.environ.get("BSG_CORE_TEMPLATE")
    modules = modules_dir or os.environ.get("BSG_MODULES_DIR")
    core_path = Path(core) if core else root / "generator-core" / "core_template"
    modules_path = Path(modules) if modules else root / "generator-core" / "modules"
    return core_path, modules_path, root
