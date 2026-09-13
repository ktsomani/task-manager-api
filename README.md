# Task Manager REST API

A standalone Python/FastAPI project for managing personal tasks, with PostgreSQL persistence, async SQLAlchemy, JWT authentication, and Alembic migrations. It has its own schema, Docker stack, tests, and CI workflow.

## Features

- Register, log in, rotate refresh tokens, log out, and view your profile.
- Create, list, read, update, and delete tasks. Every query checks ownership.
- Task status: `todo`, `in_progress`, `done`. Priority: `low`, `medium`, `high`.
- Optional description and timezone-aware due date; completion timestamps update when tasks are completed or reopened.
- Filter by status, priority, title search, and overdue state; paginate with `limit` and `offset`.
- ETag version checks prevent stale updates and deletions. PostgreSQL row locks serialize competing writes.
- JWT validation, bcrypt hashing in a thread pool, refresh-token reuse revocation, request IDs, and sanitized validation errors.

## Start

Copy `.env.example` to `.env`. Generate a unique `JWT_SECRET` using `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Set a URL-safe database password, then run:

```sh
docker compose up --build -d --wait
```

Open [API documentation](http://localhost:8001/docs). This stack uses port **8001**, its own Compose project, and a separate database volume, so it can coexist with the earlier backend. PostgreSQL is internal to Docker; to run the API directly, supply a reachable PostgreSQL URL. There are no Redis or Celery dependencies.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/auth/register` | Create account |
| POST | `/api/v1/auth/login` | Issue access and refresh tokens |
| POST | `/api/v1/auth/refresh` | Rotate refresh token |
| POST | `/api/v1/auth/logout` | Revoke login session |
| GET | `/api/v1/users/me` | View profile |
| POST | `/api/v1/tasks` | Create task |
| GET | `/api/v1/tasks` | Filter and paginate own tasks |
| GET | `/api/v1/tasks/{id}` | Read task and ETag |
| PATCH | `/api/v1/tasks/{id}` | Partially update task with If-Match |
| DELETE | `/api/v1/tasks/{id}` | Delete task with If-Match |
| GET | `/health/live` | Process liveness |
| GET | `/health/ready` | Database connectivity |

Register and log in with JSON `{"email":"alice@example.com","password":"a-long-unique-password"}`. Use `Authorization: Bearer <access_token>` on protected requests. Passwords require 12 characters and at most 72 UTF-8 bytes.

Create a task:

```json
{"title":"Prepare interview","description":"Practice SQL joins","priority":"high","due_at":"2026-09-20T09:00:00Z"}
```

Creation returns `201`, a `Location` header, and an `ETag`, initially `"1"`. To update, send `PATCH` with `If-Match: "1"` and `{"status":"done"}`. Save the replacement ETag for the next change. Missing preconditions return `428`; a stale version returns `412`. Read the latest task before retrying. Set `description` or `due_at` to `null` to clear them; title, status, and priority cannot be null. Delete also requires the current ETag.

Example filtered listing: `/api/v1/tasks?status=todo&priority=high&overdue=true&limit=20&offset=0`. The response contains `items`, `total`, `limit`, and `offset`. Search treats `%` and `_` literally. Ordering is newest first, with ID as a stable tie-breaker.

## Develop and test

```sh
python -m venv .venv
# Activate .venv for your shell
pip install -r requirements-dev.lock
pip install --no-deps -e .
pytest -q
ruff check .
ruff format --check .
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

Tests use isolated temporary SQLite databases by default. Set `TEST_DATABASE_URL` to a **disposable PostgreSQL database** to run the concurrent-write test; fixtures drop and recreate application tables. GitHub Actions runs PostgreSQL tests, migrations, lint, and a Docker image build.

Code is organized into HTTP routes (`api.py`), business workflows (`services.py`), queries (`repositories.py`), ORM models, and Pydantic schemas. Runtime and development dependencies are pinned separately. Apply migrations before starting the API; do not use ORM `create_all` for deployment.

## Deployment boundaries

No hosting or GitHub repository is provisioned by these files. Before exposing the API publicly, configure TLS, proxy-level authentication rate limiting, request-size limits, secrets, backups, monitoring, and data retention. This project does not include password recovery, email verification, or MFA. Production settings require PostgreSQL and bcrypt cost 12 or higher. Access tokens expire after 15 minutes; refresh sessions have an absolute seven-day lifetime. Serialize refresh calls: reuse of a rotated token revokes the login session. Use a different JWT secret from other applications; the issuer and audience are specific to this project.

SQLite tests do not establish PostgreSQL concurrency guarantees. Docker execution depends on an available Docker Engine.
