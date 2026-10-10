"""Command pattern base for CLI actions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Command(ABC):
    """Marker interface for CLI commands."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> None:
        """Run the command."""
