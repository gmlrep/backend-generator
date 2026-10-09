from pathlib import Path

import pytest

from generator.manifest import ManifestError, parse_manifest

VALID = """
manifest_version: "1"
id: auth
version: "1.0.0"
requires:
  core: ">=1.0.0,<2.0.0"
  modules: []
conflicts: []
python_dependencies:
  - "pyjwt>=2.13"
patches:
  - target: src/main.py
    marker: EXTENSION_ROUTERS
    fragment: fragments/auth.py.jinja
env_example:
  - "JWT_SECRET=change-me"
"""


def _write(tmp_path: Path, content: str, *, directory: str = "auth") -> Path:
    mod = tmp_path / directory
    mod.mkdir(parents=True, exist_ok=True)
    path = mod / "manifest.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_valid_manifest(tmp_path: Path) -> None:
    manifest = parse_manifest(_write(tmp_path, VALID))
    assert manifest.id == "auth"
    assert manifest.version == "1.0.0"
    assert manifest.requires_core == ">=1.0.0,<2.0.0"
    assert manifest.python_dependencies == ["pyjwt>=2.13"]
    assert manifest.env_example == ["JWT_SECRET=change-me"]
    assert len(manifest.patches) == 1
    assert manifest.patches[0].marker == "EXTENSION_ROUTERS"


def test_rejects_unsupported_manifest_version(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="unsupported manifest_version"):
        parse_manifest(_write(tmp_path, VALID.replace('"1"', '"2"', 1)))


def test_rejects_non_mapping_root(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="root must be a mapping"):
        parse_manifest(_write(tmp_path, "- just\n- a\n- list\n"))


def test_rejects_invalid_python_dependencies(tmp_path: Path) -> None:
    bad = VALID.replace('python_dependencies:\n  - "pyjwt>=2.13"', "python_dependencies: not-a-list")
    with pytest.raises(ManifestError, match="python_dependencies"):
        parse_manifest(_write(tmp_path, bad))


def test_rejects_invalid_patch(tmp_path: Path) -> None:
    bad = VALID.replace("    marker: EXTENSION_ROUTERS\n", "")
    with pytest.raises(ManifestError, match="marker"):
        parse_manifest(_write(tmp_path, bad))


def test_rejects_id_mismatch_with_directory(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="must match directory name"):
        parse_manifest(_write(tmp_path, VALID, directory="other"))
