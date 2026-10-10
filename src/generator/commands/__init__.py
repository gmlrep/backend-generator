"""CLI command classes (task-cli style)."""

from generator.commands.base import Command
from generator.commands.create import CreateCommand
from generator.commands.list_modules import ListModulesCommand
from generator.commands.list_profiles import ListProfilesCommand

__all__ = [
    "Command",
    "CreateCommand",
    "ListModulesCommand",
    "ListProfilesCommand",
]
