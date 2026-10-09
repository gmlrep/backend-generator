# Core Extension Contracts

This document is the stable public contract between the generator and the core
template. Modules may only extend the core through the markers listed here.

| Marker | File | Owner | Contract version | Injected content |
| --- | --- | --- | --- | --- |
| `EXTENSION_ROUTERS` | `src/{{project_name}}/api/v1/routes.py` | core | 1.0 | Import a router and append it to `router_list` |
| `EXTENSION_MODELS` | `src/{{project_name}}/model/__init__.py` | core | 1.0 | Import ORM models so Alembic autogenerate sees them |
| `EXTENSION_TASKS` | `src/{{project_name}}/tasks/__init__.py` | core | 1.0 | Import Celery task modules so they register on the shared app |
| `EXTENSION_CONFIG_IMPORTS` | `src/{{project_name}}/core/config.py` | core | 1.0 | Imports for module-owned settings classes |
| `EXTENSION_CONFIG_CLASSES` | `src/{{project_name}}/core/config.py` | core | 1.0 | Module-owned settings class definitions inlined into the root config |
| `EXTENSION_SETTINGS` | `src/{{project_name}}/core/config.py` | core | 1.0 | Settings fields added to the root `Settings` model |
| `EXTENSION_VALIDATION` | `src/{{project_name}}/core/config.py` | core | 1.0 | Extra checks inside `Settings.validate_for_api` |
| `EXTENSION_CONTAINER_IMPORTS` | `src/{{project_name}}/core/container.py` | core | 1.0 | Module imports used by container providers |
| `EXTENSION_CONTAINER_SERVICES` | `src/{{project_name}}/core/container.py` | core | 1.0 | Provider definitions (repositories, services) |
| `EXTENSION_CONTAINER_WIRING` | `src/{{project_name}}/core/container.py` | core | 1.0 | Module names for `WiringConfiguration` |
| `EXTENSION_DEPENDENCIES` | `src/{{project_name}}/core/dependencies.py` | core | 1.0 | Shared FastAPI dependencies |
| `EXTENSION_MIDDLEWARE` | `src/{{project_name}}/__main__.py` | core | 1.0 | Register module middleware in `AppCreator._add_middleware` |
| `EXTENSION_MAIN_IMPORTS` | `src/{{project_name}}/__main__.py` | core | 1.0 | Imports used by middleware fragments |
| `EXTENSION_APP_SETUP` | `src/{{project_name}}/__main__.py` | core | 1.0 | Post-construction app setup (metrics, instrumentation) |
| `EXTENSION_MAKE` | `Makefile` | core | 1.0 | Extra Makefile targets (production stack, etc.) |
| `EXTENSION_LIFESPAN_IMPORTS` | `src/{{project_name}}/core/lifespan.py` | core | 1.0 | Imports used by lifespan fragments |
| `EXTENSION_LIFESPAN_STARTUP` | `src/{{project_name}}/core/lifespan.py` | core | 1.0 | Resource initialisation before the app starts serving |
| `EXTENSION_LIFESPAN_SHUTDOWN` | `src/{{project_name}}/core/lifespan.py` | core | 1.0 | Resource cleanup after the app stops serving |
| `EXTENSION_COMPOSE_ENV` | `docker-compose.yml` | core | 1.0 | Extra environment variables for the `api` service |
| `EXTENSION_COMPOSE_SERVICES` | `docker-compose.yml` | core | 1.0 | Additional services (redis, elasticsearch, celery worker/beat) |
| `EXTENSION_COMPOSE_DEPENDS_ON` | `docker-compose.yml` | core | 1.0 | Additional `depends_on` entries for the `api` service |
| `EXTENSION_COMPOSE_VOLUMES` | `docker-compose.yml` | core | 1.0 | Additional named volumes |

## Ownership example: auth

JWT issuance and password hashing are **not** part of the core. The `auth`
module owns them and injects them into the core extension points:

| Concern | Provided by | Extension point |
| --- | --- | --- |
| `SecuritySettings` (JWT secret/keys/ttl) | `auth` → inline in `core/config.py` | `EXTENSION_CONFIG_IMPORTS` + `EXTENSION_CONFIG_CLASSES` + `EXTENSION_SETTINGS` |
| Strong-secret check in production | `auth` fragment | `EXTENSION_VALIDATION` |
| `create_access_token`, `decode_jwt`, `JWTBearer` | `auth` → `core/security.py` | module `files/` (no marker needed) |
| `get_password_hash`, `verify_password` | `auth` → `core/security.py` | module `files/` (no marker needed) |
| `pyjwt`, `pwdlib[bcrypt]` dependencies | `auth` manifest `python_dependencies` | `pyproject.toml` merge |
| `.env` / `.env.example` JWT variables | `auth` manifest `env_example` (Compose reads `.env`; secrets are not hardcoded) | post-step env write |
| MFA challenge on sign-in (when user opted in) | `mfa` rebinds `auth_service` with `mfa_service` | `EXTENSION_CONTAINER_SERVICES` |
| unit/integration tests | `auth` → `tests/unit_tests/auth/`, `tests/integration_tests/auth/` | module `files/` |

## Test contribution

Modules add tests as plain files; core owns the shared package markers and the
`client` fixture:

- core → `tests/conftest.py`, `tests/unit_tests/__init__.py`, `tests/integration_tests/__init__.py`
- module → `tests/unit_tests/<module>/…`, `tests/integration_tests/<module>/…`

Because each module owns its own subpackage, two modules can never collide on a
test path. The generated root `pyproject.toml` enables `pytest-asyncio` in
`auto` mode and the suite runs on `sqlite+aiosqlite:///:memory:`.

Core files are copied as-is; module `files/` are merged before fragments are
injected, so core can reference module-owned helpers without importing them.

## Validation rules

The generator aborts **before writing any file** when:

1. A marker listed in `BUILTIN_CONTRACTS` is missing from its core file.
2. A marker appears more than once in its core file (template integrity broken).
3. A target file referenced by a module patch does not exist.

Marker matching is line based: a marker is the exact comment
`# EXTENSION_<NAME>` (leading/trailing spaces are tolerated). Mentions inside
docstrings do not count.

## Package roots owned by core

To let several modules contribute files without colliding, core owns the
following package markers:

- `src/{{project_name}}/adapters/__init__.py`
- `src/{{project_name}}/infrastructure/__init__.py`
- `src/{{project_name}}/tasks/__init__.py`

Modules only add subpackages/modules below these roots (for example
`adapters/email/`, `infrastructure/redis/`, `tasks/notifications.py`).

## Module-owned extension points

A module may also expose a marker in a file it owns and let another module
patch it. These markers are not core contracts (they live outside
`core_template`), but the generator resolves them the same way because patch
targets are matched after all module files are copied:

| Marker | File | Owner | Injected content |
| --- | --- | --- | --- |
| `EXTENSION_CELERY_BEAT` | `src/{{project_name}}/infrastructure/celery/app.py` | `celery` | Apply the periodic `beat_schedule` |

## Why markers instead of AST rewriting

- The core is a normal, runnable FastAPI project at all times.
- Insertions are textual, deterministic and easy to audit and test.
- Only `.jinja` files are rendered; all other files are copied byte-for-byte,
  so braces in Python/config files are never interpreted by Jinja.

## Adding a new contract

1. Add a marker comment to the core template file.
2. Register it in `src/generator/contracts.py` (`BUILTIN_CONTRACTS`).
3. Document it in the table above.
4. Add a negative test for a missing/duplicated marker.
