"""CLI interface for the Backend Service Generator."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import jinja2
import questionary
import yaml

from generator.contracts import ContractValidator
from generator.engine import Engine, GenerationError
from generator.manifest import parse_manifest

_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_DEFAULT_PROFILE_ORDER = ["auth-service", "items-service", "full", "minimal"]
_CUSTOM_PROFILE = "custom"
_VCS_CHOICES = ("github", "gitlab")
# The chosen repository host determines the main branch and the generated CI.
_VCS_DEFAULT_BRANCH = {"github": "main", "gitlab": "main"}


def _generator_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resolve_paths(core_template: str | None, modules_dir: str | None) -> tuple[Path, Path]:
    root = _generator_root()
    core = core_template or os.environ.get("BSG_CORE_TEMPLATE")
    modules = modules_dir or os.environ.get("BSG_MODULES_DIR")
    core_path = Path(core) if core else root / "generator-core" / "core_template"
    modules_path = Path(modules) if modules else root / "generator-core" / "modules"
    return core_path, modules_path


def _validate_project_name(name: str) -> str:
    if not _PROJECT_NAME_RE.match(name):
        raise click.BadParameter(
            "Project name must start with a letter and contain only letters, digits, underscores and hyphens."
        )
    return name


def _validate_target_dir(target: Path, allow_overwrite: bool) -> None:
    target = target.resolve()
    # Path traversal check
    cwd = Path.cwd().resolve()
    try:
        target.relative_to(cwd)
    except ValueError:
        raise click.BadParameter(f"Target must be inside the current working directory: {cwd}") from None

    if target.exists() and any(target.iterdir()) and not allow_overwrite:
        raise click.BadParameter(f"Directory {target} already exists and is not empty. Use --allow-overwrite to force.")


def _status(message: str, *, ok: bool = True, err: bool = False) -> None:
    """Print a status line; colours are dropped automatically when not a TTY."""
    click.secho(message, fg="green" if ok else "red", err=err)


def _run_generation(
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
    prompt_optional_modules: bool = False,
) -> None:
    """Validate inputs, scaffold a project, then run the mandatory post-steps.

    ``.env.example`` / ``.env`` generation, the ruff check and the CI files for
    the selected ``vcs`` are always performed; the virtual environment and git
    initialization are configurable.
    """
    _validate_project_name(name)

    # Check for git before generating: if it is missing, skip initialization
    # entirely instead of failing at the end.
    if init_git and not _git_available():
        _status("[git] git not found in PATH — skipping repository initialization.", ok=False, err=True)
        init_git = False

    core_template_path, modules_path = _resolve_paths(core_template, modules_dir)
    if not core_template_path.exists():
        click.echo(f"Core template not found: {core_template_path}", err=True)
        sys.exit(1)
    if not modules_path.exists():
        click.echo(f"Modules directory not found: {modules_path}", err=True)
        sys.exit(1)

    if selected_modules is not None:
        selected = set(selected_modules)
        profile_label = _CUSTOM_PROFILE
    else:
        profile_modules = _resolve_profile(profile, modules_path)
        if profile_modules is None:
            available = ", ".join(_available_profiles(modules_path)) or "auth-service"
            click.echo(f"Unknown profile: {profile}. Available: {available}", err=True)
            sys.exit(1)
        selected = set(profile_modules)
        profile_label = profile

        if prompt_optional_modules:
            optional = _list_optional_modules(modules_path, selected)
            if optional:
                chosen = _ask_checkbox(
                    "Include optional modules (space to toggle, enter to confirm):",
                    choices=optional,
                )
                if chosen:
                    selected.update(chosen)

    if modules:
        for m in modules.split(","):
            m = m.strip()
            if m:
                selected.add(m)

    # Pull in transitive module dependencies so a hand-picked selection is valid.
    selected, added = _expand_requirements(selected, modules_path)
    if added:
        _status(f"[modules] Required dependencies added: {', '.join(added)}")

    target_dir = Path(target) if target else Path.cwd() / name
    _validate_target_dir(target_dir, allow_overwrite)

    click.echo()
    click.secho(f"Scaffolding '{name}'", bold=True)
    click.echo(f"  Profile  : {profile_label}")
    click.echo(f"  Modules  : {', '.join(sorted(selected)) or '(none)'}")
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
            core_version="1.0.0",
            allow_overwrite=allow_overwrite,
        )
    except GenerationError as exc:
        click.echo(f"Generation failed: {exc}", err=True)
        sys.exit(2)

    # Mandatory post-steps: environment example and ruff check.
    _write_env_files(target_dir, sorted(selected), modules_path, name)
    _run_ruff(target_dir)

    # Optional: create and populate a virtual environment.
    if create_venv:
        _setup_venv(target_dir)

    branch = main_branch or _VCS_DEFAULT_BRANCH.get(vcs, "main")

    # Add CI config for the selected repository host.
    _write_ci(target_dir, core_template_path.parent / "ci", vcs, branch, name)

    # Initialize a git repository (on by default).
    if init_git:
        _init_git(target_dir, branch)

    click.echo()
    click.secho(f"Project '{name}' created.", fg="green", bold=True)
    click.echo(f"  Location : {target_dir}")
    click.echo(f"  Modules  : {', '.join(sorted(selected)) or '(none)'}")
    click.echo("  Next steps:")
    click.echo(f"    cd {target_dir}")
    click.echo("    make dev")


@click.group(invoke_without_command=True)
@click.version_option(version="0.1.0", prog_name="bsg-generator")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Backend Service Generator — scaffold modular microservices.

    Run without a command to open the interactive menu.
    """
    if ctx.invoked_subcommand is None:
        _interactive_menu()


