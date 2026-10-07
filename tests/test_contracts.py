from pathlib import Path

import pytest

from generator.contracts import BUILTIN_CONTRACTS, ContractValidator, ContractViolationError


def _make_core(tmp_path: Path, project_name: str = "demo") -> Path:
    core = tmp_path / "core"
    files: dict[Path, list[str]] = {}
    for contract in BUILTIN_CONTRACTS:
        path = core / contract.path.replace("{{project_name}}", project_name)
        files.setdefault(path, []).append(contract.name)
    for path, markers in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(f"# {m}" for m in markers) + "\n", encoding="utf-8")
    return core


def test_validator_accepts_valid_core(tmp_path: Path) -> None:
    core = _make_core(tmp_path)
    ContractValidator().validate(core, "demo")


def test_validator_rejects_missing_marker(tmp_path: Path) -> None:
    core = _make_core(tmp_path)
    (core / "src/demo/model/__init__.py").write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(ContractViolationError, match="EXTENSION_MODELS"):
        ContractValidator().validate(core, "demo")


def test_validator_rejects_duplicate_marker(tmp_path: Path) -> None:
    core = _make_core(tmp_path)
    (core / "src/demo/model/__init__.py").write_text("# EXTENSION_MODELS\n# EXTENSION_MODELS\n", encoding="utf-8")
    with pytest.raises(ContractViolationError, match="appears 2 times"):
        ContractValidator().validate(core, "demo")


def test_validator_rejects_missing_file(tmp_path: Path) -> None:
    core = _make_core(tmp_path)
    (core / "src/demo/api/v1/routes.py").unlink()
    with pytest.raises(ContractViolationError, match="does not exist"):
        ContractValidator().validate(core, "demo")
