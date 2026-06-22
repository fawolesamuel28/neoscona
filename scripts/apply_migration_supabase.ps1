Param()
Set-StrictMode -Version Latest

function Read-Env($key) {
    if (Test-Path .env) {
        $line = Get-Content .env | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
        if ($line) { return ($line -split '=', 2)[1].Trim('"') }
    }
    return $null
}

$databaseUrl = Read-Env 'DATABASE_URL'
$supabaseUrl = Read-Env 'SUPABASE_URL'
$supabaseDbPassword = Read-Env 'SUPABASE_DB_PASSWORD'

if (-not $databaseUrl) {
    if ($supabaseDbPassword -and $supabaseUrl -and $supabaseUrl -like '*supabase.co*') {
        Write-Error "Automatic DATABASE_URL construction not implemented for PowerShell; please set DATABASE_URL in .env or provide psql connection string in env." -ErrorAction Stop
    }
}

if (-not $databaseUrl) { Write-Error 'DATABASE_URL is not set in .env. Aborting.'; exit 1 }

Write-Host "Applying migration migrations/005_flutterwave.sql to $databaseUrl"
& psql $databaseUrl -f migrations/005_flutterwave.sql
if ($LASTEXITCODE -ne 0) { Write-Error 'psql failed'; exit $LASTEXITCODE }
Write-Host 'Migration applied.'
