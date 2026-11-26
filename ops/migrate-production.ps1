# Applies pending database migrations to PRODUCTION.
# Shows what will run and asks before applying. Asks for the Supabase
# database password; nothing secret is stored in this file.
#
# Afterwards it saves that same (working) password to the live API on
# Vercel and to the nightly backup's GitHub secret, and redeploys the
# API. A reset Supabase password then can't leave the admin or the
# backup behind (2026-11-05: the admin went down three times that way).
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "E:\Git-ACT\AngelCare Transit\ops\migrate-production.ps1"

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\api")
$repo = "angelcaretx-dotcom/angelcare"

# Say which copy of the code this is before asking for anything. The
# migrations that run are the ones in THIS folder: an older copy, or one on
# another branch, reports "already up to date" while the new version's
# migrations are still missing (2026-11-26: the site went down because code
# was live before its migration). Uses only the local git folder, no
# network, and never stops the script: without git it says "unknown".
$codeFolder = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$codeBranch = "unknown"
$codeCommit = "unknown"
$ErrorActionPreference = "Continue"
try {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $found = git -C $codeFolder rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $codeBranch = "$found".Trim() }
        $found = git -C $codeFolder rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $codeCommit = "$found".Trim() }
    }
}
catch { }
finally { $ErrorActionPreference = "Stop" }
Write-Host "`nThis will migrate PRODUCTION with the migrations in this copy of the code:" -ForegroundColor Cyan
Write-Host "  Folder: $codeFolder"
Write-Host "  Branch: $codeBranch"
Write-Host "  Commit: $codeCommit"
Write-Host "If this is not the version you are about to put live, press Ctrl+C now and run the script from the right folder.`n" -ForegroundColor Yellow

$secure = Read-Host -AsSecureString "Supabase database password (paste with right-click)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not $pw) { throw "Password is required." }

$enc = [uri]::EscapeDataString($pw)
$hostPart = "postgres.ngdxyafqvwxejpxucexh:$enc@aws-0-us-west-2.pooler.supabase.com"
# Session-mode pooler (port 5432) for schema changes and backups; the
# live app uses transaction mode (port 6543) (ADR 0011).
$sessionUrl = "postgresql://${hostPart}:5432/postgres"
$appUrl = "postgresql://${hostPart}:6543/postgres"
$pw = $null; $enc = $null; $hostPart = $null
$env:DATABASE_URL = $sessionUrl

try {
    Write-Host "`nMigrations not yet applied to production:" -ForegroundColor Cyan
    $plan = & .venv\Scripts\python.exe manage.py showmigrations --plan 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Couldn't connect - check the password. Nothing was changed." }
    $pending = $plan | Select-String "\[ \]"
    if (-not $pending) {
        Write-Host "None - production is already up to date." -ForegroundColor Green
    } else {
        $pending | ForEach-Object { Write-Host "  $($_.Line.Trim())" }
        $answer = Read-Host "`nApply these to PRODUCTION now? Type yes to continue"
        if ($answer -ne "yes") { Write-Host "Cancelled. Nothing was changed."; return }
        & .venv\Scripts\python.exe manage.py migrate --noinput
        if ($LASTEXITCODE -ne 0) { throw "Migration failed (see above)." }
        Write-Host "Migrations applied." -ForegroundColor Green
    }
}
finally {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}

# The password just worked: make sure the live API and the backup use it.
# The vercel/gh CLIs write banners to stderr, which PowerShell 5.1 treats
# as fatal under "Stop" -- rely on exit codes from here on.
$ErrorActionPreference = "Continue"
Write-Host "`nSaving this password to the live API and the nightly backup..." -ForegroundColor Cyan
# A tool that isn't installed leaves the last exit code (the migration's 0)
# untouched, which used to read as "saved" (2026-11-26: the admin went down
# after a password reset because of it). Check the tool is there first.
$apiSaved = $false
if (Get-Command vercel -ErrorAction SilentlyContinue) {
    vercel env add DATABASE_URL production --value $appUrl --force --sensitive --yes 2>$null | Out-Null
    $apiSaved = $LASTEXITCODE -eq 0
}
$backupSaved = $false
if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh secret set BACKUP_DATABASE_URL --repo $repo --body $sessionUrl 2>$null | Out-Null
    $backupSaved = $LASTEXITCODE -eq 0
}
$appUrl = $null; $sessionUrl = $null
if (-not $apiSaved) {
    Write-Host "  NOT saved to Vercel (is the vercel tool installed and logged in?)." -ForegroundColor Yellow
    Write-Host "  If the database password was changed, the live site and admin CANNOT reach the database" -ForegroundColor Yellow
    Write-Host "  until it is saved: run update-production-database-password.ps1, or set DATABASE_URL in" -ForegroundColor Yellow
    Write-Host "  the angelcare-api project on vercel.com and redeploy. If it wasn't changed, nothing to do." -ForegroundColor Yellow
}
if (-not $backupSaved) { Write-Host "  NOT saved to GitHub for the nightly backup: run setup-nightly-backup.ps1 if the password changed." -ForegroundColor Yellow }
if ($apiSaved) {
    Write-Host "Redeploying the API so it uses it (about a minute)..." -ForegroundColor Cyan
    $latest = vercel ls angelcare-api --environment production 2>$null | Select-Object -First 1
    if ($latest) { vercel redeploy $latest.Trim() --target production 2>$null | Out-Null }
    if (-not $latest -or $LASTEXITCODE -ne 0) {
        Write-Host "  Redeploy failed: the password IS saved; push any commit to redeploy." -ForegroundColor Yellow
    }
}
Write-Host "`nDone." -ForegroundColor Green
