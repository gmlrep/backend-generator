from pathlib import Path

from click.testing import CliRunner

from generator.cli import cli
from generator.paths import generator_data_root, load_core_version, resolve_paths


def _invoke(tmp_path: Path, core_template: Path, modules_dir: Path, extra: list[str], name: str):
    runner = CliRunner()
    return runner.invoke(
        cli,
        [
            "create",
            "--name",
            name,
            "--non-interactive",
            "--core-template",
            str(core_template),
            "--modules-dir",
            str(modules_dir),
            *extra,
        ],
    )


def test_generator_data_root_finds_repo_layout() -> None:
    root = generator_data_root()
    assert (root / "generator-core" / "core_template").is_dir()
    assert load_core_version(root) == "1.0.0"
    core, modules, data_root = resolve_paths(None, None)
    assert core == data_root / "generator-core" / "core_template"
    assert modules == data_root / "generator-core" / "modules"


def test_generator_data_root_installed_layout(tmp_path: Path, monkeypatch) -> None:
    """Simulate a wheel install: generator package beside generator-core."""
    site = tmp_path / "site-packages"
    pkg = site / "generator"
    pkg.mkdir(parents=True)
    fake_paths = pkg / "paths.py"
    fake_paths.write_text("# placeholder\n", encoding="utf-8")
    core = site / "generator-core" / "core_template"
    core.mkdir(parents=True)
    (site / "generator-core" / "VERSION").write_text("1.0.0\n", encoding="utf-8")

    monkeypatch.setattr("generator.paths.__file__", str(fake_paths))
    assert generator_data_root() == site
    assert load_core_version(site) == "1.0.0"


def test_cli_non_interactive_create(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "auth-service"], "cli_service")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cli_service" / "src" / "cli_service" / "__main__.py").exists()
    # Mandatory post-steps always run.
    assert (tmp_path / "cli_service" / ".env.example").exists()
    assert (tmp_path / "cli_service" / ".env").exists()
    assert "[env] .env.example created." in result.output
    assert "[env] .env created from .env.example." in result.output
    assert "[ruff]" in result.output


def test_cli_rejects_invalid_name(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, [], "1-bad-name")
    assert result.exit_code != 0
    assert "Project name must start with a letter" in result.output


def test_cli_rejects_hyphenated_name(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, [], "payments-service")
    assert result.exit_code != 0
    assert "no hyphens" in result.output


def test_cli_rejects_existing_target(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "keep.txt").write_text("x", encoding="utf-8")
    result = _invoke(tmp_path, core_template, modules_dir, [], "taken")
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_cli_env_example(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "auth-service"], "wenv")
    assert result.exit_code == 0, result.output
    env = (tmp_path / "wenv" / ".env.example").read_text(encoding="utf-8")
    assert "JWT_SECRET" in env
    assert "JWT_ALGORITHM" in env
    # List settings must use JSON syntax so pydantic-settings can decode them.
    assert 'CORS_ALLOWED_ORIGINS=["*"]' in env
    # Module env groups are separated by a blank line from core and each other.
    assert "\n\nREDIS_HOST=" in env
    assert "\n\nJWT_SECRET=" in env
    # .env is seeded as a copy of .env.example so docker-compose can load it.
    dotenv = (tmp_path / "wenv" / ".env").read_text(encoding="utf-8")
    assert dotenv == env
    assert "JWT_SECRET" in dotenv


def test_cli_env_example_omits_jwt_without_auth(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "minimal"], "noauth")
    assert result.exit_code == 0, result.output
    env = (tmp_path / "noauth" / ".env.example").read_text(encoding="utf-8")
    assert "JWT_SECRET" not in env
    assert "POSTGRES_USER" in env


def test_cli_venv_flag_calls_setup(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    called: dict[str, Path] = {}

    def fake_setup(target_dir: Path) -> bool:
        called["target"] = target_dir
        return True

    monkeypatch.setattr("generator.postprocess.setup_venv", fake_setup)
    result = _invoke(tmp_path, core_template, modules_dir, ["--venv"], "venv_app")
    assert result.exit_code == 0, result.output
    assert called["target"] == tmp_path / "venv_app"


def test_cli_git_init_and_github_ci_by_default(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "minimal"], "git_app")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "git_app" / ".git").is_dir()
    assert "[git] Initializing repository on 'main' ..." in result.output

    ci_path = tmp_path / "git_app" / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists()
    assert not (tmp_path / "git_app" / ".gitlab-ci.yml").exists()
    ci = ci_path.read_text(encoding="utf-8")
    assert "- main" in ci
    assert "uv run ruff check ." in ci
    assert "uv run ruff format --check ." in ci
    assert "uv run mypy src" in ci
    assert "uv run pytest -q" in ci
    assert "docker build" in ci
    assert "[ci] github CI created." in result.output


