"""Shared path containment and module-id validation."""

from __future__ import annotations

import re
from pathlib import Path

from generator.errors import GenerationError

MODULE_ID_RE = re.compile(r"^[a-z0-9-]+$")


def validate_module_id(module_id: str) -> str:
    """Return ``module_id`` or raise if it is not a safe module directory name."""
    if not MODULE_ID_RE.match(module_id):
        raise GenerationError(f"Invalid module id '{module_id}': use lowercase letters, digits and hyphens only")
    return module_id


def ensure_inside(root: Path, candidate: Path, *, label: str) -> Path:
    """Resolve ``candidate`` and require it to stay under ``root``."""
    root_resolved = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise GenerationError(f"{label} escapes allowed root {root_resolved}: {candidate}") from exc
    return resolved
