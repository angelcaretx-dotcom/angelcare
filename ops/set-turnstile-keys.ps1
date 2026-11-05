# Run this after creating a Cloudflare Turnstile widget (ADR 0033). It checks
# the secret key with Cloudflare, then switches Turnstile on: the site key
# goes to the website (NEXT_PUBLIC_TURNSTILE_SITE_KEY), the secret key to
# the API (DJANGO_TURNSTILE_SECRET_KEY), and both are redeployed.
# Nothing secret is stored in this file.
#
# Order matters: the website gets its widget first. If the API demanded
# Turnstile before the website showed it, every code request would fail.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "E:\Git-ACT\AngelCare Transit\ops\set-turnstile-keys.ps1"

$ErrorActionPreference = "Stop"
$root = Join-Path $PSScriptRoot ".."

$siteKey = (Read-Host "Turnstile SITE key (starts with 0x, paste with right-click)").Trim()
$secure = Read-Host -AsSecureString "Turnstile SECRET key (starts with 0x)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $secret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not ($siteKey.StartsWith("0x") -and $secret.StartsWith("0x"))) {
    throw "Turnstile keys start with 0x. Nothing was changed."
}
if ($siteKey -eq $secret) { throw "The site key and secret key are different values. Nothing was changed." }

Write-Host "`nStep 1/3: checking the secret key with Cloudflare..." -ForegroundColor Cyan
# A made-up response: a good secret gets "invalid-input-response",
# a bad one "invalid-input-secret".
$check = Invoke-RestMethod -Method Post -Uri "https://challenges.cloudflare.com/turnstile/v0/siteverify" `
    -Body @{ secret = $secret; response = "test" }
if ($check."error-codes" -contains "invalid-input-secret") { throw "Cloudflare rejected that secret key. Nothing was changed." }
Write-Host "  OK"

# The vercel CLI writes its banner to stderr, which PowerShell 5.1 treats
# as a fatal error under "Stop" -- rely on exit codes from here on.
$ErrorActionPreference = "Continue"

function Save-Env($name, $value, $extra, $failure) {
    # Show Vercel's own message if it refuses (never the value itself).
    $out = vercel env add $name production --value $value --force --yes @extra 2>&1 |
        ForEach-Object { "$_" } | Where-Object { $_ -notmatch [regex]::Escape($value) }
    if ($LASTEXITCODE -ne 0) {
        $out | Select-Object -Last 8 | ForEach-Object { Write-Host "  vercel: $_" -ForegroundColor Yellow }
        throw $failure
    }
}

function Redeploy($project) {
    $latest = vercel ls $project --environment production 2>$null | Select-Object -First 1
    if (-not $latest) { throw "Couldn't find $project's current deployment. The key IS saved; push any commit to redeploy." }
    vercel redeploy $latest.Trim() --target production 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Redeploying $project failed. The key IS saved; push any commit to redeploy." }
}

Write-Host "Step 2/3: website: saving the site key and redeploying (about a minute)..." -ForegroundColor Cyan
Set-Location (Join-Path $root "web")
Save-Env "NEXT_PUBLIC_TURNSTILE_SITE_KEY" $siteKey @("--no-sensitive") "Saving the site key failed (Vercel's message is above). Nothing else was changed."
Redeploy "angelcare"

Write-Host "Step 3/3: API: saving the secret key and redeploying..." -ForegroundColor Cyan
Set-Location (Join-Path $root "api")
Save-Env "DJANGO_TURNSTILE_SECRET_KEY" $secret @("--sensitive") "Saving the secret key failed (Vercel's message is above). The website already has its key; run this again."
$secret = $null
Redeploy "angelcare-api"

Write-Host "`nDone. Turnstile is on. Try the contact form once to confirm the code still arrives." -ForegroundColor Green
