from pathlib import Path

import pytest
import yaml

from generator.contracts import ContractValidator
from generator.engine import Engine, GenerationError


def _engine(core_template: Path, modules_dir: Path) -> Engine:
    return Engine(core_template, modules_dir, ContractValidator())


def _make_module(modules_dir: Path, module_id: str, files: dict[str, str], **overrides) -> None:
    mod = modules_dir / module_id
    (mod / "files").mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": "1",
        "id": module_id,
        "version": "1.0.0",
        "requires": {"core": ">=1.0.0", "modules": []},
        "conflicts": [],
        "python_dependencies": [],
        "patches": [],
    }
    manifest.update(overrides)
    (mod / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    for rel, content in files.items():
        path = mod / "files" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_generate_auth_service(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "myservice"
    _engine(core_template, modules_dir).generate(target, "myservice", ["auth"], core_version="1.0.0")

    assert (target / "src/myservice/__main__.py").exists()
    assert (target / "src/myservice/model/user.py").exists()
    assert (target / "src/myservice/services/auth.py").exists()
    assert (target / "src/myservice/core/security.py").exists()
    assert (target / "src/myservice/core/security_settings.py").exists()

    config = (target / "src/myservice/core/config.py").read_text(encoding="utf-8")
    assert "from myservice.core.security_settings import SecuritySettings" in config
    assert "security: SecuritySettings" in config

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")
    assert "JWT_SECRET" in compose
    # Compose must pull variables from the generated .env file.
    assert "env_file:" in compose
    assert "- .env" in compose

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyjwt" in pyproject
    assert "pwdlib" in pyproject

    # Module-owned tests
    assert (target / "tests/unit_tests/auth/test_security.py").exists()
    assert (target / "tests/unit_tests/auth/test_auth_service.py").exists()
    assert (target / "tests/integration_tests/auth/test_auth_router.py").exists()

    routes = (target / "src/myservice/api/v1/routes.py").read_text(encoding="utf-8")
    assert "auth_router" in routes
    assert "router_list.append(auth_router)" in routes

    models = (target / "src/myservice/model/__init__.py").read_text(encoding="utf-8")
    assert "from myservice.model.user import User" in models

    container = (target / "src/myservice/core/container.py").read_text(encoding="utf-8")
    assert "user_service" in container
    assert "myservice.api.v1.endpoints.auth" in container

    deps = (target / "src/myservice/core/dependencies.py").read_text(encoding="utf-8")
    assert "get_current_user" in deps

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyjwt" in pyproject


def test_generated_project_exposes_console_script(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "scripted"
    _engine(core_template, modules_dir).generate(target, "scripted", ["auth"], core_version="1.0.0")

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" in pyproject
    assert 'scripted = "scripted.__main__:main"' in pyproject

    main_py = (target / "src/scripted/__main__.py").read_text(encoding="utf-8")
    assert "def main() -> None:" in main_py
    assert 'if __name__ == "__main__":' in main_py
    assert main_py.rstrip().endswith("main()")

    readme = (target / "README.md").read_text(encoding="utf-8")
    assert "uv run scripted" in readme


def test_generated_logging_patches_global_logger(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "logged"
    _engine(core_template, modules_dir).generate(target, "logged", ["auth"], core_version="1.0.0")

    logging_py = (target / "src/logged/core/logging.py").read_text(encoding="utf-8")
    # The patcher must be applied to the global loguru logger, otherwise the
    # ``{extra[request_id]}`` format raises ``KeyError`` outside requests.
    assert "logger.configure(patcher=logger_patcher)" in logging_py
    assert "logger = logger.patch(" not in logging_py

    middleware = (target / "src/logged/core/middleware/request_id.py").read_text(encoding="utf-8")
    assert "logger.contextualize(request_id=request_id)" in middleware


def test_generated_project_has_makefile(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "makefile_app"
    _engine(core_template, modules_dir).generate(target, "makefile_app", ["auth"], core_version="1.0.0")

    makefile = (target / "Makefile").read_text(encoding="utf-8")
    assert "PROJECT := makefile_app" in makefile
    for target_name in ("install", "run", "dev", "test", "test-cov", "lint", "format", "fix", "migrate", "revision"):
        assert f"\n{target_name}:" in makefile
    for target_name in ("docker-build", "docker-up", "docker-down", "docker-logs", "docker-shell", "clean"):
        assert f"\n{target_name}:" in makefile

    # Recipes must be tab-indented for make, and use uv / docker compose.
    assert "\n\tuv run pytest\n" in makefile
    assert "\tuv run $(PROJECT)\n" in makefile
    assert "\t$(COMPOSE) up -d\n" in makefile
    assert "COMPOSE ?= docker compose" in makefile


def test_generated_dockerfile(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "dockered"
    _engine(core_template, modules_dir).generate(target, "dockered", ["auth"], core_version="1.0.0")

    dockerfile = (target / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim-bookworm AS builder" in dockerfile
    assert "FROM python:3.12-slim-bookworm AS runtime" in dockerfile
    assert "ghcr.io/astral-sh/uv" in dockerfile
    assert "uv sync --no-install-project --no-dev" in dockerfile
    assert "uv sync --no-dev" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/health" in dockerfile
    assert 'CMD ["dockered"]' in dockerfile
    # No leftovers from the reference template.
    assert "./bash" not in dockerfile
    assert "app.sh" not in dockerfile
    assert "FAST_API_PORT" not in dockerfile


def test_core_health_test_and_router_annotation(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    # Even a module-less project must ship at least one test so CI never
    # fails with "no tests ran".
    target = tmp_path / "smoke"
    _engine(core_template, modules_dir).generate(target, "smoke", [], core_version="1.0.0")

    assert (target / "tests/integration_tests/test_health.py").exists()
    routes = (target / "src/smoke/api/v1/routes.py").read_text(encoding="utf-8")
    assert "router_list: list[APIRouter] = []" in routes


def test_items_profile_has_no_auth_security(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "items_only"
    _engine(core_template, modules_dir).generate(target, "items_only", ["items"], core_version="1.0.0")

    assert not (target / "src/items_only/core/security.py").exists()
    assert not (target / "src/items_only/core/security_settings.py").exists()
    config = (target / "src/items_only/core/config.py").read_text(encoding="utf-8")
    assert "SecuritySettings" not in config
    assert "jwt" not in config.lower()

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")
    assert "JWT_SECRET" not in compose

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyjwt" not in pyproject
    assert "pwdlib" not in pyproject

    # auth tests must not leak into an items-only project
    assert not (target / "tests/unit_tests/auth").exists()
    assert (target / "tests/unit_tests/items/test_item_service.py").exists()
    assert (target / "tests/integration_tests/items/test_item_router.py").exists()


def test_generate_auth_and_items(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "combo"
    _engine(core_template, modules_dir).generate(target, "combo", ["auth", "items"], core_version="1.0.0")

    routes = (target / "src/combo/api/v1/routes.py").read_text(encoding="utf-8")
    assert "auth_router" in routes
    assert "item_router" in routes

    container = (target / "src/combo/core/container.py").read_text(encoding="utf-8")
    assert "auth_service" in container
    assert "item_service" in container

    models = (target / "src/combo/model/__init__.py").read_text(encoding="utf-8")
    assert "from combo.model.user import User" in models
    assert "from combo.model.item import Item" in models

    assert (target / "tests/unit_tests/auth/test_security.py").exists()
    assert (target / "tests/unit_tests/items/test_item_service.py").exists()


def test_generated_sources_are_syntactically_valid(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "valid"
    _engine(core_template, modules_dir).generate(target, "valid", ["auth", "items"], core_version="1.0.0")
    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_no_unresolved_jinja_in_output(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "nojinja"
    _engine(core_template, modules_dir).generate(target, "nojinja", ["auth", "items"], core_version="1.0.0")
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert "{{project_name}}" not in text, f"Unresolved Jinja variable in {path}"
        assert ".jinja" not in path.name, f"Unrendered template file left in output: {path}"


def test_unknown_module_manifest(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    with pytest.raises(GenerationError, match="does not have manifest.yaml"):
        _engine(core_template, modules_dir).generate(tmp_path / "x", "x", ["does-not-exist"])


def test_generate_rejects_existing_target(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("data", encoding="utf-8")

    with pytest.raises(GenerationError, match="already exists"):
        _engine(core_template, modules_dir).generate(target, "existing", ["auth"])

    assert (target / "keep.txt").read_text(encoding="utf-8") == "data"


def test_generate_rejects_incompatible_core(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    with pytest.raises(GenerationError, match="requires core"):
        _engine(core_template, modules_dir).generate(tmp_path / "x", "x", ["auth"], core_version="0.1.0")


def test_generate_rejects_conflicting_file_paths(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "alpha", {"src/demo/shared.py": "ALPHA = 1\n"})
    _make_module(modules, "beta", {"src/demo/shared.py": "BETA = 2\n"})

    with pytest.raises(GenerationError, match="File conflict"):
        _engine(core_template, modules).generate(tmp_path / "out", "demo", ["alpha", "beta"])


def test_generate_rejects_missing_required_module(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "beta", {}, requires={"core": ">=1.0.0", "modules": ["alpha"]})

    with pytest.raises(GenerationError, match="requires 'alpha'"):
        _engine(core_template, modules).generate(tmp_path / "out", "demo", ["beta"])


def test_generate_rejects_circular_dependencies(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "alpha", {}, requires={"core": ">=1.0.0", "modules": ["beta"]})
    _make_module(modules, "beta", {}, requires={"core": ">=1.0.0", "modules": ["alpha"]})

    with pytest.raises(GenerationError, match="Circular dependency"):
        _engine(core_template, modules).generate(tmp_path / "out", "demo", ["alpha", "beta"])


def test_generate_rejects_conflicting_modules(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "alpha", {}, conflicts=["beta"])
    _make_module(modules, "beta", {})

    with pytest.raises(GenerationError, match="conflicts with"):
        _engine(core_template, modules).generate(tmp_path / "out", "demo", ["alpha", "beta"])


def test_no_partial_output_on_failure(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "alpha", {"src/demo/shared.py": "A = 1\n"})
    _make_module(modules, "beta", {"src/demo/shared.py": "B = 2\n"})
    target = tmp_path / "out"

    with pytest.raises(GenerationError):
        _engine(core_template, modules).generate(target, "demo", ["alpha", "beta"])

    assert not target.exists()
    assert not list(tmp_path.glob(".out_gen_*"))
