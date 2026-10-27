# Distributed Job Scheduler

A production-inspired distributed job scheduling platform for reliably executing asynchronous background jobs across multiple workers — built with FastAPI, PostgreSQL, and React. Concurrency safety comes from Postgres row-locking (`FOR UPDATE SKIP LOCKED`) rather than an external broker like Redis, Celery, or RabbitMQ.

**Repository:** https://github.com/Aki-max-min/distributed-job-scheduler

---

## Architecture Overview

Full system, ER, sequence, worker-flow, and retry-flow diagrams live in [`docs/architecture.md`](docs/architecture.md). Database rationale lives in [`docs/schema.md`](docs/schema.md).

```
Client (React) ──HTTP──▶ FastAPI Backend ──▶ PostgreSQL
                                                  ▲
                                                  │  (poll + atomic claim)
                                          Worker Service(s)
```

- Backend exposes REST APIs for auth, projects, queues, and jobs
- Worker service(s) poll the `jobs` table, atomically claim work with row-level locking, execute concurrently, and send heartbeats
- Dashboard reads aggregated stats from a single endpoint rather than the frontend composing multiple queries

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy, PostgreSQL, JWT auth, APScheduler |
| Frontend | React, Vite, Tailwind CSS, Recharts |
| Worker | Python `ThreadPoolExecutor` + `FOR UPDATE SKIP LOCKED` atomic claiming |
| Testing | Pytest (FastAPI `TestClient` + a real Postgres test database) |

## Project Structure

```
backend/
  app/
    routers/          # auth, projects, queues, jobs, dashboard
    models.py          # SQLAlchemy models (11 tables)
    worker_service.py  # polling, atomic claim, heartbeats, graceful shutdown
    retry_logic.py      # pure backoff + dead-letter routing logic
    scheduler_service.py
    security.py         # JWT auth
  run_worker.py
  tests/                # retry_logic unit tests
frontend/
  src/
    pages/              # Dashboard, Projects, QueueDetail, Workers, DeadLetter, JobLogs, Login
    components/
    context/
tests/                  # auth, job creation, atomic worker-claim tests
docs/
  architecture.md
  schema.md
```

## Setup Instructions

Clone the repo first. Every command below runs from inside it:

```bash
git clone https://github.com/Aki-max-min/distributed-job-scheduler.git
cd distributed-job-scheduler
```

You also need **PostgreSQL running on `localhost:5432`**, with a `postgres` user whose password is `postgres`.
Those are the values in `backend/.env.example`. The backend and the worker both connect to it when they start.

### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r ../requirements.txt
cp .env.example .env
psql -h localhost -U postgres -c "CREATE DATABASE job_scheduler;"
uvicorn app.main:app --reload --port 8000
```
Interactive API docs: `http://127.0.0.1:8000/docs`

### Worker
```bash
cd backend
python run_worker.py --concurrency 3
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```
App: `http://localhost:5173`

### Tests

With the backend virtualenv active. The tests use their own database, so your data is never touched:

```bash
psql -h localhost -U postgres -c "CREATE DATABASE job_scheduler_test;"
(cd backend && pytest tests)          # retry logic
PYTHONPATH=backend pytest tests       # API and worker-claim tests, from the repo root
```

## API Summary

| Resource | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login` |
| Projects | `POST /projects`, `GET /projects`, `GET /projects/{id}`, `DELETE /projects/{id}` |
| Queues | `POST`, `GET`, `PATCH` (pause/resume, concurrency), `DELETE` — under `/projects/{id}/queues` |
| Jobs | `POST` (create), `GET` (paginated, filter by status/type), `GET /{id}`, `DELETE /{id}` (cancel) — under `.../queues/{id}/jobs` |
| Dashboard | `GET /dashboard/stats` — job status counts, per-queue summary, worker list, DLQ count |

All endpoints except `/auth/*` and `/health` require a JWT bearer token. Full request/response schemas are in the Swagger UI at `/docs`.

## Scheduler Features

| Job Type | Behavior |
|---|---|
| Immediate | Eligible for claim as soon as created |
| Delayed | Requires `delay_seconds`; eligible after that offset |
| Scheduled | Requires a future `run_at` timestamp |
| Recurring | Driven by a cron expression via APScheduler |
| Batch | Creates N independent job rows from one request, each claimed and executed independently |

Queues support pause/resume, per-queue concurrency limits, and a per-queue retry policy (fixed, linear, or exponential backoff) assigned automatically on creation.

## Worker Architecture

- Polls for eligible jobs (`status = pending AND run_at <= now()`) on a fixed interval
- Claims atomically with `SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent workers never double-claim the same row — enforced at the Postgres row-lock level, not in application code
- Executes claimed jobs concurrently via a thread pool sized by `--concurrency`
- Sends heartbeats on a separate thread; job logs are written per execution
- Handles `SIGINT`/`SIGTERM` for graceful shutdown — finishes in-flight jobs before exiting
- On failure, delegates to `retry_logic.py`, which either reschedules with a backoff delay or routes the job to the Dead Letter Queue once `max_attempts` is exhausted

## Database Design Overview

11 tables: `organizations`, `users`, `projects`, `queues`, `retry_policies`, `jobs`, `scheduled_jobs`, `workers`, `worker_heartbeats`, `job_executions`, `job_logs`, `dead_letter_queue`.

- All entities use UUID primary keys
- Cascading deletes scoped by ownership (e.g., deleting an organization cascades to its projects; deleting a project cascades to its queues)
- The `jobs` table has a composite index on `(status, run_at, priority)` — the exact predicate and order the worker's claim query uses
- Check constraints enforce valid `job_type`, `status`, `role`, and `retry strategy` values at the database layer, not just in application code

Full rationale (normalization decisions, index choices, cascade behavior) is in [`docs/schema.md`](docs/schema.md).

## Screenshots

*(see Task 3 for the specific list — to be added under `docs/screenshots/`)*

## Design Decisions

| Decision | Reasoning |
|---|---|
| No message broker | A single `jobs` table with atomic row-claiming removes the operational cost of running Redis/Celery/RabbitMQ while still guaranteeing no double-processing |
| All 5 job types resolve to one `run_at` | Keeps the worker's claim logic identical regardless of job type — complexity lives in job creation, not in the hot path |
| Retry logic as a pure function | `retry_logic.py` has no DB access in its core computation, so backoff math is unit-testable without a database |
| No Alembic migrations | `Base.metadata.create_all()` was sufficient for this project's scope — documented tradeoff, not an oversight |

## Testing

Pytest suite covering the reliability-critical paths:
- **Auth** — registration, duplicate-email rejection, login, protected-route enforcement
- **Jobs** — creation across job types, validation (e.g., delayed jobs require `delay_seconds`), batch job row creation
- **Worker claiming** — exercises the actual `FOR UPDATE SKIP LOCKED` SQL against a real Postgres test database, asserting a claimed job is marked `running` with the correct `claimed_by` and `attempt_count`
- **Retry logic** — backoff calculation for fixed/linear/exponential strategies

Run with:
```bash
pytest
```

## Future Improvements

- Dead-worker detection via missed heartbeats (currently workers are tracked, but a stale heartbeat doesn't yet trigger reassignment)
- Real task execution registry in place of the current simulated (random success/failure) execution
- Multi-tenant scoping for workers (currently global, not org-scoped)
- Dedicated endpoint to manually retry a job from the Dead Letter Queue

## License

MIT — see [`LICENSE`](LICENSE).

## GitHub Repository

https://github.com/Aki-max-min/distributed-job-scheduler
