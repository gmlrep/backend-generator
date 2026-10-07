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

from generator.contracts import ContractValidator
from generator.manifest import ModuleManifest, parse_manifest


def _insert_after_marker(match: re.Match[str], combined: str) -> str:
    return f"{match.group(1)}\n{combined}"


class GenerationError(Exception):
    """Raised when generation fails before writing anything to the target."""


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

    def _copy_core(self, temp_path: Path, context: dict[str, Any]) -> None:
        for src in self.core_template_dir.rglob("*"):
            rel = src.relative_to(self.core_template_dir)
            rendered_rel = self._render_path(str(rel), context)
            dst = temp_path / rendered_rel

            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix == ".jinja":
                content = src.read_text(encoding="utf-8")
                rendered = self._jinja_env.from_string(content).render(context)
                final_dst = dst.with_suffix("")
                final_dst.write_text(rendered, encoding="utf-8")
            else:
                shutil.copy2(src, dst)

    def _apply_patches(self, temp_path: Path, manifests: list[ModuleManifest], context: dict[str, Any]) -> None:
        # Group fragments by (target, marker) so that every module's contribution is
        # inserted once, in a deterministic module order.
        grouped: dict[tuple[str, str], list[str]] = {}
        for manifest in manifests:
            module_path = self.modules_dir / manifest.id
            for patch in manifest.patches:
                target_rel = self._render_path(patch.target, context)
                target = temp_path / target_rel
                if not target.exists():
                    raise GenerationError(f"Module '{manifest.id}': patch target {target} does not exist")

                fragment_path = module_path / patch.fragment
                if not fragment_path.exists():
                    raise GenerationError(f"Module '{manifest.id}': fragment {fragment_path} does not exist")

                fragment = fragment_path.read_text(encoding="utf-8")
                rendered_fragment = self._jinja_env.from_string(fragment).render(context)
                grouped.setdefault((target_rel, patch.marker), []).append(rendered_fragment)

        for (target_rel, marker), fragments in grouped.items():
            target = temp_path / target_rel
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
            target.write_text(new_content, encoding="utf-8")

    def _copy_module_files(self, temp_path: Path, manifests: list[ModuleManifest], context: dict[str, Any]) -> None:
        file_conflicts: dict[str, str] = {}
        for manifest in manifests:
            files_dir = self.modules_dir / manifest.id / "files"
            if not files_dir.exists():
                continue
            for src in files_dir.rglob("*"):
                if src.is_dir():
                    continue
                rel = src.relative_to(files_dir)
                rendered_rel = self._render_path(str(rel), context)
                dst = temp_path / rendered_rel

                key = str(dst.relative_to(temp_path))
                if key in file_conflicts:
                    raise GenerationError(
                        f"File conflict: modules '{file_conflicts[key]}' and '{manifest.id}' both write to {key}"
                    )
                file_conflicts[key] = manifest.id

                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.suffix == ".jinja":
                    content = src.read_text(encoding="utf-8")
                    rendered = self._jinja_env.from_string(content).render(context)
                    final_dst = dst.with_suffix("")
                    final_dst.write_text(rendered, encoding="utf-8")
                else:
                    shutil.copy2(src, dst)

    def _merge_pyproject_dependencies(self, temp_path: Path, manifests: list[ModuleManifest]) -> None:
        pyproject = temp_path / "pyproject.toml"
        if not pyproject.exists():
            return

        doc = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
        project = doc.setdefault("project", tomlkit.table())
        deps = project.setdefault("dependencies", tomlkit.array())
        if not isinstance(deps, tomlkit.items.Array):
            # Convert if it was inline or missing
            project["dependencies"] = tomlkit.array()
            deps = project["dependencies"]

        existing = {d.strip() for d in deps if isinstance(d, str)}

        for manifest in manifests:
            for dep in manifest.python_dependencies:
                if dep not in existing:
                    deps.append(dep)
                    existing.add(dep)

        project["dependencies"] = deps
        pyproject.write_text(tomlkit.dumps(doc), encoding="utf-8")

    def _validate_graph(
        self,
        manifests: list[ModuleManifest],
        core_version: str,
    ) -> None:
        by_id = {m.id: m for m in manifests}

        # core compatibility
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        for manifest in manifests:
            if manifest.requires_core != "*":
                try:
                    spec = SpecifierSet(manifest.requires_core)
                    if Version(core_version) not in spec:
                        raise GenerationError(
                            f"Module '{manifest.id}' requires core {manifest.requires_core}, but core is {core_version}"
                        )
                except Exception as exc:
                    raise GenerationError(
                        f"Module '{manifest.id}': invalid core requirement '{manifest.requires_core}': {exc}"
                    ) from exc

        # module dependencies and conflicts
        for manifest in manifests:
            for req in manifest.requires_modules:
                if req not in by_id:
                    raise GenerationError(f"Module '{manifest.id}' requires '{req}' which is not selected")
            for conflict in manifest.conflicts:
                if conflict in by_id:
                    raise GenerationError(f"Module '{manifest.id}' conflicts with '{conflict}'")

        # cycle detection
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

    def generate(
        self,
        target_dir: Path,
        project_name: str,
        module_ids: list[str],
        core_version: str = "1.0.0",
        allow_overwrite: bool = False,
    ) -> None:
        target_dir = target_dir.resolve()
        if target_dir.exists() and any(target_dir.iterdir()) and not allow_overwrite:
            raise GenerationError(f"Target directory {target_dir} already exists and is not empty.")

        # Load manifests
        manifests: list[ModuleManifest] = []
        for m_id in module_ids:
            manifest_path = self.modules_dir / m_id / "manifest.yaml"
            if not manifest_path.exists():
                raise GenerationError(f"Module '{m_id}' does not have manifest.yaml")
            manifests.append(parse_manifest(manifest_path))

        self._validate_graph(manifests, core_version)

        context = {"project_name": project_name}

        # Build inside the target's parent so the final move is an atomic rename
        # on the same filesystem.
        parent = target_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}_gen_", dir=parent))

        try:
            self._copy_core(temp_path, context)
            self.validator.validate(temp_path, project_name)
            self._copy_module_files(temp_path, manifests, context)
            self._apply_patches(temp_path, manifests, context)
            self._merge_pyproject_dependencies(temp_path, manifests)

            if target_dir.exists():
                if target_dir.is_dir():
                    shutil.rmtree(target_dir)
                else:
                    target_dir.unlink()
            os.replace(temp_path, target_dir)
        except Exception:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise
