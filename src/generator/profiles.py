"""Profile and module-graph helpers for the CLI."""

from __future__ import annotations

from pathlib import Path

import yaml

from generator.constants import DEFAULT_PROFILE_ORDER
from generator.engine import GenerationError
from generator.manifest import parse_manifest


def available_profiles(modules_path: Path) -> list[str]:
    profiles_yaml = modules_path.parent / "profiles.yaml"
    if profiles_yaml.exists():
        data = yaml.safe_load(profiles_yaml.read_text(encoding="utf-8")) or {}
        names = list(data.get("profiles", {}).keys())
        if names:
            return names
    return list(DEFAULT_PROFILE_ORDER)


def resolve_profile(profile: str, modules_path: Path) -> list[str] | None:
    profiles_yaml = modules_path.parent / "profiles.yaml"
    if profiles_yaml.exists():
        data = yaml.safe_load(profiles_yaml.read_text(encoding="utf-8")) or {}
        p = data.get("profiles", {}).get(profile)
        if p is not None:
            return list(p)
    if profile == "auth-service":
        return ["auth"]
    return None


def list_all_modules(modules_path: Path) -> list[str]:
    """Every module that ships a manifest, sorted by id."""
    return [d.name for d in sorted(modules_path.iterdir()) if d.is_dir() and (d / "manifest.yaml").exists()]


def expand_requirements(selected: set[str], modules_path: Path) -> tuple[set[str], list[str]]:
    """Return the selection plus every transitively required module."""
    resolved: set[str] = set()
    pending = list(selected)
    while pending:
        module_id = pending.pop()
        if module_id in resolved:
            continue
        resolved.add(module_id)
        manifest_path = modules_path / module_id / "manifest.yaml"
        if not manifest_path.exists():
            raise GenerationError(f"Unknown module '{module_id}': no manifest.yaml found")
        for requirement in parse_manifest(manifest_path, expected_id=module_id).requires_modules:
            if requirement not in resolved:
                pending.append(requirement)
    return resolved, sorted(resolved - selected)
