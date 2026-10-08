from pathlib import Path

from click.testing import CliRunner

from generator.cli import cli


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
    # .env is seeded as a copy of .env.example so docker-compose can load it.
    dotenv = (tmp_path / "wenv" / ".env").read_text(encoding="utf-8")
    assert dotenv == env
    assert "JWT_SECRET" in dotenv


def test_cli_env_example_omits_jwt_without_auth(
    tmp_path: Path, core_template: Path, modules_dir: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _invoke(tmp_path, core_template, modules_dir, ["--profile", "items-service"], "noauth")
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

    monkeypatch.setattr("generator.cli._setup_venv", fake_setup)
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
    monkeypatch.setattr("generator.cli._git_available", lambda: False)
    called: dict[str, bool] = {}

    def fail_if_called(*args: object, **kwargs: object) -> None:
        called["called"] = True
        raise AssertionError("_init_git must not be called when git is missing")

    monkeypatch.setattr("generator.cli._init_git", fail_if_called)

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
) -> None:
    """Script questionary answers without touching a real terminal."""
    from collections import deque

    queues = {
        "select": deque(selects or []),
        "text": deque(texts or []),
        "confirm": deque(confirms or []),
        "checkbox": deque(checkboxes or []),
    }
    monkeypatch.setattr("generator.cli._ask_select", lambda *a, **k: queues["select"].popleft())
    monkeypatch.setattr("generator.cli._ask_text", lambda *a, **k: queues["text"].popleft())
    monkeypatch.setattr("generator.cli._ask_confirm", lambda *a, **k: queues["confirm"].popleft())
    monkeypatch.setattr("generator.cli._ask_checkbox", lambda *a, **k: queues["checkbox"].popleft())


def test_project_name_validator() -> None:
    from generator.cli import _validate_project_name_answer

    assert _validate_project_name_answer("valid_name") is True
    assert isinstance(_validate_project_name_answer("1bad"), str)


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

    monkeypatch.setattr("generator.cli._setup_venv", fake_setup)
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
    # items-service profile: no auth-owned security module
    assert not (tmp_path / "valid_name" / "src" / "valid_name" / "core" / "security.py").exists()


def test_cli_interactive_custom_modules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _patch_prompts(
        monkeypatch,
        selects=["create", "custom", "github"],
        texts=["custom_app"],
        confirms=[False],
        checkboxes=[["redis", "auth"]],
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
        checkboxes=[["mfa"]],
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
