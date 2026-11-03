# Run this after creating a new Resend API key. It checks the key can log
# in to Resend's SMTP server, saves it into the live API's
# DJANGO_EMAIL_HOST_PASSWORD on Vercel (with the matching SMTP settings),
# and redeploys. Nothing secret is stored in this file.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "E:\Git-ACT\AngelCare Transit\ops\update-production-email-key.ps1"

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\api")

$secure = Read-Host -AsSecureString "Resend API key, starts with re_ (paste with right-click)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not $key.StartsWith("re_")) { throw "That doesn't look like a Resend API key (they start with re_). Nothing was changed." }

Write-Host "`nStep 1/3: checking the key with Resend..." -ForegroundColor Cyan
& .venv\Scripts\python.exe -c "import sys, smtplib; s = smtplib.SMTP('smtp.resend.com', 587, timeout=15); s.starttls(); s.login('resend', sys.argv[1]); s.quit(); print('  OK')" $key
if ($LASTEXITCODE -ne 0) { throw "Resend rejected that key. Nothing was changed." }

Write-Host "Step 2/3: saving it to Vercel..." -ForegroundColor Cyan
# The vercel CLI writes its banner to stderr, which PowerShell 5.1 treats
# as a fatal error under "Stop" -- rely on exit codes from here on.
$ErrorActionPreference = "Continue"
$settings = [ordered]@{
    DJANGO_EMAIL_BACKEND       = "django.core.mail.backends.smtp.EmailBackend"
    DJANGO_EMAIL_HOST          = "smtp.resend.com"
    DJANGO_EMAIL_PORT          = "587"
    DJANGO_EMAIL_USE_TLS       = "True"
    DJANGO_EMAIL_HOST_USER     = "resend"
    DJANGO_EMAIL_HOST_PASSWORD = $key
}
foreach ($name in $settings.Keys) {
    vercel env add $name production --value $settings[$name] --force --sensitive --yes 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Saving $name failed. Run 'vercel login' if needed, then run this again." }
}
$key = $null; $settings = $null
Write-Host "  OK"

Write-Host "Step 3/3: redeploying the API (about a minute)..." -ForegroundColor Cyan
$latest = vercel ls angelcare-api --environment production 2>$null | Select-Object -First 1
if (-not $latest) { throw "Couldn't find the current deployment. The key IS saved; push any commit to redeploy." }
vercel redeploy $latest.Trim() --target production 2>$null
if ($LASTEXITCODE -ne 0) { throw "Redeploy failed. The key IS saved; push any commit to redeploy." }

Write-Host "`nDone. Send a test booking to confirm emails arrive." -ForegroundColor Green
