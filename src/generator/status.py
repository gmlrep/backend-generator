"""Shared CLI status output."""

from __future__ import annotations

import click


def status(message: str, *, ok: bool = True, err: bool = False) -> None:
    """Print a status line; colours are dropped automatically when not a TTY."""
    click.secho(message, fg="green" if ok else "red", err=err)
