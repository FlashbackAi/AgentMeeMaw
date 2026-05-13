# Production Deployment Runbook

The agent runs as 8 systemd services on a shared EC2 host alongside Node and
other Flashback services. This doc covers both fresh deployment and code
updates. It supersedes `ec2-deploy.md`.

Anything in `<angle brackets>` is a placeholder to fill at deploy time.
Common ones, grouped so you can fill them once and substitute below:

| Placeholder | What it is |
|---|---|
| `<region>` | AWS region (e.g. `ap-south-1`) |
| `<account>` | 12-digit AWS account ID |
| `<rds-endpoint>` | RDS hostname (e.g. `something.xxx.<region>.rds.amazonaws.com`) |
| `<rds-password>` | DB password for the `postgres` master user |
| `<service-token>` | Random 32-byte token, generated below |
| `<admin-service-token>` | A second random 32-byte token |
| `<voyage-key>` / `<openai-key>` / `<anthropic-key>` | Provider API keys |

Conventions used throughout (do NOT change):

| Field | Value |
|---|---|
| DB name | `flashback-agent` (hyphenated) |
| DB user | `postgres` |
| Install dir | `/opt/AgentMeeMaw` |
| Env file | `/etc/flashback-agent.env` (chmod 600, root) |
| Log dir | `/var/log/flashback-agent/` |
| API bind | `127.0.0.1:8005` (loopback only) |
| SQS prefix | `flashback_agent-` (hyphens, not underscores) |

---

## Part A — Fresh deployment

You only do all of this once per environment.

### A1. Prerequisites

- Postgres RDS instance with **pgvector + pgcrypto support** (default
  parameter group on RDS Postgres 17 includes both).
- SSH or Session Manager access into the target EC2 instance, as a user that
  can `sudo` to root.
- A `boto3`-capable IAM identity that the agent processes can use. See A4.
- A single region the entire stack (RDS, SQS, EC2) lives in. Mixing regions
  is a footgun.
- API keys for **Voyage**, **OpenAI**, **Anthropic**.

### A2. Create the database

In the RDS instance, create a new database for the agent. Don't reuse an
existing one — the migrations assume an empty database:

```sql
CREATE DATABASE "flashback-agent";
```

Connect to the new database and install extensions (in `psql`):

```sql
\c flashback-agent
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
```

In pgAdmin, instead of `\c`, right-click the `flashback-agent` database and
open a Query Tool against it, then run the two `CREATE EXTENSION` lines.

If `CREATE EXTENSION vector` errors with "extension not available", the
parameter group needs `rds.extensions` to include `vector` — apply, wait for
the parameter group to settle, then retry.

### A3. Create SQS queues + DLQ

In the target region, create one shared DLQ first:

- Name: `flashback_agent-dlq`
- Type: Standard
- Visibility timeout: 300 s
- Message retention: 14 days

Copy the DLQ ARN. Then create 8 source queues:

```
flashback_agent-extraction
flashback_agent-embedding
flashback_agent-artifact
flashback_agent-thread-detector
flashback_agent-trait-synthesizer
flashback_agent-profile-summary
flashback_agent-producers-per-session
flashback_agent-producers-weekly
```

For each source queue:
- Type: Standard
- Visibility timeout: 300 s
- Default message retention
- Dead-letter queue: select `flashback_agent-dlq`, maxReceiveCount = 5

⚠️ **Use hyphens consistently** — `flashback_agent-trait-synthesizer`, not
`flashback_agent_trait-synthesizer`. The IAM resource pattern matches on the
hyphen.

### A4. Configure AWS authentication

The agent uses the default boto3 credential chain. Pick one option:

