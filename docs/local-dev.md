# Local Development Services

This repo can run its local infrastructure with Docker:

- Postgres 16 with pgvector
- Valkey 7.2
- LocalStack SQS with all Flashback queues pre-created

The Python HTTP service and workers can run from your host shell. That keeps
real OpenAI, Anthropic, and Voyage calls straightforward because they read the
same `.env.local` file as the rest of local development.

## Start Infrastructure

Open Docker Desktop first, then run from the repo root:

```powershell
docker compose -f docker-compose.local.yml up -d
docker compose -f docker-compose.local.yml ps
```

LocalStack runs the queue init script at
`scripts/localstack/init-sqs.sh`. It creates eight queues:

```text
flashback-extraction
flashback-embedding
flashback-artifact
flashback-thread-detector
flashback-trait-synthesizer
flashback-profile-summary
flashback-producers-per-session
flashback-producers-weekly
```

## Apply Migrations

Apply all migrations:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local/apply-migrations.ps1
```

For an existing local volume that already has early migrations, resume from a
specific migration prefix:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local/apply-migrations.ps1 -StartAt 0003
```

For a disposable local database, reset and re-apply everything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local/apply-migrations.ps1 -Reset
```

Quick check:

```powershell
docker exec -i flashback-postgres psql -U flashback -d flashback -c "SELECT COUNT(*) AS starter_questions FROM questions;"
```

Expected result after migration `0002`: `15`.

## Host Environment

Copy `.env.example` to `.env.local`, keep your real provider keys private, and
use the host-side LocalStack URLs:

```text
DATABASE_URL=postgresql://flashback:flashback@localhost:15432/flashback
VALKEY_URL=redis://localhost:6379/0

AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
SQS_ENDPOINT_URL=http://localhost:4566

EMBEDDING_QUEUE_URL=http://localhost:4566/000000000000/flashback-embedding
EXTRACTION_QUEUE_URL=http://localhost:4566/000000000000/flashback-extraction
ARTIFACT_QUEUE_URL=http://localhost:4566/000000000000/flashback-artifact
THREAD_DETECTOR_QUEUE_URL=http://localhost:4566/000000000000/flashback-thread-detector
TRAIT_SYNTHESIZER_QUEUE_URL=http://localhost:4566/000000000000/flashback-trait-synthesizer
PROFILE_SUMMARY_QUEUE_URL=http://localhost:4566/000000000000/flashback-profile-summary
PRODUCERS_PER_SESSION_QUEUE_URL=http://localhost:4566/000000000000/flashback-producers-per-session
PRODUCERS_WEEKLY_QUEUE_URL=http://localhost:4566/000000000000/flashback-producers-weekly

SERVICE_TOKEN_AUTH_DISABLED=true
```

If a Python process runs inside docker compose instead of on your host, switch
`SQS_ENDPOINT_URL` and every queue URL host from `localhost` to `localstack`.

## Bring-Up Order

Start with the embedding worker and seed embeddings:

```powershell
python -m flashback.workers.embedding run
python -m flashback.workers.embedding backfill --record-type question
```

Run the HTTP service:

```powershell
python -m uvicorn flashback.http.app:create_app --factory --host 0.0.0.0 --port 8000
```

Then start the remaining long-running workers in separate terminals:

```powershell
python -m flashback.workers.extraction run
python -m flashback.workers.thread_detector run
python -m flashback.workers.trait_synthesizer run
python -m flashback.workers.profile_summary run
python -m flashback.workers.producers run-per-session
python -m flashback.workers.producers run-weekly
```

## First End-To-End Run

Create a person:

```powershell
docker exec -i flashback-postgres psql -U flashback -d flashback -c "INSERT INTO persons (name, relationship) VALUES ('Test Subject', 'father') RETURNING id;"
```

Call `/health`, `/session/start`, several `/turn` requests, then
`/session/wrap`. With `SERVICE_TOKEN_AUTH_DISABLED=true`, you do not need the
`X-Service-Token` header locally.

Watch structured logs across all processes for:

```text
turn_complete
extraction_complete
session_wrap_complete
```

Queue depth is easiest to inspect from the LocalStack container:

```powershell
docker exec -i flashback-localstack awslocal sqs list-queues
```

## Test Database

For DB-touching tests, point `TEST_DATABASE_URL` at a throwaway database because
the suite drops and recreates the `public` schema:

```powershell
docker exec -i flashback-postgres psql -U flashback -d flashback -c "CREATE DATABASE flashback_test;"
```

## Stop Services

```powershell
docker compose -f docker-compose.local.yml stop
```

To delete local Postgres, Valkey, and LocalStack data:

```powershell
docker compose -f docker-compose.local.yml down -v
```
