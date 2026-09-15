# RIP compose lifecycle (Windows entrypoint; mirror: scripts/rip.sh).
#
# ASCII-only by design: Windows PowerShell 5.1 reads BOM-less files in the
# system code page, so non-ASCII (e.g. em dashes) corrupts parsing.
#
# Why this wrapper exists instead of raw `docker compose`:
# - `up` always builds: VITE_API_URL is baked at image build and the backend
#   code layer is copied at build, so plain `up` silently reuses stale images.
# - Stale shell DB_* exports shadow .env for compose interpolation
#   (POSTGRES_*), while the backend reads .env -- cleared per invocation.
# - Host probes use 127.0.0.1, never bare localhost (IPv6-first hang).
# - NOTE: .env PORT does NOT move the compose backend (docker-compose.yml
#   pins container PORT=8000). Health URLs use HOST_*_PORT from .env.
#
# Requires PowerShell 5.1+ and Docker with compose v2.

param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "docker-compose.yml"))) {
    Write-Error "docker-compose.yml not found in $RepoRoot -- run from the repo."
    exit 1
}

if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
    Write-Error "docker CLI not found in PATH."
    exit 1
}

function Read-DotEnvValue {
    param([string]$Name, [string]$Default = "")
    $envFile = Join-Path $RepoRoot ".env"
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in (Get-Content -LiteralPath $envFile)) {
            $trimmed = $line.Trim()
            if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
            $idx = $trimmed.IndexOf("=")
            if ($idx -lt 0) { continue }
            if ($trimmed.Substring(0, $idx).Trim() -eq $Name) {
                return $trimmed.Substring($idx + 1).Trim()
            }
        }
    }
    return $Default
}

function Clear-StaleDbEnv {
    # Session-only: stale exports shadow .env for compose interpolation.
    Remove-Item Env:DB_HOST, Env:DB_PORT, Env:DB_NAME, Env:DB_USER, Env:DB_PASSWORD, Env:DB_CONNECT_TIMEOUT_S -ErrorAction SilentlyContinue
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    Clear-StaleDbEnv
    & docker compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Ensure-EnvFile {
    $envFile = Join-Path $RepoRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        Copy-Item -LiteralPath (Join-Path $RepoRoot ".env.example") -Destination $envFile
        Write-Output "Created .env from .env.example -- edit OLLAMA_BASE_URL, BGE paths and HOST_PG_PORT, then run again."
        exit 1
    }
}

function Wait-BackendHealthy {
    param([int]$TimeoutSec = 300)
    $port = Read-DotEnvValue "HOST_BACKEND_PORT" "8000"
    $url = "http://127.0.0.1:$port/api/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $res = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
            if ($res.StatusCode -eq 200) { return $true }
        }
        catch {
            Start-Sleep -Seconds 10
        }
    }
    return $false
}

function Show-Urls {
    $bPort = Read-DotEnvValue "HOST_BACKEND_PORT" "8000"
    $fPort = Read-DotEnvValue "HOST_FRONTEND_PORT" "5173"
    Write-Output "frontend: http://127.0.0.1:$fPort"
    Write-Output "backend:  http://127.0.0.1:$bPort  (health: /api/health)"
}

function Show-Usage {
    Write-Output @'
Usage: scripts/rip.ps1 <command> [services...] [flags]

Commands:
  up [services]       Env guard + up -d --build + wait healthy + URLs
  down                Stop and remove containers (volumes kept)
  fresh [-y]          WIPE pgdata + uploads (down -v), then up --build
  restart [service]   Bounce without rebuild (volumes kept)
  rebuild [service]   up -d --build scoped (default: all)
  logs [service]      Follow logs (--tail N supported)
  ps | status          Compose service table
  migrate             Re-apply backend/schema.sql to running postgres
  health              Probe backend /api/health, frontend /healthz, postgres
  help                This text
'@
}

function Invoke-Up {
    param([string[]]$Services)
    Ensure-EnvFile
    Invoke-Compose (@("up", "-d", "--build") + $Services)
    if (Wait-BackendHealthy) {
        Write-Output "Backend healthy."
        Invoke-Compose @("ps")
        Show-Urls
    }
    else {
        Write-Error "Backend did not turn healthy in time -- run 'scripts/rip.ps1 logs backend'."
        exit 1
    }
}

