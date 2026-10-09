"""Build engine: validation, temp scaffolding, patching, atomic move."""

from __future__ import annotations

import functools
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import jinja2
import tomlkit
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from generator.contracts import ContractValidator
from generator.manifest import ModuleManifest, parse_manifest

_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_MODULE_ID_RE = re.compile(r"^[a-z0-9-]+$")


def _insert_after_marker(match: re.Match[str], combined: str) -> str:
    return f"{match.group(1)}\n{combined}"


class GenerationError(Exception):
    """Raised when generation fails before writing anything to the target."""


def _ensure_inside(root: Path, candidate: Path, *, label: str) -> Path:
    """Resolve ``candidate`` and require it to stay under ``root``."""
    root_resolved = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise GenerationError(f"{label} escapes allowed root {root_resolved}: {candidate}") from exc
    return resolved


class Engine:
    def __init__(
        self,
        core_template_dir: Path,
        modules_dir: Path,
        contracts: ContractValidator | None = None,
    ) -> None:
        self.core_template_dir = core_template_dir.resolve()
        self.modules_dir = modules_dir.resolve()
        self.validator = contracts or ContractValidator()
        self._jinja_env = jinja2.Environment(undefined=jinja2.StrictUndefined)

    def _render_path(self, relative: str, context: dict[str, Any]) -> str:
        return self._jinja_env.from_string(relative).render(context)

    def _copy_core(self, temp_path: Path, context: dict[str, Any]) -> set[str]:
        """Copy the core template. Returns the set of final relative destination paths."""
        occupied: set[str] = set()
        for src in self.core_template_dir.rglob("*"):
            rel = src.relative_to(self.core_template_dir)
            rendered_rel = self._render_path(str(rel), context)
            dst = _ensure_inside(temp_path, temp_path / rendered_rel, label="Core destination")

            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix == ".jinja":
                content = src.read_text(encoding="utf-8")
                rendered = self._jinja_env.from_string(content).render(context)
                final_dst = dst.with_suffix("")
                final_dst = _ensure_inside(temp_path, final_dst, label="Core destination")
                # Force LF so shebang scripts work in Linux containers on Windows hosts.
                final_dst.write_text(rendered, encoding="utf-8", newline="\n")
                occupied.add(str(final_dst.relative_to(temp_path)))
            else:
                shutil.copy2(src, dst)
                occupied.add(str(dst.relative_to(temp_path)))
        return occupied

    def _apply_patches(
        self,
        temp_path: Path,
        manifests: list[ModuleManifest],
        context: dict[str, Any],
        optional_compose_modules: set[str] | None = None,
    ) -> None:
        # Group fragments by (target, marker) so that every module's contribution is
        # inserted once, in a deterministic module order.
        enabled_compose = optional_compose_modules if optional_compose_modules is not None else None
        grouped: dict[tuple[str, str], list[str]] = {}
        for manifest in manifests:
            module_path = _ensure_inside(
                self.modules_dir,
                self.modules_dir / manifest.id,
                label=f"Module '{manifest.id}' path",
            )
            for patch in manifest.patches:
                if patch.optional_compose and enabled_compose is not None and manifest.id not in enabled_compose:
                    continue
                target_rel = self._render_path(patch.target, context)
                target = _ensure_inside(temp_path, temp_path / target_rel, label="Patch target")
                if not target.exists():
                    raise GenerationError(f"Module '{manifest.id}': patch target {target} does not exist")

                fragment_path = _ensure_inside(
                    module_path,
                    module_path / patch.fragment,
                    label=f"Module '{manifest.id}' fragment",
                )
                if not fragment_path.exists():
                    raise GenerationError(f"Module '{manifest.id}': fragment {fragment_path} does not exist")

                fragment = fragment_path.read_text(encoding="utf-8")
                rendered_fragment = self._jinja_env.from_string(fragment).render(context)
                grouped.setdefault((target_rel, patch.marker), []).append(rendered_fragment)

        for (target_rel, marker), fragments in grouped.items():
            target = _ensure_inside(temp_path, temp_path / target_rel, label="Patch target")
            content = target.read_text(encoding="utf-8")
            marker_regex = rf"^([ \t]*#[ \t]*{re.escape(marker)}[ \t]*)$"
            marker_hits = re.findall(marker_regex, content, flags=re.MULTILINE)
            if len(marker_hits) != 1:
                raise GenerationError(
                    f"Marker '{marker}' must appear exactly once in {target} (found {len(marker_hits)})"
                )

            combined = "\n\n".join(fragment.strip("\n") for fragment in fragments) + "\n"
            new_content = re.sub(
                marker_regex,
                functools.partial(_insert_after_marker, combined=combined),
                content,
                count=1,
                flags=re.MULTILINE,
            )
            target.write_text(new_content, encoding="utf-8", newline="\n")

    def _copy_module_files(
        self,
        temp_path: Path,
        manifests: list[ModuleManifest],
        context: dict[str, Any],
        occupied: set[str],
    ) -> None:
        file_owners: dict[str, str] = {path: "<core>" for path in occupied}
        for manifest in manifests:
            files_dir = self.modules_dir / manifest.id / "files"
            if not files_dir.exists():
                continue
            files_dir = _ensure_inside(
                self.modules_dir / manifest.id,
                files_dir,
                label=f"Module '{manifest.id}' files",
            )
            for src in files_dir.rglob("*"):
                if src.is_dir():
                    continue
                rel = src.relative_to(files_dir)
                rendered_rel = self._render_path(str(rel), context)
                dst = _ensure_inside(temp_path, temp_path / rendered_rel, label="Module destination")

                if src.suffix == ".jinja":
                    final_dst = _ensure_inside(temp_path, dst.with_suffix(""), label="Module destination")
                else:
                    final_dst = dst

                key = str(final_dst.relative_to(temp_path))
                if key in file_owners:
                    raise GenerationError(
                        f"File conflict: '{file_owners[key]}' and '{manifest.id}' both write to {key}"
                    )
                file_owners[key] = manifest.id

                final_dst.parent.mkdir(parents=True, exist_ok=True)
                if src.suffix == ".jinja":
                    content = src.read_text(encoding="utf-8")
                    rendered = self._jinja_env.from_string(content).render(context)
                    final_dst.write_text(rendered, encoding="utf-8", newline="\n")
                else:
                    shutil.copy2(src, final_dst)

    def _merge_pyproject_dependencies(self, temp_path: Path, manifests: list[ModuleManifest]) -> None:
        pyproject = temp_path / "pyproject.toml"
        if not pyproject.exists():
            return

        doc = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
        project = doc.setdefault("project", tomlkit.table())
        deps = project.setdefault("dependencies", tomlkit.array())
        if not isinstance(deps, tomlkit.items.Array):
            project["dependencies"] = tomlkit.array()
            deps = project["dependencies"]

        by_name: dict[str, str] = {}
        for entry in deps:
            if not isinstance(entry, str):
                continue
            try:
                req = Requirement(entry.strip())
            except InvalidRequirement:
                by_name[entry.strip().lower()] = entry.strip()
                continue
            by_name[req.name.lower()] = entry.strip()

        for manifest in manifests:
            for dep in manifest.python_dependencies:
                try:
                    req = Requirement(dep)
                except InvalidRequirement as exc:
                    raise GenerationError(
                        f"Module '{manifest.id}': invalid python dependency '{dep}': {exc}"
                    ) from exc
                key = req.name.lower()
                if key in by_name and by_name[key] != dep:
                    raise GenerationError(
                        f"Conflicting dependency for '{req.name}': "
                        f"existing '{by_name[key]}' vs module '{manifest.id}' '{dep}'"
                    )
                if key not in by_name:
                    deps.append(dep)
                    by_name[key] = dep

        project["dependencies"] = deps
        pyproject.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")

    def _validate_graph(
        self,
        manifests: list[ModuleManifest],
        core_version: str,
    ) -> None:
        by_id = {m.id: m for m in manifests}

        for manifest in manifests:
            if manifest.requires_core != "*":
                try:
                    spec = SpecifierSet(manifest.requires_core)
                except InvalidSpecifier as exc:
                    raise GenerationError(
                        f"Module '{manifest.id}': invalid core requirement '{manifest.requires_core}': {exc}"
                    ) from exc
                try:
                    version = Version(core_version)
                except InvalidVersion as exc:
                    raise GenerationError(f"Invalid core version '{core_version}': {exc}") from exc
                if version not in spec:
                    raise GenerationError(
                        f"Module '{manifest.id}' requires core {manifest.requires_core}, but core is {core_version}"
                    )

        for manifest in manifests:
            for req in manifest.requires_modules:
                if req not in by_id:
                    raise GenerationError(f"Module '{manifest.id}' requires '{req}' which is not selected")
            for conflict in manifest.conflicts:
                if conflict in by_id:
                    raise GenerationError(f"Module '{manifest.id}' conflicts with '{conflict}'")

        visited: set[str] = set()
        rec_stack: set[str] = set()

        def dfs(node_id: str) -> None:
            visited.add(node_id)
            rec_stack.add(node_id)
            for dep in by_id[node_id].requires_modules:
                if dep not in visited:
                    dfs(dep)
                elif dep in rec_stack:
                    raise GenerationError(f"Circular dependency detected involving '{node_id}' -> '{dep}'")
            rec_stack.remove(node_id)

        for m_id in by_id:
            if m_id not in visited:
                dfs(m_id)

    def _expand_module_ids(self, module_ids: list[str]) -> list[str]:
        """Include transitive ``requires.modules`` so callers may list leaves only."""
        resolved: set[str] = set()
        pending = list(module_ids)
        while pending:
            module_id = pending.pop()
            if module_id in resolved:
                continue
            if not _MODULE_ID_RE.match(module_id):
                raise GenerationError(f"Invalid module id '{module_id}': use lowercase letters, digits and hyphens only")
            module_dir = _ensure_inside(self.modules_dir, self.modules_dir / module_id, label=f"Module '{module_id}'")
            manifest_path = module_dir / "manifest.yaml"
            if not manifest_path.exists():
                raise GenerationError(f"Unknown module '{module_id}': no manifest.yaml found")
            resolved.add(module_id)
            for requirement in parse_manifest(manifest_path, expected_id=module_id).requires_modules:
                if requirement not in resolved:
                    pending.append(requirement)
        return sorted(resolved)

    def _topological_order(self, manifests: list[ModuleManifest]) -> list[ModuleManifest]:
        """Return manifests ordered so dependencies come before dependents."""
        by_id = {m.id: m for m in manifests}
        ordered: list[ModuleManifest] = []
        visited: set[str] = set()

        def visit(module_id: str) -> None:
            if module_id in visited:
                return
            visited.add(module_id)
            for dependency in by_id[module_id].requires_modules:
                if dependency in by_id:
                    visit(dependency)
            ordered.append(by_id[module_id])

        for manifest in manifests:
            visit(manifest.id)
        return ordered

    def _commit_temp_to_target(self, temp_path: Path, target_dir: Path) -> None:
        """Swap ``temp_path`` into ``target_dir`` without deleting the live tree first."""
        backup: Path | None = None
        if target_dir.exists():
            backup = target_dir.with_name(f".{target_dir.name}_bak_{os.getpid()}")
            if backup.exists():
                if backup.is_dir():
                    shutil.rmtree(backup)
                else:
                    backup.unlink()
            os.replace(target_dir, backup)
        try:
            os.replace(temp_path, target_dir)
        except Exception:
            if backup is not None and backup.exists() and not target_dir.exists():
                os.replace(backup, target_dir)
            raise
        if backup is not None and backup.exists():
            if backup.is_dir():
                shutil.rmtree(backup, ignore_errors=True)
            else:
                backup.unlink(missing_ok=True)

    def generate(
        self,
        target_dir: Path,
        project_name: str,
        module_ids: list[str],
        core_version: str = "1.0.0",
        allow_overwrite: bool = False,
        optional_compose_modules: set[str] | None = None,
    ) -> None:
        if not _PROJECT_NAME_RE.match(project_name):
            raise GenerationError(
                "Project name must start with a letter and contain only letters, digits, underscores and hyphens."
            )

        target_dir = target_dir.resolve()
        if target_dir.exists():
            if target_dir.is_file():
                if not allow_overwrite:
                    raise GenerationError(f"Target path {target_dir} exists and is a file.")
            elif target_dir.is_dir() and any(target_dir.iterdir()) and not allow_overwrite:
                raise GenerationError(f"Target directory {target_dir} already exists and is not empty.")

        expanded_ids = self._expand_module_ids(module_ids)
        manifests: list[ModuleManifest] = []
        for m_id in expanded_ids:
            if not _MODULE_ID_RE.match(m_id):
                raise GenerationError(f"Invalid module id '{m_id}': use lowercase letters, digits and hyphens only")
            module_dir = _ensure_inside(self.modules_dir, self.modules_dir / m_id, label=f"Module '{m_id}'")
            manifest_path = module_dir / "manifest.yaml"
            if not manifest_path.exists():
                raise GenerationError(f"Module '{m_id}' does not have manifest.yaml")
            manifests.append(parse_manifest(manifest_path, expected_id=m_id))

        self._validate_graph(manifests, core_version)
        manifests = self._topological_order(manifests)

        context = {"project_name": project_name}

        parent = target_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}_gen_", dir=parent))

        try:
            occupied = self._copy_core(temp_path, context)
            self.validator.validate(temp_path, project_name)
            self._copy_module_files(temp_path, manifests, context, occupied)
            self._apply_patches(temp_path, manifests, context, optional_compose_modules=optional_compose_modules)
            self.validator.validate(temp_path, project_name)
            self._merge_pyproject_dependencies(temp_path, manifests)
            self._commit_temp_to_target(temp_path, target_dir)
        except Exception:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise
