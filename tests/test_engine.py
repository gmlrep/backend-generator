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


def _make_module_with_fragments(
    modules_dir: Path,
    module_id: str,
    patches: list[dict[str, str]],
    fragments: dict[str, str],
    **overrides,
) -> None:
    mod = modules_dir / module_id
    (mod / "files").mkdir(parents=True, exist_ok=True)
    (mod / "fragments").mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": "1",
        "id": module_id,
        "version": "1.0.0",
        "requires": {"core": ">=1.0.0", "modules": []},
        "conflicts": [],
        "python_dependencies": [],
        "patches": patches,
    }
    manifest.update(overrides)
    (mod / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    for rel, content in fragments.items():
        path = mod / "fragments" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_generate_auth_service(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "myservice"
    _engine(core_template, modules_dir).generate(target, "myservice", ["redis", "auth"], core_version="1.0.0")

    assert (target / "src/myservice/__main__.py").exists()
    assert (target / "src/myservice/model/user.py").exists()
    assert (target / "src/myservice/services/auth.py").exists()
    assert (target / "src/myservice/core/security.py").exists()
    assert not (target / "src/myservice/core/security_config.py").exists()

    config = (target / "src/myservice/core/config.py").read_text(encoding="utf-8")
    assert "from pathlib import Path" in config
    assert "class SecuritySettings(BaseSettings):" in config
    assert "security: SecuritySettings" in config

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")
    # JWT secrets live in .env only; Compose must not hardcode them.
    assert "JWT_SECRET=" not in compose
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
    _engine(core_template, modules_dir).generate(target, "scripted", ["redis", "auth"], core_version="1.0.0")

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
    _engine(core_template, modules_dir).generate(target, "logged", ["redis", "auth"], core_version="1.0.0")

    logging_py = (target / "src/logged/core/logging.py").read_text(encoding="utf-8")
    # The patcher must be applied to the global loguru logger, otherwise the
    # ``{extra[request_id]}`` format raises ``KeyError`` outside requests.
    assert "logger.configure(patcher=logger_patcher)" in logging_py
    assert "logger = logger.patch(" not in logging_py

    middleware = (target / "src/logged/core/middleware/request_id.py").read_text(encoding="utf-8")
    assert "logger.contextualize(request_id=request_id)" in middleware


def test_generated_project_has_makefile(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "makefile_app"
    _engine(core_template, modules_dir).generate(target, "makefile_app", ["redis", "auth"], core_version="1.0.0")

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
    _engine(core_template, modules_dir).generate(target, "dockered", ["redis", "auth"], core_version="1.0.0")

    dockerfile = (target / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim-bookworm AS builder" in dockerfile
    assert "FROM python:3.12-slim-bookworm AS runtime" in dockerfile
    assert "ghcr.io/astral-sh/uv" in dockerfile
    assert "uv sync --no-install-project --no-dev" in dockerfile
    assert "uv sync --no-dev" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/health" in dockerfile
    assert 'CMD ["dockered"]' in dockerfile
    assert 'ENTRYPOINT ["./docker-entrypoint.sh"]' in dockerfile
    # No leftovers from the reference template.
    assert "./bash" not in dockerfile
    assert "app.sh" not in dockerfile
    assert "FAST_API_PORT" not in dockerfile
    # Shebang scripts must use LF so Linux containers work when generated on Windows.
    entrypoint = (target / "docker-entrypoint.sh").read_bytes()
    assert b"\r" not in entrypoint
    assert entrypoint.startswith(b"#!/usr/bin/env sh\n")


def test_generated_project_runs_migrations_on_startup(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "migrator"
    _engine(core_template, modules_dir).generate(target, "migrator", ["redis", "celery"], core_version="1.0.0")

    entrypoint = target / "docker-entrypoint.sh"
    assert entrypoint.exists()
    assert "python -m migrator.scripts.init_db" in entrypoint.read_text(encoding="utf-8")

    init_db = (target / "src/migrator/scripts/init_db.py").read_text(encoding="utf-8")
    assert "get_table_names" in init_db
    assert "command.upgrade" in init_db
    assert "create_all" in init_db
    assert "alembic_version" in init_db

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    api_env = compose["services"]["api"]["environment"]
    worker_env = compose["services"]["worker"]["environment"]
    assert "RUN_MIGRATIONS=true" in api_env
    assert "RUN_MIGRATIONS=false" in worker_env

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_core_health_test_and_router_annotation(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    # Even a module-less project must ship at least one test so CI never
    # fails with "no tests ran".
    target = tmp_path / "smoke"
    _engine(core_template, modules_dir).generate(target, "smoke", [], core_version="1.0.0")

    assert (target / "tests/integration_tests/test_health.py").exists()
    routes = (target / "src/smoke/api/v1/routes.py").read_text(encoding="utf-8")
    assert "router_list: list[APIRouter] = []" in routes


def test_items_profile_pulls_auth(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "items_only"
    _engine(core_template, modules_dir).generate(target, "items_only", ["items"], core_version="1.0.0")

    # items requires auth (and transitive redis / rate-limit).
    assert (target / "src/items_only/core/security.py").exists()
    config = (target / "src/items_only/core/config.py").read_text(encoding="utf-8")
    assert "SecuritySettings" in config

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyjwt" in pyproject

    assert (target / "tests/unit_tests/auth").exists()
    assert (target / "tests/unit_tests/items/test_item_service.py").exists()
    assert (target / "tests/integration_tests/items/test_item_router.py").exists()


def test_generate_auth_and_items(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "combo"
    _engine(core_template, modules_dir).generate(target, "combo", ["redis", "auth", "items"], core_version="1.0.0")

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
    _engine(core_template, modules_dir).generate(target, "valid", ["redis", "auth", "items"], core_version="1.0.0")
    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_no_unresolved_jinja_in_output(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "nojinja"
    _engine(core_template, modules_dir).generate(target, "nojinja", ["redis", "auth", "items"], core_version="1.0.0")
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
    with pytest.raises(GenerationError, match="Unknown module 'does-not-exist'"):
        _engine(core_template, modules_dir).generate(tmp_path / "x", "x", ["does-not-exist"])


def test_generate_rejects_existing_target(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("data", encoding="utf-8")

    with pytest.raises(GenerationError, match="already exists"):
        _engine(core_template, modules_dir).generate(target, "existing", ["redis", "auth"])

    assert (target / "keep.txt").read_text(encoding="utf-8") == "data"


def test_generate_rejects_incompatible_core(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    with pytest.raises(GenerationError, match="requires core"):
        _engine(core_template, modules_dir).generate(tmp_path / "x", "x", ["redis", "auth"], core_version="0.1.0")


def test_generate_rejects_conflicting_file_paths(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "alpha", {"src/demo/shared.py": "ALPHA = 1\n"})
    _make_module(modules, "beta", {"src/demo/shared.py": "BETA = 2\n"})

    with pytest.raises(GenerationError, match="File conflict"):
        _engine(core_template, modules).generate(tmp_path / "out", "demo", ["alpha", "beta"])


def test_generate_rejects_missing_required_module(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "beta", {}, requires={"core": ">=1.0.0", "modules": ["alpha"]})

    with pytest.raises(GenerationError, match="Unknown module 'alpha'"):
        _engine(core_template, modules).generate(tmp_path / "out", "demo", ["beta"])


def test_generate_rejects_kafka_and_rabbitmq_together(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    with pytest.raises(GenerationError, match="conflicts with"):
        _engine(core_template, modules_dir).generate(
            tmp_path / "brokers", "brokers", ["rabbitmq", "kafka"], core_version="1.0.0"
        )


def test_overwrite_preserves_backup_on_swap(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "swapme"
    engine = _engine(core_template, modules_dir)
    engine.generate(target, "swapme", [], core_version="1.0.0")
    marker = target / "KEEP.txt"
    marker.write_text("old", encoding="utf-8")
    engine.generate(target, "swapme", [], core_version="1.0.0", allow_overwrite=True)
    assert (target / "src/swapme/__main__.py").exists()
    assert not marker.exists()


def test_rejects_hyphenated_project_name(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    with pytest.raises(GenerationError, match="no hyphens"):
        _engine(core_template, modules_dir).generate(
            tmp_path / "payments-service", "payments-service", [], core_version="1.0.0"
        )


def test_rejects_module_path_traversal(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    with pytest.raises(GenerationError, match="Invalid module id"):
        _engine(core_template, modules_dir).generate(tmp_path / "evil", "evil", ["../etc"], core_version="1.0.0")


def test_rejects_target_file_without_overwrite(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "as_file"
    target.write_text("nope", encoding="utf-8")
    with pytest.raises(GenerationError, match="is a file"):
        _engine(core_template, modules_dir).generate(target, "as_file", [], core_version="1.0.0")


def test_rejects_conflicting_dependency_pins(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module(modules, "alpha", {}, python_dependencies=["redis>=5"])
    _make_module(modules, "beta", {}, python_dependencies=["redis>=6"])
    with pytest.raises(GenerationError, match="Conflicting dependency"):
        _engine(core_template, modules).generate(tmp_path / "out", "demo", ["alpha", "beta"])


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


def test_core_exposes_infrastructure_extension_points(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "infra"
    _engine(core_template, modules_dir).generate(target, "infra", [], core_version="1.0.0")

    # lifespan replaced the old events module and carries extension markers.
    assert (target / "src/infra/core/lifespan.py").exists()
    assert not (target / "src/infra/core/events.py").exists()
    lifespan = (target / "src/infra/core/lifespan.py").read_text(encoding="utf-8")
    assert "# EXTENSION_LIFESPAN_IMPORTS" in lifespan
    assert "# EXTENSION_LIFESPAN_STARTUP" in lifespan
    assert "# EXTENSION_LIFESPAN_SHUTDOWN" in lifespan

    main_py = (target / "src/infra/__main__.py").read_text(encoding="utf-8")
    assert "from infra.core.lifespan import lifespan" in main_py
    assert "# EXTENSION_MAIN_IMPORTS" in main_py
    assert "# EXTENSION_MIDDLEWARE" in main_py
    assert "# EXTENSION_APP_SETUP" in main_py

    makefile = (target / "Makefile").read_text(encoding="utf-8")
    assert "# EXTENSION_MAKE" in makefile

    config = (target / "src/infra/core/config.py").read_text(encoding="utf-8")
    assert "# EXTENSION_CONFIG_IMPORTS" in config
    assert "# EXTENSION_CONFIG_CLASSES" in config
    assert "# EXTENSION_SETTINGS" in config

    # Broker-agnostic messaging facade shared by the rabbitmq/kafka modules.
    assert (target / "src/infra/infrastructure/messaging.py").exists()

    # Shared, core-owned package roots so modules never collide on __init__.py.
    for root in ("adapters", "infrastructure", "tasks"):
        assert (target / f"src/infra/{root}/__init__.py").exists()

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres:18-alpine" in compose
    # Postgres 18 data directory is pinned to the mounted volume.
    assert "PGDATA: /var/lib/postgresql/data" in compose
    assert "# EXTENSION_COMPOSE_ENV" in compose
    assert "# EXTENSION_COMPOSE_SERVICES" in compose
    assert "# EXTENSION_COMPOSE_DEPENDS_ON" in compose
    assert "# EXTENSION_COMPOSE_VOLUMES" in compose


def test_module_fragments_extend_compose_and_lifespan(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    _make_module_with_fragments(
        modules,
        "cache",
        patches=[
            {
                "target": "docker-compose.yml",
                "marker": "EXTENSION_COMPOSE_SERVICES",
                "fragment": "fragments/service.yml",
            },
            {
                "target": "docker-compose.yml",
                "marker": "EXTENSION_COMPOSE_DEPENDS_ON",
                "fragment": "fragments/depends.yml",
            },
            {
                "target": "docker-compose.yml",
                "marker": "EXTENSION_COMPOSE_VOLUMES",
                "fragment": "fragments/volume.yml",
            },
            {
                "target": "src/{{project_name}}/__main__.py",
                "marker": "EXTENSION_MIDDLEWARE",
                "fragment": "fragments/middleware.py",
            },
            {
                "target": "src/{{project_name}}/core/lifespan.py",
                "marker": "EXTENSION_LIFESPAN_STARTUP",
                "fragment": "fragments/startup.py",
            },
        ],
        fragments={
            "service.yml": "  cache:\n    image: redis:7-alpine\n",
            "depends.yml": "      cache:\n        condition: service_started\n",
            "volume.yml": "  cachedata:\n",
            "middleware.py": "        self.app.add_middleware(SomeMiddleware)\n",
            "startup.py": "    app.state.cache = object()\n",
        },
    )

    target = tmp_path / "out"
    _engine(core_template, modules).generate(target, "demo", ["cache"], core_version="1.0.0")

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["cache"]["image"] == "redis:7-alpine"
    assert "cache" in compose["services"]["api"]["depends_on"]
    assert "cachedata" in compose["volumes"]

    main_py = (target / "src/demo/__main__.py").read_text(encoding="utf-8")
    assert "self.app.add_middleware(SomeMiddleware)" in main_py

    lifespan = (target / "src/demo/core/lifespan.py").read_text(encoding="utf-8")
    assert "app.state.cache = object()" in lifespan


def test_generate_redis_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withredis"
    _engine(core_template, modules_dir).generate(target, "withredis", ["redis"], core_version="1.0.0")

    assert (target / "src/withredis/infrastructure/redis/client.py").exists()
    assert (target / "src/withredis/infrastructure/redis/config.py").exists()
    assert (target / "tests/unit_tests/redis/test_client.py").exists()

    config = (target / "src/withredis/core/config.py").read_text(encoding="utf-8")
    assert "RedisSettings" in config
    assert "redis: RedisSettings" in config

    container = (target / "src/withredis/core/container.py").read_text(encoding="utf-8")
    assert "RedisClient" in container
    assert "redis_client = providers.Singleton" in container

    lifespan = (target / "src/withredis/core/lifespan.py").read_text(encoding="utf-8")
    assert "container.redis_client()" in lifespan
    assert "await redis_client.close()" in lifespan

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["redis"]["image"] == "redis:8-alpine"
    assert "redis" in compose["services"]["api"]["depends_on"]
    assert "redisdata" in compose["volumes"]

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "redis>=" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_rabbitmq_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withrabbit"
    _engine(core_template, modules_dir).generate(
        target,
        "withrabbit",
        ["rabbitmq"],
        core_version="1.0.0",
        optional_compose_modules=set(),
    )

    assert (target / "src/withrabbit/infrastructure/rabbitmq/broker.py").exists()
    assert (target / "src/withrabbit/infrastructure/rabbitmq/config.py").exists()
    assert (target / "tests/unit_tests/rabbitmq/test_broker.py").exists()

    config = (target / "src/withrabbit/core/config.py").read_text(encoding="utf-8")
    assert "RabbitMQSettings" in config
    assert "rabbitmq: RabbitMQSettings" in config

    container = (target / "src/withrabbit/core/container.py").read_text(encoding="utf-8")
    assert "create_rabbitmq_broker" in container
    assert "rabbitmq_broker = providers.Singleton" in container

    lifespan = (target / "src/withrabbit/core/lifespan.py").read_text(encoding="utf-8")
    assert "container.rabbitmq_broker()" in lifespan
    assert "await rabbitmq_broker.stop()" in lifespan

    # Module alone wires the client; Compose broker is opt-in.
    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "rabbitmq" not in compose["services"]

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "faststream[rabbit]" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_rabbitmq_with_compose_service(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withrabbitc"
    _engine(core_template, modules_dir).generate(
        target,
        "withrabbitc",
        ["rabbitmq"],
        core_version="1.0.0",
        optional_compose_modules={"rabbitmq"},
    )
    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["rabbitmq"]["image"] == "rabbitmq:4-management-alpine"
    assert "rabbitmq" in compose["services"]["api"]["depends_on"]
    assert "rabbitmqdata" in compose["volumes"]


def test_generate_kafka_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withkafka"
    _engine(core_template, modules_dir).generate(
        target,
        "withkafka",
        ["kafka"],
        core_version="1.0.0",
        optional_compose_modules=set(),
    )

    assert (target / "src/withkafka/infrastructure/kafka/broker.py").exists()
    assert (target / "src/withkafka/infrastructure/kafka/config.py").exists()
    assert (target / "tests/unit_tests/kafka/test_broker.py").exists()

    config = (target / "src/withkafka/core/config.py").read_text(encoding="utf-8")
    assert "KafkaSettings" in config
    assert "kafka: KafkaSettings" in config

    container = (target / "src/withkafka/core/container.py").read_text(encoding="utf-8")
    assert "create_kafka_broker" in container
    assert "kafka_broker = providers.Singleton" in container

    lifespan = (target / "src/withkafka/core/lifespan.py").read_text(encoding="utf-8")
    assert "container.kafka_broker()" in lifespan
    assert "await kafka_broker.stop()" in lifespan

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "kafka" not in compose["services"]

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "faststream[kafka]" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_kafka_with_compose_service(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withkafkac"
    _engine(core_template, modules_dir).generate(
        target,
        "withkafkac",
        ["kafka"],
        core_version="1.0.0",
        optional_compose_modules={"kafka"},
    )
    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "kafka" in compose["services"]
    assert "kafka" in compose["services"]["api"]["depends_on"]
    assert "kafkadata" in compose["volumes"]


def test_generate_celery_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withcelery"
    _engine(core_template, modules_dir).generate(target, "withcelery", ["redis", "celery"], core_version="1.0.0")

    assert (target / "src/withcelery/infrastructure/celery/app.py").exists()
    assert (target / "src/withcelery/infrastructure/celery/config.py").exists()
    assert (target / "tests/unit_tests/celery/test_app.py").exists()

    config = (target / "src/withcelery/core/config.py").read_text(encoding="utf-8")
    assert "CelerySettings" in config
    assert "celery: CelerySettings" in config

    container = (target / "src/withcelery/core/container.py").read_text(encoding="utf-8")
    assert "celery_app = providers.Singleton" in container

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "worker" in compose["services"]
    assert "redis" in compose["services"]

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "celery[redis]" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_celery_auto_expands_redis(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "celery_only"
    _engine(core_template, modules_dir).generate(target, "celery_only", ["celery"], core_version="1.0.0")
    assert (target / "src/celery_only/infrastructure/redis/client.py").exists()
    assert (target / "src/celery_only/infrastructure/celery/app.py").exists()


def test_generate_rate_limit_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withlimits"
    _engine(core_template, modules_dir).generate(target, "withlimits", ["redis", "rate-limit"], core_version="1.0.0")

    assert (target / "src/withlimits/core/middleware/rate_limit.py").exists()
    assert (target / "src/withlimits/services/rate_limit.py").exists()
    assert not (target / "src/withlimits/core/middleware/rate_limit_config.py").exists()
    assert (target / "tests/unit_tests/rate_limit/test_rate_limit.py").exists()

    main_py = (target / "src/withlimits/__main__.py").read_text(encoding="utf-8")
    assert "RateLimitMiddleware" in main_py
    assert "add_middleware(" in main_py

    deps = (target / "src/withlimits/core/dependencies.py").read_text(encoding="utf-8")
    assert "rate_limit" in deps

    config = (target / "src/withlimits/core/config.py").read_text(encoding="utf-8")
    assert "class RateLimitSettings(BaseSettings):" in config
    assert "rate_limit: RateLimitSettings" in config

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_mfa_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withmfa"
    _engine(core_template, modules_dir).generate(
        target, "withmfa", ["redis", "auth", "email", "mfa"], core_version="1.0.0"
    )

    assert (target / "src/withmfa/model/mfa.py").exists()
    assert (target / "src/withmfa/services/mfa.py").exists()
    assert (target / "src/withmfa/services/mfa_code_store.py").exists()
    assert not (target / "src/withmfa/services/mfa_config.py").exists()
    assert (target / "src/withmfa/adapters/email/config.py").exists()
    assert (target / "src/withmfa/api/v1/endpoints/mfa.py").exists()

    config = (target / "src/withmfa/core/config.py").read_text(encoding="utf-8")
    assert "class MfaSettings(BaseSettings):" in config
    assert "mfa: MfaSettings" in config

    container = (target / "src/withmfa/core/container.py").read_text(encoding="utf-8")
    assert "mfa_service = providers.Factory" in container
    assert "withmfa.api.v1.endpoints.mfa" in container

    models = (target / "src/withmfa/model/__init__.py").read_text(encoding="utf-8")
    assert "from withmfa.model.mfa import MfaCredential" in models

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyotp" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_mfa_auto_expands_auth_and_email(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "mfa_only"
    _engine(core_template, modules_dir).generate(target, "mfa_only", ["mfa"], core_version="1.0.0")
    assert (target / "src/mfa_only/core/security.py").exists()
    assert (target / "src/mfa_only/adapters/email/config.py").exists()
    assert (target / "src/mfa_only/model/mfa.py").exists()


def test_core_omits_module_only_dependencies(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "bare"
    _engine(core_template, modules_dir).generate(target, "bare", [], core_version="1.0.0")
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "aiosmtplib" not in pyproject


def test_generate_email_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withemail"
    _engine(core_template, modules_dir).generate(target, "withemail", ["email"], core_version="1.0.0")

    assert (target / "src/withemail/adapters/email/smtp_sender.py").exists()
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "aiosmtplib" in pyproject


def test_generate_logging_elasticsearch_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withesp"
    _engine(core_template, modules_dir).generate(target, "withesp", ["logging-elasticsearch"], core_version="1.0.0")

    assert (target / "src/withesp/infrastructure/logging/elasticsearch.py").exists()
    assert (target / "src/withesp/infrastructure/logging/config.py").exists()
    assert (target / "src/withesp/infrastructure/logging/config.py").exists()

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "elasticsearch" in compose["services"]
    assert "esdata" in compose["volumes"]

    lifespan = (target / "src/withesp/core/lifespan.py").read_text(encoding="utf-8")
    assert "ElasticsearchLogSink" in lifespan
    assert "add_log_sink" in lifespan

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "elasticsearch" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_openai_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withopenai"
    _engine(core_template, modules_dir).generate(target, "withopenai", ["openai"], core_version="1.0.0")

    assert (target / "src/withopenai/adapters/openai/interface.py").exists()
    assert (target / "src/withopenai/adapters/openai/client.py").exists()
    assert (target / "src/withopenai/adapters/openai/config.py").exists()
    assert (target / "src/withopenai/services/openai_tasks.py").exists()

    container = (target / "src/withopenai/core/container.py").read_text(encoding="utf-8")
    assert "OpenAITextGenerator" in container
    assert "openai_service = providers.Factory" in container

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "openai" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_s3_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withs3"
    _engine(core_template, modules_dir).generate(target, "withs3", ["s3"], core_version="1.0.0")

    assert (target / "src/withs3/adapters/s3/interface.py").exists()
    assert (target / "src/withs3/adapters/s3/client.py").exists()
    assert (target / "src/withs3/adapters/s3/config.py").exists()
    assert (target / "src/withs3/services/storage.py").exists()

    container = (target / "src/withs3/core/container.py").read_text(encoding="utf-8")
    assert "S3Storage" in container
    assert "storage_service = providers.Factory" in container

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "aioboto3" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_push_notifications_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withpush"
    _engine(core_template, modules_dir).generate(
        target, "withpush", ["redis", "celery", "push-notifications"], core_version="1.0.0"
    )

    assert (target / "src/withpush/adapters/firebase/client.py").exists()
    assert (target / "src/withpush/adapters/firebase/config.py").exists()
    assert (target / "src/withpush/services/notifications.py").exists()
    assert (target / "src/withpush/tasks/notifications.py").exists()

    tasks_init = (target / "src/withpush/tasks/__init__.py").read_text(encoding="utf-8")
    assert "from withpush.tasks.notifications import send_push" in tasks_init

    container = (target / "src/withpush/core/container.py").read_text(encoding="utf-8")
    assert "notification_service = providers.Factory" in container

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "firebase-admin" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_users_profiles_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withprofiles"
    _engine(core_template, modules_dir).generate(
        target, "withprofiles", ["redis", "auth", "users-profiles"], core_version="1.0.0"
    )

    assert (target / "src/withprofiles/model/profile.py").exists()
    assert (target / "src/withprofiles/repository/profile.py").exists()
    assert (target / "src/withprofiles/services/users.py").exists()
    assert (target / "src/withprofiles/api/v1/endpoints/users.py").exists()

    models = (target / "src/withprofiles/model/__init__.py").read_text(encoding="utf-8")
    assert "from withprofiles.model.profile import Profile" in models

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_roles_permissions_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withroles"
    _engine(core_template, modules_dir).generate(
        target,
        "withroles",
        ["redis", "auth", "users-profiles", "roles-permissions"],
        core_version="1.0.0",
    )

    assert (target / "src/withroles/model/role.py").exists()
    assert (target / "src/withroles/model/permission.py").exists()
    assert (target / "src/withroles/services/permissions.py").exists()
    assert (target / "src/withroles/services/permission_dependencies.py").exists()

    container = (target / "src/withroles/core/container.py").read_text(encoding="utf-8")
    assert "permission_service = providers.Factory" in container

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_fragments_are_applied_in_dependency_order(tmp_path: Path, core_template: Path) -> None:
    modules = tmp_path / "modules"
    patch = {
        "target": "src/{{project_name}}/core/dependencies.py",
        "marker": "EXTENSION_DEPENDENCIES",
        "fragment": "fragments/snippet.py",
    }
    _make_module_with_fragments(
        modules,
        "beta",
        patches=[patch],
        fragments={"snippet.py": "BETA = 1\n"},
    )
    _make_module_with_fragments(
        modules,
        "alpha",
        patches=[patch],
        fragments={"snippet.py": "ALPHA = BETA\n"},
        requires={"core": ">=1.0.0", "modules": ["beta"]},
    )

    target = tmp_path / "ordered"
    # Pass modules alphabetically: the engine must still emit beta before alpha.
    _engine(core_template, modules).generate(target, "ordered", ["alpha", "beta"])

    content = (target / "src/ordered/core/dependencies.py").read_text(encoding="utf-8")
    assert content.index("BETA = 1") < content.index("ALPHA = BETA")


def test_generate_flower_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withflower"
    _engine(core_template, modules_dir).generate(
        target, "withflower", ["redis", "celery", "flower"], core_version="1.0.0"
    )

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "flower" in compose["services"]
    assert compose["services"]["flower"]["ports"] == ["${FLOWER_PORT:-5555}:5555"]

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "flower" in pyproject


def test_generate_celery_beat_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withbeat"
    _engine(core_template, modules_dir).generate(
        target, "withbeat", ["redis", "celery", "celery-beat"], core_version="1.0.0"
    )

    assert (target / "src/withbeat/infrastructure/celery/beat.py").exists()
    assert (target / "src/withbeat/tasks/beat.py").exists()

    app = (target / "src/withbeat/infrastructure/celery/app.py").read_text(encoding="utf-8")
    assert "BEAT_SCHEDULE" in app
    assert "app.conf.beat_schedule" in app

    tasks_init = (target / "src/withbeat/tasks/__init__.py").read_text(encoding="utf-8")
    assert "from withbeat.tasks.beat import heartbeat" in tasks_init

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    beat_cmd = compose["services"]["beat"]["command"]
    assert "--schedule=/tmp/celerybeat-schedule" in beat_cmd
    assert "--pidfile=/tmp/celerybeat.pid" in beat_cmd

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_prometheus_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withmetrics"
    _engine(core_template, modules_dir).generate(target, "withmetrics", ["prometheus"], core_version="1.0.0")

    assert (target / "src/withmetrics/infrastructure/prometheus/instrumentation.py").exists()
    assert (target / "src/withmetrics/infrastructure/prometheus/prometheus.yml").exists()
    assert (target / "src/withmetrics/infrastructure/prometheus/config.py").exists()

    main_py = (target / "src/withmetrics/__main__.py").read_text(encoding="utf-8")
    assert "setup_metrics" in main_py
    assert "settings.prometheus.ENABLED" in main_py

    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "prometheus" in compose["services"]
    assert "prometheusdata" in compose["volumes"]

    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "prometheus-fastapi-instrumentator" in pyproject

    for py_file in target.rglob("*.py"):
        compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")


def test_generate_caddy_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withcaddy"
    _engine(core_template, modules_dir).generate(target, "withcaddy", ["redis", "auth", "caddy"], core_version="1.0.0")

    caddyfile = (target / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy api:{$API_PORT:9000}" in caddyfile

    prod = (target / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "ports: !reset []" in prod
    assert "caddy:2-alpine" in prod
    assert "443:443" in prod

    makefile = (target / "Makefile").read_text(encoding="utf-8")
    assert "\nprod-up:" in makefile
    assert "-f docker-compose.prod.yml up -d --build" in makefile

    # Dev stack keeps the API port open and has no Caddy service.
    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "caddy" not in compose["services"]
    assert compose["services"]["api"]["ports"] == ["${API_PORT:-9000}:${API_PORT:-9000}"]


def test_caddy_requires_nothing_but_is_standalone(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "caddyonly"
    _engine(core_template, modules_dir).generate(target, "caddyonly", ["caddy"], core_version="1.0.0")
    assert (target / "Caddyfile").exists()
    assert (target / "docker-compose.prod.yml").exists()


def test_generate_redis_cache_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withcache"
    _engine(core_template, modules_dir).generate(target, "withcache", ["redis", "redis-cache"], core_version="1.0.0")
    assert (target / "src/withcache/infrastructure/redis/cache.py").exists()
    config = (target / "src/withcache/core/config.py").read_text(encoding="utf-8")
    assert "CacheSettings" in config
    assert "cache:" in config


def test_generate_rustfs_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withrustfs"
    _engine(core_template, modules_dir).generate(target, "withrustfs", ["s3", "rustfs"], core_version="1.0.0")
    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "rustfs" in compose["services"]
    assert compose["services"]["rustfs"]["image"].startswith("rustfs/rustfs:")
    assert compose["services"]["rustfs"]["image"] != "rustfs/rustfs:latest"
    assert "rustfsdata" in compose["volumes"]
    assert "S3_ENDPOINT_URL=http://rustfs:9000" in (target / "docker-compose.yml").read_text(encoding="utf-8")
    assert "RUSTFS_CONSOLE_ENABLE=${RUSTFS_CONSOLE_ENABLE:-false}" in (target / "docker-compose.yml").read_text(
        encoding="utf-8"
    )


def test_generate_opentelemetry_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withotel"
    _engine(core_template, modules_dir).generate(target, "withotel", ["opentelemetry"], core_version="1.0.0")
    assert (target / "src/withotel/infrastructure/telemetry/setup.py").exists()
    main_py = (target / "src/withotel/__main__.py").read_text(encoding="utf-8")
    assert "setup_tracing" in main_py
    compose = yaml.safe_load((target / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "otel-collector" in compose["services"]
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "opentelemetry-api" in pyproject


def test_generate_external_api_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withext"
    _engine(core_template, modules_dir).generate(target, "withext", ["external-api"], core_version="1.0.0")
    assert (target / "src/withext/adapters/external_api/client.py").exists()
    assert (target / "src/withext/services/external_api.py").exists()
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "aiohttp" in pyproject
    # Module must not pull httpx; core/dev may still list httpx in dependency-groups only.
    deps_block = pyproject.split("[dependency-groups]")[0]
    assert "httpx" not in deps_block


def test_generate_oauth_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withoauth"
    _engine(core_template, modules_dir).generate(target, "withoauth", ["redis", "auth", "oauth"], core_version="1.0.0")
    assert (target / "src/withoauth/services/oauth.py").exists()
    assert (target / "src/withoauth/model/oauth_account.py").exists()
    routes = (target / "src/withoauth/api/v1/routes.py").read_text(encoding="utf-8")
    assert "oauth_router" in routes
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "authlib" in pyproject


def test_generate_scheduler_api_module(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    target = tmp_path / "withsched"
    _engine(core_template, modules_dir).generate(
        target, "withsched", ["redis", "celery", "celery-beat", "auth", "scheduler-api"], core_version="1.0.0"
    )
    assert (target / "src/withsched/services/scheduler.py").exists()
    app = (target / "src/withsched/infrastructure/celery/app.py").read_text(encoding="utf-8")
    assert "redbeat.RedBeatScheduler" in app
    routes = (target / "src/withsched/api/v1/routes.py").read_text(encoding="utf-8")
    assert "scheduler_router" in routes
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "celery-redbeat" in pyproject
    service = (target / "src/withsched/services/scheduler.py").read_text(encoding="utf-8")
    assert "_assert_allowed_task" in service
    assert r"withsched\.tasks\." in service or "withsched.tasks." in service


def test_expand_requirements_rejects_path_traversal(modules_dir: Path) -> None:
    from generator.profiles import expand_requirements

    with pytest.raises(GenerationError, match="Invalid module id"):
        expand_requirements({"../etc"}, modules_dir)


def test_sandboxed_jinja_blocks_attribute_traversal(tmp_path: Path, core_template: Path) -> None:
    from jinja2.exceptions import SecurityError

    modules = tmp_path / "modules"
    evil = modules / "evil-ssti"
    (evil / "files").mkdir(parents=True)
    (evil / "manifest.yaml").write_text(
        "manifest_version: '1'\n"
        "id: evil-ssti\n"
        "version: '1.0.0'\n"
        "requires: {core: '>=1.0.0', modules: []}\n"
        "conflicts: []\n"
        "python_dependencies: []\n"
        "patches: []\n",
        encoding="utf-8",
    )
    (evil / "files" / "pwned.txt.jinja").write_text(
        "{{ project_name.__class__.__mro__ }}",
        encoding="utf-8",
    )
    target = tmp_path / "ssti"
    with pytest.raises(SecurityError):
        _engine(core_template, modules).generate(target, "ssti", ["evil-ssti"], core_version="1.0.0")


def test_write_ci_destination_containment(tmp_path: Path) -> None:
    from generator.errors import GenerationError
    from generator.paths_safe import ensure_inside

    target = tmp_path / "proj"
    target.mkdir()
    with pytest.raises(GenerationError, match="escapes"):
        ensure_inside(target, target / ".." / "outside.yml", label="CI destination")


def test_generate_auth_oauth_service_profile(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    from generator.profiles import resolve_profile

    modules = resolve_profile("auth-oauth-service", modules_dir)
    assert modules is not None
    target = tmp_path / "oauthprof"
    _engine(core_template, modules_dir).generate(target, "oauthprof", modules, core_version="1.0.0")
    oauth = (target / "src/oauthprof/services/oauth.py").read_text(encoding="utf-8")
    assert "code_challenge" in oauth
    assert "getdel" in oauth
    assert "issue_tokens" in oauth
    assert "email_verified" in oauth


def test_generate_observability_profile(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    from generator.profiles import resolve_profile

    modules = resolve_profile("observability", modules_dir)
    assert modules is not None
    target = tmp_path / "obsprof"
    _engine(core_template, modules_dir).generate(target, "obsprof", modules, core_version="1.0.0")
    assert (target / "src/obsprof/infrastructure/telemetry/setup.py").exists()
    assert (target / "src/obsprof/infrastructure/prometheus/instrumentation.py").exists()
    metrics = (target / "src/obsprof/infrastructure/prometheus/instrumentation.py").read_text(encoding="utf-8")
    assert "Bearer" in metrics


def test_generate_full_profile(tmp_path: Path, core_template: Path, modules_dir: Path) -> None:
    from generator.profiles import resolve_profile

    modules = resolve_profile("full", modules_dir)
    assert modules is not None
    target = tmp_path / "fullprof"
    _engine(core_template, modules_dir).generate(target, "fullprof", modules, core_version="1.0.0")
    assert (target / "src/fullprof/services/oauth.py").exists()
    assert (target / "src/fullprof/services/scheduler.py").exists()
    assert (target / "src/fullprof/infrastructure/redis/cache.py").exists()
