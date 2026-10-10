"""List available generator modules."""

from __future__ import annotations

import sys
from typing import Any

import click

from generator.commands.base import Command
from generator.engine import GenerationError
from generator.manifest import parse_manifest
from generator.paths import resolve_paths
from generator.profiles import list_all_modules


class ListModulesCommand(Command):
    def execute(self, **kwargs: Any) -> None:
        modules_dir: str | None = kwargs.get("modules_dir")
        try:
            path = resolve_paths(None, modules_dir)[1]
        except GenerationError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(2)
        modules = list_all_modules(path)
        click.secho("Available modules:", bold=True)
        for module_id in modules:
            manifest = parse_manifest(path / module_id / "manifest.yaml")
            requires = f"  (requires: {', '.join(manifest.requires_modules)})" if manifest.requires_modules else ""
            click.echo(f"  {module_id}{requires}")
