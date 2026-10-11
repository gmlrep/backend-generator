<div align="center">

# Backend Service Generator

[![Test](https://img.shields.io/github/actions/workflow/status/gmlrep/backend-generator/ci.yml?branch=main&label=Tests)](https://github.com/gmlrep/backend-generator/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/gmlrep/backend-generator?color=brightgreen)](https://github.com/gmlrep/backend-generator/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Scaffold FastAPI microservices from a stable core template plus declarative
module manifests (`bsg-generator`).

</div>

## Quick start

```bash
# From this repo (editable)
uv sync
uv run bsg-generator

# Or install the tool globally
uv tool install .
bsg-generator
```

Interactive flow: project name → service profile (or custom modules) →
repository host (GitHub / GitLab / None) → optional Docker Compose sidecars →
venv prompt.

```bash
# Non-interactive
bsg-generator create --name my_service --profile auth-service --non-interactive

bsg-generator create --name my_service --profile messaging \
  --compose-services rabbitmq --non-interactive --venv

bsg-generator create --name my_service --non-interactive --vcs none --no-git

bsg-generator list-profiles
bsg-generator list-modules
```

Project names must be valid Python packages: letters, digits, underscores
(no hyphens).

## Features

- Declarative modules under `generator-core/modules/<id>/` (`manifest.yaml`,
  `files/`, `fragments/`) patched into fixed `EXTENSION_*` markers
- Module Python deps merged into `pyproject.toml` only when that module is selected
- Auth, JWT, and password hashing live in the `auth` module (not core)
- Shared `MessageBroker` facade for `rabbitmq` / `kafka` (FastStream)
- Atomic generate (temp dir + `os.replace`), validation before write
- Post-steps: `.env.example` + `.env`, ruff format/check; optional `--venv`
- Git init by default (`--no-git` to skip); CI for `--vcs github|gitlab` or none
- Makefile, Docker image with migration-aware entrypoint, optional Caddy prod override

Extension marker contracts: [docs/contracts.md](docs/contracts.md).

## Docker Compose sidecars

Selecting `rabbitmq` / `kafka` as modules wires the **Python client** only
(external broker). Broker containers, Flower, RustFS, and Caddy are added on the
“Add Docker Compose services” step (or via `--compose-services`):

| Option     | When available                         |
|------------|----------------------------------------|
| `rabbitmq` | `rabbitmq` module selected             |
| `kafka`    | `kafka` module selected                |
| `flower`   | `celery` module selected               |
| `rustfs`   | `s3` module selected                   |
| `caddy`    | always                                 |

## Layout

```
generator-core/
  core_template/      # FastAPI core with EXTENSION_* markers
  modules/            # auth, redis, celery, email, mfa, items, …
  profiles.yaml
  VERSION             # core-template compatibility contract
src/generator/        # contracts, manifest, engine, CLI
tests/
```

## Modules

```
redis ──► redis-cache
redis ──► celery ──► flower, celery-beat ──► scheduler-api (also needs auth)
redis ──► rate-limit ──► auth ──► mfa, oauth
auth ──► users-profiles ──► roles-permissions
items ──► auth
email ──► mfa, push-notifications
s3 ──► rustfs (Compose checkbox)
rabbitmq XOR kafka    (shared MessageBroker facade)
openai, external-api, prometheus, opentelemetry, caddy, logging-elasticsearch
```

Profiles: `minimal`, `auth-service`, `auth-mfa-service`, `auth-oauth-service`,
`items-service`, `users-service`, `notifications-service`, `celery-tools`,
`observability`, `messaging`, `messaging-kafka`, `full`, plus interactive `custom`.

## Production (Caddy + TLS)

The `caddy` module (or Compose checkbox / `--compose-services caddy`) adds
`docker-compose.prod.yml`. Dev keeps the API port open; prod closes it and
fronts TLS via Caddy:

```bash
make prod-up      # -f docker-compose.yml -f docker-compose.prod.yml
make prod-logs
make prod-down
```

Set `DOMAIN` and `ACME_EMAIL` in `.env` (`localhost` uses Caddy’s internal CA).

## Versioning

While major is `0` (beta): `0.MINOR.PATCH` — minor for features/hardening,
patch for hotfixes. Tool version (`pyproject.toml`) is independent of
`generator-core/VERSION`.

Pushing a new `project.version` on `main` creates a GitHub Release
(`.github/workflows/release.yml`).

## Development

```bash
uv sync
uv run pytest
uv run pre-commit install
uv run pre-commit run --all-files
```

Override template paths with `BSG_CORE_TEMPLATE` / `BSG_MODULES_DIR` if needed.

## Generation flow

1. Validate name, target, module graph, conflicts (nothing written yet)
2. Assemble in a temp dir: core + module files + marker patches + dep merge
3. Atomic `os.replace` into the target
4. Write `.env` / `.env.example`, run ruff; optional venv
5. Write CI for selected VCS; init git unless `--no-git`
