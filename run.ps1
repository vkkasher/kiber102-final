param([switch]$Install, [switch]$Stop, [switch]$Dev)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$localDir = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force -Path $localDir | Out-Null
$processFile = Join-Path $localDir 'processes.json'
. (Join-Path $projectRoot 'scripts/local_processes.ps1')
. (Join-Path $projectRoot 'scripts/local_llm.ps1')
if ($Stop) {
    if (Test-Path -LiteralPath $processFile) {
        $saved = Get-Content -Raw -LiteralPath $processFile | ConvertFrom-Json
        if (-not $saved.PSObject.Properties['started']) { $saved | Add-Member -NotePropertyName started -NotePropertyValue ([pscustomobject]@{api=$null;web=$null;worker=$null;bot=$null}) }
        foreach ($serviceName in @('bot','worker','web','api')) {
            if ($saved.PSObject.Properties[$serviceName]) { Stop-OwnedService $serviceName $saved $projectRoot $processFile }
        }
        Remove-Item -LiteralPath $processFile
    }
    Stop-LocalLlm $projectRoot
    Write-Host 'Локальные процессы остановлены.'
    exit
}
if (-not (Test-Path -LiteralPath '.env')) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    function New-HexSecret([int]$Length) {
        $buf = New-Object byte[] $Length
        $rng.GetBytes($buf)
        return ([BitConverter]::ToString($buf)).Replace('-', '').ToLowerInvariant()
    }
    $adminPassword = New-HexSecret 24
    $sessionSecret = New-HexSecret 48
    $databasePassword = New-HexSecret 32
    $dbFile = (Join-Path $localDir 'catalog.db').Replace('\', '/')
    @"
DATABASE_URL=sqlite:///$dbFile
ADMIN_PASSWORD=$adminPassword
ADMIN_SESSION_SECRET=$sessionSecret
POSTGRES_PASSWORD=$databasePassword
SOURCE_ALLOWED_HOSTS=www.bashopera.ru,bashopera.ru,rusdram.ru,www.culture.ru,culture.ru,bashgf.ru,api.timepad.ru,pro.culture.ru,rusdramteatr.ru
API_INTERNAL_URL=http://127.0.0.1:8000
API_URL=http://127.0.0.1:8000
WEB_URL=http://localhost:3000
"@ | Set-Content -LiteralPath '.env' -Encoding utf8
    Write-Host 'Создан .env со случайным локальным паролем администратора.'
}
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { python -m venv .venv; $Install = $true }
if ($Install) {
    & $pythonPath -X utf8 -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить Python зависимости' }
}
& $pythonPath -X utf8 scripts/configure.py
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить локальную конфигурацию' }
# Populate child process environment without printing credentials or passing them in argv.
$serviceEnvironment = & $pythonPath -X utf8 -c "import json; from dotenv import dotenv_values; print(json.dumps(dotenv_values('.env', encoding='utf-8-sig')))" | ConvertFrom-Json
foreach ($property in $serviceEnvironment.PSObject.Properties) {
    if ($null -ne $property.Value) { [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, 'Process') }
}
if (-not (Test-Path -LiteralPath 'apps/web/node_modules')) {
    Push-Location apps/web
    npm ci
    Pop-Location
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить web зависимости' }
}
Start-LocalLlm $projectRoot $pythonPath ([bool]$Install)
Start-LocalServices $projectRoot $pythonPath $localDir $processFile ([bool]$Dev)
