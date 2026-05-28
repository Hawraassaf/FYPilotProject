# ============================================================
# FYPilot — Start All Services (Windows PowerShell)
# Run from the project root: .\scripts\start-all.ps1
# ============================================================

# ── Development database configuration ──────────────────────
$env:PGHOST      = "localhost"
$env:PGPORT      = "5432"
$env:PGDATABASE  = "fyp_db"
$env:PGUSER      = "postgres"
$env:PGPASSWORD  = "123456"
$env:AI_SERVICE_URL = "http://localhost:8000"
$env:ASPNETCORE_ENVIRONMENT = "Development"

$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  FYPilot — Starting all services" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Database : $env:PGHOST/$env:PGDATABASE (user: $env:PGUSER)" -ForegroundColor Gray
Write-Host "AI URL   : $env:AI_SERVICE_URL" -ForegroundColor Gray
Write-Host ""

# ── Terminal 1: Python AI service ───────────────────────────
Write-Host "Starting Python AI service..." -ForegroundColor Yellow
$aiPath = Join-Path $ProjectRoot "services\FYPilot.AI"

$aiScript = @"
Set-Location "$aiPath"
if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
}
.venv\Scripts\Activate.ps1
pip install -r requirements.txt --quiet
Write-Host "Python AI service starting on http://localhost:8000" -ForegroundColor Green
python run.py
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $aiScript

Start-Sleep -Seconds 3

# ── Terminal 2: FYPilot.Web Razor Pages ─────────────────────
Write-Host "Starting FYPilot.Web (Razor Pages)..." -ForegroundColor Yellow
$webPath = Join-Path $ProjectRoot "src\FYPilot.Web"

$webScript = @"
Set-Location "$webPath"
`$env:PGHOST      = "$($env:PGHOST)"
`$env:PGPORT      = "$($env:PGPORT)"
`$env:PGDATABASE  = "$($env:PGDATABASE)"
`$env:PGUSER      = "$($env:PGUSER)"
`$env:PGPASSWORD  = "$($env:PGPASSWORD)"
`$env:AI_SERVICE_URL = "$($env:AI_SERVICE_URL)"
`$env:ASPNETCORE_ENVIRONMENT = "Development"
Write-Host "FYPilot.Web starting on http://localhost:5000" -ForegroundColor Green
dotnet run --urls http://localhost:5000
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $webScript

# ── Summary ─────────────────────────────────────────────────
Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  Services starting in separate windows" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Python AI  :  http://localhost:8000/health" -ForegroundColor Green
Write-Host "Web app    :  http://localhost:5000" -ForegroundColor Green
Write-Host "System test:  http://localhost:5000/SystemTest" -ForegroundColor Green
Write-Host ""
Write-Host "Optional — REST API (Swagger):" -ForegroundColor Gray
Write-Host "  cd src\FYPilot.Api" -ForegroundColor Gray
Write-Host "  dotnet run --urls http://localhost:8080" -ForegroundColor Gray
Write-Host "  http://localhost:8080/swagger" -ForegroundColor Gray
Write-Host ""
Write-Host "Demo accounts (password: password123):" -ForegroundColor Gray
Write-Host "  student@fyp.com   supervisor@fyp.com   admin@fyp.com" -ForegroundColor Gray
Write-Host ""
