"""CLI interface for the Backend Service Generator.

Thin Click wiring; behavior lives in ``generator.commands`` (command pattern).
"""

from __future__ import annotations

import click

from generator.commands import CreateCommand, ListModulesCommand, ListProfilesCommand
from generator.constants import PACKAGE_VERSION
from generator.prompts import run_interactive_menu


@click.group(invoke_without_command=True)
@click.version_option(version=PACKAGE_VERSION, prog_name="bsg-generator")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Backend Service Generator — scaffold modular microservices.

    Run without a command to open the interactive menu.
    """
    if ctx.invoked_subcommand is None:
        run_interactive_menu()


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
    type=click.Choice(["github", "gitlab", "none"]),
    default="github",
    show_default=True,
    help="Repository host for CI (github/gitlab), or none to skip CI templates.",
)
@click.option(
    "--core-template",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    envvar="BSG_CORE_TEMPLATE",
)
@click.option(
    "--compose-services",
    default="",
    help="Comma-separated optional Compose sidecars: rabbitmq,kafka,flower,caddy "
    "(non-interactive). Interactive mode asks via checkbox.",
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
    compose_services: str,
    core_template: str | None,
    modules_dir: str | None,
) -> None:
    """Create a new FastAPI microservice project."""
    CreateCommand().execute(
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
        prompt_compose_services=not non_interactive,
        compose_services=compose_services,
    )


@cli.command()
def list_profiles() -> None:
    """List available generation profiles."""
    ListProfilesCommand().execute()


@cli.command()
@click.option("--modules-dir", type=click.Path(exists=True, file_okay=False), default=None, envvar="BSG_MODULES_DIR")
def list_modules(modules_dir: str | None) -> None:
    """List available modules."""
    ListModulesCommand().execute(modules_dir=modules_dir)


if __name__ == "__main__":
    cli()
