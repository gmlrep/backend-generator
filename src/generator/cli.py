"""CLI interface for the Backend Service Generator."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import click
import jinja2
import yaml

from generator.contracts import ContractValidator
from generator.engine import Engine, GenerationError
from generator.manifest import parse_manifest

_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_DEFAULT_PROFILE_ORDER = ["auth-service", "items-service", "full", "minimal"]
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
        click.echo("[git] git not found in PATH — skipping repository initialization.", err=True)
        init_git = False

    core_template_path, modules_path = _resolve_paths(core_template, modules_dir)
    if not core_template_path.exists():
        click.echo(f"Core template not found: {core_template_path}", err=True)
        sys.exit(1)
    if not modules_path.exists():
        click.echo(f"Modules directory not found: {modules_path}", err=True)
        sys.exit(1)

    profile_modules = _resolve_profile(profile, modules_path)
    if profile_modules is None:
        available = ", ".join(_available_profiles(modules_path)) or "auth-service"
        click.echo(f"Unknown profile: {profile}. Available: {available}", err=True)
        sys.exit(1)

    selected = set(profile_modules)

    if prompt_optional_modules:
        optional = _list_optional_modules(modules_path, selected)
        for opt in optional:
            if click.confirm(f"  Include optional module '{opt}'?", default=False):
                selected.add(opt)

    if modules:
        for m in modules.split(","):
            m = m.strip()
            if m:
                selected.add(m)

    target_dir = Path(target) if target else Path.cwd() / name
    _validate_target_dir(target_dir, allow_overwrite)

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
    _write_env_files(target_dir, sorted(selected), modules_path)
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

    click.echo(f"Project '{name}' created at {target_dir}")


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


def _interactive_menu() -> None:
    """Bare-command menu. Only ``create`` is available for now."""
    click.echo("What would you like to do?")
    click.echo("  1) create")
    click.prompt("Select", type=click.IntRange(1, 1), default=1, show_default=True)
    _interactive_create()


def _interactive_create() -> None:
    modules_path = _resolve_paths(None, None)[1]

    name = _prompt_project_name()
    profile = _prompt_profile(modules_path)
    vcs = _prompt_vcs()

    target_dir = Path.cwd() / name
    allow_overwrite = False
    if target_dir.exists() and any(target_dir.iterdir()):
        if not click.confirm(f"Directory '{target_dir}' already exists. Overwrite?", default=False):
            click.echo("Aborted.")
            raise click.Abort()
        allow_overwrite = True

    create_venv = click.confirm("Create a virtual environment and install dependencies?", default=True)

    _run_generation(
        name=name,
        profile=profile,
        allow_overwrite=allow_overwrite,
        create_venv=create_venv,
        vcs=vcs,
    )


def _prompt_project_name() -> str:
    while True:
        name = click.prompt("Project name")
        if _PROJECT_NAME_RE.match(name):
            return name
        click.echo("Invalid name: use letters, digits, '_' or '-', starting with a letter.", err=True)


def _prompt_profile(modules_path: Path) -> str:
    profiles = _available_profiles(modules_path)
    click.echo("Select a service to generate:")
    for index, profile in enumerate(profiles, start=1):
        click.echo(f"  {index}) {profile}")
    choice = click.prompt("Select", type=click.IntRange(1, len(profiles)), default=1)
    return profiles[choice - 1]


def _prompt_vcs() -> str:
    click.echo("Which repository host will you use?")
    for index, vcs in enumerate(_VCS_CHOICES, start=1):
        click.echo(f"  {index}) {vcs}")
    choice = click.prompt("Select", type=click.IntRange(1, len(_VCS_CHOICES)), default=1)
    return _VCS_CHOICES[choice - 1]


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


def _list_optional_modules(modules_path: Path, mandatory: set[str]) -> list[str]:
    opts = []
    for d in sorted(modules_path.iterdir()):
        if d.is_dir() and d.name not in mandatory:
            manifest = d / "manifest.yaml"
            if manifest.exists():
                opts.append(d.name)
    return opts


def _write_env_files(target_dir: Path, module_ids: list[str], modules_path: Path) -> None:
    click.echo("[env] Writing .env.example ...")
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
    for module_id in module_ids:
        manifest_path = modules_path / module_id / "manifest.yaml"
        if manifest_path.exists():
            lines.extend(parse_manifest(manifest_path).env_example)
    content = "\n".join(lines) + "\n"
    (target_dir / ".env.example").write_text(content, encoding="utf-8")
    click.echo("[env] .env.example created.")
    # Seed a working .env from the example so docker-compose can load it.
    (target_dir / ".env").write_text(content, encoding="utf-8")
    click.echo("[env] .env created from .env.example.")


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
    click.echo("[ruff] Running format and check ...")
    ruff = _find_ruff()
    if ruff is None:
        click.echo("[ruff] FAILED: ruff is not installed, check skipped.", err=True)
        return False

    subprocess.run([ruff, "format", str(target_dir)], check=False, capture_output=True, text=True)
    subprocess.run([ruff, "check", "--fix", str(target_dir)], check=False, capture_output=True, text=True)
    result = subprocess.run([ruff, "check", str(target_dir)], check=False, capture_output=True, text=True)

    if result.returncode == 0:
        click.echo("[ruff] PASSED: no issues found.")
        return True

    click.echo("[ruff] FAILED: issues remain:", err=True)
    click.echo((result.stdout or result.stderr).strip(), err=True)
    return False


def _setup_venv(target_dir: Path) -> bool:
    """Create ``.venv`` inside the generated project and install dependencies."""
    venv_dir = target_dir / ".venv"
    click.echo(f"[venv] Creating virtual environment at {venv_dir} ...")

    uv = shutil.which("uv")
    if uv:
        click.echo("[venv] uv found: downloading interpreter and dependencies (uv sync) ...")
        result = subprocess.run([uv, "sync", "--project", str(target_dir)], check=False, text=True)
    else:
        click.echo("[venv] uv not found: falling back to 'python -m venv' + pip ...")
        result = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=False, text=True)
        if result.returncode == 0:
            scripts_dir = "Scripts" if os.name == "nt" else "bin"
            pip = venv_dir / scripts_dir / "pip"
            result = subprocess.run([str(pip), "install", "-e", str(target_dir)], check=False, text=True)

    if result.returncode == 0:
        click.echo("[venv] PASSED: virtual environment is ready.")
        return True

    click.echo("[venv] FAILED: could not create the virtual environment.", err=True)
    return False


def _git_available() -> bool:
    """Return ``True`` when a ``git`` executable is available on PATH."""
    return shutil.which("git") is not None


def _init_git(target_dir: Path, branch: str = "main") -> bool:
    """Initialize a git repository on ``branch`` and make a best-effort initial commit."""
    git = shutil.which("git")
    if git is None:
        click.echo("[git] FAILED: git is not installed, repository not initialized.", err=True)
        return False

    click.echo(f"[git] Initializing repository on '{branch}' ...")
    result = subprocess.run([git, "init", "-b", branch], cwd=target_dir, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        click.echo(f"[git] FAILED: {(result.stderr or result.stdout).strip()}", err=True)
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
        click.echo("[git] Repository initialized with an initial commit.")
    else:
        click.echo("[git] Repository initialized (no commit: configure git user.name/user.email).")
    return True


def _write_ci(target_dir: Path, ci_root: Path, vcs: str, main_branch: str, project_name: str) -> None:
    """Copy the CI templates for ``vcs`` into the generated project."""
    src_root = ci_root / vcs
    if not src_root.exists():
        click.echo(f"[ci] No CI templates for '{vcs}' at {src_root}, skipping.", err=True)
        return

    click.echo(f"[ci] Adding {vcs} CI (main branch: {main_branch}) ...")
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
    click.echo(f"[ci] {vcs} CI created.")


@cli.command()
def list_profiles() -> None:
    """List available generation profiles."""
    modules_path = _resolve_paths(None, None)[1]
    click.echo("Available profiles:")
    for name in _available_profiles(modules_path):
        modules = _resolve_profile(name, modules_path) or []
        rendered = ", ".join(modules) if modules else "(none)"
        click.echo(f"  {name:14s} -> {rendered}")


@cli.command()
@click.option("--modules-dir", type=click.Path(exists=True, file_okay=False), default=None, envvar="BSG_MODULES_DIR")
def list_modules(modules_dir: str | None) -> None:
    """List available modules."""
    path = _resolve_paths(None, modules_dir)[1]
    click.echo("Available modules:")
    for d in sorted(path.iterdir()):
        if d.is_dir() and (d / "manifest.yaml").exists():
            click.echo(f"  {d.name}")


if __name__ == "__main__":
    cli()
