"""Manifest parsing and validation for generator modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class Patch:
    target: str
    marker: str
    fragment: str
    # When true, the patch is only applied if the module id is listed in
    # ``Engine.generate(..., optional_compose_modules=...)``.
    optional_compose: bool = False


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    manifest_version: str
    id: str
    version: str
    requires_core: str
    requires_modules: list[str]
    conflicts: list[str]
    python_dependencies: list[str]
    patches: list[Patch]
    env_example: list[str]


class ManifestError(Exception):
    """Raised when manifest is invalid or violates constraints."""


def _extract_str(data: dict[str, Any], key: str, manifest_id: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ManifestError(f"Module '{manifest_id}': '{key}' must be a string")
    return value


def _extract_list(data: dict[str, Any], key: str, manifest_id: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ManifestError(f"Module '{manifest_id}': '{key}' must be a list")
    return value


def parse_manifest(path: Path, *, expected_id: str | None = None) -> ModuleManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError(f"Manifest {path} root must be a mapping")

    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        raise ManifestError(f"Manifest {path}: 'id' must be a non-empty string")
    manifest_id = raw_id

    directory_name = path.parent.name
    if expected_id is not None and manifest_id != expected_id:
        raise ManifestError(
            f"Module directory '{expected_id}' declares id '{manifest_id}' (must match)"
        )
    if manifest_id != directory_name:
        raise ManifestError(
            f"Module '{manifest_id}': manifest id must match directory name '{directory_name}'"
        )

    manifest_version = _extract_str(raw, "manifest_version", manifest_id)
    if manifest_version != "1":
        raise ManifestError(f"Module '{manifest_id}': unsupported manifest_version {manifest_version}")

    version = _extract_str(raw, "version", manifest_id)

    requires = raw.get("requires", {})
    if not isinstance(requires, dict):
        raise ManifestError(f"Module '{manifest_id}': 'requires' must be a mapping")
    requires_core = requires.get("core", "*")
    if not isinstance(requires_core, str):
        raise ManifestError(f"Module '{manifest_id}': 'requires.core' must be a string")
    requires_modules = requires.get("modules", [])
    if not isinstance(requires_modules, list) or any(not isinstance(m, str) for m in requires_modules):
        raise ManifestError(f"Module '{manifest_id}': 'requires.modules' must be a list of strings")

    conflicts = _extract_list(raw, "conflicts", manifest_id)
    if any(not isinstance(c, str) for c in conflicts):
        raise ManifestError(f"Module '{manifest_id}': 'conflicts' must be a list of strings")

    python_dependencies = _extract_list(raw, "python_dependencies", manifest_id)
    if any(not isinstance(d, str) for d in python_dependencies):
        raise ManifestError(f"Module '{manifest_id}': 'python_dependencies' must be a list of strings")

    env_example = raw.get("env_example", [])
    if not isinstance(env_example, list) or any(not isinstance(e, str) for e in env_example):
        raise ManifestError(f"Module '{manifest_id}': 'env_example' must be a list of strings")

    patches_raw = raw.get("patches", [])
    if not isinstance(patches_raw, list):
        raise ManifestError(f"Module '{manifest_id}': 'patches' must be a list")

    patches: list[Patch] = []
    for p in patches_raw:
        if not isinstance(p, dict):
            raise ManifestError(f"Module '{manifest_id}': each patch must be a mapping")
        optional_compose = p.get("optional_compose", False)
        if not isinstance(optional_compose, bool):
            raise ManifestError(f"Module '{manifest_id}': 'optional_compose' must be a boolean")
        patches.append(
            Patch(
                target=_extract_str(p, "target", manifest_id),
                marker=_extract_str(p, "marker", manifest_id),
                fragment=_extract_str(p, "fragment", manifest_id),
                optional_compose=optional_compose,
            )
        )

    return ModuleManifest(
        manifest_version=manifest_version,
        id=manifest_id,
        version=version,
        requires_core=requires_core,
        requires_modules=list(requires_modules),
        conflicts=list(conflicts),
        python_dependencies=list(python_dependencies),
        patches=patches,
        env_example=list(env_example),
    )
