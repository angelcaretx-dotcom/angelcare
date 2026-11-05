# Run once to switch on the daily standing-order job (ADR 0035). It makes
# a random CRON_SECRET, saves it on the API (angelcare-api) and
# redeploys. Vercel Cron then sends it with each daily call; without it
# the job refuses to run. Nothing secret is stored in this file or shown.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "E:\Git-ACT\AngelCare Transit\ops\set-cron-secret.ps1"

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\api")

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret = ([BitConverter]::ToString($bytes) -replace "-", "").ToLower()

# The vercel CLI writes its banner to stderr, which PowerShell 5.1 treats
# as a fatal error under "Stop" -- rely on exit codes from here on.
$ErrorActionPreference = "Continue"

Write-Host "Step 1/2: saving CRON_SECRET on the API..." -ForegroundColor Cyan
$out = vercel env add CRON_SECRET production --value $secret --force --sensitive --yes 2>&1 |
    ForEach-Object { "$_" } | Where-Object { $_ -notmatch $secret }
$secret = $null
if ($LASTEXITCODE -ne 0) {
    $out | Select-Object -Last 8 | ForEach-Object { Write-Host "  vercel: $_" -ForegroundColor Yellow }
    throw "Saving CRON_SECRET failed (Vercel's message is above). Nothing else was changed."
}

Write-Host "Step 2/2: redeploying the API (about a minute)..." -ForegroundColor Cyan
$latest = vercel ls angelcare-api --environment production 2>$null | Select-Object -First 1
if (-not $latest) { throw "Couldn't find the API's current deployment. The secret IS saved; push any commit to redeploy." }
vercel redeploy $latest.Trim() --target production 2>$null
if ($LASTEXITCODE -ne 0) { throw "Redeploying failed. The secret IS saved; push any commit to redeploy." }

Write-Host "`nDone. Standing orders will be topped up every morning (about 4-5 AM Texas time)." -ForegroundColor Green
