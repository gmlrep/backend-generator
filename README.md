# Backend Service Generator

A modular scaffolding engine that generates FastAPI microservices from a
stable core template plus declarative module manifests.

## Features

- **Fixed extension contracts.** The core template exposes named markers
  (`EXTENSION_ROUTERS`, `EXTENSION_MODELS`, `EXTENSION_CONFIG_IMPORTS`,
  `EXTENSION_CONFIG_CLASSES`, `EXTENSION_SETTINGS`, `EXTENSION_VALIDATION`, `EXTENSION_CONTAINER_IMPORTS`,
  `EXTENSION_CONTAINER_SERVICES`, `EXTENSION_CONTAINER_WIRING`,
  `EXTENSION_DEPENDENCIES`, `EXTENSION_TASKS`, `EXTENSION_MIDDLEWARE`,
  `EXTENSION_MAIN_IMPORTS`, `EXTENSION_APP_SETUP`,
  `EXTENSION_LIFESPAN_IMPORTS`, `EXTENSION_LIFESPAN_STARTUP`,
  `EXTENSION_LIFESPAN_SHUTDOWN`, `EXTENSION_COMPOSE_ENV`,
  `EXTENSION_COMPOSE_SERVICES`, `EXTENSION_COMPOSE_DEPENDS_ON`,
  `EXTENSION_COMPOSE_VOLUMES`, `EXTENSION_MAKE`). The generator validates that every marker exists
  exactly once before writing anything.
- **Auth owns JWT and hashing.** JWT issuance, `JWTBearer`, password hashing
  and the `SecuritySettings` live in the `auth` module, not in core. Core stays
  free of auth-specific code.
- **Universal message broker facade.** The `rabbitmq` and `kafka` modules
  both build the dependency-free `MessageBroker` facade
  (`infrastructure/messaging.py`) on top of FastStream, so services call the
  same `publish`/`subscribe` API regardless of the transport.
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
- **Interactive questionary menu.** The bare `bsg-generator` command opens an
  arrow-key menu (project name, service profile with module preview, repository
  host, optional-module checkboxes, venv) instead of numbered prompts.
- **Makefile included.** Every project ships a `Makefile` with `make help`,
  local (`run`, `dev`, `test`, `lint`, `migrate`) and docker
  (`docker-up`, `docker-down`, `docker-logs`) targets.
- **Migrations on container start.** The image entrypoint inspects the
  database: when it has no tables yet it applies `alembic upgrade head`; when
  the schema is already present it skips migrations. Only the `api` service
  runs them (`RUN_MIGRATIONS=true`); workers set `RUN_MIGRATIONS=false`.
- **Git repository by default.** Generated projects are initialized as a git
  repository with an initial commit; pass `--no-git` to opt out. There is no
  interactive prompt for it. If `git` is missing from `PATH`, initialization is
  skipped early with a warning and generation still succeeds.
- **CI for GitHub or GitLab.** The repository host (asked interactively or set
  with `--vcs`) determines the main branch and generates CI only for that host.
  Choose `None` / `--vcs none` to skip CI. The pipeline runs `ruff check`,
  `ruff format --check`, `mypy`, `pytest` and a docker build.

## CLI

```bash
# Interactive menu (no arguments) — arrow-key navigation powered by questionary
uv run bsg-generator
#   ? What would you like to do?  create
#   ? Project name                my_service
#   ? Select a service to generate: auth-service  redis, auth
#   ? Which repository host will you use?  github / gitlab / None
#   ? Add Docker Compose services ...  (checkbox: rabbitmq / kafka / flower / caddy;
#       rabbitmq/kafka modules alone only add the Python client — check here to
#       also run the broker in docker-compose)
#   ? Create a virtual environment and install dependencies?  (y/N)
#   On the custom-modules checkbox, Backspace returns to the service list.
#
# Pick "custom" in the service list to choose any combination of modules
# from a checkbox list (required dependencies are added automatically):
#   ? Select a service to generate: custom  pick any modules manually
#   ? Select modules to include ...  (checkbox: auth, items, mfa, kafka, ...)

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
    redis/            # async Redis client + compose service
    celery/           # Celery app + worker service
    rate-limit/       # global middleware + per-route dependency
    auth/             # JWT access/refresh, OAuth2, Basic, Bearer
    email/            # EmailSender protocol + SMTP adapter
    mfa/              # TOTP + email one-time codes
    logging-elasticsearch/  # Loguru sink shipping to Elasticsearch
    openai/           # OpenAI-compatible text generation adapter
    s3/               # S3-compatible object storage adapter
    push-notifications/     # Firebase push adapter + Celery tasks
    users-profiles/   # user profiles/settings
    roles-permissions/# roles, permissions and guards
    flower/           # Flower UI for Celery (optional)
    celery-beat/      # periodic/cron task scheduler (optional)
    prometheus/       # /metrics endpoint + Prometheus service
    caddy/            # production TLS reverse proxy (docker-compose.prod.yml)
    rabbitmq/         # FastStream RabbitMQ broker + compose service
    kafka/            # FastStream Kafka broker (external broker only)
  profiles.yaml
src/generator/        # contracts, manifest, engine, CLI
tests/                # positive and negative scenarios
```

## Modules and dependency graph

```
redis
 ├── celery ──► push-notifications
 ├── rate-limit ◄── auth (auth also requires rate-limit)
 ├── auth ──► mfa (also needs email; MFA is per-user opt-in)
 └── logging-elasticsearch
email
 ├── mfa
 └── push-notifications
auth ──► users-profiles ──► roles-permissions
items ──► auth
openai, s3            (standalone)
celery ──► flower, celery-beat
prometheus, caddy     (standalone)
rabbitmq XOR kafka    (mutual exclusion; shared MessageBroker facade)
```

## Production (Caddy + TLS)

The `caddy` module adds a production compose override. The dev stack keeps the
API port open and has no Caddy; the production stack closes the public API port
and exposes it only through Caddy over the internal Docker network:

```bash
make prod-up      # docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
make prod-logs
make prod-down
```

Set `DOMAIN` (defaults to `localhost`) and `ACME_EMAIL` in `.env`. A real domain
gets a Let's Encrypt certificate automatically; `localhost` uses Caddy's
internal CA.

Adapters are defined as `typing.Protocol` ports and bound to concrete
implementations in the DI container, so tests inject fakes without touching
business logic (see `docs/contracts.md`).

## Versioning

While the package major version is `0` (beta), versions follow
`0.MINOR.PATCH`: **minor** for features and hardening, **patch** for
hotfixes. The installed tool version is independent of
`generator-core/VERSION` (the core-template compatibility contract).

## Install and run from any directory

```bash
uv tool install .
bsg-generator create --name my_service --profile auth-service --non-interactive
```

The CLI locates packaged `generator-core` next to the `generator` package
(or the repo root in editable installs), so it works outside the source tree.
Override with `BSG_CORE_TEMPLATE` / `BSG_MODULES_DIR` if needed.

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
6. **CI:** generate the pipeline for the selected `--vcs` (GitHub or GitLab),
   or skip when `--vcs none`.
7. **Git** (default on): initialize a repository on the provider's main branch
   and make an initial commit; `--no-git` skips it.

## License

MIT — see [LICENSE](LICENSE).