**Option 1 — EC2 instance role (cleanest).** Create an IAM role with this
inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:GetQueueAttributes",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:SendMessage",
        "sqs:ChangeMessageVisibility"
      ],
      "Resource": "arn:aws:sqs:<region>:<account>:flashback_agent-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams",
        "logs:PutRetentionPolicy"
      ],
      "Resource": "arn:aws:logs:<region>:<account>:log-group:/flashback-agent/*"
    }
  ]
}
```

If the box also uses Session Manager for shell access, also attach
`AmazonSSMManagedInstanceCore` so SSM keeps working. Attach the role as the
instance profile (EC2 → Instances → *Actions* → *Security* → *Modify IAM
role*).

**Option 2 — Static IAM user credentials.** Used when the box already has an
IAM user attached via `~/.aws/credentials` for sibling services. Add the
same two statements above to that user's policy and skip the instance role
swap. The CloudWatch agent will need extra config (see A12) to use these
credentials.

### A5. Prepare the EC2 host

SSH in as root (or `sudo su`). On Amazon Linux 2023:

```bash
dnf install -y python3.11 python3.11-pip python3.11-devel git gcc
dnf install -y redis6
systemctl enable --now redis6

python3 --version       # still 3.9.x — unchanged
python3.11 --version    # new
redis6-cli ping         # PONG
```

Notes:
- Python 3.11 is installed alongside 3.9. The system `python3` symlink stays
  on 3.9 — nothing else on the box is touched. The agent runs from its own
  venv (next step).
- AL2023 doesn't ship `valkey`. `redis6` is wire-compatible; the agent's
  `VALKEY_URL=redis://...` works against it unchanged.

### A6. Clone the repo and install

```bash
cd /opt
git clone https://github.com/<org>/AgentMeeMaw.git
cd /opt/AgentMeeMaw

python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .
```

If the GitHub repo is private and root doesn't have credentials cached, set
up `~/.git-credentials` first or use SSH (`git@github.com:<org>/AgentMeeMaw.git`).

### A7. Generate service tokens

The agent authenticates Node's requests via two random tokens:

```bash
python3.11 -c "import secrets; print(secrets.token_urlsafe(32))"  # SERVICE_TOKEN
python3.11 -c "import secrets; print(secrets.token_urlsafe(32))"  # ADMIN_SERVICE_TOKEN
```

Save both outputs — you'll paste them in the next step. They never appear
again, so if you lose them, regenerate and re-deploy.

### A8. Write the env file

This is the full env file the service reads at runtime. Paste this on the
EC2 host, with `<placeholders>` replaced with real values from A7 and your
provider/AWS console. Then `chmod 600`.

