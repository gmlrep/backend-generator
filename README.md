# Backend Service Generator

A modular scaffolding engine that generates FastAPI microservices from a
stable core template plus declarative module manifests.

## Features

- **Fixed extension contracts.** The core template exposes named markers
  (`EXTENSION_ROUTERS`, `EXTENSION_MODELS`, `EXTENSION_CONFIG_IMPORTS`,
  `EXTENSION_SETTINGS`, `EXTENSION_VALIDATION`, `EXTENSION_CONTAINER_IMPORTS`,
  `EXTENSION_CONTAINER_SERVICES`, `EXTENSION_CONTAINER_WIRING`,
  `EXTENSION_DEPENDENCIES`). The generator validates that every marker exists
  exactly once before writing anything.
- **Auth owns JWT and hashing.** JWT issuance, `JWTBearer`, password hashing
  and the `SecuritySettings` live in the `auth` module, not in core. Core stays
  free of auth-specific code.
- **Modules ship their own tests.** Each module contributes unit and
  integration tests under `tests/`. The generated suite runs against an
  in-memory SQLite database, so `pytest` works with no external services.
- **`.jinja`-only templating.** Only files with an explicit `.jinja` suffix are
  rendered. Everything else is copied byte-for-byte, so arbitrary `{}` in
  Python or config files is never mangled.
- **Structural config updates.** Python dependencies are merged into
  `pyproject.toml` with `tomlkit`, preserving formatting.
- **Declarative modules.** Each module lives under `generator-core/modules/<id>/`
  with a `manifest.yaml`, `files/` and `fragments/`.
- **Validation-first build.** Module graph, core compatibility, required
  modules, cycles and file conflicts are checked before the temp build starts.
- **Atomic output.** The project is assembled in a temp directory next to the
  target and moved into place with `os.replace`.
- **Mandatory post-steps.** Every generation writes `.env.example` and a
  working `.env` (a copy of the example) so docker-compose can load it, then
  runs a ruff format + check, reporting `[ruff] PASSED` or `[ruff] FAILED`. A
  virtual environment can be created and populated with `--venv` (or the
  interactive prompt).
- **Runnable console script.** Generated projects declare `[project.scripts]`,
  so `uv run <project>` starts the service via `main()` (identical to
  `python -m <project>`).
- **Makefile included.** Every project ships a `Makefile` with `make help`,
  local (`run`, `dev`, `test`, `lint`, `migrate`) and docker
  (`docker-up`, `docker-down`, `docker-logs`) targets.
- **Git repository by default.** Generated projects are initialized as a git
  repository with an initial commit; pass `--no-git` to opt out. There is no
  interactive prompt for it. If `git` is missing from `PATH`, initialization is
  skipped early with a warning and generation still succeeds.
- **CI for GitHub or GitLab.** The repository host (asked interactively or set
  with `--vcs`) determines the main branch and generates CI only for that host.
  The pipeline runs `ruff check`, `ruff format --check`, `mypy`, `pytest` and a
  docker build.

## CLI

```bash
# Interactive menu (no arguments)
uv run bsg-generator
#   What would you like to do?
#     1) create
#   -> project name -> service -> repository host -> create venv?

# Non-interactive (CI). .env.example + ruff always run.
bsg-generator create \
  --name my_service \
  --profile auth-service \
  --non-interactive

# Also create and populate .venv
bsg-generator create --name my_service --profile full --non-interactive --venv

# Skip git initialization (on by default)
bsg-generator create --name my_service --non-interactive --no-git

# Choose the repository host (drives the main branch and CI)
bsg-generator create --name my_service --non-interactive --vcs gitlab

bsg-generator list-profiles
bsg-generator list-modules
```

## Layout

```
generator-core/
  core_template/      # FastAPI core with EXTENSION_* markers
  modules/
    auth/             # auth module: manifest + files + fragments
  profiles.yaml
src/generator/        # contracts, manifest, engine, CLI
tests/                # positive and negative scenarios
```

## Development

```bash
uv sync
uv run pytest

# Install git hooks (ruff, mypy, file checks)
uv run pre-commit install
uv run pre-commit run --all-files
```

Or with a plain virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
pip install pytest pytest-cov ruff mypy
pytest
```

## Generation flow

1. **Validate** the request: project name, target directory, module graph,
   core version compatibility and file conflicts. No bytes are written yet.
2. **Assemble** into a temporary directory next to the target:
   - copy the core, rendering only `*.jinja` files;
   - copy module `files/` (again, only `*.jinja` is rendered);
   - patch `*.jinja` fragments under their markers;
   - merge module dependencies into `pyproject.toml` with `tomlkit`.
3. **Commit** the result with `os.replace` (atomic rename on one filesystem).
4. **Post-process** (always): write `.env.example` and `.env`, then run
   `ruff format` and `ruff check`, logging `[ruff] PASSED` / `[ruff] FAILED`.
5. **Optional:** create `.venv` and install dependencies (`--venv`), logging
   `[venv] ...` progress and the final result.
6. **CI** (always): generate the pipeline for the selected `--vcs` (GitHub or
   GitLab).
7. **Git** (default on): initialize a repository on the provider's main branch
   and make an initial commit; `--no-git` skips it.

## License

MIT — see [LICENSE](LICENSE).
