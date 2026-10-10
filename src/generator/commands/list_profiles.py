"""List available generation profiles."""

from __future__ import annotations

import sys
from typing import Any

import click

from generator.commands.base import Command
from generator.engine import GenerationError
from generator.paths import resolve_paths
from generator.profiles import available_profiles, resolve_profile


class ListProfilesCommand(Command):
    def execute(self, **kwargs: Any) -> None:
        try:
            modules_path = resolve_paths(None, None)[1]
        except GenerationError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(2)
        profiles = available_profiles(modules_path)
        width = max((len(name) for name in profiles), default=0)
        click.secho("Available profiles:", bold=True)
        for name in profiles:
            modules = resolve_profile(name, modules_path) or []
            rendered = ", ".join(modules) if modules else "(none)"
            click.echo(f"  {name:<{width}}  ->  {rendered}")
