# scripts/start-local.ps1
# One command to start the full local agent stack.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
# Flags:  -Reset   wipe Postgres and re-apply all migrations from scratch

param([switch]$Reset)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot

function Write-Step($n, $label) { Write-Host ""; Write-Host "[$n] $label" -ForegroundColor Cyan }
function Write-Ok($msg)         { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Run($msg)        { Write-Host "    ... $msg" -ForegroundColor Yellow }
function Write-Fail($msg)       { Write-Host "    ERR $msg" -ForegroundColor Red }

Set-Location $repo

Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
Write-Host " Flashback local agent stack  " -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan

# --- 1. Infra -----------------------------------------------------------------
Write-Step "1/5" "Infra (Postgres / Valkey / LocalStack)"

$need    = @("flashback-postgres", "flashback-valkey", "flashback-localstack")
$running = docker ps --format "{{.Names}}" 2>&1
$missing = $need | Where-Object { $running -notcontains $_ }

if ($missing.Count -gt 0) {
    Write-Run "Starting: $($missing -join ', ')"
    docker compose -f docker-compose.local.yml up -d | Out-Null
    Write-Run "Waiting for healthy..."
    $attempts = 0
    do {
        Start-Sleep 2
        $healthy = docker ps --filter "health=healthy" --format "{{.Names}}" 2>&1
        $still   = $need | Where-Object { $healthy -notcontains $_ }
        $attempts++
    } while ($still.Count -gt 0 -and $attempts -lt 30)
    if ($still.Count -gt 0) { Write-Fail "Timed out: $($still -join ', ')"; exit 1 }
    Write-Ok "Containers healthy."
} else {
    Write-Ok "All containers already running."
}

# --- 2. Migrations ------------------------------------------------------------
Write-Step "2/5" "Migrations"

if ($Reset) {
    Write-Run "Resetting public schema..."
    docker exec -i flashback-postgres psql -U flashback -d flashback -v ON_ERROR_STOP=1 -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;" | Out-Null
}

$appliedRaw = docker exec -i flashback-postgres psql -U flashback -d flashback -t -c "SELECT filename FROM schema_migrations ORDER BY filename" 2>$null
if ($appliedRaw) {
    $applied = $appliedRaw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
} else {
    $applied = @()
}

$migFiles  = Get-ChildItem "$repo\migrations" -Filter "*.up.sql" | Sort-Object Name
$unapplied = $migFiles | Where-Object { $applied -notcontains $_.Name }

if ($unapplied.Count -eq 0) {
    Write-Ok "Up to date ($($migFiles.Count) migration files)."
} else {
    Write-Run "Applying $($unapplied.Count) new migration(s)..."
    foreach ($mig in $unapplied) {
        Write-Run $mig.Name
        $sql = Get-Content $mig.FullName -Raw
        $sql | docker exec -i flashback-postgres psql -U flashback -d flashback -v ON_ERROR_STOP=1
        if ($LASTEXITCODE -ne 0) { Write-Fail "Failed on $($mig.Name)"; exit 1 }
        $checksum = (Get-FileHash $mig.FullName -Algorithm MD5).Hash
        docker exec -i flashback-postgres psql -U flashback -d flashback -c "INSERT INTO schema_migrations(filename,checksum) VALUES('$($mig.Name)','$checksum') ON CONFLICT DO NOTHING" | Out-Null
    }
    Write-Ok "All migrations applied."
}

# --- 3. Question embeddings ---------------------------------------------------
Write-Step "3/5" "Question embeddings"

$unembedRaw = docker exec -i flashback-postgres psql -U flashback -d flashback -t -c "SELECT COUNT(*) FROM questions WHERE source='starter_anchor' AND person_id IS NULL AND embedding IS NULL" 2>&1
$unembed    = [int](($unembedRaw | Where-Object { $_ -match '\d' } | Select-Object -First 1).ToString().Trim())

if ($unembed -gt 0) {
    Write-Run "Backfilling $unembed question(s) -- calls Voyage API..."
    python -m flashback.workers.embedding backfill --record-type question
    if ($LASTEXITCODE -ne 0) { Write-Fail "Embedding backfill failed."; exit 1 }
    Write-Ok "Backfill complete."
} else {
    Write-Ok "All questions embedded."
}

# --- 4. HTTP service ----------------------------------------------------------
Write-Step "4/5" "HTTP service (:8000)"

$listening = netstat -ano 2>&1 | Select-String "0\.0\.0\.0:8000\s.*LISTENING"
if ($listening) {
    Write-Ok "Already running."
} else {
    Write-Run "Starting uvicorn..."
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$repo'; python -m uvicorn flashback.http.app:create_app --factory --host 0.0.0.0 --port 8000" -WindowStyle Normal
    Start-Sleep 3
    Write-Ok "Started in new window."
}

# --- 5. Workers ---------------------------------------------------------------
Write-Step "5/5" "Workers (embedding / extraction)"

Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$repo'; python -m flashback.workers.embedding run" -WindowStyle Normal
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$repo'; python -m flashback.workers.extraction run" -WindowStyle Normal

Write-Ok "Started in new windows."

# --- Done ---------------------------------------------------------------------
Write-Host ""
Write-Host "==============================" -ForegroundColor Cyan
try {
    $health = (Invoke-WebRequest http://localhost:8000/health -UseBasicParsing).Content
    Write-Host " Agent ready." -ForegroundColor Green
    Write-Host " $health" -ForegroundColor Gray
} catch {
    Write-Host " Agent starting -- check http://localhost:8000/health" -ForegroundColor Yellow
}
Write-Host "==============================" -ForegroundColor Cyan
Write-Host ""