def test_cli_gitlab_ci(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "minimal", "--vcs", "gitlab"], "gl_app")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "gl_app" / ".gitlab-ci.yml").exists()
    assert not (tmp_path / "gl_app" / ".github").exists()
    ci = (tmp_path / "gl_app" / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert 'CI_COMMIT_BRANCH == "main"' in ci
    assert "uv run ruff check ." in ci
    assert "uv run mypy src" in ci
    assert "uv run pytest -q" in ci
    assert "[ci] gitlab CI created." in result.output


def test_cli_compose_services_flower_with_celery(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "minimal", "--modules", "celery", "--compose-services", "flower", "--non-interactive"],
        "withflower",
    )
    assert result.exit_code == 0, result.output
    compose = (tmp_path / "withflower" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "flower:" in compose
    assert (tmp_path / "withflower" / "src" / "withflower" / "infrastructure" / "celery" / "app.py").exists()


def test_cli_compose_services_rustfs_with_s3(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "minimal", "--modules", "s3", "--compose-services", "rustfs", "--non-interactive"],
        "withrustfs",
    )
    assert result.exit_code == 0, result.output
    compose = (tmp_path / "withrustfs" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "rustfs:" in compose
    assert "rustfsdata:" in compose


def test_cli_compose_services_kafka_flag(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "minimal", "--modules", "kafka", "--compose-services", "kafka", "--non-interactive"],
        "withkafkac",
    )
    assert result.exit_code == 0, result.output
    compose = (tmp_path / "withkafkac" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "kafka:" in compose
    assert "kafkadata:" in compose


def test_cli_kafka_without_compose_flag_omits_broker(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "minimal", "--modules", "kafka", "--non-interactive"],
        "kafkaclient",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "kafkaclient" / "src" / "kafkaclient" / "infrastructure" / "kafka" / "broker.py").exists()
    compose = (tmp_path / "kafkaclient" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "kafka:" not in compose


def test_cli_messaging_profile_omits_rabbitmq_compose_by_default(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "messaging", "--non-interactive"],
        "msg_ext",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "msg_ext" / "src" / "msg_ext" / "infrastructure" / "rabbitmq" / "broker.py").exists()
    compose = (tmp_path / "msg_ext" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "rabbitmq:" not in compose


def test_cli_interactive_compose_checkbox(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_prompts(
        monkeypatch,
        selects=["create", "messaging", "none"],
        texts=["msg_app"],
        confirms=[False],
        checkboxes=[["rabbitmq"]],
    )
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0, result.output
    compose = (tmp_path / "msg_app" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "rabbitmq:" in compose


def test_cli_interactive_compose_offers_caddy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_prompts(
        monkeypatch,
        selects=["create", "minimal", "none"],
        texts=["caddy_app"],
        confirms=[False],
        checkboxes=[["caddy"]],
    )
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "caddy_app" / "docker-compose.prod.yml").exists()
    assert (tmp_path / "caddy_app" / "Caddyfile").exists()


def test_cli_compose_services_caddy_flag(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "minimal", "--compose-services", "caddy", "--non-interactive"],
        "withcaddy",
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "withcaddy" / "docker-compose.prod.yml").exists()


def test_cli_vcs_none_skips_ci(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "minimal", "--vcs", "none"], "noc_i")
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "noc_i" / ".github").exists()
    assert not (tmp_path / "noc_i" / ".gitlab-ci.yml").exists()
    assert "[ci] Skipped (no repository host selected)." in result.output
    assert (tmp_path / "noc_i" / ".git").is_dir()


def test_cli_no_git_flag(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "minimal", "--no-git"], "nogit_app")
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "nogit_app" / ".git").exists()
    assert "[git]" not in result.output
    # CI is still generated for the selected VCS.
    assert (tmp_path / "nogit_app" / ".github" / "workflows" / "ci.yml").exists()


def test_cli_skips_git_init_when_git_missing(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("generator.postprocess.git_available", lambda: False)
    called: dict[str, bool] = {}

    def fail_if_called(*args: object, **kwargs: object) -> None:
        called["called"] = True
        raise AssertionError("init_git must not be called when git is missing")

    monkeypatch.setattr("generator.postprocess.init_git", fail_if_called)

    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "minimal"], "nogitbin")
    assert result.exit_code == 0, result.output
    assert "git not found in PATH" in result.output
    assert "called" not in called
    assert not (tmp_path / "nogitbin" / ".git").exists()
    # Generation still succeeds and CI is produced.
    assert (tmp_path / "nogitbin" / ".github" / "workflows" / "ci.yml").exists()


