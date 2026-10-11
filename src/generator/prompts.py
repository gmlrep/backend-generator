"""Interactive questionary prompts for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import questionary

from generator.commands.create import CreateCommand
from generator.constants import CUSTOM_PROFILE, GO_BACK, PROJECT_NAME_RE, VCS_PROMPT_CHOICES
from generator.manifest import parse_manifest
from generator.paths import resolve_paths
from generator.paths_safe import ensure_inside, validate_module_id
from generator.profiles import available_profiles, list_all_modules, resolve_profile


def ask_select(message: str, choices: Any, default: Any = None) -> Any:
    """Thin wrapper around :func:`questionary.select` (patchable in tests)."""
    return questionary.select(message, choices=choices, default=default).ask()


def ask_text(message: str, validate: Any = None) -> Any:
    return questionary.text(message, validate=validate).ask()


def ask_confirm(message: str, default: bool = False) -> Any:
    return questionary.confirm(message, default=default).ask()


def ask_checkbox(message: str, choices: Any, **kwargs: Any) -> Any:
    return questionary.checkbox(message, choices=choices, **kwargs).ask()


def ask_checkbox_with_back(message: str, choices: Any) -> Any:
    """Checkbox where Backspace returns ``GO_BACK`` (custom modules step only)."""
    from prompt_toolkit.keys import Keys

    question = questionary.checkbox(
        message,
        choices=choices,
        instruction=(
            "(Use arrow keys to move, <space> to select, <a> to toggle, <i> to invert, <backspace> to go back)"
        ),
    )
    bindings = question.application.key_bindings
    if bindings is None:
        raise RuntimeError("checkbox application has no key bindings")

    @bindings.add(Keys.Backspace, eager=True)  # type: ignore[attr-defined]
    def _go_back(event: Any) -> None:
        event.app.exit(result=GO_BACK)

    return question.ask()


def validate_project_name_answer(value: str) -> bool | str:
    if PROJECT_NAME_RE.match(value):
        return True
    return "Use letters, digits or '_', starting with a letter (no hyphens)."


def prompt_project_name() -> str:
    name = ask_text("Project name", validate=validate_project_name_answer)
    if name is None:
        raise click.Abort()
    return name


def prompt_profile(modules_path: Path) -> str:
    profiles = available_profiles(modules_path)
    rows = [(profile, resolve_profile(profile, modules_path) or []) for profile in profiles]
    width = max([len(profile) for profile, _ in rows] + [len(CUSTOM_PROFILE)])

    choices: list[questionary.Choice] = []
    for profile, modules in rows:
        modules_label = ", ".join(modules) if modules else "no modules"
        choices.append(questionary.Choice(title=f"{profile:<{width}}  {modules_label}", value=profile))
    choices.append(questionary.Separator("─" * (width + 4)))
    choices.append(
        questionary.Choice(
            title=f"{CUSTOM_PROFILE:<{width}}  pick any modules manually",
            value=CUSTOM_PROFILE,
        )
    )

    answer = ask_select("Select a service to generate:", choices=choices)
    if answer is None:
        raise click.Abort()
    return answer


def prompt_modules(modules_path: Path) -> list[str] | Any:
    """Checkbox list over every available module, used by the ``custom`` flow.

    Returns ``GO_BACK`` when the user presses Backspace so the profile step
    can be shown again.
    """
    choices: list[questionary.Choice] = []
    modules_root = modules_path.resolve()
    for module_id in list_all_modules(modules_path):
        validate_module_id(module_id)
        manifest = parse_manifest(
            ensure_inside(modules_root, modules_root / module_id / "manifest.yaml", label=f"Module '{module_id}'")
        )
        label = module_id
        if manifest.requires_modules:
            label += f"  (requires: {', '.join(manifest.requires_modules)})"
        choices.append(questionary.Choice(title=label, value=module_id))

    while True:
        chosen = ask_checkbox_with_back(
            "Select modules to include (space to toggle, enter to confirm):",
            choices=choices,
        )
        if chosen is GO_BACK:
            return GO_BACK
        if chosen is None:
            return None
        if chosen:
            return list(chosen)
        click.echo("Pick at least one module (choose the 'minimal' service for none).", err=True)


def prompt_vcs() -> str:
    answer = ask_select(
        "Which repository host will you use?",
        choices=list(VCS_PROMPT_CHOICES),
        default=VCS_PROMPT_CHOICES[0],
    )
    if answer is None:
        raise click.Abort()
    return answer


def run_interactive_menu() -> None:
    """Bare-command menu. Only ``create`` is available for now."""
    action = ask_select(
        "What would you like to do?",
        choices=[questionary.Choice(title="create", value="create")],
    )
    if action is None:
        raise click.Abort()
    if action == "create":
        run_interactive_create()


def run_interactive_create() -> None:
    modules_path = resolve_paths(None, None)[1]

    name = prompt_project_name()

    selected_modules: list[str] | None = None
    while True:
        profile = prompt_profile(modules_path)
        if profile != CUSTOM_PROFILE:
            selected_modules = None
            break
        chosen = prompt_modules(modules_path)
        if chosen is GO_BACK:
            continue
        if chosen is None:
            raise click.Abort()
        selected_modules = chosen
        break

    vcs = prompt_vcs()

    target_dir = Path.cwd() / name
    allow_overwrite = False
    if target_dir.exists():
        if target_dir.is_file() or (target_dir.is_dir() and any(target_dir.iterdir())):
            overwrite = ask_confirm(f"Path '{target_dir}' already exists. Overwrite?", default=False)
            if not overwrite:
                click.echo("Aborted.")
                raise click.Abort()
            allow_overwrite = True

    create_venv = ask_confirm("Create a virtual environment and install dependencies?", default=True)
    if create_venv is None:
        raise click.Abort()

    CreateCommand().execute(
        name=name,
        profile=profile,
        selected_modules=selected_modules,
        allow_overwrite=allow_overwrite,
        create_venv=create_venv,
        vcs=vcs,
        prompt_compose_services=True,
    )
