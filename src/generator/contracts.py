"""Contract validation for core template extension markers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True, slots=True)
class Contract:
    name: str
    path: str
    version: str = "1.0"


BUILTIN_CONTRACTS: Final[tuple[Contract, ...]] = (
    Contract(name="EXTENSION_ROUTERS", path="src/{{project_name}}/api/v1/routes.py", version="1.0"),
    Contract(name="EXTENSION_MODELS", path="src/{{project_name}}/model/__init__.py", version="1.0"),
    Contract(name="EXTENSION_TASKS", path="src/{{project_name}}/tasks/__init__.py", version="1.0"),
    Contract(name="EXTENSION_CONFIG_IMPORTS", path="src/{{project_name}}/core/config.py", version="1.0"),
    Contract(name="EXTENSION_CONFIG_CLASSES", path="src/{{project_name}}/core/config.py", version="1.0"),
    Contract(name="EXTENSION_SETTINGS", path="src/{{project_name}}/core/config.py", version="1.0"),
    Contract(name="EXTENSION_VALIDATION", path="src/{{project_name}}/core/config.py", version="1.0"),
    Contract(name="EXTENSION_CONTAINER_IMPORTS", path="src/{{project_name}}/core/container.py", version="1.0"),
    Contract(name="EXTENSION_CONTAINER_SERVICES", path="src/{{project_name}}/core/container.py", version="1.0"),
    Contract(name="EXTENSION_CONTAINER_WIRING", path="src/{{project_name}}/core/container.py", version="1.0"),
    Contract(name="EXTENSION_DEPENDENCIES", path="src/{{project_name}}/core/dependencies.py", version="1.0"),
    Contract(name="EXTENSION_MIDDLEWARE", path="src/{{project_name}}/__main__.py", version="1.0"),
    Contract(name="EXTENSION_MAIN_IMPORTS", path="src/{{project_name}}/__main__.py", version="1.0"),
    Contract(name="EXTENSION_APP_SETUP", path="src/{{project_name}}/__main__.py", version="1.0"),
    Contract(name="EXTENSION_MAKE", path="Makefile", version="1.0"),
    Contract(name="EXTENSION_LIFESPAN_IMPORTS", path="src/{{project_name}}/core/lifespan.py", version="1.0"),
    Contract(name="EXTENSION_LIFESPAN_STARTUP", path="src/{{project_name}}/core/lifespan.py", version="1.0"),
    Contract(name="EXTENSION_LIFESPAN_SHUTDOWN", path="src/{{project_name}}/core/lifespan.py", version="1.0"),
    Contract(name="EXTENSION_COMPOSE_ENV", path="docker-compose.yml", version="1.0"),
    Contract(name="EXTENSION_COMPOSE_SERVICES", path="docker-compose.yml", version="1.0"),
    Contract(name="EXTENSION_COMPOSE_DEPENDS_ON", path="docker-compose.yml", version="1.0"),
    Contract(name="EXTENSION_COMPOSE_VOLUMES", path="docker-compose.yml", version="1.0"),
)

MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[ \t]*#[ \t]*(EXTENSION_\w+)[ \t]*$", re.MULTILINE)


class ContractViolationError(Exception):
    """Raised when a core contract marker is missing, duplicated or unknown."""


class ContractValidator:
    def __init__(self, contracts: tuple[Contract, ...] = BUILTIN_CONTRACTS) -> None:
        self.contracts = contracts

    def validate(self, core_path: Path, project_name: str) -> None:
        for contract in self.contracts:
            file_path = core_path / contract.path.replace("{{project_name}}", project_name)
            if not file_path.exists():
                raise ContractViolationError(f"Contract '{contract.name}': expected file {file_path} does not exist")

            content = file_path.read_text(encoding="utf-8")
            matches = MARKER_PATTERN.findall(content)
            count = matches.count(contract.name)

            if count == 0:
                raise ContractViolationError(
                    f"Contract '{contract.name}' (v{contract.version}) not found in {file_path}. "
                    f"Generator and core template versions may be incompatible."
                )
            if count > 1:
                raise ContractViolationError(
                    f"Contract '{contract.name}' appears {count} times in {file_path}. "
                    f"Template integrity has been compromised."
                )