def test_cli_explicit_modules_full(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--modules", "auth,items"], "full_app")
    assert result.exit_code == 0, result.output
    container = (tmp_path / "full_app" / "src" / "full_app" / "core" / "container.py").read_text(encoding="utf-8")
    assert "auth_service" in container
    assert "item_service" in container


def test_cli_rejects_path_traversal(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "create",
            "--name",
            "escape",
            "--target",
            str(tmp_path / "outside"),
            "--non-interactive",
            "--core-template",
            str(core_template),
            "--modules-dir",
            str(modules_dir),
        ],
    )
    assert result.exit_code != 0
    assert "inside the current working directory" in result.output
    assert not (tmp_path / "outside").exists()


def test_cli_allow_overwrite(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "regen"
    target.mkdir()
    (target / "stale.txt").write_text("old", encoding="utf-8")
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "auth-service", "--allow-overwrite"],
        "regen",
    )
    assert result.exit_code == 0, result.output
    assert (target / "src" / "regen" / "__main__.py").exists()
    assert not (target / "stale.txt").exists()


def test_cli_list_profiles(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["list-profiles"])
    assert result.exit_code == 0
    assert "auth-service" in result.output
    assert "items-service" in result.output


def test_cli_list_modules(tmp_path: Path, modules_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["list-modules", "--modules-dir", str(modules_dir)])
    assert result.exit_code == 0
    assert "auth" in result.output
    assert "items" in result.output
    # Required modules are shown next to the module id.
    assert "requires:" in result.output


def _patch_prompts(
    monkeypatch,
    *,
    selects: list | None = None,
    texts: list | None = None,
    confirms: list | None = None,
    checkboxes: list | None = None,
    module_checkboxes: list | None = None,
) -> None:
    """Script questionary answers without touching a real terminal."""
    from collections import deque

    queues = {
        "select": deque(selects or []),
        "text": deque(texts or []),
        "confirm": deque(confirms or []),
        "checkbox": deque(checkboxes or []),
        "modules": deque(module_checkboxes if module_checkboxes is not None else checkboxes or []),
    }
    monkeypatch.setattr("generator.prompts.ask_select", lambda *a, **k: queues["select"].popleft())
    monkeypatch.setattr("generator.prompts.ask_text", lambda *a, **k: queues["text"].popleft())
    monkeypatch.setattr("generator.prompts.ask_confirm", lambda *a, **k: queues["confirm"].popleft())
    monkeypatch.setattr("generator.prompts.ask_checkbox", lambda *a, **k: queues["checkbox"].popleft())
    monkeypatch.setattr(
        "generator.prompts.ask_checkbox_with_back",
        lambda *a, **k: queues["modules"].popleft(),
    )


def test_project_name_validator() -> None:
    from generator.prompts import validate_project_name_answer

    assert validate_project_name_answer("valid_name") is True
    assert isinstance(validate_project_name_answer("1bad"), str)
    assert isinstance(validate_project_name_answer("payments-service"), str)


def test_compose_service_choices_includes_brokers_when_selected() -> None:
    from generator.compose_services import compose_service_choices

    values = {c.value for c in compose_service_choices({"rabbitmq"})}
    assert "rabbitmq" in values
    assert "caddy" in values
    assert "kafka" not in values
    assert "flower" not in values

    values = {c.value for c in compose_service_choices({"kafka", "celery"})}
    assert "kafka" in values
    assert "flower" in values
    assert "caddy" in values
    assert "rabbitmq" not in values

    values = {c.value for c in compose_service_choices({"s3"})}
    assert "rustfs" in values
    assert "caddy" in values

    values = {c.value for c in compose_service_choices({"celery", "flower", "caddy", "s3", "rustfs"})}
    assert "flower" not in values
    assert "rustfs" not in values
    assert "caddy" in values
    caddy_choice = next(
        c for c in compose_service_choices({"celery", "flower", "caddy", "s3", "rustfs"}) if c.value == "caddy"
    )
    assert caddy_choice.checked is True

    bare = {c.value for c in compose_service_choices(set())}
    assert bare == {"caddy"}