@cli.command()
@click.option("--name", "-n", required=True, help="Project name (Python package friendly).")
@click.option("--profile", "-p", default="auth-service", help="Generation profile.")
@click.option("--modules", "-m", default="", help="Comma-separated optional module IDs.")
@click.option("--target", "-t", default=None, help="Target directory (default: ./<name>).")
@click.option("--non-interactive", is_flag=True, help="Non-interactive mode for CI.")
@click.option("--allow-overwrite", is_flag=True, help="Allow overwriting an existing directory.")
@click.option("--venv/--no-venv", default=False, help="Create a virtual environment and install dependencies.")
@click.option("--git/--no-git", default=True, show_default=True, help="Initialize a git repository.")
@click.option(
    "--vcs",
    type=click.Choice(["github", "gitlab"]),
    default="github",
    show_default=True,
    help="Repository host: determines the main branch and the generated CI.",
)
@click.option(
    "--core-template",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    envvar="BSG_CORE_TEMPLATE",
)
@click.option("--modules-dir", type=click.Path(exists=True, file_okay=False), default=None, envvar="BSG_MODULES_DIR")
def create(
    name: str,
    profile: str,
    modules: str,
    target: str | None,
    non_interactive: bool,
    allow_overwrite: bool,
    venv: bool,
    git: bool,
    vcs: str,
    core_template: str | None,
    modules_dir: str | None,
) -> None:
    """Create a new FastAPI microservice project."""
    _run_generation(
        name=name,
        profile=profile,
        modules=modules,
        target=target,
        allow_overwrite=allow_overwrite,
        create_venv=venv,
        init_git=git,
        vcs=vcs,
        core_template=core_template,
        modules_dir=modules_dir,
        prompt_optional_modules=not non_interactive,
    )


def _ask_select(message: str, choices: Any, default: Any = None) -> Any:
    """Thin wrapper around :func:`questionary.select` (patchable in tests)."""
    return questionary.select(message, choices=choices, default=default).ask()


def _ask_text(message: str, validate: Any = None) -> Any:
    return questionary.text(message, validate=validate).ask()


def _ask_confirm(message: str, default: bool = False) -> Any:
    return questionary.confirm(message, default=default).ask()


def _ask_checkbox(message: str, choices: Any) -> Any:
    return questionary.checkbox(message, choices=choices).ask()


