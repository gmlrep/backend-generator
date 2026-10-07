from pathlib import Path

import pytest

GENERATOR_ROOT = Path(__file__).resolve().parent.parent
CORE_TEMPLATE = GENERATOR_ROOT / "generator-core" / "core_template"
MODULES_DIR = GENERATOR_ROOT / "generator-core" / "modules"


@pytest.fixture
def core_template() -> Path:
    return CORE_TEMPLATE


@pytest.fixture
def modules_dir() -> Path:
    return MODULES_DIR