```bash
cat > /etc/flashback-agent.env <<'EOF'
DATABASE_URL=postgresql://postgres:<rds-password>@<rds-endpoint>:5432/flashback-agent?sslmode=require
VALKEY_URL=redis://127.0.0.1:6379/0

HTTP_HOST=127.0.0.1
HTTP_PORT=8005
TRUSTED_HOSTS=127.0.0.1,localhost

SERVICE_TOKEN=<service-token>
ADMIN_SERVICE_TOKEN=<admin-service-token>
SERVICE_TOKEN_AUTH_DISABLED=false

AWS_REGION=<region>

EMBEDDING_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-embedding
EXTRACTION_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-extraction
ARTIFACT_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-artifact
THREAD_DETECTOR_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-thread-detector
TRAIT_SYNTHESIZER_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-trait-synthesizer
PROFILE_SUMMARY_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-profile-summary
PRODUCERS_PER_SESSION_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-producers-per-session
PRODUCERS_WEEKLY_QUEUE_URL=https://sqs.<region>.amazonaws.com/<account>/flashback_agent-producers-weekly

VOYAGE_API_KEY=<voyage-key>
OPENAI_API_KEY=<openai-key>
ANTHROPIC_API_KEY=<anthropic-key>

EMBEDDING_MODEL=voyage-3-large
EMBEDDING_MODEL_VERSION=2025-01-07

SQS_MAX_MESSAGES=10
SQS_WAIT_SECONDS=20
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=4

WORKING_MEMORY_TTL_SECONDS=86400
WORKING_MEMORY_TRANSCRIPT_LIMIT=30
MAX_REQUEST_BODY_BYTES=262144
TURN_RATE_LIMIT_PER_MINUTE=60

LLM_SMALL_PROVIDER=openai
LLM_SMALL_MODEL=gpt-5.1
LLM_BIG_PROVIDER=anthropic
LLM_BIG_MODEL=claude-sonnet-4-6

LLM_PROVIDER_STORE_ENABLED=false
LLM_PROVIDER_USER_ID=flashback-service
LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
LLM_CIRCUIT_BREAKER_OPEN_SECONDS=30

LLM_INTENT_MODEL=gpt-5.1
LLM_INTENT_TIMEOUT_SECONDS=8
LLM_INTENT_MAX_TOKENS=800

LLM_SEGMENT_DETECTOR_PROVIDER=openai
LLM_SEGMENT_DETECTOR_MODEL=gpt-5.1
LLM_SEGMENT_DETECTOR_TIMEOUT_SECONDS=10
LLM_SEGMENT_DETECTOR_MAX_TOKENS=1000
SEGMENT_DETECTOR_USER_TURN_CADENCE=6

LLM_RESPONSE_PROVIDER=anthropic
LLM_RESPONSE_MODEL=claude-sonnet-4-6
LLM_RESPONSE_TIMEOUT_SECONDS=12
LLM_RESPONSE_MAX_TOKENS=400

LLM_SESSION_SUMMARY_PROVIDER=anthropic
LLM_SESSION_SUMMARY_MODEL=claude-sonnet-4-6
LLM_SESSION_SUMMARY_TIMEOUT_SECONDS=12
LLM_SESSION_SUMMARY_MAX_TOKENS=600

LLM_EXTRACTION_PROVIDER=anthropic
LLM_EXTRACTION_MODEL=claude-sonnet-4-6
LLM_EXTRACTION_TIMEOUT_SECONDS=45
LLM_EXTRACTION_MAX_TOKENS=4000

LLM_COMPATIBILITY_PROVIDER=openai
LLM_COMPATIBILITY_MODEL=gpt-5.1
LLM_COMPATIBILITY_TIMEOUT_SECONDS=8
LLM_COMPATIBILITY_MAX_TOKENS=800

LLM_TRAIT_MERGE_PROVIDER=openai
LLM_TRAIT_MERGE_MODEL=gpt-5.1
LLM_TRAIT_MERGE_TIMEOUT_SECONDS=8
LLM_TRAIT_MERGE_MAX_TOKENS=500

LLM_NODE_EDIT_PROVIDER=anthropic
LLM_NODE_EDIT_MODEL=claude-sonnet-4-6
LLM_NODE_EDIT_TIMEOUT_SECONDS=30
LLM_NODE_EDIT_MAX_TOKENS=3000

EXTRACTION_REFINEMENT_DISTANCE_THRESHOLD=0.35
EXTRACTION_REFINEMENT_CANDIDATE_LIMIT=3
EXTRACTION_VOYAGE_QUERY_TIMEOUT_SECONDS=5

RETRIEVAL_QUERY_EMBED_TIMEOUT_SECONDS=2
RETRIEVAL_DEFAULT_LIMIT=10
RETRIEVAL_MAX_LIMIT=50

THREAD_DETECTOR_CADENCE=15
THREAD_DETECTOR_MIN_CLUSTER_SIZE=3
THREAD_DETECTOR_EXISTING_MATCH_DISTANCE=0.4

LLM_THREAD_NAMING_PROVIDER=anthropic
LLM_THREAD_NAMING_MODEL=claude-sonnet-4-6
LLM_THREAD_NAMING_TIMEOUT_SECONDS=30
LLM_THREAD_NAMING_MAX_TOKENS=800

LLM_P4_PROVIDER=anthropic
LLM_P4_MODEL=claude-sonnet-4-6
LLM_P4_TIMEOUT_SECONDS=30
LLM_P4_MAX_TOKENS=800

LLM_TRAIT_SYNTH_PROVIDER=openai
LLM_TRAIT_SYNTH_MODEL=gpt-5.1
LLM_TRAIT_SYNTH_TIMEOUT_SECONDS=15
LLM_TRAIT_SYNTH_MAX_TOKENS=1500

LLM_PROFILE_SUMMARY_PROVIDER=anthropic
LLM_PROFILE_SUMMARY_MODEL=claude-sonnet-4-6
LLM_PROFILE_SUMMARY_TIMEOUT_SECONDS=30
LLM_PROFILE_SUMMARY_MAX_TOKENS=600

PROFILE_SUMMARY_TOP_TRAITS_MAX=7
PROFILE_SUMMARY_TOP_THREADS_MAX=5
PROFILE_SUMMARY_TOP_ENTITIES_MAX=8

LLM_PROFILE_FACTS_PROVIDER=openai
LLM_PROFILE_FACTS_MODEL=gpt-5.1
LLM_PROFILE_FACTS_TIMEOUT_SECONDS=15
LLM_PROFILE_FACTS_MAX_TOKENS=800
PROFILE_FACTS_MAX_PER_RUN=5
PROFILE_FACTS_MAX_ACTIVE_PER_PERSON=25

LLM_PRODUCER_PROVIDER=openai
LLM_PRODUCER_MODEL=gpt-5.1
LLM_PRODUCER_TIMEOUT_SECONDS=30
LLM_PRODUCER_MAX_TOKENS=3000

P2_MAX_ENTITIES_PER_RUN=3
P2_QUESTIONS_PER_ENTITY=2
P3_MAX_GAPS_PER_RUN=3
P3_QUESTIONS_PER_GAP=4
P5_MAX_DIMENSIONS_PER_RUN=5
P5_QUESTIONS_PER_DIMENSION=2
P5_DIMENSION_COVERAGE_THRESHOLD=3
EOF

chmod 600 /etc/flashback-agent.env
```

