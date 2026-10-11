"""Optional Docker Compose sidecar selection for generation."""

from __future__ import annotations

from pathlib import Path

import click
import questionary

from generator.constants import COMPOSE_SERVICE_OPTIONS
from generator.engine import GenerationError
from generator.profiles import expand_requirements
from generator.status import status


def compose_service_choices(selected: set[str]) -> list[questionary.Choice]:
    """Checkbox options for optional Compose sidecars based on selected modules.

    Caddy is always offered. Broker toggles (rabbitmq/kafka) appear when that
    module is already selected. Flower / RustFS appear when celery / s3 are
    selected and the add-on is not already included.
    """
    choices: list[questionary.Choice] = []
    for trigger, value, label in COMPOSE_SERVICE_OPTIONS:
        if trigger is not None and trigger not in selected:
            continue
        # Add-on modules: hide once already selected.
        # Brokers keep showing (value == trigger). Caddy is always listed.
        if value in {"flower", "rustfs"} and value in selected:
            continue
        checked = value == "caddy" and value in selected
        choices.append(questionary.Choice(title=label, value=value, checked=checked))
    return choices


def parse_compose_services_flag(raw: str) -> set[str]:
    allowed = {value for _, value, _ in COMPOSE_SERVICE_OPTIONS}
    chosen: set[str] = set()
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in allowed:
            raise GenerationError(f"Unknown compose service '{name}'. Allowed: {', '.join(sorted(allowed))}")
        chosen.add(name)
    return chosen


def resolve_compose_services(
    selected: set[str],
    modules_path: Path,
    *,
    compose_services: str,
    prompt: bool,
) -> tuple[set[str], set[str]]:
    """Return ``(optional_compose_modules, updated_selected)``.

    Selecting rabbitmq/kafka modules only wires the Python client (external broker).
    Docker broker services are added only when chosen on this step / via
    ``--compose-services``. Interactive mode always offers the checkbox (at least
    Caddy; rabbitmq/kafka appear when those modules are selected).
    """
    updated = set(selected)
    if prompt:
        # Lazy import: prompts → CreateCommand → compose_services.
        from generator.prompts import ask_checkbox

        choices = compose_service_choices(updated)
        if not choices:
            return set(), updated
        chosen = ask_checkbox(
            "Add Docker Compose services (space to toggle, enter to confirm):",
            choices=choices,
        )
        if chosen is None:
            raise click.Abort()
        chosen_set = set(chosen)
    else:
        # Non-interactive: brokers stay external unless explicitly listed.
        chosen_set = parse_compose_services_flag(compose_services) if compose_services.strip() else set()

    optional_compose: set[str] = set()
    if "rabbitmq" in chosen_set and "rabbitmq" in updated:
        optional_compose.add("rabbitmq")
    if "kafka" in chosen_set and "kafka" in updated:
        optional_compose.add("kafka")

    modules_to_add: list[str] = []
    if "flower" in chosen_set:
        if "celery" not in updated:
            raise GenerationError("Compose service 'flower' requires the celery module")
        modules_to_add.append("flower")
    if "rustfs" in chosen_set:
        if "s3" not in updated:
            raise GenerationError("Compose service 'rustfs' requires the s3 module")
        modules_to_add.append("rustfs")
    if "caddy" in chosen_set:
        modules_to_add.append("caddy")

    if modules_to_add:
        updated.update(modules_to_add)
        updated, added = expand_requirements(updated, modules_path)
        if added:
            status(f"[modules] Required dependencies added: {', '.join(added)}")

    if optional_compose or modules_to_add:
        shown = sorted(optional_compose | set(modules_to_add))
        status(f"[compose] Optional services: {', '.join(shown)}")
    return optional_compose, updated