def _interactive_menu() -> None:
    """Bare-command menu. Only ``create`` is available for now."""
    action = _ask_select(
        "What would you like to do?",
        choices=[questionary.Choice(title="create", value="create")],
    )
    if action is None:
        raise click.Abort()
    if action == "create":
        _interactive_create()


def _interactive_create() -> None:
    modules_path = _resolve_paths(None, None)[1]

    name = _prompt_project_name()
    profile = _prompt_profile(modules_path)

    selected_modules: list[str] | None = None
    if profile == _CUSTOM_PROFILE:
        selected_modules = _prompt_modules(modules_path)
        if selected_modules is None:
            raise click.Abort()

    vcs = _prompt_vcs()

    target_dir = Path.cwd() / name
    allow_overwrite = False
    if target_dir.exists() and any(target_dir.iterdir()):
        overwrite = _ask_confirm(f"Directory '{target_dir}' already exists. Overwrite?", default=False)
        if not overwrite:
            click.echo("Aborted.")
            raise click.Abort()
        allow_overwrite = True

    create_venv = _ask_confirm("Create a virtual environment and install dependencies?", default=True)
    if create_venv is None:
        raise click.Abort()

    _run_generation(
        name=name,
        profile=profile,
        selected_modules=selected_modules,
        allow_overwrite=allow_overwrite,
        create_venv=create_venv,
        vcs=vcs,
        prompt_optional_modules=selected_modules is None,
    )


def _validate_project_name_answer(value: str) -> bool | str:
    if _PROJECT_NAME_RE.match(value):
        return True
    return "Use letters, digits, '_' or '-', starting with a letter."


def _prompt_project_name() -> str:
    name = _ask_text("Project name", validate=_validate_project_name_answer)
    if name is None:
        raise click.Abort()
    return name


def _prompt_profile(modules_path: Path) -> str:
    profiles = _available_profiles(modules_path)
    rows = [(profile, _resolve_profile(profile, modules_path) or []) for profile in profiles]
    width = max([len(profile) for profile, _ in rows] + [len(_CUSTOM_PROFILE)])

    choices: list[questionary.Choice] = []
    for profile, modules in rows:
        modules_label = ", ".join(modules) if modules else "no modules"
        choices.append(questionary.Choice(title=f"{profile:<{width}}  {modules_label}", value=profile))
    choices.append(questionary.Separator("─" * (width + 4)))
    choices.append(
        questionary.Choice(
            title=f"{_CUSTOM_PROFILE:<{width}}  pick any modules manually",
            value=_CUSTOM_PROFILE,
        )
    )

    answer = _ask_select("Select a service to generate:", choices=choices)
    if answer is None:
        raise click.Abort()
    return answer


def _prompt_modules(modules_path: Path) -> list[str] | None:
    """Checkbox list over every available module, used by the ``custom`` flow."""
    choices: list[questionary.Choice] = []
    for module_id in _list_all_modules(modules_path):
        manifest = parse_manifest(modules_path / module_id / "manifest.yaml")
        label = module_id
        if manifest.requires_modules:
            label += f"  (requires: {', '.join(manifest.requires_modules)})"
        choices.append(questionary.Choice(title=label, value=module_id))

    while True:
        chosen = _ask_checkbox("Select modules to include (space to toggle, enter to confirm):", choices=choices)
        if chosen is None:
            return None
        if chosen:
            return list(chosen)
        click.echo("Pick at least one module (choose the 'minimal' service for none).", err=True)


def _prompt_vcs() -> str:
    answer = _ask_select(
        "Which repository host will you use?",
        choices=list(_VCS_CHOICES),
        default=_VCS_CHOICES[0],
    )
    if answer is None:
        raise click.Abort()
    return answer


def _available_profiles(modules_path: Path) -> list[str]:
    profiles_yaml = modules_path.parent / "profiles.yaml"
    if profiles_yaml.exists():
        data = yaml.safe_load(profiles_yaml.read_text(encoding="utf-8")) or {}
        names = list(data.get("profiles", {}).keys())
        if names:
            return names
    return list(_DEFAULT_PROFILE_ORDER)


