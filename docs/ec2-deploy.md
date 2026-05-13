# EC2 Agent Deployment

This runbook matches the intended EC2 topology:

- Node already runs on the same instance on port `8000`.
- The Flashback agent API runs on the same instance on port `8005`.
- Valkey runs directly on the EC2 host.
- Postgres is RDS.
- Queues are real AWS SQS.

The smoothest setup here is a Python virtual environment plus `systemd`.

## 1. Prepare EC2

SSH into the instance and verify Python and Valkey:

```bash
python3.11 --version
valkey-cli ping
```

Expected Valkey result:

```text
PONG
```

Keep Valkey local to the instance when Node and the agent are both on the same
host:

```text
VALKEY_URL=redis://127.0.0.1:6379/0
```

## 2. Prepare AWS

RDS:

- Use PostgreSQL with `pgvector` support.
- Allow inbound `5432` from this EC2 instance security group.
- The database user must be able to run:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
```

SQS:

Create these queues in the same AWS region:

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

Attach an IAM role to the EC2 instance with:

```text
sqs:GetQueueAttributes
sqs:ReceiveMessage
sqs:DeleteMessage
sqs:SendMessage
sqs:ChangeMessageVisibility
```

## 3. Install The Agent

Use `/opt/AgentMeeMaw` as the deploy directory:

```bash
cd /opt
sudo git clone <your-repo-url> AgentMeeMaw
sudo chown -R $USER:$USER /opt/AgentMeeMaw
cd /opt/AgentMeeMaw

python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .
```

For updates later:

```bash
cd /opt/AgentMeeMaw
git pull
source .venv/bin/activate
pip install .
```

## 4. Create The Env File

```bash
sudo nano /etc/flashback-agent.env
sudo chmod 600 /etc/flashback-agent.env
```

Use this shape:

```text
DATABASE_URL=postgresql://flashback:replace-with-password@replace-with-rds-endpoint:5432/flashback?sslmode=require
VALKEY_URL=redis://127.0.0.1:6379/0

HTTP_HOST=127.0.0.1
HTTP_PORT=8005
TRUSTED_HOSTS=127.0.0.1,localhost

SERVICE_TOKEN=replace-with-random-token
ADMIN_SERVICE_TOKEN=replace-with-different-random-token
SERVICE_TOKEN_AUTH_DISABLED=false

AWS_REGION=us-east-1

EMBEDDING_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-embedding
EXTRACTION_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-extraction
ARTIFACT_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-artifact
THREAD_DETECTOR_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-thread-detector
TRAIT_SYNTHESIZER_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-trait-synthesizer
PROFILE_SUMMARY_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-profile-summary
PRODUCERS_PER_SESSION_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-producers-per-session
PRODUCERS_WEEKLY_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/flashback-producers-weekly

VOYAGE_API_KEY=replace-with-voyage-key
OPENAI_API_KEY=replace-with-openai-key
ANTHROPIC_API_KEY=replace-with-anthropic-key

EMBEDDING_MODEL=voyage-3-large
EMBEDDING_MODEL_VERSION=2025-01-07
SQS_MAX_MESSAGES=10
SQS_WAIT_SECONDS=20
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=4
```

Generate service tokens:

```bash
python3.11 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run it twice, once for `SERVICE_TOKEN` and once for `ADMIN_SERVICE_TOKEN`.

## 5. Run Migrations

```bash
cd /opt/AgentMeeMaw
set -a
source /etc/flashback-agent.env
set +a
.venv/bin/python scripts/migrate.py
```

To preview pending migrations:

```bash
.venv/bin/python scripts/migrate.py --dry-run
```

Starter questions are inserted by migrations. After that, you still need to
backfill their embeddings.

## 6. Create systemd Services

Create the API service:

```bash
sudo nano /etc/systemd/system/flashback-agent-api.service
```

```ini
[Unit]
Description=Flashback Agent API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/AgentMeeMaw
EnvironmentFile=/etc/flashback-agent.env
ExecStart=/opt/AgentMeeMaw/.venv/bin/uvicorn flashback.http.app:create_app --factory --host 127.0.0.1 --port 8005
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create one worker template for the workers that use the `run` subcommand:

```bash
sudo nano /etc/systemd/system/flashback-agent-worker@.service
```

```ini
[Unit]
Description=Flashback Agent Worker %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/AgentMeeMaw
EnvironmentFile=/etc/flashback-agent.env
ExecStart=/opt/AgentMeeMaw/.venv/bin/python -m flashback.workers.%i run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable the API and workers:

```bash
sudo systemctl daemon-reload

sudo systemctl enable --now flashback-agent-api
sudo systemctl enable --now flashback-agent-worker@embedding
sudo systemctl enable --now flashback-agent-worker@extraction
sudo systemctl enable --now flashback-agent-worker@thread_detector
sudo systemctl enable --now flashback-agent-worker@trait_synthesizer
sudo systemctl enable --now flashback-agent-worker@profile_summary
```

The producers have subcommands, so give them dedicated services:

```bash
sudo nano /etc/systemd/system/flashback-agent-producers-per-session.service
```

```ini
[Unit]
Description=Flashback Producers Per Session Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/AgentMeeMaw
EnvironmentFile=/etc/flashback-agent.env
ExecStart=/opt/AgentMeeMaw/.venv/bin/python -m flashback.workers.producers run-per-session
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo nano /etc/systemd/system/flashback-agent-producers-weekly.service
```

```ini
[Unit]
Description=Flashback Producers Weekly Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/AgentMeeMaw
EnvironmentFile=/etc/flashback-agent.env
ExecStart=/opt/AgentMeeMaw/.venv/bin/python -m flashback.workers.producers run-weekly
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flashback-agent-producers-per-session
sudo systemctl enable --now flashback-agent-producers-weekly
```

## 7. Seed Starter Question Embeddings

```bash
cd /opt/AgentMeeMaw
set -a
source /etc/flashback-agent.env
set +a
.venv/bin/python -m flashback.workers.embedding backfill --record-type question
```

## 8. Check Health

```bash
curl http://127.0.0.1:8005/health
```

If it returns `"status":"ok"`, configure Node:

```text
AGENT_BASE_URL=http://127.0.0.1:8005
```

Node must send:

```text
X-Service-Token: <SERVICE_TOKEN from /etc/flashback-agent.env>
```

## 9. Useful Commands

```bash
sudo systemctl status flashback-agent-api
sudo journalctl -u flashback-agent-api -f
sudo journalctl -u flashback-agent-worker@embedding -f
```

Restart everything after an update:

```bash
sudo systemctl restart flashback-agent-api
sudo systemctl restart flashback-agent-worker@embedding
sudo systemctl restart flashback-agent-worker@extraction
sudo systemctl restart flashback-agent-worker@thread_detector
sudo systemctl restart flashback-agent-worker@trait_synthesizer
sudo systemctl restart flashback-agent-worker@profile_summary
sudo systemctl restart flashback-agent-producers-per-session
sudo systemctl restart flashback-agent-producers-weekly
```
