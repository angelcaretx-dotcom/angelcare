# Run this after the Supabase database password changes. It saves the new
# password into the live API's DATABASE_URL on Vercel and redeploys, so
# the website and admin can reach the database again. It checks the
# password works BEFORE changing anything. Nothing secret is stored in
# this file.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "E:\Git-ACT\AngelCare Transit\ops\update-production-database-password.ps1"

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\api")

$secure = Read-Host -AsSecureString "Supabase database password (paste with right-click)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if (-not $pw) { throw "Password is required." }

$enc = [uri]::EscapeDataString($pw)
# Transaction-mode pooler (port 6543) -- what the live app uses (ADR 0011).
$url = "postgresql://postgres.ngdxyafqvwxejpxucexh:$enc@aws-0-us-west-2.pooler.supabase.com:6543/postgres"
$pw = $null; $enc = $null

Write-Host "`nStep 1/3: checking the password..." -ForegroundColor Cyan
& .venv\Scripts\python.exe -c "import sys, psycopg; psycopg.connect(sys.argv[1], connect_timeout=10, prepare_threshold=None).close(); print('  OK')" $url
if ($LASTEXITCODE -ne 0) { throw "That password didn't work. Nothing was changed." }

Write-Host "Step 2/3: saving it to Vercel..." -ForegroundColor Cyan
vercel env add DATABASE_URL production --value $url --force --sensitive --yes | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Saving to Vercel failed (see above). Run 'vercel login' if it asked you to log in." }
$url = $null
Write-Host "  OK"

Write-Host "Step 3/3: redeploying the API (about a minute)..." -ForegroundColor Cyan
$latest = vercel ls angelcare-api --environment production 2>$null | Select-Object -First 1
if (-not $latest) { throw "Couldn't find the current deployment. The password IS saved; push any commit to redeploy." }
vercel redeploy $latest.Trim() --target production
if ($LASTEXITCODE -ne 0) { throw "Redeploy failed (see above). The password IS saved; push any commit to redeploy." }

Write-Host "`nDone. The website can reach the database again." -ForegroundColor Green