def test_cli_bare_command_opens_interactive_menu(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_prompts(
        monkeypatch,
        selects=["create", "auth-service", "github"],
        texts=["menu_app"],
        confirms=[False],
        checkboxes=[[]],
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "menu_app" / "src" / "menu_app" / "__main__.py").exists()
    assert (tmp_path / "menu_app" / ".env.example").exists()


def test_cli_interactive_venv_is_created_when_selected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    called: dict[str, Path] = {}

    def fake_setup(target_dir: Path) -> bool:
        called["target"] = target_dir
        return True

    monkeypatch.setattr("generator.postprocess.setup_venv", fake_setup)
    _patch_prompts(
        monkeypatch,
        selects=["create", "auth-service", "github"],
        texts=["venv_app"],
        confirms=[True],
        checkboxes=[[]],
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert called["target"] == tmp_path / "venv_app"


def test_cli_interactive_items_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_prompts(
        monkeypatch,
        selects=["create", "items-service", "github"],
        texts=["valid_name"],
        confirms=[False],
        checkboxes=[[]],
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "valid_name" / "src" / "valid_name" / "__main__.py").exists()
    # items-service pulls auth for protected CRUD
    assert (tmp_path / "valid_name" / "src" / "valid_name" / "core" / "security.py").exists()


def test_cli_interactive_custom_modules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_prompts(
        monkeypatch,
        selects=["create", "custom", "github"],
        texts=["custom_app"],
        confirms=[False],
        checkboxes=[[]],
        module_checkboxes=[["redis", "auth"]],
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    base = tmp_path / "custom_app" / "src" / "custom_app"
    assert (base / "core" / "security.py").exists()
    assert (base / "model" / "user.py").exists()
    assert (base / "infrastructure" / "redis" / "client.py").exists()


def test_cli_interactive_custom_expands_requirements(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_prompts(
        monkeypatch,
        selects=["create", "custom", "github"],
        texts=["custom_mfa"],
        confirms=[False],
        checkboxes=[[]],
        module_checkboxes=[["mfa"]],
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    base = tmp_path / "custom_mfa" / "src" / "custom_mfa"
    assert (base / "model" / "mfa.py").exists()
    # mfa requires auth, redis and email, which are pulled in automatically.
    assert (base / "core" / "security.py").exists()
    assert (base / "adapters" / "email" / "config.py").exists()
    assert (base / "infrastructure" / "redis" / "client.py").exists()
    assert "[modules] Required dependencies added:" in result.output


def test_cli_interactive_custom_modules_backspace_returns_to_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from generator.constants import GO_BACK

    _patch_prompts(
        monkeypatch,
        selects=["create", "custom", "auth-service", "github"],
        texts=["backed_app"],
        confirms=[False],
        checkboxes=[[]],
        module_checkboxes=[GO_BACK],
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "backed_app" / "src" / "backed_app" / "core" / "security.py").exists()


def test_cli_interactive_confirm_overwrite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "existing_app"
    target.mkdir()
    (target / "stale.txt").write_text("old", encoding="utf-8")

    _patch_prompts(
        monkeypatch,
        selects=["create", "auth-service", "github"],
        texts=["existing_app"],
        confirms=[True, False],
        checkboxes=[[]],
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert not (target / "stale.txt").exists()
    assert (target / "src" / "existing_app" / "__main__.py").exists()


def test_cli_rejects_invalid_module_id(tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        ["--profile", "minimal", "--modules", "../evil"],
        "badmod",
    )
    assert result.exit_code != 0
    assert "Invalid module id" in result.output


def test_cli_seeds_flower_and_rustfs_secrets(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        tmp_path,
        core_template,
        modules_dir,
        [
            "--profile",
            "minimal",
            "--modules",
            "celery,s3",
            "--compose-services",
            "flower,rustfs",
            "--non-interactive",
            "--no-git",
        ],
        "secrety",
    )
    assert result.exit_code == 0, result.output
    example = (tmp_path / "secrety" / ".env.example").read_text(encoding="utf-8")
    dotenv = (tmp_path / "secrety" / ".env").read_text(encoding="utf-8")
    assert "FLOWER_BASIC_AUTH=admin:replace-me" in example
    assert "FLOWER_BASIC_AUTH=admin:replace-me" not in dotenv
    assert "FLOWER_BASIC_AUTH=admin:" in dotenv
    assert "RUSTFS_ACCESS_KEY=replace-me" in example
    assert "RUSTFS_ACCESS_KEY=replace-me" not in dotenv
    assert "RUSTFS_ACCESS_KEY=" in dotenv
