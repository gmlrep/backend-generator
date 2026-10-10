"""Shared CLI constants."""

from __future__ import annotations

import re

import questionary

# Must be a valid Python package name (no hyphens — they break imports).
PROJECT_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
DEFAULT_PROFILE_ORDER = ["auth-service", "items-service", "full", "minimal"]
CUSTOM_PROFILE = "custom"
VCS_CHOICES = ("github", "gitlab", "none")
# The chosen repository host determines the main branch and the generated CI.
# ``none`` skips CI templates; git init (if enabled) still uses ``main``.
VCS_DEFAULT_BRANCH = {"github": "main", "gitlab": "main", "none": "main"}
VCS_PROMPT_CHOICES = (
    questionary.Choice(title="github", value="github"),
    questionary.Choice(title="gitlab", value="gitlab"),
    questionary.Choice(title="None", value="none"),
)
PACKAGE_VERSION = "0.2.0"
# Sentinel returned by the custom-modules checkbox when the user presses Backspace.
GO_BACK = object()
# Optional Compose / infra add-ons. ``trigger`` is the module that must already
# be selected for the option to appear; ``None`` means always offered.
COMPOSE_SERVICE_OPTIONS = (
    ("rabbitmq", "rabbitmq", "RabbitMQ broker service"),
    ("kafka", "kafka", "Kafka broker service"),
    ("celery", "flower", "Flower (Celery monitoring UI)"),
    (None, "caddy", "Caddy (TLS reverse proxy / docker-compose.prod.yml)"),
)