function Invoke-Fresh {
    param([string[]]$Flags, [string[]]$Services)
    if (-not ($Flags -contains "-y" -or $Flags -contains "--yes")) {
        Write-Output "WARNING: this deletes pgdata AND uploads (DB, files, artifacts)."
        $answer = Read-Host "Type YES to continue"
        if ($answer -ne "YES") { Write-Output "Aborted."; exit 0 }
    }
    Invoke-Compose @("down", "-v")
    Invoke-Up $Services
}

function Invoke-Logs {
    param([string[]]$CLI)
    $tail = "100"
    $services = @()
    for ($i = 0; $i -lt $CLI.Count; $i++) {
        if (($CLI[$i] -eq "--tail") -and ($i + 1 -lt $CLI.Count)) {
            $tail = $CLI[$i + 1]; $i++
        }
        elseif ($CLI[$i].StartsWith("--tail=")) {
            $tail = $CLI[$i].Substring(7)
        }
        else { $services += $CLI[$i] }
    }
    Invoke-Compose (@("logs", "-f", "--tail", $tail) + $services)
}

function Invoke-Migrate {
    $dbUser = Read-DotEnvValue "DB_USER" "rip"
    $dbName = Read-DotEnvValue "DB_NAME" "rip"
    Clear-StaleDbEnv
    & docker compose exec -T postgres pg_isready -U $dbUser -d $dbName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "postgres is not running -- run 'scripts/rip.ps1 up' first."
        exit 1
    }
    Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "backend/schema.sql") |
        & docker compose exec -T postgres psql -U $dbUser -d $dbName -v ON_ERROR_STOP=1 -f -
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output "schema.sql re-applied to database '$dbName'."
}

function Invoke-Health {
    $failed = $false
    $bPort = Read-DotEnvValue "HOST_BACKEND_PORT" "8000"
    $fPort = Read-DotEnvValue "HOST_FRONTEND_PORT" "5173"
    try {
        $b = Invoke-WebRequest -Uri "http://127.0.0.1:$bPort/api/health" -UseBasicParsing -TimeoutSec 10
        Write-Output "backend:  $($b.StatusCode)  http://127.0.0.1:$bPort/api/health"
    }
    catch { Write-Output "backend:  FAIL  http://127.0.0.1:$bPort/api/health"; $failed = $true }
    try {
        $f = Invoke-WebRequest -Uri "http://127.0.0.1:$fPort/healthz" -UseBasicParsing -TimeoutSec 10
        Write-Output "frontend: $($f.StatusCode)  http://127.0.0.1:$fPort/healthz"
    }
    catch { Write-Output "frontend: FAIL  http://127.0.0.1:$fPort/healthz"; $failed = $true }
    $dbUser = Read-DotEnvValue "DB_USER" "rip"
    $dbName = Read-DotEnvValue "DB_NAME" "rip"
    Clear-StaleDbEnv
    & docker compose exec -T postgres pg_isready -U $dbUser -d $dbName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Output "postgres: ready" }
    else { Write-Output "postgres: FAIL (not running?)"; $failed = $true }
    Show-Urls
    if ($failed) { exit 1 }
}

switch ($Command.ToLowerInvariant()) {
    "up" { Invoke-Up $Rest }
    "down" { Invoke-Compose (@("down") + $Rest) }
    "fresh" { Invoke-Fresh -Flags $Rest -Services @($Rest | Where-Object { $_ -ne "-y" -and $_ -ne "--yes" }) }
    "restart" { Invoke-Compose (@("restart") + $Rest) }
    "rebuild" {
        if ($Rest.Count -eq 0) { Invoke-Compose @("up", "-d", "--build") }
        else { Invoke-Compose (@("up", "-d", "--build") + $Rest) }
        if (Wait-BackendHealthy) { Write-Output "Backend healthy."; Show-Urls }
        else { Write-Error "Backend did not turn healthy in time."; exit 1 }
    }
    "logs" { Invoke-Logs $Rest }
    "ps" { Invoke-Compose (@("ps") + $Rest) }
    "status" { Invoke-Compose (@("ps") + $Rest) }
    "migrate" { Invoke-Migrate }
    "health" { Invoke-Health }
    default { Show-Usage }
}