Sanity-check it parses and Postgres is reachable:

```bash
cd /opt/AgentMeeMaw
set -a
source /etc/flashback-agent.env
set +a
.venv/bin/python -c "import psycopg; c = psycopg.connect('$DATABASE_URL'); print(c.info.server_version); c.close()"
```

Expected: a 6-digit integer (e.g. `170006` for Postgres 17.0.6).

### A9. Run migrations

```bash
cd /opt/AgentMeeMaw
.venv/bin/python scripts/migrate.py --dry-run    # preview pending
.venv/bin/python scripts/migrate.py              # apply
```

You should see `applied 0001_initial_schema.up.sql` through the latest
migration. The last few migrations seed the starter question bank — their
embeddings are filled in step A12.

### A10. Create systemd services and log directory

```bash
mkdir -p /var/log/flashback-agent
chmod 755 /var/log/flashback-agent

# --- API ---
cat > /etc/systemd/system/flashback-agent-api.service <<'EOF'
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
StandardOutput=append:/var/log/flashback-agent/api.log
StandardError=append:/var/log/flashback-agent/api.log

[Install]
WantedBy=multi-user.target
EOF

# --- Worker template (5 instances will use this) ---
cat > /etc/systemd/system/flashback-agent-worker@.service <<'EOF'
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
StandardOutput=append:/var/log/flashback-agent/worker-%i.log
StandardError=append:/var/log/flashback-agent/worker-%i.log

[Install]
WantedBy=multi-user.target
EOF

# --- Producers (different ExecStart, can't share the template) ---
cat > /etc/systemd/system/flashback-agent-producers-per-session.service <<'EOF'
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
StandardOutput=append:/var/log/flashback-agent/producers-per-session.log
StandardError=append:/var/log/flashback-agent/producers-per-session.log

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/flashback-agent-producers-weekly.service <<'EOF'
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
StandardOutput=append:/var/log/flashback-agent/producers-weekly.log
StandardError=append:/var/log/flashback-agent/producers-weekly.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
```

### A11. Start the services

```bash
systemctl enable --now flashback-agent-api
systemctl enable --now flashback-agent-worker@embedding
systemctl enable --now flashback-agent-worker@extraction
systemctl enable --now flashback-agent-worker@thread_detector
systemctl enable --now flashback-agent-worker@trait_synthesizer
systemctl enable --now flashback-agent-worker@profile_summary
systemctl enable --now flashback-agent-producers-per-session
systemctl enable --now flashback-agent-producers-weekly

systemctl --no-pager --no-legend list-units 'flashback-agent-*'
```

All 8 should report `loaded active running`. Verify the API:

```bash
curl -s http://127.0.0.1:8005/health
```

Expected:

```json
{"status":"ok","checks":{"valkey":"ok","postgres":"ok","sqs.extraction":"ok", ...}}
```

If any check is not `ok`, look at the corresponding log in
`/var/log/flashback-agent/`.

### A12. Backfill starter question embeddings