def _resolve_profile(profile: str, modules_path: Path) -> list[str] | None:
    profiles_yaml = modules_path.parent / "profiles.yaml"
    if profiles_yaml.exists():
        data = yaml.safe_load(profiles_yaml.read_text(encoding="utf-8")) or {}
        p = data.get("profiles", {}).get(profile)
        if p is not None:
            return list(p)
    if profile == "auth-service":
        return ["auth"]
    return None


def _list_all_modules(modules_path: Path) -> list[str]:
    """Every module that ships a manifest, sorted by id."""
    return [d.name for d in sorted(modules_path.iterdir()) if d.is_dir() and (d / "manifest.yaml").exists()]


def _list_optional_modules(modules_path: Path, mandatory: set[str]) -> list[str]:
    return [module_id for module_id in _list_all_modules(modules_path) if module_id not in mandatory]


def _expand_requirements(selected: set[str], modules_path: Path) -> tuple[set[str], list[str]]:
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
            continue
        for requirement in parse_manifest(manifest_path).requires_modules:
            if requirement not in resolved:
                pending.append(requirement)
    return resolved, sorted(resolved - selected)


def _write_env_files(target_dir: Path, module_ids: list[str], modules_path: Path, project_name: str) -> None:
    _status("[env] Writing .env.example ...")
    lines = [
        "ENVIRONMENT=local",
        "API_HOST=0.0.0.0",
        "API_PORT=9000",
        "API_DEBUG=false",
        "API_TITLE=FastAPI Service",
        "API_VERSION=0.1.0",
        'CORS_ALLOWED_ORIGINS=["*"]',
        "POSTGRES_USER=postgres",
        "POSTGRES_PASSWORD=postgres",
        "POSTGRES_DB=postgres",
        "POSTGRES_HOST=localhost",
        "POSTGRES_PORT=5432",
        "DB_ECHO=false",
        "LOG_LEVEL=INFO",
    ]
    # Module-owned settings (e.g. auth owns the JWT variables).
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    context = {"project_name": project_name}
    for module_id in module_ids:
        manifest_path = modules_path / module_id / "manifest.yaml"
        if manifest_path.exists():
            for line in parse_manifest(manifest_path).env_example:
                lines.append(env.from_string(line).render(context))
    content = "\n".join(lines) + "\n"
    (target_dir / ".env.example").write_text(content, encoding="utf-8")
    _status("[env] .env.example created.")
    # Seed a working .env from the example so docker-compose can load it.
    (target_dir / ".env").write_text(content, encoding="utf-8")
    _status("[env] .env created from .env.example.")


def _find_ruff() -> str | None:
    found = shutil.which("ruff")
    if found:
        return found
    candidate = Path(sys.executable).parent / "ruff"
    if candidate.exists():
        return str(candidate)
    return None


def _run_ruff(target_dir: Path) -> bool:
    """Format then check the generated project. Returns ``True`` on success."""
    _status("[ruff] Running format and check ...")
    ruff = _find_ruff()
    if ruff is None:
        _status("[ruff] FAILED: ruff is not installed, check skipped.", ok=False, err=True)
        return False

    subprocess.run([ruff, "format", str(target_dir)], check=False, capture_output=True, text=True)
    subprocess.run([ruff, "check", "--fix", str(target_dir)], check=False, capture_output=True, text=True)
    result = subprocess.run([ruff, "check", str(target_dir)], check=False, capture_output=True, text=True)

    if result.returncode == 0:
        _status("[ruff] PASSED: no issues found.")
        return True

    _status("[ruff] FAILED: issues remain:", ok=False, err=True)
    click.echo((result.stdout or result.stderr).strip(), err=True)
    return False


