# Local Development Services

This repo can run its local data services with Docker:

- Postgres 16 with pgvector
- Valkey 7.2

pgAdmin is still useful as the GUI for Postgres. Docker runs the server;
pgAdmin connects to it.

## Start Services

Open Docker Desktop first, then run from the repo root:

```powershell
docker compose -f docker-compose.local.yml up -d
```

Check status:

```powershell
docker compose -f docker-compose.local.yml ps
```

## Apply Migrations

```powershell
docker exec -i flashback-postgres psql -U flashback -d flashback -v ON_ERROR_STOP=1 -f /migrations/0001_initial_schema.up.sql
docker exec -i flashback-postgres psql -U flashback -d flashback -v ON_ERROR_STOP=1 -f /migrations/0002_seed_starter_questions.up.sql
```

Quick check:

```powershell
docker exec -i flashback-postgres psql -U flashback -d flashback -c "SELECT COUNT(*) FROM questions;"
```

Expected result after step 2 migration: `15`.

## Connection Strings

Use these locally:

```powershell
$env:DATABASE_URL="postgresql://flashback:flashback@localhost:15432/flashback"
$env:TEST_DATABASE_URL="postgresql://flashback:flashback@localhost:15432/flashback_test"
$env:VALKEY_URL="redis://localhost:6379/0"
```

For this repo's test suite, `TEST_DATABASE_URL` should point at a
throwaway DB because tests drop and recreate the `public` schema.
Create it once:

```powershell
docker exec -i flashback-postgres psql -U flashback -d flashback -c "CREATE DATABASE flashback_test;"
```

## pgAdmin

In pgAdmin, register the Docker-backed Postgres server:

General tab:

```text
Name: Flashback Local
```

Connection tab:

```text
Host name/address: localhost
Port: 15432
Maintenance database: flashback
Username: flashback
Password: flashback
Save password: On
```

No SSH tunnel is needed.

## Stop Services

```powershell
docker compose -f docker-compose.local.yml stop
```

To delete local database and Valkey data:

```powershell
docker compose -f docker-compose.local.yml down -v
```