Migrations seed the starter questions but don't embed them. Push them to the
embedding queue — the running embedding worker will drain them:

```bash
cd /opt/AgentMeeMaw
set -a
source /etc/flashback-agent.env
set +a
.venv/bin/python -m flashback.workers.embedding backfill --record-type question
```

Wait ~30 seconds, then verify:

```bash
.venv/bin/python -c "
import psycopg, os
c = psycopg.connect(os.environ['DATABASE_URL'])
with c.cursor() as cur:
    cur.execute(\"SELECT count(*) FROM questions WHERE status = 'active'\")
    total = cur.fetchone()[0]
    cur.execute(\"SELECT count(*) FROM questions WHERE status = 'active' AND embedding IS NOT NULL\")
    embedded = cur.fetchone()[0]
    print(f'{embedded}/{total} embedded')
"
```

Should print `<N>/<N> embedded` where N is the current starter count.

### A13. Install + configure the CloudWatch agent

```bash
dnf install -y amazon-cloudwatch-agent

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'EOF'
{
  "agent": { "metrics_collection_interval": 60, "run_as_user": "root" },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {"file_path": "/var/log/flashback-agent/api.log",                       "log_group_name": "/flashback-agent/api",                       "log_stream_name": "{instance_id}", "retention_in_days": 30},
          {"file_path": "/var/log/flashback-agent/worker-embedding.log",          "log_group_name": "/flashback-agent/worker-embedding",          "log_stream_name": "{instance_id}", "retention_in_days": 30},
          {"file_path": "/var/log/flashback-agent/worker-extraction.log",         "log_group_name": "/flashback-agent/worker-extraction",         "log_stream_name": "{instance_id}", "retention_in_days": 30},
          {"file_path": "/var/log/flashback-agent/worker-thread_detector.log",    "log_group_name": "/flashback-agent/worker-thread-detector",    "log_stream_name": "{instance_id}", "retention_in_days": 30},
          {"file_path": "/var/log/flashback-agent/worker-trait_synthesizer.log",  "log_group_name": "/flashback-agent/worker-trait-synthesizer",  "log_stream_name": "{instance_id}", "retention_in_days": 30},
          {"file_path": "/var/log/flashback-agent/worker-profile_summary.log",    "log_group_name": "/flashback-agent/worker-profile-summary",    "log_stream_name": "{instance_id}", "retention_in_days": 30},
          {"file_path": "/var/log/flashback-agent/producers-per-session.log",     "log_group_name": "/flashback-agent/producers-per-session",     "log_stream_name": "{instance_id}", "retention_in_days": 30},
          {"file_path": "/var/log/flashback-agent/producers-weekly.log",          "log_group_name": "/flashback-agent/producers-weekly",          "log_stream_name": "{instance_id}", "retention_in_days": 30}
        ]
      }
    }
  }
}
EOF
```

If you picked **Option 2** auth (static IAM user in `/root/.aws/credentials`),
also point the CW agent at those credentials — by default it tries the
instance profile:

```bash
cat > /opt/aws/amazon-cloudwatch-agent/etc/common-config.toml <<'EOF'
[credentials]
  shared_credential_profile = "default"
  shared_credential_file = "/root/.aws/credentials"
EOF
```

Apply the config and start the agent:

```bash
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
  -s

systemctl enable amazon-cloudwatch-agent
tail -20 /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
```

Look for `piping log from /flashback-agent/...` lines and no
`NoCredentialProviders` / `AccessDenied` errors. Refresh CloudWatch → Log
groups (same region) — 8 groups under `/flashback-agent/...` appear within
a minute.

### A14. Hand the agent off to Node

Tell the Node backend (separate repo) where the agent lives:

```ini
AGENT_BASE_URL=http://127.0.0.1:8005
AGENT_SERVICE_TOKEN=<value of SERVICE_TOKEN from /etc/flashback-agent.env>
```

Node sends the token as the `X-Service-Token` header on every request. The
agent rejects requests with the wrong token unless
`SERVICE_TOKEN_AUTH_DISABLED=true`, which must never be set in production.

---

## Part B — Code update

For every subsequent deploy after a code change.

### B1. Pull the new code

```bash
cd /opt/AgentMeeMaw
git fetch
git log --oneline HEAD..origin/main   # see what's coming
git pull
```

