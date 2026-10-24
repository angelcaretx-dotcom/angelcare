# Applies pending database migrations to PRODUCTION.
# Shows what will run and asks before applying. Asks for the Supabase
# database password; nothing secret is stored in this file.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "E:\Git-ACT\AngelCare Transit\ops\migrate-production.ps1"

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\api")

$secure = Read-Host -AsSecureString "Supabase database password (paste with right-click)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not $pw) { throw "Password is required." }

$enc = [uri]::EscapeDataString($pw)
# Session-mode pooler (port 5432) for schema changes (ADR 0011).
$env:DATABASE_URL = "postgresql://postgres.ngdxyafqvwxejpxucexh:$enc@aws-0-us-west-2.pooler.supabase.com:5432/postgres"
$pw = $null; $enc = $null

try {
    Write-Host "`nMigrations not yet applied to production:" -ForegroundColor Cyan
    $plan = & .venv\Scripts\python.exe manage.py showmigrations --plan 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Couldn't connect - check the password. Nothing was changed." }
    $pending = $plan | Select-String "\[ \]"
    if (-not $pending) { Write-Host "None - production is already up to date." -ForegroundColor Green; return }
    $pending | ForEach-Object { Write-Host "  $($_.Line.Trim())" }

    $answer = Read-Host "`nApply these to PRODUCTION now? Type yes to continue"
    if ($answer -ne "yes") { Write-Host "Cancelled. Nothing was changed."; return }

    & .venv\Scripts\python.exe manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw "Migration failed (see above)." }
    Write-Host "`nDone." -ForegroundColor Green
}
finally {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}
