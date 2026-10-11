"""Post-generation steps: env files, ruff, venv, git, CI."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import click
import jinja2
from jinja2.sandbox import SandboxedEnvironment

from generator.manifest import parse_manifest
from generator.paths_safe import ensure_inside, validate_module_id
from generator.status import status


def _jinja_env() -> SandboxedEnvironment:
    return SandboxedEnvironment(undefined=jinja2.StrictUndefined)


def _seed_runtime_secrets(content: str, module_ids: list[str]) -> str:
    """Replace weak placeholder secrets in ``.env`` (not ``.env.example``)."""
    selected = set(module_ids)
    lines = content.splitlines()
    out: list[str] = []
    rustfs_key: str | None = None
    for line in lines:
        if "flower" in selected and line.startswith("FLOWER_BASIC_AUTH="):
            out.append(f"FLOWER_BASIC_AUTH=admin:{secrets.token_urlsafe(16)}")
            continue
        if "rustfs" in selected and (
            line.startswith("RUSTFS_ACCESS_KEY=")
            or line.startswith("RUSTFS_SECRET_KEY=")
            or line.startswith("S3_ACCESS_KEY=")
            or line.startswith("S3_SECRET_KEY=")
        ):
            if rustfs_key is None:
                rustfs_key = secrets.token_urlsafe(24)
            key_name = line.split("=", 1)[0]
            out.append(f"{key_name}={rustfs_key}")
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def write_env_files(target_dir: Path, module_ids: list[str], modules_path: Path, project_name: str) -> None:
    status("[env] Writing .env.example ...")
    lines = [
        "ENVIRONMENT=local",
        "API_HOST=0.0.0.0",
        "API_PORT=9000",
        "API_DEBUG=false",
        "API_TITLE=FastAPI Service",
        "API_VERSION=0.1.0",
        'CORS_ALLOWED_ORIGINS=["*"]',
        "POSTGRES_USER=postgres",
        "POSTGRES_PASSWORD=postgres",
        "POSTGRES_DB=postgres",
        "POSTGRES_HOST=localhost",
        "POSTGRES_PORT=5432",
        "DB_ECHO=false",
        "LOG_LEVEL=INFO",
    ]
    # Module-owned settings (e.g. auth owns the JWT variables).
    # Blank line between the core block and each module group.
    env = _jinja_env()
    context = {"project_name": project_name}
    modules_path = modules_path.resolve()
    for module_id in module_ids:
        validate_module_id(module_id)
        module_dir = ensure_inside(modules_path, modules_path / module_id, label=f"Module '{module_id}'")
        manifest_path = module_dir / "manifest.yaml"
        if not manifest_path.exists():
            continue
        module_lines = [env.from_string(line).render(context) for line in parse_manifest(manifest_path).env_example]
        if not module_lines:
            continue
        lines.append("")
        lines.extend(module_lines)
    content = "\n".join(lines) + "\n"
    (target_dir / ".env.example").write_text(content, encoding="utf-8", newline="\n")
    status("[env] .env.example created.")
    # Seed a working .env from the example so docker-compose can load it.
    runtime = _seed_runtime_secrets(content, module_ids)
    (target_dir / ".env").write_text(runtime, encoding="utf-8", newline="\n")
    status("[env] .env created from .env.example.")


def find_ruff() -> str | None:
    found = shutil.which("ruff")
    if found:
        return found
    candidate = Path(sys.executable).parent / "ruff"
    if candidate.exists():
        return str(candidate)
    return None


def run_ruff(target_dir: Path) -> bool:
    """Format then check the generated project. Returns ``True`` on success."""
    status("[ruff] Running format and check ...")
    ruff = find_ruff()
    if ruff is None:
        status("[ruff] FAILED: ruff is not installed, check skipped.", ok=False, err=True)
        return False

    subprocess.run([ruff, "format", str(target_dir)], check=False, capture_output=True, text=True)
    subprocess.run([ruff, "check", "--fix", str(target_dir)], check=False, capture_output=True, text=True)
    result = subprocess.run([ruff, "check", str(target_dir)], check=False, capture_output=True, text=True)

    if result.returncode == 0:
        status("[ruff] PASSED: no issues found.")
        return True

    status("[ruff] FAILED: issues remain:", ok=False, err=True)
    click.echo((result.stdout or result.stderr).strip(), err=True)
    return False


def setup_venv(target_dir: Path) -> bool:
    """Create ``.venv`` inside the generated project and install dependencies."""
    venv_dir = target_dir / ".venv"
    status(f"[venv] Creating virtual environment at {venv_dir} ...")

    uv = shutil.which("uv")
    if uv:
        status("[venv] uv found: downloading interpreter and dependencies (uv sync) ...")
        result = subprocess.run([uv, "sync", "--project", str(target_dir)], check=False, text=True)
    else:
        status("[venv] uv not found: falling back to 'python -m venv' + pip ...")
        result = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=False, text=True)
        if result.returncode == 0:
            scripts_dir = "Scripts" if os.name == "nt" else "bin"
            pip = venv_dir / scripts_dir / "pip"
            result = subprocess.run([str(pip), "install", "-e", str(target_dir)], check=False, text=True)

    if result.returncode == 0:
        status("[venv] PASSED: virtual environment is ready.")
        return True

    status("[venv] FAILED: could not create the virtual environment.", ok=False, err=True)
    return False


def git_available() -> bool:
    """Return ``True`` when a ``git`` executable is available on PATH."""
    return shutil.which("git") is not None


def init_git(target_dir: Path, branch: str = "main") -> bool:
    """Initialize a git repository on ``branch`` and make a best-effort initial commit."""
    git = shutil.which("git")
    if git is None:
        status("[git] FAILED: git is not installed, repository not initialized.", ok=False, err=True)
        return False

    status(f"[git] Initializing repository on '{branch}' ...")
    result = subprocess.run([git, "init", "-b", branch], cwd=target_dir, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        status(f"[git] FAILED: {(result.stderr or result.stdout).strip()}", ok=False, err=True)
        return False

    subprocess.run([git, "add", "-A"], cwd=target_dir, check=False, capture_output=True, text=True)
    commit = subprocess.run(
        [git, "commit", "-m", "Initial commit"],
        cwd=target_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if commit.returncode == 0:
        status("[git] Repository initialized with an initial commit.")
    else:
        status("[git] Repository initialized (no commit: configure git user.name/user.email).")
    return True


def write_ci(target_dir: Path, ci_root: Path, vcs: str, main_branch: str, project_name: str) -> None:
    """Copy the CI templates for ``vcs`` into the generated project."""
    src_root = ci_root / vcs
    if not src_root.exists():
        status(f"[ci] No CI templates for '{vcs}' at {src_root}, skipping.", ok=False, err=True)
        return

    status(f"[ci] Adding {vcs} CI (main branch: {main_branch}) ...")
    env = _jinja_env()
    context = {"main_branch": main_branch, "project_name": project_name}
    target_dir = target_dir.resolve()
    for src in sorted(src_root.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(src_root)
        dst = ensure_inside(target_dir, target_dir / rel, label="CI destination")
        if src.suffix == ".jinja":
            final_dst = ensure_inside(target_dir, dst.with_suffix(""), label="CI destination")
            final_dst.parent.mkdir(parents=True, exist_ok=True)
            rendered = env.from_string(src.read_text(encoding="utf-8")).render(context)
            final_dst.write_text(rendered, encoding="utf-8", newline="\n")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    status(f"[ci] {vcs} CI created.")
