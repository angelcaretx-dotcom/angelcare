# One-time setup for the nightly encrypted backup (ADR 0026): stores the
# four GitHub Actions secrets the workflow needs, and generates the
# backup encryption passphrase -- which YOU must keep. Nothing secret is
# stored in this file.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "E:\Git-ACT\AngelCare Transit\ops\setup-nightly-backup.ps1"

$ErrorActionPreference = "Stop"
$repo = "angelcaretx-dotcom/angelcare"
Set-Location (Join-Path $PSScriptRoot "..\api")

function Read-Secret($prompt) {
    $secure = Read-Host -AsSecureString $prompt
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

Write-Host "Step 1/4: checking GitHub access..." -ForegroundColor Cyan
gh api "repos/$repo/actions/secrets" --silent
if ($LASTEXITCODE -ne 0) { throw "The GitHub CLI can't manage secrets for $repo. Run 'gh auth login' first. Nothing was saved." }
Write-Host "  OK"

$dbPassword = Read-Secret "Supabase DATABASE password (paste with right-click)"
$storageKey = Read-Secret "Supabase service_role / secret API key (Supabase > Settings > API)"
if (-not $dbPassword -or -not $storageKey) { throw "Both values are required. Nothing was saved." }

$enc = [uri]::EscapeDataString($dbPassword)
$dbUrl = "postgresql://postgres.ngdxyafqvwxejpxucexh:$enc@aws-0-us-west-2.pooler.supabase.com:5432/postgres"
$dbPassword = $null; $enc = $null

Write-Host "`nStep 2/4: checking the database password..." -ForegroundColor Cyan
& .venv\Scripts\python.exe -c "import sys, psycopg; psycopg.connect(sys.argv[1], connect_timeout=10).close(); print('  OK')" $dbUrl
if ($LASTEXITCODE -ne 0) { throw "That database password didn't work. Nothing was saved." }

Write-Host "Step 3/4: checking the storage key..." -ForegroundColor Cyan
try {
    $null = Invoke-RestMethod -Uri "https://ngdxyafqvwxejpxucexh.supabase.co/storage/v1/bucket" -Headers @{ Authorization = "Bearer $storageKey"; apikey = $storageKey }
    Write-Host "  OK"
} catch { throw "That storage key didn't work (use the service_role / secret key, not the anon key). Nothing was saved." }

# 24 random characters (no look-alikes), in groups of 4.
$alphabet = "abcdefghjkmnpqrstuvwxyz23456789".ToCharArray()
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 24
$rng.GetBytes($bytes)
$chars = $bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] }
$passphrase = (0..5 | ForEach-Object { -join $chars[($_ * 4)..($_ * 4 + 3)] }) -join "-"

Write-Host ""
Write-Host "==================== BACKUP PASSPHRASE ====================" -ForegroundColor Yellow
Write-Host "  $passphrase" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "Save this in your password manager NOW (name it 'AngelCare backup passphrase')."
Write-Host "Without it, no backup can ever be opened. It can't be shown again."
$check = Read-Host "`nType the LAST 4 characters of the passphrase to confirm you saved it"
if ($check -ne $passphrase.Substring($passphrase.Length - 4)) { throw "That didn't match. Nothing was saved; run this again (you'll get a new passphrase)." }

Write-Host "`nStep 4/4: saving GitHub Actions secrets..." -ForegroundColor Cyan
$secrets = [ordered]@{
    BACKUP_DATABASE_URL  = $dbUrl
    SUPABASE_STORAGE_URL = "https://ngdxyafqvwxejpxucexh.supabase.co"
    SUPABASE_STORAGE_KEY = $storageKey
    BACKUP_PASSPHRASE    = $passphrase
}
foreach ($name in $secrets.Keys) {
    gh secret set $name --repo $repo --body $secrets[$name]
    if ($LASTEXITCODE -ne 0) { throw "Saving $name failed (see above)." }
}
$dbUrl = $null; $storageKey = $null; $passphrase = $null; $secrets = $null

Write-Host "`nDone. All 4 secrets saved." -ForegroundColor Green