def _setup_venv(target_dir: Path) -> bool:
    """Create ``.venv`` inside the generated project and install dependencies."""
    venv_dir = target_dir / ".venv"
    _status(f"[venv] Creating virtual environment at {venv_dir} ...")

    uv = shutil.which("uv")
    if uv:
        _status("[venv] uv found: downloading interpreter and dependencies (uv sync) ...")
        result = subprocess.run([uv, "sync", "--project", str(target_dir)], check=False, text=True)
    else:
        _status("[venv] uv not found: falling back to 'python -m venv' + pip ...")
        result = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=False, text=True)
        if result.returncode == 0:
            scripts_dir = "Scripts" if os.name == "nt" else "bin"
            pip = venv_dir / scripts_dir / "pip"
            result = subprocess.run([str(pip), "install", "-e", str(target_dir)], check=False, text=True)

    if result.returncode == 0:
        _status("[venv] PASSED: virtual environment is ready.")
        return True

    _status("[venv] FAILED: could not create the virtual environment.", ok=False, err=True)
    return False


def _git_available() -> bool:
    """Return ``True`` when a ``git`` executable is available on PATH."""
    return shutil.which("git") is not None


def _init_git(target_dir: Path, branch: str = "main") -> bool:
    """Initialize a git repository on ``branch`` and make a best-effort initial commit."""
    git = shutil.which("git")
    if git is None:
        _status("[git] FAILED: git is not installed, repository not initialized.", ok=False, err=True)
        return False

    _status(f"[git] Initializing repository on '{branch}' ...")
    result = subprocess.run([git, "init", "-b", branch], cwd=target_dir, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        _status(f"[git] FAILED: {(result.stderr or result.stdout).strip()}", ok=False, err=True)
        return False

    subprocess.run([git, "add", "-A"], cwd=target_dir, check=False, capture_output=True, text=True)
    commit = subprocess.run(
        [git, "commit", "-m", "Initial commit"],
        cwd=target_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if commit.returncode == 0:
        _status("[git] Repository initialized with an initial commit.")
    else:
        _status("[git] Repository initialized (no commit: configure git user.name/user.email).")
    return True


def _write_ci(target_dir: Path, ci_root: Path, vcs: str, main_branch: str, project_name: str) -> None:
    """Copy the CI templates for ``vcs`` into the generated project."""
    src_root = ci_root / vcs
    if not src_root.exists():
        _status(f"[ci] No CI templates for '{vcs}' at {src_root}, skipping.", ok=False, err=True)
        return

    _status(f"[ci] Adding {vcs} CI (main branch: {main_branch}) ...")
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    context = {"main_branch": main_branch, "project_name": project_name}
    for src in sorted(src_root.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(src_root)
        dst = target_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".jinja":
            rendered = env.from_string(src.read_text(encoding="utf-8")).render(context)
            dst.with_suffix("").write_text(rendered, encoding="utf-8")
        else:
            shutil.copy2(src, dst)
    _status(f"[ci] {vcs} CI created.")


@cli.command()
def list_profiles() -> None:
    """List available generation profiles."""
    modules_path = _resolve_paths(None, None)[1]
    profiles = _available_profiles(modules_path)
    width = max((len(name) for name in profiles), default=0)
    click.secho("Available profiles:", bold=True)
    for name in profiles:
        modules = _resolve_profile(name, modules_path) or []
        rendered = ", ".join(modules) if modules else "(none)"
        click.echo(f"  {name:<{width}}  ->  {rendered}")


@cli.command()
@click.option("--modules-dir", type=click.Path(exists=True, file_okay=False), default=None, envvar="BSG_MODULES_DIR")
def list_modules(modules_dir: str | None) -> None:
    """List available modules."""
    path = _resolve_paths(None, modules_dir)[1]
    modules = _list_all_modules(path)
    click.secho("Available modules:", bold=True)
    for module_id in modules:
        manifest = parse_manifest(path / module_id / "manifest.yaml")
        requires = f"  (requires: {', '.join(manifest.requires_modules)})" if manifest.requires_modules else ""
        click.echo(f"  {module_id}{requires}")


if __name__ == "__main__":
    cli()