### B2. Reinstall deps (if any changed)

```bash
source .venv/bin/activate
pip install .
```

`pip install .` is idempotent — safe to run every time. Cheap if nothing
changed, picks up any new packages from `pyproject.toml`.

### B3. Check for and run new migrations

```bash
set -a
source /etc/flashback-agent.env
set +a
.venv/bin/python scripts/migrate.py --dry-run
```

If the output is empty, no migrations are pending — skip to B4. Otherwise:

```bash
.venv/bin/python scripts/migrate.py
```

Migrations are safe to run before services restart — the schema is additive
(`status` flips, no destructive UPDATEs) and the running services tolerate
new columns being added.

### B4. Restart everything

```bash
systemctl restart flashback-agent-api flashback-agent-worker@{embedding,extraction,thread_detector,trait_synthesizer,profile_summary} flashback-agent-producers-{per-session,weekly}
```

### B5. Verify

```bash
systemctl --no-pager --no-legend list-units 'flashback-agent-*'
curl -s http://127.0.0.1:8005/health
```

All 8 units should be `active running`. `/health` should return `status: ok`.
If a worker crashes on start, tail its log:

```bash
journalctl -u flashback-agent-worker@extraction -n 50 --no-pager
tail -100 /var/log/flashback-agent/worker-extraction.log
```

### B6. Rolling back

```bash
cd /opt/AgentMeeMaw
git log --oneline -10            # find the previous good commit
git checkout <previous-sha>
pip install .
systemctl restart flashback-agent-api flashback-agent-worker@{embedding,extraction,thread_detector,trait_synthesizer,profile_summary} flashback-agent-producers-{per-session,weekly}
```

Don't auto-apply down migrations — they exist (`*.down.sql`) but reverse data
changes. Roll the code back first, then decide manually whether the new
migrations need reversing.

If the latest deploy added a new SQS queue or env var, you'll also need to
revert those — git won't.

---

## Operational reference

### Looking at logs

```bash
# Live tail (local file)
tail -f /var/log/flashback-agent/api.log

# systemd journal (catches startup errors before the file is opened)
journalctl -u flashback-agent-api -f

# CloudWatch
# Console → Log groups → /flashback-agent/<service> → search/tail in the UI
```

### Re-embedding after an embedding-model change

If `EMBEDDING_MODEL` or `EMBEDDING_MODEL_VERSION` is bumped, all existing
embeddings become invalid (invariant #3 — never mix vectors from different
models). Re-embed everything:

```bash
.venv/bin/python -m flashback.workers.embedding backfill --record-type question
.venv/bin/python -m flashback.workers.embedding backfill --record-type moment
.venv/bin/python -m flashback.workers.embedding backfill --record-type entity
.venv/bin/python -m flashback.workers.embedding backfill --record-type trait
.venv/bin/python -m flashback.workers.embedding backfill --record-type thread
.venv/bin/python -m flashback.workers.embedding backfill --record-type profile_fact
```

The embedding worker drains them as they're pushed.

### Dead-letter queue

`flashback_agent-dlq` collects messages that fail 5 delivery attempts. Check
it after any deploy that touched extraction/embedding logic. SQS console →
`flashback_agent-dlq` → *Send and receive messages* → *Poll for messages*.

### Common pitfalls

- **Queue naming.** Use hyphens after the prefix: `flashback_agent-trait-synthesizer`
  not `flashback_agent_trait-synthesizer`. The IAM resource pattern matches
  on the hyphen.
- **DB name has a hyphen** (`flashback-agent`). Don't simplify it to
  `flashback_agent` in the connection string — it has to match exactly.
- **Don't reuse an existing database.** Migrations assume an empty database
  and will conflict with sibling schemas.
- **`/etc/flashback-agent.env` is the only env source in production.**
  `.env`, `.env.local`, `.env.production` in the repo are templates and
  never loaded at runtime.
- **AWS auth lives in one place per box.** If you're using static creds in
  `/root/.aws/credentials`, the CloudWatch agent needs `common-config.toml`
  pointed at the same profile — otherwise it defaults to the instance role
  and can fail silently with `NoCredentialProviders`.
