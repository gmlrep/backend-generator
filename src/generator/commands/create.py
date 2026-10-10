"""Create-project command: validate, scaffold, post-process."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import jinja2

from generator import postprocess
from generator.commands.base import Command
from generator.compose_services import resolve_compose_services
from generator.constants import CUSTOM_PROFILE, PROJECT_NAME_RE, VCS_DEFAULT_BRANCH
from generator.contracts import ContractValidator, ContractViolationError
from generator.engine import Engine, GenerationError
from generator.manifest import ManifestError
from generator.paths import load_core_version, resolve_paths
from generator.profiles import available_profiles, expand_requirements, resolve_profile
from generator.status import status


def validate_project_name(name: str) -> str:
    if not PROJECT_NAME_RE.match(name):
        raise click.BadParameter(
            "Project name must start with a letter and contain only letters, digits and underscores (no hyphens)."
        )
    return name


def validate_target_dir(target: Path, allow_overwrite: bool) -> None:
    target = target.resolve()
    # Path traversal check
    cwd = Path.cwd().resolve()
    try:
        target.relative_to(cwd)
    except ValueError:
        raise click.BadParameter(f"Target must be inside the current working directory: {cwd}") from None

    if not target.exists():
        return
    if target.is_file():
        if not allow_overwrite:
            raise click.BadParameter(f"Path {target} exists and is a file. Use --allow-overwrite to replace it.")
        return
    if target.is_dir() and any(target.iterdir()) and not allow_overwrite:
        raise click.BadParameter(f"Directory {target} already exists and is not empty. Use --allow-overwrite to force.")


class CreateCommand(Command):
    """Scaffold a new FastAPI microservice project."""

    def execute(self, **kwargs: Any) -> None:
        self.run_generation(**kwargs)

    def run_generation(
        self,
        *,
        name: str,
        profile: str,
        modules: str = "",
        target: str | None = None,
        allow_overwrite: bool = False,
        create_venv: bool = False,
        init_git: bool = True,
        vcs: str = "github",
        main_branch: str | None = None,
        core_template: str | None = None,
        modules_dir: str | None = None,
        selected_modules: list[str] | None = None,
        prompt_compose_services: bool = False,
        compose_services: str = "",
    ) -> None:
        """Validate inputs, scaffold a project, then run the mandatory post-steps.

        ``.env.example`` / ``.env`` generation and the ruff check are always
        performed. CI files are written for ``github`` / ``gitlab``; ``none`` skips
        CI. The virtual environment and git initialization are configurable.
        """
        validate_project_name(name)

        # Check for git before generating: if it is missing, skip initialization
        # entirely instead of failing at the end.
        if init_git and not postprocess.git_available():
            status("[git] git not found in PATH — skipping repository initialization.", ok=False, err=True)
            init_git = False

        try:
            core_template_path, modules_path, data_root = resolve_paths(core_template, modules_dir)
        except GenerationError as exc:
            click.echo(f"Generation failed: {exc}", err=True)
            sys.exit(2)
        if not core_template_path.exists():
            click.echo(f"Core template not found: {core_template_path}", err=True)
            sys.exit(1)
        if not modules_path.exists():
            click.echo(f"Modules directory not found: {modules_path}", err=True)
            sys.exit(1)
        core_version = load_core_version(data_root)

        if selected_modules is not None:
            selected = set(selected_modules)
            profile_label = CUSTOM_PROFILE
        else:
            profile_modules = resolve_profile(profile, modules_path)
            if profile_modules is None:
                available = ", ".join(available_profiles(modules_path)) or "auth-service"
                click.echo(f"Unknown profile: {profile}. Available: {available}", err=True)
                sys.exit(1)
            selected = set(profile_modules)
            profile_label = profile

        if modules:
            for m in modules.split(","):
                m = m.strip()
                if m:
                    selected.add(m)

        # Pull in transitive module dependencies so a hand-picked selection is valid.
        selected, added = expand_requirements(selected, modules_path)
        if added:
            status(f"[modules] Required dependencies added: {', '.join(added)}")

        # Optional Docker Compose sidecars for broker/worker stacks.
        optional_compose, selected = resolve_compose_services(
            selected,
            modules_path,
            compose_services=compose_services,
            prompt=prompt_compose_services,
        )

        target_dir = Path(target) if target else Path.cwd() / name
        validate_target_dir(target_dir, allow_overwrite)

        click.echo()
        click.secho(f"Scaffolding '{name}'", bold=True)
        click.echo(f"  Profile  : {profile_label}")
        click.echo(f"  Modules  : {', '.join(sorted(selected)) or '(none)'}")
        if optional_compose:
            click.echo(f"  Compose  : {', '.join(sorted(optional_compose))}")
        click.echo(f"  Target   : {target_dir}")
        click.echo()

        engine = Engine(
            core_template_dir=core_template_path,
            modules_dir=modules_path,
            contracts=ContractValidator(),
        )

        try:
            engine.generate(
                target_dir=target_dir,
                project_name=name,
                module_ids=sorted(selected),
                core_version=core_version,
                allow_overwrite=allow_overwrite,
                optional_compose_modules=optional_compose,
            )
        except (GenerationError, ManifestError, ContractViolationError, jinja2.TemplateError) as exc:
            click.echo(f"Generation failed: {exc}", err=True)
            sys.exit(2)

        # Mandatory post-steps: environment example and ruff check.
        postprocess.write_env_files(target_dir, sorted(selected), modules_path, name)
        if not postprocess.run_ruff(target_dir):
            sys.exit(2)

        # Optional: create and populate a virtual environment.
        if create_venv and not postprocess.setup_venv(target_dir):
            sys.exit(2)

        branch = main_branch or VCS_DEFAULT_BRANCH.get(vcs, "main")

        # Add CI config for the selected repository host (skipped when vcs=none).
        if vcs != "none":
            postprocess.write_ci(target_dir, core_template_path.parent / "ci", vcs, branch, name)
        else:
            status("[ci] Skipped (no repository host selected).")

        # Initialize a git repository (on by default).
        if init_git:
            postprocess.init_git(target_dir, branch)

        click.echo()
        click.secho(f"Project '{name}' created.", fg="green", bold=True)
        click.echo(f"  Location : {target_dir}")
        click.echo(f"  Modules  : {', '.join(sorted(selected)) or '(none)'}")
        click.echo("  Next steps:")
        click.echo(f"    cd {target_dir}")
        click.echo("    make dev")
